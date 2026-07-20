"""Phase 1D D8b: provider_state_schema_ready + backend selection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from conftest import FixedClock
from infrastructure.persistence.in_memory_provider_state import (
    InMemoryProviderCacheStore,
    InMemoryProviderHealthStore,
    InMemoryProviderRateLimitStore,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.provider_cache_store import SqlAlchemyProviderCacheStore
from infrastructure.persistence.provider_health_store import (
    SqlAlchemyProviderHealthStore,
)
from infrastructure.persistence.provider_rate_limit_store import (
    SqlAlchemyProviderRateLimitStore,
)
from infrastructure.persistence.provider_state_backend import (
    ProviderStateBackend,
    build_provider_state_backend,
    provider_state_schema_ready,
)
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def redactor() -> DefaultSecretRedactor:
    return DefaultSecretRedactor()


def test_schema_ready_false_on_empty_sqlite(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        assert provider_state_schema_ready(eng) is False
    finally:
        eng.dispose()


def test_schema_ready_false_when_only_partial_tables(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    try:
        with eng.begin() as conn:
            conn.execute(
                text("CREATE TABLE provider_cache (cache_key TEXT PRIMARY KEY, payload TEXT)")
            )
            conn.execute(
                text(
                    "CREATE TABLE provider_health ("
                    "vendor TEXT, category TEXT, PRIMARY KEY (vendor, category))"
                )
            )
            # missing provider_rate_limits
        assert provider_state_schema_ready(eng) is False
    finally:
        eng.dispose()


def test_schema_ready_true_when_all_three_tables(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'ready.db'}")
    try:
        Base.metadata.create_all(eng)
        assert provider_state_schema_ready(eng) is True
    finally:
        eng.dispose()


def test_schema_ready_false_safely_on_inspection_error() -> None:
    """Inspection failures must return False without leaking URL/SQL/chain."""
    boom_url = "sqlite:////secret/path/test-secret-db.sqlite"
    engine = MagicMock(spec=Engine)
    engine.url = boom_url

    def _explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(f"inspect failed for {boom_url} SELECT * FROM x")

    # Patch inspect via sqlalchemy.inspect used inside module by making
    # get_inspector path fail: provider_state_schema_ready calls inspect(engine).
    # MagicMock as engine makes sqlalchemy.inspect raise or return bad inspector.
    inspector = MagicMock()
    inspector.get_table_names.side_effect = _explode

    import infrastructure.persistence.provider_state_backend as backend_mod

    original_inspect = backend_mod.inspect

    def _fake_inspect(_engine: Any) -> Any:
        raise RuntimeError(f"cannot inspect {boom_url}")

    backend_mod.inspect = _fake_inspect  # type: ignore[assignment]
    try:
        result = provider_state_schema_ready(engine)
        assert result is False
    finally:
        backend_mod.inspect = original_inspect  # type: ignore[assignment]


def test_schema_ready_false_does_not_leak_exception_details() -> None:
    """Public API returns bool only — no raised exception on inspect failure."""
    engine = MagicMock()
    engine.__class__ = type("BadEngine", (), {})  # type: ignore[misc]

    import infrastructure.persistence.provider_state_backend as backend_mod

    secret = "test-secret-leak-probe-password@host/db"
    original = backend_mod.inspect

    def _boom(_e: Any) -> Any:
        raise OSError(f"connection refused: {secret}")

    backend_mod.inspect = _boom  # type: ignore[assignment]
    try:
        assert provider_state_schema_ready(engine) is False
    finally:
        backend_mod.inspect = original  # type: ignore[assignment]


def test_build_backend_uses_in_memory_when_not_ready(
    tmp_path: Path, clock: FixedClock, redactor: DefaultSecretRedactor
) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        backend = build_provider_state_backend(eng, clock, redactor)
        assert isinstance(backend, ProviderStateBackend)
        assert backend.uses_sql is False
        assert isinstance(backend.cache_store, InMemoryProviderCacheStore)
        assert isinstance(backend.health_store, InMemoryProviderHealthStore)
        assert isinstance(backend.rate_limit_store, InMemoryProviderRateLimitStore)
    finally:
        eng.dispose()


def test_build_backend_uses_sql_when_ready(
    tmp_path: Path, clock: FixedClock, redactor: DefaultSecretRedactor
) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    try:
        Base.metadata.create_all(eng)
        backend = build_provider_state_backend(eng, clock, redactor)
        assert backend.uses_sql is True
        assert isinstance(backend.cache_store, SqlAlchemyProviderCacheStore)
        assert isinstance(backend.health_store, SqlAlchemyProviderHealthStore)
        assert isinstance(backend.rate_limit_store, SqlAlchemyProviderRateLimitStore)
    finally:
        eng.dispose()


def test_build_backend_never_mixes_sql_and_memory(
    tmp_path: Path, clock: FixedClock, redactor: DefaultSecretRedactor
) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'partial_mix.db'}")
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE provider_cache (cache_key TEXT PRIMARY KEY)"))
        backend = build_provider_state_backend(eng, clock, redactor)
        # Partial tables → full in-memory, never mixed.
        assert backend.uses_sql is False
        assert isinstance(backend.cache_store, InMemoryProviderCacheStore)
        assert isinstance(backend.health_store, InMemoryProviderHealthStore)
        assert isinstance(backend.rate_limit_store, InMemoryProviderRateLimitStore)
    finally:
        eng.dispose()
