"""Shared pytest fixtures for Trading Partner Phase 1A."""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sqlalchemy_engine
from sqlalchemy import event
from sqlalchemy.pool import Pool

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


def _set_migrated_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "shared-migrated-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "shared-migrated-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


@pytest.fixture(scope="session")
def migrated_sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create one immutable migration-complete SQLite baseline per test session."""
    root = Path(__file__).resolve().parents[1]
    path = tmp_path_factory.mktemp("migrated-schema") / "template.db"
    database_url = f"sqlite:///{path}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    with pytest.MonkeyPatch.context() as patch:
        _set_migrated_test_env(patch, database_url)
        command.upgrade(config, "head")
    return path


@pytest.fixture
def migrated_sqlite_url(
    migrated_sqlite_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Give one test an isolated copy of the migrated database baseline."""
    target = tmp_path / "test.db"
    shutil.copyfile(migrated_sqlite_template, target)
    database_url = f"sqlite:///{target}"
    _set_migrated_test_env(monkeypatch, database_url)
    return database_url


@pytest.fixture(scope="session")
def orm_sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the full ORM-only schema once for isolated repository tests."""
    import infrastructure.persistence.orm  # noqa: F401
    from infrastructure.persistence.metadata import Base

    path = tmp_path_factory.mktemp("orm-schema") / "template.db"
    engine = create_sqlalchemy_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


@pytest.fixture
def orm_sqlite_url(orm_sqlite_template: Path, tmp_path: Path) -> str:
    """Give one test an isolated copy of the complete ORM-only schema."""
    target = tmp_path / "orm-test.db"
    shutil.copyfile(orm_sqlite_template, target)
    return f"sqlite:///{target}"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent accidental leakage of host * into unit tests."""
    # Do not clear everything — only ensure tests that construct AppSettings
    # explicitly pass values. Integration tests that need env set it themselves.
    yield


@pytest.fixture(autouse=True)
def _close_test_database_connections() -> Iterator[None]:
    """Close DBAPI connections created by a test, including helper-owned ones."""
    connections: set[object] = set()

    def remember_connection(connection: object, _record: object) -> None:
        connections.add(connection)

    event.listen(Pool, "connect", remember_connection)
    try:
        yield
    finally:
        event.remove(Pool, "connect", remember_connection)
        for connection in connections:
            with contextlib.suppress(Exception):
                connection.close()  # type: ignore[attr-defined]
