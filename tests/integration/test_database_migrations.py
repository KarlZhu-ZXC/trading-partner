"""Alembic upgrade/downgrade/upgrade round-trip tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _alembic_heads(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version")).all()
        }


_PHASE1D_TABLES = {
    "instruments",
    "instrument_aliases",
    "provider_cache",
    "provider_health",
    "provider_rate_limits",
}

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

_PHASE1C_TABLES = {
    "research_evidence",
    "case_evidence_links",
    "evidence_assessments",
    "research_reports",
    "research_events",
    "decision_records",
    "journal_entries",
    "research_search_documents",
    "research_search_document_cases",
    "research_search_document_instruments",
    "research_search_document_tags",
    "research_search_fts",
}

_PHASE2_TABLES = {
    "post_market_sync_runs",
    "reddit_provider_cooldown",
    "reddit_sample_cache",
    "risk_policies",
    "monitor_identities",
    "monitor_versions",
    "monitor_rule_states",
    "monitor_events",
    "monitor_event_resolutions",
    "monitor_runs",
    "monitor_run_observations",
    "monitor_notification_outbox",
}

_HARDENING_TABLES = {
    "challenge_review_resolutions",
    "research_run_fact_artifacts",
}

_PHASE3_TABLES = {
    "account_activity_coverage_receipts",
    "industry_metric_observations",
    "futures_products",
    "futures_product_versions",
    "futures_contracts",
    "futures_contract_versions",
    "futures_contract_statistics",
    "continuous_series_definitions",
    "continuous_contract_mappings",
    "trade_plan_identities",
    "trade_plan_versions",
    "trade_plan_conditions",
    "provider_route_receipts",
}

_HEAD_TARGET = "head"
_HEAD_REVISIONS = frozenset({"0029_dukascopy_light_oil_cfd"})
_PHASE1B_REVISION = "0002_phase1b_research_state"

_EXPECTED_SCHEMA_VERSIONS = {
    "phase1a_foundation",
    "phase1b_research_state",
    "phase1d_instrument_provider",
    "phase1c_research_memory",
    "phase1f_us_proxy_seeds",
    "0006_phase1i_account_portfolio",
    "0007_phase1k_challenge_reviews",
    "0008_phase1l_workflows",
    "0009_phase2_watchlist_hub",
    "0010_post_market_sync_runs",
    "0011_reddit_rss_resilience",
    "0012_phase2b_risk_engine",
    "0013_phase2c_monitoring",
    "0014_phase3_commodity_futures",
    "0015_phase3b_industry_metrics",
    "0016_monitor_valid_until",
    "0017_phase3a_futures_definitions",
    "0018_phase3a_otc_spot_seeds",
    "0019_phase3a_futures_statistics",
    "0020_phase3d_plan_controls",
    "0021_challenge_review_idempotency",
    "0022_workflow_execution_replay",
    "0023_monitoring_hub_v3",
    "0024_monitor_notification_outbox",
    "0025_monitor_run_notification_outbox",
    "0026_korean_market_support",
    "0027_account_activity_coverage",
    "0028_provider_route_history",
    "0029_dukascopy_light_oil_cfd",
}


def test_migration_round_trip(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "migrate.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)

    cfg = _alembic_config(database_url, project_root)

    # upgrade branch head under test
    command.upgrade(cfg, _HEAD_TARGET)
    engine = create_engine(database_url)
    insp = inspect(engine)
    tables_after_first = set(insp.get_table_names())
    assert "schema_versions" in tables_after_first
    assert "system_audit_log" in tables_after_first
    assert "alembic_version" in tables_after_first
    assert _PHASE1B_TABLES.issubset(tables_after_first)
    assert _PHASE1D_TABLES.issubset(tables_after_first)
    assert _PHASE1C_TABLES.issubset(tables_after_first)
    assert _PHASE2_TABLES.issubset(tables_after_first)
    assert _PHASE3_TABLES.issubset(tables_after_first)
    assert _HARDENING_TABLES.issubset(tables_after_first)

    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == _EXPECTED_SCHEMA_VERSIONS
        assert _alembic_heads(engine) == _HEAD_REVISIONS

    # downgrade base
    command.downgrade(cfg, "base")
    insp = inspect(engine)
    tables_after_down = set(insp.get_table_names())
    assert "schema_versions" not in tables_after_down
    assert "system_audit_log" not in tables_after_down
    assert not _PHASE1B_TABLES.intersection(tables_after_down)
    assert not _PHASE1D_TABLES.intersection(tables_after_down)
    assert not _PHASE1C_TABLES.intersection(tables_after_down)
    assert not _PHASE2_TABLES.intersection(tables_after_down)
    assert not _PHASE3_TABLES.intersection(tables_after_down)
    assert not _HARDENING_TABLES.intersection(tables_after_down)

    # upgrade head again
    command.upgrade(cfg, _HEAD_TARGET)
    insp = inspect(engine)
    tables_after_second = set(insp.get_table_names())
    assert _PHASE2_TABLES.issubset(tables_after_second)
    assert _HARDENING_TABLES.issubset(tables_after_second)
    assert tables_after_second == tables_after_first
    engine.dispose()


def test_phase1d_migration_round_trip_preserves_1b_data(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit downgrade to 0002 drops 1D+1C; 0001/0002 data must survive re-upgrade.

    Uses absolute revision ``0002_phase1b_research_state`` (not relative ``-1``) so
    later heads do not change the Phase 1D intent of this test.
    """
    db_path = tmp_path / "phase1d_rt.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, _HEAD_TARGET)
    engine = create_engine(database_url)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO system_audit_log("
                "audit_id, event_type, request_id, recorded_at, payload_json"
                ") VALUES ("
                "'audit_00000000-0000-7000-8000-000000000001', 'test', NULL, "
                "'2026-07-17T12:00:00+00:00', '{}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO investment_cases("
                "case_id, case_type, title, summary, status, topic_tags_json, "
                "created_at, updated_at, created_by, linked_case_ids_json, "
                "evidence_ids_json, report_ids_json, event_ids_json, "
                "decision_ids_json, schema_version"
                ") VALUES ("
                "'case_00000000-0000-7000-8000-000000000001', 'theme', 't', 's', "
                "'draft', '[]', "
                "'2026-07-17T12:00:00+00:00', '2026-07-17T12:00:00+00:00', "
                "'user', '[]', '[]', '[]', '[]', '[]', 1)"
            )
        )
        # Non-seed instrument so UNIQUE(asset_type, market, symbol) stays free of
        # the deterministic 0003 minimum seed (which already includes NVDA).
        conn.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, symbol, name, market, exchange, currency, "
                "timezone, asset_type, is_active, listing_status, "
                "metadata_version, created_at, updated_at"
                ") VALUES ("
                "'equity:US:AAPL', 'AAPL', 'Apple Inc.', 'US', 'NASDAQ', "
                "'USD', 'America/New_York', 'equity', 1, 'active', "
                "1, '2026-07-17T12:00:00+00:00', '2026-07-17T12:00:00+00:00')"
            )
        )
        # Confirm migration seeds are present before downgrade (20 head seeds + AAPL).
        assert conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar() == 21

    # Explicit downgrade to 0002 (not relative -1): 1D+1C gone, 1A+1B data remain.
    command.downgrade(cfg, _PHASE1B_REVISION)
    tables_mid = set(inspect(engine).get_table_names())
    assert not _PHASE1D_TABLES.intersection(tables_mid)
    assert not _PHASE1C_TABLES.intersection(tables_mid)
    assert not _PHASE2_TABLES.intersection(tables_mid)
    assert _PHASE1B_TABLES.issubset(tables_mid)
    assert "schema_versions" in tables_mid
    assert "system_audit_log" in tables_mid

    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == {"phase1a_foundation", "phase1b_research_state"}
        assert _alembic_heads(engine) == {_PHASE1B_REVISION}
        assert conn.execute(text("SELECT COUNT(*) FROM investment_cases")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM system_audit_log")).scalar() == 1

    # upgrade head restores 1D seed + 1C schema; 1B data still present
    command.upgrade(cfg, _HEAD_TARGET)
    tables_head = set(inspect(engine).get_table_names())
    assert _PHASE1D_TABLES.issubset(tables_head)
    assert _PHASE1C_TABLES.issubset(tables_head)
    assert _PHASE2_TABLES.issubset(tables_head)
    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == _EXPECTED_SCHEMA_VERSIONS
        assert "phase1d_instrument_provider" in versions
        assert "phase1c_research_memory" in versions
        assert _alembic_heads(engine) == _HEAD_REVISIONS
        assert conn.execute(text("SELECT COUNT(*) FROM investment_cases")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM system_audit_log")).scalar() == 1
        # Manual row was dropped; re-upgrade restores 16 legacy seeds and 4 OTC seeds.
        assert conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar() == 20

    # second upgrade is idempotent
    command.upgrade(cfg, _HEAD_TARGET)
    with engine.connect() as conn:
        assert _alembic_heads(engine) == _HEAD_REVISIONS
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == _EXPECTED_SCHEMA_VERSIONS

    engine.dispose()
