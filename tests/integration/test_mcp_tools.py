"""MCP tool smoke tests via application container (Phase 1D D8b hard regressions)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from bootstrap import build_application
from domain.common.enums import AppEnvironment, LogLevel, Market, VendorId
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.provider_cache_store import SqlAlchemyProviderCacheStore
from infrastructure.persistence.provider_state_backend import (
    provider_state_schema_ready,
)
from interfaces.mcp.server import PUBLIC_TOOL_NAMES, create_mcp_server

AS_OF = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_create_mcp_server(test_settings: AppSettings) -> None:
    container = build_application(test_settings)
    try:
        server = create_mcp_server(container)
        assert server is not None
    finally:
        container.close()


def test_system_health_via_service(test_settings: AppSettings) -> None:
    container = build_application(test_settings)
    try:
        envelope = container.health_service.check()
        assert envelope.ok is True
        assert envelope.data is not None
        payload = envelope.model_dump(mode="json")
        assert payload["ok"] is True
        assert "status" in payload["data"]
    finally:
        container.close()


async def test_market_mock_snapshot_via_coordinator(test_settings: AppSettings) -> None:
    container = build_application(test_settings)
    try:
        envelope = await container.mock_market_snapshot_coordinator.get_snapshot(
            Market.US, "NVDA", AS_OF
        )
        assert envelope.ok is True
        payload = envelope.model_dump(mode="json")
        assert payload["degraded"] is True
        assert payload["warnings"][0]["code"] == "MOCK_DATA"
        assert payload["data"]["latest_market_row"]["close"] == "173.00"
        assert payload["data"]["instrument"]["instrument_id"] == "equity:US:NVDA"
    finally:
        await container.aclose()


async def test_d8b_fresh_unmigrated_both_markets_fixture_and_mock_data(
    test_settings: AppSettings,
) -> None:
    """Hard #1: fresh unmigrated SQLite — both markets succeed; MOCK_DATA only; fixtures."""
    container = build_application(test_settings)
    try:
        assert provider_state_schema_ready(container.database.engine) is False

        us = await container.mock_market_snapshot_coordinator.get_snapshot(Market.US, "NVDA", AS_OF)
        assert us.ok is True
        assert us.degraded is True
        assert us.warnings[0].code == "MOCK_DATA"
        # First live call: only business warning is MOCK_DATA (no CACHE_SERVED yet).
        assert [w.code for w in us.warnings] == ["MOCK_DATA"]
        assert us.data is not None
        assert us.data.latest_market_row.close == Decimal("173.00")
        assert us.data.instrument.instrument_id == "equity:US:NVDA"

        a = await container.mock_market_snapshot_coordinator.get_snapshot(
            Market.A_SHARE, "600519.SH", AS_OF
        )
        assert a.ok is True
        assert a.degraded is True
        assert a.warnings[0].code == "MOCK_DATA"
        assert [w.code for w in a.warnings] == ["MOCK_DATA"]
        assert a.data is not None
        assert a.data.latest_market_row.close == Decimal("1505.00")
        assert a.data.instrument.instrument_id == "equity:A_SHARE:600519.SH"
    finally:
        await container.aclose()


async def test_d8b_second_request_cache_hit_mock_data_first_and_cache_served(
    test_settings: AppSettings,
) -> None:
    """Hard #2: second identical request hits memory cache; adapter not re-called."""
    container = build_application(test_settings)
    try:
        # Spy on underlying mock provider after adapters are wired.
        us_adapter = container.vendor_registry.get(VendorId.MOCK_US)
        original = us_adapter._provider.get_snapshot  # type: ignore[attr-defined]
        call_count = {"n": 0}

        async def _counting(instrument: Any, as_of: datetime) -> Any:
            call_count["n"] += 1
            return await original(instrument, as_of)

        with patch.object(
            us_adapter._provider,  # type: ignore[attr-defined]
            "get_snapshot",
            new=AsyncMock(side_effect=_counting),
        ):
            first = await container.mock_market_snapshot_coordinator.get_snapshot(
                Market.US, "NVDA", AS_OF
            )
            assert first.ok is True
            assert [w.code for w in first.warnings] == ["MOCK_DATA"]
            assert call_count["n"] == 1

            second = await container.mock_market_snapshot_coordinator.get_snapshot(
                Market.US, "NVDA", AS_OF
            )
            assert second.ok is True
            codes = [w.code for w in second.warnings]
            assert codes[0] == "MOCK_DATA"
            assert "CACHE_SERVED" in codes
            assert call_count["n"] == 1  # adapter not called again
            assert second.data is not None
            assert second.data.latest_market_row.close == Decimal("173.00")
    finally:
        await container.aclose()


def _set_alembic_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    """Alembic env.py loads URL via AppSettings — must set process env."""
    for key in list(__import__("os").environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "d8b-migrated-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "d8b-migrated-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


@pytest.mark.asyncio
async def test_d8b_migrated_0003_sql_provider_state_rows(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard #3: after migration 0003, SQL stores write cache/health/rate rows."""
    db_path = tmp_path / "migrated_d8b.db"
    database_url = f"sqlite:///{db_path}"
    _set_alembic_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="trading-partner-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=database_url,
        mcp_server_name="trading-partner-test",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
    )
    container = build_application(settings)
    try:
        assert provider_state_schema_ready(container.database.engine) is True
        engine = container.provider_router._engine  # type: ignore[attr-defined]
        assert isinstance(engine._cache_store, SqlAlchemyProviderCacheStore)  # type: ignore[attr-defined]

        env = await container.mock_market_snapshot_coordinator.get_snapshot(
            Market.US, "NVDA", AS_OF
        )
        assert env.ok is True
        assert env.warnings[0].code == "MOCK_DATA"

        with container.database.engine.connect() as conn:
            cache_count = conn.execute(text("SELECT COUNT(*) FROM provider_cache")).scalar()
            health_count = conn.execute(text("SELECT COUNT(*) FROM provider_health")).scalar()
            rate_count = conn.execute(text("SELECT COUNT(*) FROM provider_rate_limits")).scalar()
        assert cache_count is not None and int(cache_count) >= 1
        assert health_count is not None and int(health_count) >= 1
        assert rate_count is not None and int(rate_count) >= 1
    finally:
        await container.aclose()


async def test_mcp_tool_functions_registered(test_settings: AppSettings) -> None:
    """Wire contract: public surface is exactly 30 tools through Phase 1F."""
    container = build_application(test_settings)
    try:
        server = create_mcp_server(container)
        tools = getattr(server, "_tool_manager", None) or getattr(server, "_tools", None)
        if tools is not None:
            names: set[str] = set()
            if hasattr(tools, "list_tools"):
                listed = tools.list_tools()
                if hasattr(listed, "__iter__"):
                    for t in listed:
                        name = getattr(t, "name", None) or (
                            t.get("name") if isinstance(t, dict) else None
                        )
                        if name:
                            names.add(name)
            elif isinstance(tools, dict):
                names = set(tools.keys())
            elif hasattr(tools, "_tools"):
                names = set(tools._tools.keys())
            if names:
                assert names == set(PUBLIC_TOOL_NAMES)
                assert "system_health" in names
                assert "system_health" in names
                assert "investment_case_create" in names
                assert "instrument_resolve" in names
                assert "research_search" in names
                assert "journal_append" in names
                assert "decision_record_append" in names
                assert "a_share_get_facts" in names
                assert "research_search_reports" in names
                assert "evidence_create" not in names
                assert len(names) == 52
        assert len(PUBLIC_TOOL_NAMES) == 52
    finally:
        await container.aclose()
