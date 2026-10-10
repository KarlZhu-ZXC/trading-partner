"""Bounded invocation metering; payloads never enter the usage store."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter_ns
from typing import Any

from pydantic import ValidationError

from application.ports.capability_usage import CapabilityUsageRecord, CapabilityUsageStore

_SOURCE: ContextVar[str | None] = ContextVar("capability_usage_source", default=None)
_SOURCES = frozenset({"MCP", "CONSOLE", "AGENT", "INTERNAL"})


@contextmanager
def capability_usage_source(source: str) -> Iterator[None]:
    if source not in _SOURCES:
        raise ValueError("Invalid usage source")
    token = _SOURCE.set(source)
    try:
        yield
    finally:
        _SOURCE.reset(token)


def _failure_outcome(error: BaseException) -> str:
    cause = getattr(error, "cause", None)
    if isinstance(cause, BaseException):
        error = cause
    if isinstance(error, asyncio.CancelledError):
        return "CANCELLED"
    if isinstance(error, PermissionError):
        return "DENIED"
    if isinstance(error, ValidationError | LookupError):
        return "INVALID_REQUEST"
    return "ERROR"


def _result_outcome(result: Any) -> str:
    if isinstance(result, Mapping) and result.get("ok") is False:
        errors = result.get("errors")
        if isinstance(errors, list | tuple) and any(
            isinstance(error, Mapping) and error.get("code") == "TOOL_INPUT_INVALID"
            for error in errors
        ):
            return "INVALID_REQUEST"
        return "TOOL_FAILURE"
    return "OK"


class CapabilityUsageMeter:
    def __init__(
        self, store: CapabilityUsageStore | None = None, *, source: str = "INTERNAL"
    ) -> None:
        if source not in _SOURCES:
            raise ValueError("Invalid usage source")
        self._store = store
        self._source = source
        self.recording_failures = 0

    async def call(
        self,
        capability: str,
        operation: str,
        invoke: Callable[[], Awaitable[Any]],
    ) -> Any:
        if self._store is None:
            return await invoke()
        started = perf_counter_ns()
        outcome = "ERROR"
        try:
            result = await invoke()
            try:
                outcome = _result_outcome(result)
            except Exception:  # noqa: BLE001 - classification is diagnostic, not business validation
                self.recording_failures += 1
                outcome = "OK"
            return result
        except BaseException as error:
            outcome = _failure_outcome(error)
            raise
        finally:
            elapsed_ms = min(86_400_000, max(0, (perf_counter_ns() - started) // 1_000_000))
            try:
                # No await/retry here: diagnostic failure or cancellation must
                # never replace the known outcome of a broker or research call.
                self._store.record(
                    CapabilityUsageRecord(
                        source=_SOURCE.get() or self._source,
                        capability=capability,
                        operation=operation,
                        outcome=outcome,
                        elapsed_ms=elapsed_ms,
                    )
                )
            except Exception:  # noqa: BLE001 - diagnostics cannot change business outcomes
                self.recording_failures += 1
