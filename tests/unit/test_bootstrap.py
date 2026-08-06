"""Bootstrap composition root tests (Phase 1D D8b + Phase 1E E5b)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from application.services.post_market_sync_service import PostMarketSyncService
from application.services.watchlist_hub_service import WatchlistHubService
from bootstrap import (
    ApplicationContainer,
    BootstrapOverrides,
    build_application,
)
from domain.common.enums import AppEnvironment, LogLevel, VendorId
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.composition import enabled_account_provider_order
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.in_memory_provider_state import (
    InMemoryProviderCacheStore,
)
from infrastructure.persistence.provider_state_backend import (
    provider_state_schema_ready,
)
from infrastructure.providers.a_share.codecs import (
    CODEC_CHIP_DISTRIBUTION,
    CODEC_SENTIMENT,
)
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.tencent import TencentAShareAdapter
from infrastructure.providers.a_share.trading_calendar import (
    JsonAShareTradingCalendar,
    load_default_a_share_trading_calendar,
)
from infrastructure.providers.common.httpx_transport import HttpxTransport
from infrastructure.providers.common.null_category_provider import NullCategoryProvider
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine
from interfaces.mcp.server import PUBLIC_TOOL_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PACKAGES = ("application", "domain", "infrastructure", "interfaces")
AS_OF = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)

# Exact registry: real A-share/US/cross-asset vendors plus NULL.
_REAL_A_SHARE_VENDORS = (
    VendorId.TENCENT,
    VendorId.EASTMONEY,
    VendorId.SINA,
    VendorId.CNINFO,
    VendorId.THS,
    VendorId.CLS,
    VendorId.SSE,
    VendorId.SZSE,
    VendorId.HKEX,
    VendorId.IWENCAI,
    VendorId.NAHS,
)
_REAL_US_VENDORS = (
    VendorId.YFINANCE,
    VendorId.SINA_FUTURES,
    VendorId.EASTMONEY_FUTURES,
    VendorId.ALPHA_VANTAGE,
    VendorId.SEC_EDGAR,
    VendorId.FRED,
    VendorId.REDDIT,
    VendorId.MOOMOO_FEED,
    VendorId.POLYMARKET,
    VendorId.SCHWAB,
    VendorId.MOOMOO,
    VendorId.MANUAL_CSV,
    VendorId.CME_PUBLIC,
    VendorId.DCE_OFFICIAL,
    VendorId.DUKASCOPY,
    VendorId.IG_WEEKEND_GOLD,
)
_EXPECTED_REGISTERED = (
    frozenset(_REAL_A_SHARE_VENDORS) | frozenset(_REAL_US_VENDORS) | frozenset({VendorId.NULL})
)


def test_enabled_account_provider_order_filters_optional_unselected_sources() -> None:
    candidates = (VendorId.SCHWAB, VendorId.MOOMOO, VendorId.MANUAL_CSV)

    assert enabled_account_provider_order(candidates, ("SCHWAB", "MOOMOO")) == (
        VendorId.SCHWAB,
        VendorId.MOOMOO,
    )
    assert enabled_account_provider_order(candidates, ("MANUAL_CSV",)) == (
        VendorId.MANUAL_CSV,
    )


class _FakeTransport:
    """Minimal transport that records aclose without network I/O."""

    def __init__(self) -> None:
        self.aclose_calls = 0
        self.send_calls = 0

    async def send(self, request: object) -> object:
        self.send_calls += 1
        raise AssertionError("bootstrap identity tests must not hit the network")

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _RecordingDatabase:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.closed = False

    def close(self) -> None:
        self._events.append("database.close")
        self.closed = True


class _RecordingTransport:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self._events.append("transport.aclose")
        self.aclose_calls += 1


def test_build_application_returns_container(test_settings: AppSettings) -> None:
    container = build_application(test_settings)
    try:
        assert isinstance(container, ApplicationContainer)
        assert container.settings is test_settings
        assert container.context.clock is not None
        assert container.context.id_generator is not None
        assert container.context.secret_redactor is not None
        assert isinstance(container.providers.registry, VendorRegistry)
        assert container.providers.router is not None
        assert container.services.health is not None
        assert container.services.investment_cases is not None
        assert container.services.thesis_revisions is not None
        assert container.services.research_state is not None
        assert container.services.instruments is not None
        assert container.services.research_archive is not None
        assert container.services.research_search is not None
        assert container.services.research_timeline is not None
        assert container.services.journal is not None
        assert container.services.decisions is not None
        assert container.services.a_share is not None
        assert container.services.us_market is not None
        assert container.services.market is not None
        assert container.services.technical is not None
        assert container.services.us_research is not None
        assert container.services.us_context is not None
        assert container.services.portfolio is not None
        assert container.services.risk is not None
        assert container.services.monitoring is not None
        assert isinstance(container.services.watchlist, WatchlistHubService)
        assert isinstance(container.operations.post_market_sync, PostMarketSyncService)
        assert isinstance(
            container.operations.post_market_sync._calendar,  # type: ignore[attr-defined]
            XnysMarketSessionCalendar,
        )
        assert container.operations.post_market_sync._portfolio is (  # type: ignore[attr-defined]
            container.services.portfolio
        )
        assert container.operations.post_market_sync._watchlist is (  # type: ignore[attr-defined]
            container.services.watchlist
        )
        assert container.operations.post_market_sync._delay.total_seconds() == (  # type: ignore[attr-defined]
            test_settings.post_market_sync_delay_minutes * 60
        )
        # Real adapters remain registered even when a provider is disabled.
        registered = frozenset(container.providers.registry.list_registered())
        assert registered == _EXPECTED_REGISTERED
        assert isinstance(container.providers.registry.get(VendorId.NULL), NullCategoryProvider)
        assert VendorId.YFINANCE in registered
        assert VendorId.ALPHA_VANTAGE in registered
        assert VendorId.BROKER not in registered
        # Fresh unmigrated SQLite → schema not ready (in-memory state path).
        assert provider_state_schema_ready(container.resources.database.engine) is False
        assert len(PUBLIC_TOOL_NAMES) == 28
    finally:
        container.close()


def test_post_market_sync_uses_settings_delay_minutes(tmp_sqlite_url: str) -> None:
    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="trading-partner-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=tmp_sqlite_url,
        mcp_server_name="trading-partner-test",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        post_market_sync_delay_minutes=37,
    )
    container = build_application(settings)
    try:
        assert isinstance(container.operations.post_market_sync, PostMarketSyncService)
        assert container.operations.post_market_sync._delay.total_seconds() == 37 * 60  # type: ignore[attr-defined]
        assert isinstance(
            container.operations.post_market_sync._calendar,  # type: ignore[attr-defined]
            XnysMarketSessionCalendar,
        )
    finally:
        container.close()


def test_build_application_fresh_sqlite_uses_in_memory_provider_state(
    test_settings: AppSettings,
) -> None:
    """Unmigrated DB must select full in-memory provider state (not SQL mix)."""
    container = build_application(test_settings)
    try:
        # Peer into engine: router engine holds cache_store as private attr.
        engine = container.providers.router._engine  # type: ignore[attr-defined]
        cache = engine._cache_store  # type: ignore[attr-defined]
        assert isinstance(cache, InMemoryProviderCacheStore)
    finally:
        container.close()


def test_build_application_passes_exact_settings_to_router_engine(
    test_settings: AppSettings,
) -> None:
    """v1.28: no bootstrap wrapper/override — engine receives the same AppSettings."""
    container = build_application(test_settings)
    try:
        engine = container.providers.router._engine  # type: ignore[attr-defined]
        assert isinstance(engine, ProviderRouterEngine)
        assert engine._settings is test_settings  # type: ignore[attr-defined]
        assert engine._settings is container.settings  # type: ignore[attr-defined]
        # Product default is True; bootstrap must not invent a different value.
        assert test_settings.stale_guard_allow_closed_last_bar is True
        assert (
            engine._settings.stale_guard_allow_closed_last_bar  # type: ignore[attr-defined]
            is test_settings.stale_guard_allow_closed_last_bar
        )
    finally:
        container.close()


def test_e5b_registry_disabled_vendor_remains_registered_unconfigured(
    tmp_sqlite_url: str,
) -> None:
    """Disabled adapters stay registered; is_configured is the skip signal."""
    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="trading-partner-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=tmp_sqlite_url,
        mcp_server_name="trading-partner-test",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        tencent_enabled=False,
        eastmoney_enabled=False,
        iwencai_enabled=False,
    )
    transport = _FakeTransport()
    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01, jitter_seconds=0.0
    )
    calendar = load_default_a_share_trading_calendar()
    container = build_application(
        settings,
        overrides=BootstrapOverrides(
            a_share_transport=transport,  # type: ignore[arg-type]
            eastmoney_gate=gate,
            a_share_calendar=calendar,
        ),
    )
    try:
        registered = frozenset(container.providers.registry.list_registered())
        assert registered == _EXPECTED_REGISTERED
        tencent = container.providers.registry.get(VendorId.TENCENT)
        eastmoney = container.providers.registry.get(VendorId.EASTMONEY)
        iwencai = container.providers.registry.get(VendorId.IWENCAI)
        assert isinstance(tencent, TencentAShareAdapter)
        assert isinstance(eastmoney, EastmoneyAShareAdapter)
        assert tencent.is_configured() is False
        assert eastmoney.is_configured() is False
        assert container.providers.registry.get(VendorId.EASTMONEY_FUTURES).is_configured() is False
        assert iwencai.is_configured() is False
        # SSE/SZSE/HKEX remain configured (no enable flags in Phase 1E).
        assert container.providers.registry.get(VendorId.SSE).is_configured() is True
        assert container.providers.registry.get(VendorId.SZSE).is_configured() is True
        assert container.providers.registry.get(VendorId.HKEX).is_configured() is True
    finally:
        container.close()
    assert transport.aclose_calls == 0
    assert transport.send_calls == 0


def test_e5b_single_transport_gate_calendar_and_codec_identity(
    test_settings: AppSettings,
) -> None:
    transport = _FakeTransport()
    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01, jitter_seconds=0.0
    )
    calendar = load_default_a_share_trading_calendar()
    container = build_application(
        test_settings,
        overrides=BootstrapOverrides(
            a_share_transport=transport,  # type: ignore[arg-type]
            eastmoney_gate=gate,
            a_share_calendar=calendar,
        ),
    )
    try:
        assert container.services.a_share._clock is container.context.clock  # type: ignore[attr-defined]
        assert container.resources.a_share_transport is None

        tencent = container.providers.registry.get(VendorId.TENCENT)
        eastmoney = container.providers.registry.get(VendorId.EASTMONEY)
        sina = container.providers.registry.get(VendorId.SINA)
        assert tencent._transport is transport  # type: ignore[attr-defined]
        assert eastmoney._transport is transport  # type: ignore[attr-defined]
        assert sina._transport is transport  # type: ignore[attr-defined]
        yfinance = container.providers.registry.get(VendorId.YFINANCE)
        alpha = container.providers.registry.get(VendorId.ALPHA_VANTAGE)
        assert yfinance._transport is transport  # type: ignore[attr-defined]
        assert alpha._transport is transport  # type: ignore[attr-defined]
        assert eastmoney._gate is gate  # type: ignore[attr-defined]
        assert eastmoney._calendar is calendar  # type: ignore[attr-defined]

        # Shared codecs constructed once and injected by identity.
        snap = container.services.a_share._snapshot_service  # type: ignore[attr-defined]
        structure = container.services.a_share._market_structure_service  # type: ignore[attr-defined]
        capital = container.services.a_share._capital_service  # type: ignore[attr-defined]
        sentiment = container.services.a_share._sentiment_service  # type: ignore[attr-defined]
        assert snap._quote_codec is structure._quote_codec  # type: ignore[attr-defined]
        assert snap._news_codec is sentiment._news_codec  # type: ignore[attr-defined]
        assert (
            snap._corporate_actions_codec  # type: ignore[attr-defined]
            is capital._corporate_actions_codec  # type: ignore[attr-defined]
        )
        assert capital._chip_distribution_codec.codec_id == CODEC_CHIP_DISTRIBUTION  # type: ignore[attr-defined]
        assert capital._chip_distribution_codec.codec_id == (  # type: ignore[attr-defined]
            "a_share_chip_distribution.v2"
        )
        assert sentiment._sentiment_codec.codec_id == CODEC_SENTIMENT  # type: ignore[attr-defined]
        assert sentiment._sentiment_codec.codec_id == "a_share_sentiment.v2"  # type: ignore[attr-defined]

        # Windows come from E5 settings, not legacy freshness_* defaults.
        assert snap._current_window_seconds == (  # type: ignore[attr-defined]
            test_settings.a_share_current_window_seconds
        )
        assert structure._freshness_window_seconds == (  # type: ignore[attr-defined]
            test_settings.a_share_current_window_seconds
        )
        assert eastmoney._current_window_seconds == (  # type: ignore[attr-defined]
            test_settings.a_share_current_window_seconds
        )
        assert eastmoney._max_fresh_seconds == (  # type: ignore[attr-defined]
            test_settings.a_share_max_fresh_seconds
        )
        assert eastmoney._max_delayed_seconds == (  # type: ignore[attr-defined]
            test_settings.a_share_max_delayed_seconds
        )
        assert tencent._max_fresh_seconds == (  # type: ignore[attr-defined]
            test_settings.a_share_max_fresh_seconds
        )
    finally:
        container.close()
    assert transport.aclose_calls == 0


def test_e5b_owned_transport_is_httpx_and_construction_is_offline(
    test_settings: AppSettings,
) -> None:
    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01, jitter_seconds=0.0
    )
    container = build_application(
        test_settings,
        overrides=BootstrapOverrides(eastmoney_gate=gate),
    )
    try:
        owned = container.resources.a_share_transport
        assert isinstance(owned, HttpxTransport)
        structure = container.services.a_share._market_structure_service  # type: ignore[attr-defined]
        assert isinstance(structure._calendar, JsonAShareTradingCalendar)  # type: ignore[attr-defined]
        # Owned transport shared into real adapters.
        eastmoney = container.providers.registry.get(VendorId.EASTMONEY)
        assert eastmoney._transport is owned  # type: ignore[attr-defined]
    finally:
        container.close()


def test_general_proxy_is_shared_by_cross_asset_providers(
    test_settings: AppSettings,
) -> None:
    settings = test_settings.model_copy(
        update={"provider_proxy_url": "http://127.0.0.1:7891"}
    )
    container = build_application(settings)
    try:
        cross_asset = container.resources.cross_asset_transport
        polymarket = container.providers.registry.get(VendorId.POLYMARKET)
        cme = container.providers.registry.get(VendorId.CME_PUBLIC)
        assert isinstance(cross_asset, HttpxTransport)
        assert cme._transport is cross_asset  # type: ignore[attr-defined]
        assert polymarket._transport is cross_asset  # type: ignore[attr-defined]
    finally:
        container.close()


def test_e5b_sync_close_is_idempotent_and_closes_owned_transport(
    test_settings: AppSettings,
) -> None:
    events: list[str] = []
    transport = _RecordingTransport(events)
    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01, jitter_seconds=0.0
    )
    container = build_application(
        test_settings,
        overrides=BootstrapOverrides(
            a_share_transport=_FakeTransport(),  # type: ignore[arg-type]
            eastmoney_gate=gate,
        ),
    )
    # Force an owned transport + recording database for order/idempotency checks.
    container.resources.a_share_transport = transport  # type: ignore[assignment]
    container.resources.database = _RecordingDatabase(events)  # type: ignore[assignment]

    container.close()
    container.close()
    assert events == ["transport.aclose", "database.close"]
    assert transport.aclose_calls == 1
    assert container.resources.database.closed is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_e5b_async_aclose_idempotent_and_running_loop_close_errors(
    test_settings: AppSettings,
) -> None:
    events: list[str] = []
    transport = _RecordingTransport(events)
    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01, jitter_seconds=0.0
    )
    container = build_application(
        test_settings,
        overrides=BootstrapOverrides(
            a_share_transport=_FakeTransport(),  # type: ignore[arg-type]
            eastmoney_gate=gate,
        ),
    )
    container.resources.a_share_transport = transport  # type: ignore[assignment]
    container.resources.database = _RecordingDatabase(events)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="await container.aclose"):
        container.close()

    await container.aclose()
    await container.aclose()
    assert events == ["transport.aclose", "database.close"]
    assert transport.aclose_calls == 1


def test_e5b_overrides_clock_identity(test_settings: AppSettings) -> None:
    class _Clock:
        def now(self) -> datetime:
            return AS_OF

    clock = _Clock()
    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01, jitter_seconds=0.0
    )
    container = build_application(
        test_settings,
        overrides=BootstrapOverrides(
            clock=clock,  # type: ignore[arg-type]
            a_share_transport=_FakeTransport(),  # type: ignore[arg-type]
            eastmoney_gate=gate,
        ),
    )
    try:
        assert container.context.clock is clock
        snapshot = container.services.a_share._snapshot_service  # type: ignore[attr-defined]
        assert snapshot._clock is clock  # type: ignore[attr-defined]
    finally:
        container.close()


def test_import_bootstrap_has_no_side_effects(tmp_path: Path) -> None:
    database_path = tmp_path / "must-not-exist.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database_path}",
    }
    # Drop pytest/pythonpath overrides so this mirrors plain `uv run python`.
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "import bootstrap"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not database_path.exists()


def test_editable_imports_resolve_from_project_src() -> None:
    """Subprocess (no PYTHONPATH) must load all five top-level packages from src/."""
    expected = {
        "bootstrap": str((PROJECT_ROOT / "src" / "bootstrap.py").resolve()),
        **{
            name: str((PROJECT_ROOT / "src" / name / "__init__.py").resolve())
            for name in EXPECTED_PACKAGES
        },
    }
    script = f"""
from pathlib import Path
import bootstrap, application, domain, infrastructure, interfaces
mods = {{
    "bootstrap": bootstrap,
    "application": application,
    "domain": domain,
    "infrastructure": infrastructure,
    "interfaces": interfaces,
}}
expected = {expected!r}
for name, mod in mods.items():
    got = str(Path(mod.__file__).resolve())
    print(f"{{name}}={{got}}")
    assert got == expected[name], (name, got, expected[name])
fields = getattr(bootstrap.ApplicationContainer, "__dataclass_fields__", {{}})
assert set(fields) == {{"settings", "context", "resources", "providers", "services", "operations"}}
assert hasattr(bootstrap, "build_application")
assert hasattr(bootstrap, "BootstrapOverrides")
print("OK")
"""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert "OK" in result.stdout


def test_package_scripts_register_post_market_and_watchlist_cli() -> None:
    """Console scripts for operational CLIs must be installed and importable."""
    from importlib.metadata import entry_points

    scripts = {
        ep.name: ep.value
        for ep in entry_points(group="console_scripts")
        if ep.name.startswith("trading-partner")
    }
    assert scripts["trading-partner-mcp"] == "interfaces.mcp.server:main"
    assert scripts["trading-partner-post-market-sync"] == "interfaces.cli.post_market_sync:main"
    assert scripts["trading-partner-account-transactions"] == (
        "interfaces.cli.account_transactions:main"
    )
    assert scripts["trading-partner-watchlist-sync"] == "interfaces.cli.watchlist_sync:main"
    assert scripts["trading-partner-futures-sync"] == "interfaces.cli.futures_sync:main"
    # Resolve load targets without invoking side-effecting main() bodies.
    for name in (
        "trading-partner-post-market-sync",
        "trading-partner-account-transactions",
        "trading-partner-watchlist-sync",
        "trading-partner-futures-sync",
    ):
        ep = next(e for e in entry_points(group="console_scripts") if e.name == name)
        loaded = ep.load()
        assert callable(loaded)
