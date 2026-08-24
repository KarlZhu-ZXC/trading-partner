"""Transport-neutral, secret-safe telemetry boundary."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol

TelemetryValue = str | bool | int | float


class TelemetrySpan(Protocol):
    def set_attribute(self, key: str, value: TelemetryValue) -> None: ...


class Telemetry(Protocol):
    def start_span(
        self,
        name: str,
        attributes: Mapping[str, TelemetryValue] | None = None,
    ) -> AbstractContextManager[TelemetrySpan]: ...

    def current_trace_id(self) -> str | None: ...


class _NoopSpan:
    def set_attribute(self, key: str, value: TelemetryValue) -> None:
        _ = key, value


class _NoopSpanContext:
    def __enter__(self) -> _NoopSpan:
        return _NoopSpan()

    def __exit__(self, *args: object) -> None:
        return None


class NoopTelemetry:
    def start_span(
        self,
        name: str,
        attributes: Mapping[str, TelemetryValue] | None = None,
    ) -> AbstractContextManager[TelemetrySpan]:
        _ = name, attributes
        return _NoopSpanContext()

    def current_trace_id(self) -> str | None:
        return None


NOOP_TELEMETRY = NoopTelemetry()


__all__ = [
    "NOOP_TELEMETRY",
    "NoopTelemetry",
    "Telemetry",
    "TelemetrySpan",
    "TelemetryValue",
]
