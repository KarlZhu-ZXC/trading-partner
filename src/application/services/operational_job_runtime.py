"""Unified durable lease/heartbeat runtime for launchd-triggered jobs."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

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
        outcome: OperationalJobOutcome[T] | None = None
        failure_code: str | None = None
        cancelled: asyncio.CancelledError | None = None
        try:
            outcome = await operation()
        except asyncio.CancelledError as error:
            cancelled = error
            failure_code = "OPERATIONAL_JOB_CANCELLED"
        except Exception as error:  # noqa: BLE001 - persist only safe type/code
            failure_code = (
                error.code
                if isinstance(error, TradingPartnerError)
                and _SAFE_CODE.fullmatch(error.code) is not None
                else "OPERATIONAL_JOB_EXECUTION_FAILED"
            )
        finally:
            stop.set()
            await heartbeat

        async with state_lock:
            current = state[0]
            completed = self._clock.now()
            terminal = await asyncio.to_thread(
                self._repository.finish,
                current.job_run_id,
                lease_owner_hash=self._lease_owner_hash,
                expected_version=current.version,
                status=(
                    outcome.status.value
                    if outcome is not None
                    else OperationalJobStatus.FAILED.value
                ),
                result_code=outcome.result_code if outcome is not None else None,
                error_code=(outcome.error_code if outcome is not None else failure_code),
                completed_at=completed,
            )
        execution = OperationalJobExecution(
            run=terminal,
            invoked=True,
            value=outcome.value if outcome is not None else None,
        )
        if cancelled is not None:
            raise cancelled
        return execution

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
]
