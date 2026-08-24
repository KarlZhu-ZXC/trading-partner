"""Secret-safe OpenTelemetry configuration and application-port adapter."""

from __future__ import annotations

import atexit
import hashlib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode

from application.ports.telemetry import (
    NOOP_TELEMETRY,
    Telemetry,
    TelemetrySpan,
    TelemetryValue,
)
from infrastructure.config.settings import AppSettings

_SAFE_SPAN_NAMES = frozenset({"agent.turn", "provider.route"})
_SAFE_ATTRIBUTE_KEY = re.compile(r"^tp\.[a-z][a-z0-9_.-]{0,95}$")
_SENSITIVE_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "header",
    "password",
    "payload",
    "prompt",
    "query",
    "secret",
    "token",
    "url",
)
_SAFE_STRING_ATTRIBUTES = {
    "tp.category": re.compile(r"^[A-Z][A-Z0-9_]{0,79}$"),
    "tp.channel": re.compile(r"^[a-z][a-z0-9_]{0,39}$"),
    "tp.criticality": re.compile(r"^[A-Z][A-Z0-9_]{0,39}$"),
    "tp.error_code": re.compile(r"^[A-Z][A-Z0-9_]{0,95}$"),
    "tp.error_type": re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$"),
    "tp.market": re.compile(r"^[A-Z][A-Z0-9_]{0,39}$"),
    "tp.model_id": re.compile(r"^[a-z][a-z0-9_.-]{0,79}$"),
    "tp.operation": re.compile(r"^[a-z][a-z0-9_.-]{0,95}$"),
    "tp.selected_vendor": re.compile(r"^[a-z][a-z0-9_]{0,79}$"),
    "tp.status": re.compile(r"^[a-z][a-z0-9_]{0,39}$"),
}
_SAFE_NUMERIC_ATTRIBUTES = {
    "tp.attempt_count",
    "tp.chain_length",
    "tp.content_chars",
    "tp.tool_receipts",
    "tp.tool_rounds",
}
_SAFE_BOOLEAN_ATTRIBUTES = {"tp.bypass_cache", "tp.web_search_used"}


def hash_telemetry_id(value: str) -> str:
    """Return a stable non-reversible correlation key for an opaque identity."""

    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _safe_attributes(
    values: Mapping[str, TelemetryValue] | None,
) -> dict[str, TelemetryValue]:
    result: dict[str, TelemetryValue] = {}
    for key, value in (values or {}).items():
        lowered = key.casefold()
        if (
            _SAFE_ATTRIBUTE_KEY.fullmatch(key) is None
            or any(part in lowered for part in _SENSITIVE_PARTS)
        ):
            continue
        string_pattern = _SAFE_STRING_ATTRIBUTES.get(key)
        if isinstance(value, str) and string_pattern is not None:
            if string_pattern.fullmatch(value) is not None:
                result[key] = value
        elif (
            isinstance(value, bool)
            and key in _SAFE_BOOLEAN_ATTRIBUTES
            or isinstance(value, int | float)
            and not isinstance(value, bool)
            and key in _SAFE_NUMERIC_ATTRIBUTES
        ):
            result[key] = value
    return result


class _SpanAdapter:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: TelemetryValue) -> None:
        safe = _safe_attributes({key: value})
        if key in safe:
            self._span.set_attribute(key, safe[key])


class OpenTelemetryAdapter:
    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer("trading_partner")

    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: Mapping[str, TelemetryValue] | None = None,
    ) -> Iterator[TelemetrySpan]:
        safe_name = name if name in _SAFE_SPAN_NAMES else "tp.operation"
        with self._tracer.start_as_current_span(
            safe_name,
            attributes=_safe_attributes(attributes),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            adapter = _SpanAdapter(span)
            try:
                yield adapter
            except BaseException as error:
                # Exception messages and stack traces may contain Provider
                # payloads or credentials, so retain only the bounded type.
                adapter.set_attribute("tp.error_type", type(error).__name__[:80])
                span.set_status(Status(StatusCode.ERROR))
                raise

    def current_trace_id(self) -> str | None:
        span = trace.get_current_span()
        context = span.get_span_context()
        if not context.is_valid:
            return None
        return f"{context.trace_id:032x}"

    def force_flush(self, timeout_millis: int = 5_000) -> bool:
        return bool(self._provider.force_flush(timeout_millis=timeout_millis))

    def shutdown(self) -> None:
        self._provider.shutdown()


@dataclass(slots=True)
class _TracingState:
    telemetry: OpenTelemetryAdapter
    registered_global: bool


_LOCK = Lock()
_STATE: _TracingState | None = None


def configure_tracing(
    settings: AppSettings,
    *,
    exporter: SpanExporter | None = None,
    register_global: bool = True,
) -> Telemetry:
    """Configure one process-wide tracer; disabled mode remains a no-op."""

    global _STATE
    if not settings.otel_tracing_enabled:
        return NOOP_TELEMETRY
    with _LOCK:
        if _STATE is not None:
            return _STATE.telemetry
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.otel_service_name,
                    "service.version": "0.6.0",
                }
            ),
            sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
        )
        selected_exporter = exporter
        if selected_exporter is None and settings.otel_exporter == "otlp_http":
            selected_exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
            )
        if selected_exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(selected_exporter))
        if register_global:
            trace.set_tracer_provider(provider)
        telemetry = OpenTelemetryAdapter(provider)
        _STATE = _TracingState(telemetry=telemetry, registered_global=register_global)
        atexit.register(telemetry.shutdown)
        return telemetry


def get_telemetry() -> Telemetry:
    return _STATE.telemetry if _STATE is not None else NOOP_TELEMETRY


def _reset_tracing_for_tests() -> None:
    global _STATE
    with _LOCK:
        if _STATE is not None:
            _STATE.telemetry.shutdown()
        _STATE = None


__all__ = [
    "OpenTelemetryAdapter",
    "configure_tracing",
    "get_telemetry",
    "hash_telemetry_id",
]
