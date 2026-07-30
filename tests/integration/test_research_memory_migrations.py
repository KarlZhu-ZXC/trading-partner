"""Phase 1C C2a Alembic 0004 migration round-trip tests.

Phase 1C scope is pinned to explicit revision targets (0004 / 0003) so later
heads (currently 0024_monitor_notification_outbox) do
not change downgrade meaning.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

_PHASE1C_REVISION = "0004_phase1c_research_memory"
_PHASE1D_REVISION = "0003_phase1d_instrument_provider"
_HEAD_REVISIONS = frozenset({"0024_monitor_notification_outbox"})

_PHASE1C_BUSINESS_TABLES = {
    "research_evidence",
    "case_evidence_links",
    "evidence_assessments",
    "research_reports",
    "research_events",
    "decision_records",
    "journal_entries",
}

_PHASE1C_SEARCH_TABLES = {
    "research_search_documents",
    "research_search_document_cases",
    "research_search_document_instruments",
    "research_search_document_tags",
    "research_search_fts",
}

_PHASE1C_TABLES = _PHASE1C_BUSINESS_TABLES | _PHASE1C_SEARCH_TABLES

_PHASE1B_TABLES = {
    "investment_cases",
    "theses",
    "thesis_revisions",
    "assumptions",
    "invalidation_conditions",
    "open_questions",
    "watchlist_items",
    "candidate_thesis_revisions",
}

_PHASE1D_TABLES = {
    "instruments",
    "instrument_aliases",
    "provider_cache",
    "provider_health",
    "provider_rate_limits",
}

_FTS_TRIGGERS = {
    "research_search_documents_ai",
    "research_search_documents_ad",
    "research_search_documents_au",
}

_HEX64_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TS = "2026-07-17T12:00:00+00:00"


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "research-memory-migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "research-memory-migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _table_names(engine: object) -> set[str]:
    return set(inspect(engine).get_table_names())


def _insert_representative_prior_rows(conn: object) -> None:
    """Insert representative 1A / 1B / 1D rows that must survive 0004 round-trip."""
    conn.execute(text("PRAGMA foreign_keys=ON"))  # type: ignore[union-attr]
    conn.execute(  # type: ignore[union-attr]
        text(
            "INSERT INTO system_audit_log("
            "audit_id, event_type, request_id, recorded_at, payload_json"
            ") VALUES ("
            "'audit_c2a_00000000-0000-7000-8000-000000000001', 'c2a_rt', NULL, "
            f"'{_TS}', '{{}}')"
        )
    )
    conn.execute(  # type: ignore[union-attr]
        text(
            "INSERT INTO investment_cases("
            "case_id, case_type, title, summary, status, topic_tags_json, "
            "created_at, updated_at, created_by, linked_case_ids_json, "
            "evidence_ids_json, report_ids_json, event_ids_json, "
            "decision_ids_json, schema_version"
            ") VALUES ("
            "'case_c2a_00000000-0000-7000-8000-000000000001', 'theme', 'c2a', "
            f"'s', 'draft', '[]', '{_TS}', '{_TS}', 'user', "
            "'[]', '[]', '[]', '[]', '[]', 1)"
        )
    )
    # Non-seed instrument so UNIQUE(asset_type, market, symbol) stays free of 0003 seed.
    conn.execute(  # type: ignore[union-attr]
        text(
            "INSERT INTO instruments("
            "instrument_id, symbol, name, market, exchange, currency, "
            "timezone, asset_type, is_active, listing_status, "
            "metadata_version, created_at, updated_at"
            ") VALUES ("
            "'equity:US:MSFT', 'MSFT', 'Microsoft', 'US', 'NASDAQ', "
            f"'USD', 'America/New_York', 'equity', 1, 'active', "
            f"1, '{_TS}', '{_TS}')"
        )
    )


def test_alembic_head_is_phase3d_plan_controls(
    project_root: Path,
) -> None:
    """Current chain has one Phase 3D head; Phase 1C remains 0004."""
    cfg = _alembic_config("sqlite:///:memory:", project_root)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert set(heads) == _HEAD_REVISIONS


def test_phase1c_upgrade_creates_all_schema_objects(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade explicitly to 0004 to preserve Phase 1C schema scope (not current head)."""
    db_path = tmp_path / "phase1c_up.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, _PHASE1C_REVISION)
    engine = create_engine(database_url)
    tables = _table_names(engine)
    assert _PHASE1C_TABLES.issubset(tables)
    assert _PHASE1B_TABLES.issubset(tables)
    assert _PHASE1D_TABLES.issubset(tables)
    assert "schema_versions" in tables
    assert "system_audit_log" in tables

    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == {
            "phase1a_foundation",
            "phase1b_research_state",
            "phase1d_instrument_provider",
            "phase1c_research_memory",
        }
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == _PHASE1C_REVISION

        triggers = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE 'research_search_documents_%'"
                )
            ).all()
        }
        assert triggers == _FTS_TRIGGERS

        fts_sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='research_search_fts'")
        ).scalar()
        assert fts_sql is not None
        assert "USING fts5" in fts_sql
        assert "content='research_search_documents'" in fts_sql
        assert "content_rowid='rowid'" in fts_sql
        assert "tokenize='unicode61'" in fts_sql

        probe_left = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE name LIKE '%fts5%probe%' OR name LIKE '__tp_fts5%'"
            )
        ).all()
        assert probe_left == []

    engine.dispose()


def test_phase1c_downgrade_to_0003_drops_only_phase1c(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upgrade 0004 -> seed 1A/1B/1D -> downgrade 0003 -> re-upgrade 0004.

    Uses absolute revisions (not relative ``-1`` / head) so Phase 1F 0005 does
    not change the Phase 1C-only drop semantics of this test.
    """
    db_path = tmp_path / "phase1c_rt.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, _PHASE1C_REVISION)
    engine = create_engine(database_url)
    tables_at_0004 = _table_names(engine)

    with engine.begin() as conn:
        _insert_representative_prior_rows(conn)
        # Also insert a Phase 1C row that must disappear on downgrade.
        conn.execute(
            text(
                "INSERT INTO research_evidence("
                "evidence_id, evidence_type, origin, title, summary, "
                "source_name, observed_at, instrument_ids_json, topic_tags_json, "
                "quality, reliability, content_sha256, recorded_by, schema_version"
                ") VALUES ("
                "'evidence_c2a_00000000-0000-7000-8000-000000000001', "
                "'company_news', 'external_fact', 't', 's', "
                f"'src', '{_TS}', '[]', '[]', "
                f"'primary', 'high', '{_HEX64_A}', 'system', 1)"
            )
        )
        seed_instruments = conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar()
        # At 0004: 0003 minimum 8 + MSFT (QQQ/IWM arrive only in 0005).
        assert seed_instruments == 9

    command.downgrade(cfg, _PHASE1D_REVISION)
    tables_mid = _table_names(engine)
    assert not _PHASE1C_TABLES.intersection(tables_mid)
    assert _PHASE1B_TABLES.issubset(tables_mid)
    assert _PHASE1D_TABLES.issubset(tables_mid)
    assert "schema_versions" in tables_mid
    assert "system_audit_log" in tables_mid

    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == {
            "phase1a_foundation",
            "phase1b_research_state",
            "phase1d_instrument_provider",
        }
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == _PHASE1D_REVISION
        assert conn.execute(text("SELECT COUNT(*) FROM investment_cases")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM system_audit_log")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar() == 9
        triggers = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'research_search_documents_%'"
            )
        ).all()
        assert triggers == []

    command.upgrade(cfg, _PHASE1C_REVISION)
    tables_again = _table_names(engine)
    assert tables_again == tables_at_0004
    assert _PHASE1C_TABLES.issubset(tables_again)

    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert "phase1c_research_memory" in versions
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == _PHASE1C_REVISION
        assert conn.execute(text("SELECT COUNT(*) FROM investment_cases")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM system_audit_log")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar() == 9
        # Phase 1C business rows are gone after downgrade; table is empty.
        assert conn.execute(text("SELECT COUNT(*) FROM research_evidence")).scalar() == 0

    # second upgrade is idempotent
    command.upgrade(cfg, _PHASE1C_REVISION)
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == _PHASE1C_REVISION
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == {
            "phase1a_foundation",
            "phase1b_research_state",
            "phase1d_instrument_provider",
            "phase1c_research_memory",
        }

    engine.dispose()


def test_phase1c_full_base_round_trip(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-trip through base using explicit Phase 1C revision (not current head)."""
    db_path = tmp_path / "phase1c_base_rt.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, _PHASE1C_REVISION)
    engine = create_engine(database_url)
    tables_first = _table_names(engine)
    assert _PHASE1C_TABLES.issubset(tables_first)

    command.downgrade(cfg, "base")
    tables_base = _table_names(engine)
    assert "schema_versions" not in tables_base
    assert not _PHASE1C_TABLES.intersection(tables_base)
    assert not _PHASE1B_TABLES.intersection(tables_base)
    assert not _PHASE1D_TABLES.intersection(tables_base)

    command.upgrade(cfg, _PHASE1C_REVISION)
    assert _table_names(engine) == tables_first
    engine.dispose()
