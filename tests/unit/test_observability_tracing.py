from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from domain.common.enums import AppEnvironment, LogLevel
from infrastructure.config.settings import AppSettings
from infrastructure.observability.tracing import (
    OpenTelemetryAdapter,
    _reset_tracing_for_tests,
    configure_tracing,
    hash_telemetry_id,
)


def _settings(tmp_path: Path, **overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "app_name": "tp-observability-test",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": f"sqlite:///{tmp_path / 'trace.db'}",
        "mcp_server_name": "tp-observability-test",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 1.0,
        "otel_tracing_enabled": True,
        "otel_service_name": "tp-test",
        "otel_exporter": "none",
    }
    values.update(overrides)
    return AppSettings(_env_file=None, **values)  # type: ignore[arg-type]


def test_tracing_exports_only_allowlisted_safe_attributes(tmp_path: Path) -> None:
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    telemetry = configure_tracing(
        _settings(tmp_path),
        exporter=exporter,
        register_global=False,
    )
    assert isinstance(telemetry, OpenTelemetryAdapter)
    with telemetry.start_span(
        "agent.turn",
        {
            "tp.prompt": "api_key=must-not-export",
            "tp.operation": "https://secret.example/token",
            "tp.status": "prompt text",
            "http.url": "https://secret.example/token",
        },
    ) as span:
        trace_id = telemetry.current_trace_id()
        span.set_attribute("tp.operation", "attention")
        span.set_attribute("tp.status", "running")
        span.set_attribute("tp.tool_rounds", 2)
        span.set_attribute("tp.payload_size", 100)
    assert telemetry.force_flush()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = dict(spans[0].attributes)
    assert trace_id is not None and len(trace_id) == 32
    assert attributes == {
        "tp.operation": "attention",
        "tp.status": "running",
        "tp.tool_rounds": 2,
    }
    assert "must-not-export" not in repr(spans)
    _reset_tracing_for_tests()


def test_tracing_records_error_type_without_exception_body(tmp_path: Path) -> None:
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    telemetry = configure_tracing(
        _settings(tmp_path),
        exporter=exporter,
        register_global=False,
    )
    with (
        pytest.raises(RuntimeError, match="must-not-export"),
        telemetry.start_span("provider.route"),
    ):
        raise RuntimeError("authorization=must-not-export")
    assert telemetry.force_flush()
    span = exporter.get_finished_spans()[0]
    assert span.attributes["tp.error_type"] == "RuntimeError"
    assert span.events == ()
    assert "must-not-export" not in repr(span)
    _reset_tracing_for_tests()


def test_telemetry_identity_hash_is_stable_and_non_reversible() -> None:
    value = "agent_conversation_private"
    hashed = hash_telemetry_id(value)
    assert hashed == hash_telemetry_id(value)
    assert len(hashed) == 16
    assert value not in hashed


def test_unknown_span_name_cannot_export_sensitive_text(tmp_path: Path) -> None:
    _reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    telemetry = configure_tracing(
        _settings(tmp_path),
        exporter=exporter,
        register_global=False,
    )
    with telemetry.start_span("api_key"):
        pass
    assert telemetry.force_flush()
    spans = exporter.get_finished_spans()
    assert spans[0].name == "tp.operation"
    assert "api_key" not in repr(spans)
    _reset_tracing_for_tests()


def test_otel_endpoint_rejects_embedded_credentials_and_is_redacted(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="without credentials"):
        _settings(
            tmp_path,
            otel_exporter="otlp_http",
            otel_exporter_otlp_endpoint="https://user:password@example.com/v1/traces",
        )
    settings = _settings(
        tmp_path,
        otel_exporter="otlp_http",
        otel_exporter_otlp_endpoint="https://collector.example/v1/traces",
    )
    assert settings.redacted_dict()["otel_exporter_otlp_endpoint"] == "***REDACTED***"
