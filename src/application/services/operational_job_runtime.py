"""Unified durable lease/heartbeat runtime for launchd-triggered jobs."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.operational_job_repository import OperationalJobRepository
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.operations.enums import OperationalJobStatus
from domain.operations.models import OperationalJobRun

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")

_HEARTBEAT_FAILURE_CODE = "OPERATIONAL_JOB_HEARTBEAT_FAILED"
_FINISH_FAILURE_CODE = "OPERATIONAL_JOB_FINISH_FAILED"
_CLAIM_CALLBACK_FAILURE_CODE = "OPERATIONAL_JOB_CLAIM_CALLBACK_FAILED"


class OperationalJobRuntimeError(TradingPartnerError):
    """Safe application error for a job receipt that cannot be finalized."""

    default_code = "OPERATIONAL_JOB_RUNTIME_FAILED"


@dataclass(frozen=True, slots=True)
class OperationalJobOutcome[T]:
    status: OperationalJobStatus
    result_code: str
    value: T | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is OperationalJobStatus.RUNNING:
            raise ValueError("Operational Job outcome must be terminal")
        if _SAFE_CODE.fullmatch(self.result_code) is None:
            raise ValueError("Operational Job result_code is invalid")
        if self.error_code is not None and _SAFE_CODE.fullmatch(self.error_code) is None:
            raise ValueError("Operational Job error_code is invalid")
        if self.status in {OperationalJobStatus.FAILED, OperationalJobStatus.INTERRUPTED}:
            if self.error_code is None:
                raise ValueError("Failed/interrupted Operational Job outcome requires error_code")
        elif self.error_code is not None:
            raise ValueError("Successful/skipped Operational Job outcome cannot carry error_code")


@dataclass(frozen=True, slots=True)
class OperationalJobExecution[T]:
    run: OperationalJobRun
    invoked: bool
    value: T | None = None


class DurableOperationalJobRuntime:
    def __init__(
        self,
        repository: OperationalJobRepository,
        clock: Clock,
        id_generator: IdGenerator,
        *,
        lease_owner: str | None = None,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = id_generator
        raw_owner = lease_owner or secrets.token_hex(16)
        self._lease_owner_hash = hashlib.sha256(raw_owner.encode()).hexdigest()[:32]
        if heartbeat_interval_seconds is not None and heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def execute[T](
        self,
        *,
        job_name: str,
        idempotency_key: str,
        operation: Callable[[], Awaitable[OperationalJobOutcome[T]]],
        lease_seconds: int = 90,
        on_claim: Callable[[OperationalJobRun], None] | None = None,
    ) -> OperationalJobExecution[T]:
        if _SAFE_NAME.fullmatch(job_name) is None:
            raise ValueError("job_name is invalid")
        if _SAFE_KEY.fullmatch(idempotency_key) is None:
            raise ValueError("idempotency_key is invalid")
        if not 30 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be in [30,3600]")
        now = self._clock.now()
        candidate = OperationalJobRun(
            job_run_id=self._ids.new(EntityIdPrefix.OPERATIONAL_JOB_RUN),
            job_name=job_name,
            idempotency_key=idempotency_key,
            status=OperationalJobStatus.RUNNING,
            attempt=1,
            lease_owner_hash=self._lease_owner_hash,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            started_at=now,
            updated_at=now,
        )
        claim = await asyncio.to_thread(self._repository.claim, candidate, now=now)
        try:
            if on_claim is not None:
                on_claim(claim.run)
        except Exception:  # noqa: BLE001 - callback text must not escape
            if claim.claimed:
                await self._finish_claim_callback_failure(claim.run)
            raise self._safe_runtime_error(_CLAIM_CALLBACK_FAILURE_CODE) from None
        if not claim.claimed:
            return OperationalJobExecution(run=claim.run, invoked=False)

        state = [claim.run]
        stop = asyncio.Event()
        state_lock = asyncio.Lock()
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(
                state,
                state_lock,
                stop,
                lease_seconds=lease_seconds,
            )
        )

        async def invoke_operation() -> OperationalJobOutcome[T]:
            # Keep invocation inside the child task so even a callable that
            # raises before returning its awaitable is captured and finalized.
            return await operation()

        operation_task = asyncio.create_task(invoke_operation())
        outcome: OperationalJobOutcome[T] | None = None
        failure_code: str | None = None
        cancelled: asyncio.CancelledError | None = None
        heartbeat_failure: BaseException | None = None
        externally_cancelled: asyncio.CancelledError | None = None
        try:
            done, _ = await asyncio.wait(
                {operation_task, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                heartbeat_failure = self._task_exception(heartbeat)
                if heartbeat_failure is not None:
                    # A failed lease is no longer safe for business work.  The
                    # operation may have reached an external side effect, so
                    # cancel and drain it once; never invoke it again.
                    operation_task.cancel()
                    await self._drain_task(operation_task)
                else:
                    outcome, failure_code, cancelled = self._operation_result(operation_task)
            else:
                outcome, failure_code, cancelled = self._operation_result(operation_task)
        except asyncio.CancelledError as error:
            # Preserve caller cancellation while still stopping the operation
            # and heartbeat before attempting the durable terminal receipt.
            externally_cancelled = error
            operation_task.cancel()
            await self._drain_task(operation_task)
        finally:
            stop.set()
            heartbeat_error = await self._drain_task(heartbeat)
            if heartbeat_error is not None and heartbeat_failure is None:
                heartbeat_failure = heartbeat_error

        error_code: str | None
        if heartbeat_failure is not None:
            outcome = None
            failure_code = _HEARTBEAT_FAILURE_CODE
            cancelled = None
            terminal_status = OperationalJobStatus.INTERRUPTED
            result_code = None
            error_code = _HEARTBEAT_FAILURE_CODE
        elif externally_cancelled is not None:
            outcome = None
            failure_code = "OPERATIONAL_JOB_CANCELLED"
            terminal_status = OperationalJobStatus.INTERRUPTED
            result_code = None
            error_code = failure_code
        elif cancelled is not None:
            terminal_status = OperationalJobStatus.INTERRUPTED
            result_code = None
            error_code = failure_code
        elif outcome is not None:
            terminal_status = outcome.status
            result_code = outcome.result_code
            error_code = outcome.error_code
        else:
            terminal_status = OperationalJobStatus.FAILED
            result_code = None
            error_code = failure_code or "OPERATIONAL_JOB_EXECUTION_FAILED"

        try:
            terminal = await self._finish(
                state[0],
                state_lock=state_lock,
                status=terminal_status,
                result_code=result_code,
                error_code=error_code,
            )
        except OperationalJobRuntimeError:
            if externally_cancelled is not None:
                raise externally_cancelled from None
            if cancelled is not None:
                raise cancelled from None
            raise
        execution = OperationalJobExecution(
            run=terminal,
            invoked=True,
            value=(
                outcome.value
                if outcome is not None
                and terminal.attempt == state[0].attempt
                and terminal.lease_owner_hash == state[0].lease_owner_hash
                and terminal.status is outcome.status
                and terminal.result_code == outcome.result_code
                else None
            ),
        )
        if externally_cancelled is not None:
            raise externally_cancelled
        if cancelled is not None:
            raise cancelled
        return execution

    @staticmethod
    def _task_exception(task: asyncio.Task[Any]) -> BaseException | None:
        """Read a completed task's exception without allowing it to leak."""

        try:
            task.result()
        except BaseException as error:  # noqa: BLE001 - task lifecycle inspection
            return error
        return None

    @staticmethod
    async def _drain_task(task: asyncio.Task[Any]) -> BaseException | None:
        """Await a child task and return, rather than propagate, its failure."""

        try:
            await task
        except BaseException as error:  # noqa: BLE001 - cleanup must complete
            return error
        return None

    @staticmethod
    def _operation_result[T](
        task: asyncio.Task[OperationalJobOutcome[T]],
    ) -> tuple[OperationalJobOutcome[T] | None, str | None, asyncio.CancelledError | None]:
        try:
            result = task.result()
            if not isinstance(result, OperationalJobOutcome):
                return None, "OPERATIONAL_JOB_EXECUTION_FAILED", None
            return result, None, None
        except asyncio.CancelledError as error:
            return None, "OPERATIONAL_JOB_CANCELLED", error
        except TimeoutError:
            return None, "OPERATIONAL_JOB_TIMEOUT", None
        except Exception as error:  # noqa: BLE001 - persist only safe type/code
            return (
                None,
                error.code
                if isinstance(error, TradingPartnerError)
                and _SAFE_CODE.fullmatch(error.code) is not None
                else "OPERATIONAL_JOB_EXECUTION_FAILED",
                None,
            )

    def _safe_runtime_error(self, code: str) -> OperationalJobRuntimeError:
        return OperationalJobRuntimeError(
            "Operational Job receipt could not be finalized.",
            code=code,
        )

    async def _finish_claim_callback_failure(self, claimed: OperationalJobRun) -> None:
        """Close a claim whose callback failed before operation dispatch."""

        try:
            await asyncio.to_thread(
                self._repository.finish,
                claimed.job_run_id,
                lease_owner_hash=self._lease_owner_hash,
                expected_version=claimed.version,
                status=OperationalJobStatus.FAILED.value,
                result_code=None,
                error_code=_CLAIM_CALLBACK_FAILURE_CODE,
                completed_at=self._clock.now(),
            )
        except BaseException:  # noqa: BLE001 - no operation may be re-executed
            # A failed callback must never leave a freshly claimed operation
            # looking available.  A later recovery pass will close an unknown
            # receipt if this write itself did not land.
            return

    async def _finish(
        self,
        current: OperationalJobRun,
        *,
        state_lock: asyncio.Lock,
        status: OperationalJobStatus,
        result_code: str | None,
        error_code: str | None,
    ) -> OperationalJobRun:
        async with state_lock:
            write_task = asyncio.create_task(
                asyncio.to_thread(
                    self._repository.finish,
                    current.job_run_id,
                    lease_owner_hash=self._lease_owner_hash,
                    expected_version=current.version,
                    status=status.value,
                    result_code=result_code,
                    error_code=error_code,
                    completed_at=self._clock.now(),
                )
            )
            try:
                return await asyncio.shield(write_task)
            except asyncio.CancelledError as cancellation:
                # Finish is a single write.  If the caller is cancelled while
                # it is in flight, drain that write before preserving the
                # caller's cancellation semantics.
                try:
                    await write_task
                except BaseException:
                    await self._read_receipt(current.job_run_id)
                raise cancellation from None
            except BaseException:
                # A finish write is never retried.  Read once to distinguish a
                # known durable receipt from an unknown write outcome.  A
                # different lease owner remains protected by repository CAS.
                receipt = await self._read_receipt(current.job_run_id)
                if receipt is not None and receipt.status is not OperationalJobStatus.RUNNING:
                    return receipt
                raise self._safe_runtime_error(
                    _HEARTBEAT_FAILURE_CODE
                    if error_code == _HEARTBEAT_FAILURE_CODE
                    else _FINISH_FAILURE_CODE
                ) from None

    async def _read_receipt(self, job_run_id: str) -> OperationalJobRun | None:
        try:
            return await asyncio.to_thread(self._repository.get, job_run_id)
        except asyncio.CancelledError:
            raise
        except BaseException:
            return None

    async def _heartbeat_loop(
        self,
        state: list[OperationalJobRun],
        state_lock: asyncio.Lock,
        stop: asyncio.Event,
        *,
        lease_seconds: int,
    ) -> None:
        interval = self._heartbeat_interval_seconds or max(10.0, lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            async with state_lock:
                now = self._clock.now()
                state[0] = await asyncio.to_thread(
                    self._repository.heartbeat,
                    state[0].job_run_id,
                    lease_owner_hash=self._lease_owner_hash,
                    expected_version=state[0].version,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )

    async def get_by_key(self, job_name: str, idempotency_key: str) -> OperationalJobRun | None:
        return await asyncio.to_thread(self._repository.get_by_key, job_name, idempotency_key)

    async def recover_expired(self, *, limit: int = 100) -> int:
        return await asyncio.to_thread(
            self._repository.recover_expired,
            now=self._clock.now(),
            limit=limit,
        )


__all__ = [
    "DurableOperationalJobRuntime",
    "OperationalJobExecution",
    "OperationalJobOutcome",
    "OperationalJobRuntimeError",
]
