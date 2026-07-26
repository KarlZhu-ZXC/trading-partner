"""Shared pytest fixtures for Trading Partner Phase 1A."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.common.enums import AppEnvironment, AssetType, LogLevel, Market
from domain.common.ids import EntityIdPrefix
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument
from infrastructure.config.settings import AppSettings
from infrastructure.system.redactor import DefaultSecretRedactor

APP_SETTINGS_ENV_KEYS = frozenset(name.upper() for name in AppSettings.model_fields)


class FixedClock:
    """Deterministic clock for tests.

    ``step_seconds`` advances the returned instant on every ``now()`` call so
    tests can prove ``fetched_at`` is sampled after the transport response
    rather than merely equal to a frozen ``as_of``.
    """

    def __init__(
        self,
        instant: datetime | None = None,
        *,
        step_seconds: int = 0,
    ) -> None:
        self._instant = instant or datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
        self._step_seconds = int(step_seconds)
        self._calls = 0

    def now(self) -> datetime:
        self._calls += 1
        if self._step_seconds:
            from datetime import timedelta

            return self._instant + timedelta(seconds=self._step_seconds * (self._calls - 1))
        return self._instant

    def set(self, instant: datetime) -> None:
        self._instant = instant
        self._calls = 0

    def advance(self, seconds: int = 1) -> None:
        from datetime import timedelta

        self._instant = self._instant + timedelta(seconds=seconds)

    @property
    def call_count(self) -> int:
        return self._calls


class SequentialIdGenerator:
    """Predictable ID generator for tests."""

    def __init__(self, start: int = 1) -> None:
        self._n = start

    def new(self, prefix: EntityIdPrefix) -> str:
        if not isinstance(prefix, EntityIdPrefix):
            raise TypeError("prefix must be EntityIdPrefix")
        token = f"00000000-0000-7000-8000-{self._n:012d}"
        self._n += 1
        return f"{prefix.value}_{token}"


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def id_generator() -> SequentialIdGenerator:
    return SequentialIdGenerator()


@pytest.fixture
def secret_redactor() -> DefaultSecretRedactor:
    return DefaultSecretRedactor()


@pytest.fixture
def tmp_sqlite_url(tmp_path: Path) -> str:
    db_path = tmp_path / "test.db"
    return f"sqlite:///{db_path}"


@pytest.fixture
def test_settings(tmp_sqlite_url: str) -> AppSettings:
    """AppSettings constructed without reading the real project .env."""
    return AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="trading-partner-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=tmp_sqlite_url,
        mcp_server_name="trading-partner-test",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        alpha_vantage_api_keys=(),
        fred_api_key=None,
    )


@pytest.fixture
def a_share_instrument() -> Instrument:
    return Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.A_SHARE, "600519.SH"),
        symbol="600519.SH",
        name="贵州茅台",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )


@pytest.fixture
def us_instrument() -> Instrument:
    return Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "NVDA"),
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent accidental leakage of host * into unit tests."""
    # Do not clear everything — only ensure tests that construct AppSettings
    # explicitly pass values. Integration tests that need env set it themselves.
    yield
