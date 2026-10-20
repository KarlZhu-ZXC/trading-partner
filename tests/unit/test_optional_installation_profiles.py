"""Minimal installs fail explicitly at optional operation boundaries, never at discovery."""

from __future__ import annotations

import builtins
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from domain.common.errors import ProviderNotConfigured
from infrastructure.calendars.kr_market_session_calendar import XkrxMarketSessionCalendar
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.config.settings import AppSettings
from infrastructure.observability.tracing import (
    _reset_tracing_for_tests,
    configure_tracing,
    get_telemetry,
)
from infrastructure.technical import MatplotlibChartRenderer, TALibIndicatorEngine

ROOT = Path(__file__).resolve().parents[2]


def test_install_profiles_preserve_full_developer_and_chart_capabilities():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(row for row in lock["package"] if row["name"] == "trading-partner")
    core = {row["name"] for row in package["dependencies"]}
    optional = {
        "ta-lib",
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-http",
        "yfinance",
        "exchange-calendars",
    }
    assert not optional & core
    assert "opentelemetry-api" in core
    required = optional
    assert required <= {row["name"] for row in package["optional-dependencies"]["all"]}
    assert required <= {row["name"] for row in package["dev-dependencies"]["dev"]}
    assert {"matplotlib", "ta-lib"} <= {
        row["name"] for row in package["optional-dependencies"]["chart"]
    }
    assert set(project["project"]["optional-dependencies"]) == set(
        package["metadata"]["provides-extras"]
    )
    assert {"yfinance", "exchange-calendars"} == {
        row["name"] for row in package["optional-dependencies"]["market-data"]
    }


@pytest.mark.parametrize("dependency", ["talib", "numpy"])
@pytest.mark.parametrize("operation", ["analysis", "chart"])
def test_missing_numerical_dependency_identifies_install_extra(monkeypatch, dependency, operation):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith("infrastructure.technical."):
            raise ModuleNotFoundError("sensitive upstream detail", name=dependency)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ProviderNotConfigured) as caught:
        if operation == "analysis":
            TALibIndicatorEngine().analyze((), interval="1d")
        else:
            MatplotlibChartRenderer().render(
                instrument_id="equity:US:SYNTHETIC",
                bars=(),
                analysis=None,  # No backend loads, so no analysis is consulted.
            )
    extra = "technical" if operation == "analysis" else "chart"
    assert caught.value.code == "PROVIDER_NOT_CONFIGURED"
    assert caught.value.retryable is False
    assert caught.value.details["extra"] == extra
    assert f"trading-partner[{extra}]" in str(caught.value)
    assert "sensitive" not in str(caught.value)


def test_unrelated_technical_import_bug_is_not_masked_as_missing_extra(monkeypatch):
    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name.startswith("infrastructure.technical."):
            raise ModuleNotFoundError("unrelated code bug", name="synthetic_internal_bug")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ModuleNotFoundError, match="unrelated code bug"):
        TALibIndicatorEngine().analyze((), interval="1d")


@pytest.mark.parametrize("factory", [XnysMarketSessionCalendar, XkrxMarketSessionCalendar])
def test_missing_market_extra_never_invents_exchange_sessions(factory, monkeypatch):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "exchange_calendars":
            raise ModuleNotFoundError("sensitive upstream detail", name=name)
        return real_import(name, *args, **kwargs)

    calendar = factory()
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", missing)
        with pytest.raises(ProviderNotConfigured) as caught:
            calendar.session_at(datetime(2026, 10, 1, tzinfo=UTC))
        assert caught.value.details["extra"] == "market-data"
        assert "sensitive" not in str(caught.value)
        assert "_calendar" not in calendar.__dict__
    # An installation/repair can be retried; no failure is memoized.
    assert calendar.session_at(datetime(2026, 10, 1, 18, tzinfo=UTC)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["fundamentals", "statements", "breadth", "calendar"])
async def test_missing_yahoo_extra_fails_before_worker_network_or_data_access(
    monkeypatch, operation
):
    from infrastructure.providers.us.yfinance_breadth_client import YFinanceBreadthClient
    from infrastructure.providers.us.yfinance_calendar_client import YFinanceCalendarClient
    from infrastructure.providers.us.yfinance_fundamentals_client import YFinanceFundamentalsClient
    from infrastructure.providers.us.yfinance_statements_client import YFinanceStatementsClient

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "yfinance.exceptions":
            raise ModuleNotFoundError("sensitive upstream detail", name="yfinance")
        return real_import(name, *args, **kwargs)

    worker = MagicMock(side_effect=AssertionError("must not start a provider worker"))
    monkeypatch.setattr("asyncio.to_thread", worker)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ProviderNotConfigured) as caught:
        if operation == "fundamentals":
            await YFinanceFundamentalsClient().get_info("SYNTHETIC", timeout_seconds=1)
        elif operation == "statements":
            await YFinanceStatementsClient().get_tables(
                "SYNTHETIC", frequency="yearly", timeout_seconds=1
            )
        elif operation == "breadth":
            await YFinanceBreadthClient().get_current(timeout_seconds=1)
        else:
            await YFinanceCalendarClient._run(worker, timeout_seconds=1)
    assert caught.value.details == {
        "vendor": "yfinance",
        "extra": "market-data",
        "operation": operation,
    }
    worker.assert_not_called()


@pytest.mark.asyncio
async def test_missing_calendar_stops_sgov_before_account_quote_notification_or_order(monkeypatch):
    from application.services.sgov_shadow_plan_service import SgovShadowPlanService

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "exchange_calendars":
            raise ModuleNotFoundError("synthetic absent calendar", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    portfolio, preview, notifications, orders, clock = (MagicMock() for _ in range(5))
    clock.now.return_value = datetime(2026, 10, 1, 18, tzinfo=UTC)
    service = SgovShadowPlanService(
        calendar=XnysMarketSessionCalendar(),
        portfolio=portfolio,
        preview=preview,
        broker_orders=orders,
        notifications=notifications,
        clock=clock,
    )
    with pytest.raises(ProviderNotConfigured):
        await service.run_if_due(auto_execute=True)
    for dependency in (portfolio, preview, notifications, orders):
        assert not dependency.mock_calls


@pytest.mark.parametrize("dependency", ["opentelemetry.sdk", "opentelemetry.exporter"])
def test_explicit_tracing_missing_extra_fails_and_can_retry_after_install(
    tmp_path, monkeypatch, dependency
):
    from application.ports.telemetry import NOOP_TELEMETRY

    _reset_tracing_for_tests()
    settings = AppSettings(
        _env_file=None,
        app_name="synthetic",
        app_env="test",
        log_level="INFO",
        database_url="sqlite:///:memory:",
        mcp_server_name="synthetic",
        default_timezone="UTC",
        provider_timeout_seconds=1,
        runtime_root=tmp_path,
        otel_tracing_enabled=True,
        otel_exporter="otlp_http",
        otel_exporter_otlp_endpoint="http://127.0.0.1:4318/v1/traces",
    )
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith(dependency):
            raise ModuleNotFoundError("sensitive endpoint detail", name=dependency)
        return real_import(name, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(builtins, "__import__", missing)
            with pytest.raises(ProviderNotConfigured) as caught:
                configure_tracing(settings, register_global=False)
            assert caught.value.details["extra"] == "tracing"
            assert "sensitive" not in str(caught.value)
            assert get_telemetry() is NOOP_TELEMETRY
        # Retry has no remote exporter; do not send spans or contact a collector.
        settings = settings.model_copy(update={"otel_exporter": "none"})
        telemetry = configure_tracing(settings, register_global=False)
        assert telemetry is not NOOP_TELEMETRY
        assert get_telemetry() is telemetry
    finally:
        _reset_tracing_for_tests()
