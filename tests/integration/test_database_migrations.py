"""Alembic upgrade/downgrade/upgrade round-trip tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


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
    "notification_outbox",
    "monitor_judgments",
}

_HARDENING_TABLES = {
    "challenge_review_resolutions",
    "research_run_fact_artifacts",
}

_TRADE_RETRO_TABLES = {
    "trade_retro_plan_snapshots",
    "trade_retro_runs",
    "trade_retro_export_receipts",
    "trade_retro_review_revisions",
}

_SCORECARD_TABLES = {"judgment_scorecard_runs"}

_AGENDA_TABLES = {"catalyst_agenda_items", "catalyst_agenda_versions"}
_BROKER_EXECUTION_TABLES = {"broker_order_intents"}
_AGENT_RUNTIME_TABLES = {
    "agent_conversations",
    "agent_channel_bindings",
    "agent_messages",
    "agent_tool_receipts",
    "agent_pending_actions",
    "agent_channel_cursors",
    "agent_channel_handoffs",
    "agent_turns",
}
_REVIEW_ITEM_TABLES = {
    "review_items",
    "review_item_actions",
    "review_item_occurrences",
}
_PHASE4_TABLES = {
    "transaction_decision_links",
    "trade_cycle_override_revisions",
    "behavior_review_runs",
    "behavior_action_observations",
    "journal_activations",
    "daily_equity_snapshots",
    "external_note_identities",
    "external_note_revisions",
    "external_note_interpretations",
    "external_note_sync_receipts",
    "external_note_review_revisions",
    "external_note_review_drafts",
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
_HEAD_REVISIONS = frozenset({"0072_external_note_review_drafts"})
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
    "0030_generic_notification_outbox",
}


def test_moomoo_margin_semantics_migration_discards_legacy_value(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'moomoo-margin.db'}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0050_agent_preferences")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO account_snapshots("
                "snapshot_id, fingerprint, account_ref, provider, environment, "
                "base_currency, account_as_of, fetched_at, cash, buying_power, "
                "net_assets, margin_used, open_orders_json, degraded, warning_codes_json"
                ") VALUES ("
                "'snapshot_legacy_moomoo', 'legacy-moomoo-fingerprint', 'moomoo_hash', "
                "'moomoo', 'real', 'USD', '2026-08-12T21:11:21+00:00', "
                "'2026-08-12T21:11:21+00:00', '167.45', '2118.58', '3153.05', "
                "'2080.96', '[]', 1, '[]')"
            )
        )

    command.upgrade(cfg, "0051_moomoo_margin_semantics")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT margin_used FROM account_snapshots "
                    "WHERE snapshot_id='snapshot_legacy_moomoo'"
                )
            ).scalar_one()
            is None
        )

    command.downgrade(cfg, "0050_agent_preferences")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT margin_used FROM account_snapshots "
                    "WHERE snapshot_id='snapshot_legacy_moomoo'"
                )
            ).scalar_one()
            is None
        )
    engine.dispose()


def test_observation_revision_key_migration_backfills_and_allows_content_reversion(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'observation-revision-key.db'}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0064_external_notes")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO external_note_identities(note_id,source,external_id,title,"
                "primary_instrument_id,created_at,last_seen_at) VALUES "
                "('external_note_legacy','MOOMOO_NOTE','afrm','AFRM',"
                "'equity:US:AFRM','2026-08-27T12:00:00+00:00',"
                "'2026-08-27T12:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO external_note_revisions(note_revision_id,note_id,version,"
                "content_sha256,title,summary,full_body,coverage,source_timestamp,"
                "observed_at,visibility,related_stock_ids_json,related_codes_json,"
                "blocks_json) VALUES ('external_note_revision_legacy',"
                "'external_note_legacy',1,:hash,'AFRM','body','body','FULL',"
                "'2026-08-27T12:00:00+00:00','2026-08-27T12:00:00+00:00',"
                "'SELF','[]','[]','[]')"
            ),
            {"hash": "a" * 64},
        )

    command.upgrade(cfg, "0065_observation_revision_keys")
    with engine.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT source_revision_key FROM external_note_revisions "
                    "WHERE note_revision_id='external_note_revision_legacy'"
                )
            ).scalar_one()
            == "legacy:external_note_revision_legacy"
        )
        conn.execute(
            text(
                "INSERT INTO external_note_revisions(note_revision_id,note_id,version,"
                "content_sha256,source_revision_key,title,summary,full_body,coverage,"
                "source_timestamp,observed_at,visibility,related_stock_ids_json,"
                "related_codes_json,blocks_json) VALUES "
                "('external_note_revision_reversion','external_note_legacy',2,:hash,"
                "'source:new-observation','AFRM','body','body','FULL',"
                "'2026-08-27T12:02:00+00:00','2026-08-27T12:02:00+00:00',"
                "'SELF','[]','[]','[]')"
            ),
            {"hash": "a" * 64},
        )
    engine.dispose()


def test_post_market_observation_migration_preserves_historical_receipts(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'post-market-observations.db'}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0066_decision_external_note_revision")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO post_market_sync_runs("
                "run_id,market_session_date,scheduled_for,started_at,completed_at,status,"
                "portfolio_status,watchlist_status,account_snapshot_ids,"
                "watchlist_groups_synced,watchlist_membership_relations_synced,"
                "warning_codes,error_codes,attempt_count) VALUES ("
                "'run_legacy','2026-08-28','2026-08-28T20:10:00+00:00',"
                "'2026-08-28T20:10:00+00:00','2026-08-28T20:11:00+00:00',"
                "'SUCCEEDED','SUCCEEDED','SUCCEEDED','[]',1,2,'[]','[]',1)"
            )
        )

    command.upgrade(cfg, "0067_post_market_observation_sync")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status,observation_status,observation_notes_seen "
                "FROM post_market_sync_runs WHERE run_id='run_legacy'"
            )
        ).one()
        assert tuple(row) == ("SUCCEEDED", None, None)
    command.downgrade(cfg, "0066_decision_external_note_revision")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM post_market_sync_runs WHERE run_id='run_legacy'")
            ).scalar_one()
            == "SUCCEEDED"
        )
    engine.dispose()


def test_external_note_review_migration_backfills_confirmed_decision(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'external-note-reviews.db'}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0070_retire_unlinked_review_items")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO investment_cases("
                "case_id,case_type,title,summary,status,topic_tags_json,created_at,"
                "updated_at,created_by,linked_case_ids_json,evidence_ids_json,"
                "report_ids_json,event_ids_json,decision_ids_json,schema_version) VALUES("
                "'case_review_migration','company','NVDA','NVDA research','active','[]',"
                "'2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00','user',"
                "'[]','[]','[]','[]','[]',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO external_note_identities("
                "note_id,source,external_id,title,created_at,last_seen_at) VALUES("
                "'external_note_migration','MOOMOO_NOTE','nvda-migration','NVDA',"
                "'2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO external_note_revisions("
                "note_revision_id,note_id,version,content_sha256,source_revision_key,"
                "title,summary,full_body,coverage,source_timestamp,observed_at,visibility,"
                "related_stock_ids_json,related_codes_json,blocks_json) VALUES("
                "'external_note_revision_migration','external_note_migration',1,:hash,"
                "'source:migration','NVDA','Updated view','Updated view','FULL',"
                "'2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00','SELF',"
                "'[]','[]','[]')"
            ),
            {"hash": "a" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO decision_records("
                "decision_id,case_id,decision_type,title,rationale,decided_at,recorded_at,"
                "decided_by,confirmation_mode,thesis_revision_ids_json,evidence_ids_json,"
                "report_ids_json,external_note_revision_id,idempotency_key,"
                "idempotency_payload_sha256,schema_version) VALUES("
                "'decision_review_migration','case_review_migration','no_action',"
                "'Hold for evidence','No action until confirmation',"
                "'2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00','user',"
                "'normal','[]','[]','[]','external_note_revision_migration',"
                "'decision-review-migration',:hash,1)"
            ),
            {"hash": "b" * 64},
        )

    command.upgrade(cfg, "0071_external_note_reviews")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status,subject_id,decision_id,note_revision_id,version "
                "FROM external_note_review_revisions"
            )
        ).one()
        assert tuple(row) == (
            "NO_ACTION",
            "case_review_migration",
            "decision_review_migration",
            "external_note_revision_migration",
            1,
        )
    engine.dispose()


def test_moomoo_instrument_identity_migration_unifies_soxl_history(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'moomoo-instrument-identity.db'}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0067_post_market_observation_sync")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id,symbol,name,market,exchange,currency,timezone,asset_type,"
                "is_active,listing_status,metadata_version,created_at,updated_at) VALUES ("
                "'etf:US:SOXL','SOXL','Direxion Daily Semiconductor Bull 3X Shares',"
                "'US','ARCA','USD','America/New_York','etf',1,'active',1,"
                "'2026-08-30T00:00:00+00:00','2026-08-30T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id,symbol,name,market,exchange,currency,timezone,asset_type,"
                "is_active,listing_status,metadata_version,created_at,updated_at) VALUES "
                "('equity:US:GDXU','GDXU','Bank of Montreal','US','UNKNOWN','USD',"
                "'America/New_York','equity',1,'active',1,"
                "'2026-08-30T00:00:00+00:00','2026-08-30T00:00:00+00:00'),"
                "('etf:US:GDXU','GDXU','MicroSectors Gold Miners 3X Leveraged ETNs',"
                "'US','ARCA','USD','America/New_York','etf',1,'active',1,"
                "'2026-08-30T00:00:00+00:00','2026-08-30T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO account_snapshots("
                "snapshot_id,fingerprint,account_ref,provider,environment,base_currency,"
                "account_as_of,fetched_at,open_orders_json,degraded,warning_codes_json) VALUES ("
                "'snapshot_soxl','fingerprint-soxl','moomoo_hash','moomoo','real','USD',"
                "'2026-08-27T00:00:00+00:00','2026-08-27T00:00:00+00:00',"
                "'[{\"instrument_id\":\"equity:US:SOXL\"}]',1,'[]')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO account_transactions("
                "provider,account_ref,provider_transaction_id,instrument_id,kind,side,"
                "quantity,price,fees,currency,occurred_at,cash_amount,source_type,"
                "mapping_version) VALUES "
                "('moomoo','moomoo_hash','deal_option_equity',"
                "'equity:US:NIO260702C5000','trade','buy','1','1',NULL,'USD',"
                "'2026-08-27T00:00:00+00:00',NULL,'HISTORY_DEAL','moomoo_deals_v1'),"
                "('moomoo','moomoo_hash','deal_option_spaces',"
                "'option:US:FCX   260821C00065000','trade','buy','1','1',NULL,'USD',"
                "'2026-08-27T00:00:00+00:00',NULL,'HISTORY_DEAL','moomoo_deals_v1'),"
                "('moomoo','moomoo_hash','deal_unregistered','equity:US:PBR','trade',"
                "'buy','1','1',NULL,'USD','2026-08-27T00:00:00+00:00',NULL,"
                "'HISTORY_DEAL','moomoo_deals_v1')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO account_positions("
                "snapshot_id,instrument_id,side,quantity,currency) VALUES ("
                "'snapshot_soxl','equity:US:SOXL','long','10','USD')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO account_transactions("
                "provider,account_ref,provider_transaction_id,instrument_id,kind,side,"
                "quantity,price,fees,currency,occurred_at,cash_amount,source_type,"
                "mapping_version) VALUES ("
                "'moomoo','moomoo_hash','deal_soxl','equity:US:SOXL','trade','buy',"
                "'10','100',NULL,'USD','2026-08-27T00:00:00+00:00',NULL,"
                "'HISTORY_DEAL','moomoo_deals_v1')"
            )
        )

    command.upgrade(cfg, "0069_journal_instrument_identity")
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT instrument_id,mapping_version FROM account_transactions "
                "WHERE provider_transaction_id='deal_soxl'"
            )
        ).one() == ("etf:US:SOXL", "moomoo_deals_v2")
        assert (
            conn.execute(
                text(
                    "SELECT instrument_id FROM account_positions WHERE snapshot_id='snapshot_soxl'"
                )
            ).scalar_one()
            == "etf:US:SOXL"
        )
        assert (
            "etf:US:SOXL"
            in conn.execute(
                text(
                    "SELECT open_orders_json FROM account_snapshots "
                    "WHERE snapshot_id='snapshot_soxl'"
                )
            ).scalar_one()
        )
        assert conn.execute(
            text(
                "SELECT provider_transaction_id,instrument_id FROM account_transactions "
                "WHERE provider_transaction_id IN ('deal_option_equity','deal_option_spaces') "
                "ORDER BY provider_transaction_id"
            )
        ).all() == [
            ("deal_option_equity", "option:US:NIO260702C5000"),
            ("deal_option_spaces", "option:US:FCX260821C00065000"),
        ]
        assert conn.execute(
            text(
                "SELECT count(*) FROM account_transactions AS transaction_row "
                "LEFT JOIN instruments AS instrument "
                "ON instrument.instrument_id=transaction_row.instrument_id "
                "WHERE transaction_row.provider='moomoo' AND instrument.instrument_id IS NULL"
            )
        ).scalar_one() == 0
        assert conn.execute(
            text("SELECT instrument_id FROM instruments WHERE symbol='GDXU'")
        ).scalars().all() == ["etf:US:GDXU"]

    command.downgrade(cfg, "0067_post_market_observation_sync")
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT instrument_id,mapping_version FROM account_transactions "
                "WHERE provider_transaction_id='deal_soxl'"
            )
        ).one() == ("equity:US:SOXL", "moomoo_deals_v1")
    engine.dispose()


def test_retire_unlinked_review_items_migration_auto_resolves_active_queue_rows(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'retire-unlinked-review.db'}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0069_journal_instrument_identity")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO review_items("
            "review_item_id,source_key,source_type,source_ref,subject_id,title,detail,"
            "severity,recommended_action,href,status,active_at_source,first_seen_at,"
            "last_seen_at,due_at,resolved_at,resolved_by,resolution_note,resolution_ref,"
            "occurrence_count,version) VALUES ("
            "'review_unlinked','UNLINKED_ACTIVITY:test','UNLINKED_ACTIVITY',"
            "'UNLINKED_ACTIVITY:test',NULL,'Unlinked account activity','Legacy queue row',"
            "'ATTENTION','LINK_DECISION_OR_CLASSIFY','/portfolio#unlinked-activity',"
            "'OPEN',1,'2026-08-01T00:00:00Z','2026-08-01T00:00:00Z',"
            "NULL,NULL,NULL,NULL,NULL,1,1)"
        ))
        conn.execute(text(
            "INSERT INTO review_item_occurrences("
            "review_item_id,occurrence_no,opened_at,last_seen_at,first_acknowledged_at,"
            "first_acknowledged_by,resolved_at,resolved_by,resolution_mode) VALUES ("
            "'review_unlinked',1,'2026-08-01T00:00:00Z','2026-08-01T00:00:00Z',"
            "NULL,NULL,NULL,NULL,NULL)"
        ))

    command.upgrade(cfg, "0070_retire_unlinked_review_items")
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT status,active_at_source,resolved_by,resolution_ref,version "
            "FROM review_items WHERE review_item_id='review_unlinked'"
        )).one() == (
            "AUTO_RESOLVED",
            0,
            "system",
            "policy:unlinked-activity-not-review-queue",
            2,
        )
        assert conn.execute(text(
            "SELECT resolved_by,resolution_mode FROM review_item_occurrences "
            "WHERE review_item_id='review_unlinked' AND occurrence_no=1"
        )).one() == ("system", "AUTO")
    engine.dispose()


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
    assert _TRADE_RETRO_TABLES.issubset(tables_after_first)
    assert _SCORECARD_TABLES.issubset(tables_after_first)
    assert _AGENDA_TABLES.issubset(tables_after_first)
    assert _BROKER_EXECUTION_TABLES.issubset(tables_after_first)
    assert _AGENT_RUNTIME_TABLES.issubset(tables_after_first)
    assert _REVIEW_ITEM_TABLES.issubset(tables_after_first)
    assert _PHASE4_TABLES.issubset(tables_after_first)
    agenda_columns = {item["name"] for item in insp.get_columns("catalyst_agenda_versions")}
    assert {
        "case_id",
        "source_type",
        "source_vendor",
        "historical_vintage",
        "source_visible_at",
        "linked_evidence_id",
        "outcome_occurred_at",
        "outcome_note",
    }.issubset(agenda_columns)
    judgment_columns = {item["name"] for item in insp.get_columns("monitor_judgments")}
    assert {"web_search_used", "web_source_urls"}.issubset(judgment_columns)
    selection_columns = {item["name"] for item in insp.get_columns("watchlist_items")}
    assert {"instrument_id", "selection_reason"}.issubset(selection_columns)
    observation_columns = {item["name"] for item in insp.get_columns("monitor_run_observations")}
    assert "diagnostics_json" in observation_columns
    agent_turn_columns = {item["name"] for item in insp.get_columns("agent_turns")}
    assert {
        "model",
        "error_http_status",
        "error_retryable",
        "error_attempts",
    }.issubset(agent_turn_columns)
    decision_columns = {item["name"] for item in insp.get_columns("decision_records")}
    assert {
        "strategy_code",
        "strategy_version",
        "scenario",
        "trade_plan_id",
        "trade_plan_version",
        "review_due_at",
        "external_note_revision_id",
    }.issubset(decision_columns)
    post_market_columns = {item["name"] for item in insp.get_columns("post_market_sync_runs")}
    assert {
        "observation_status",
        "observation_notes_seen",
        "observation_revisions_created",
        "observation_full_count",
        "observation_summary_only_count",
    }.issubset(post_market_columns)
    broker_order_columns = {item["name"] for item in insp.get_columns("broker_order_intents")}
    assert {
        "subject_id",
        "decision_id",
        "trade_plan_id",
        "trade_plan_version",
    }.issubset(broker_order_columns)
    annotation_columns = {item["name"] for item in insp.get_columns("transaction_decision_links")}
    assert {"classification", "order_intent_id"}.issubset(annotation_columns)
    assert "uq_watchlist_selected_per_case" in {
        item["name"] for item in insp.get_indexes("watchlist_items")
    }

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


def test_generic_notification_migration_preserves_monitor_event_and_run_rows(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0030 maps both legacy source columns and reconstructs them on downgrade."""

    db_path = tmp_path / "notification-migrate.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "0029_dukascopy_light_oil_cfd")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO monitor_notification_outbox("
                "notification_id, source_event_id, source_run_id, channel, title, body, "
                "status, attempt_count, next_attempt_at, created_at"
                ") VALUES "
                "('legacy-event', 'event-1', NULL, 'TELEGRAM', 'event title', 'event body', "
                "'PENDING', 1, '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00'),"
                "('legacy-run', NULL, 'run-1', 'TELEGRAM', 'run title', 'run body', "
                "'DELIVERED', 2, '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00')"
            )
        )
    command.upgrade(cfg, _HEAD_TARGET)
    assert "notification_outbox" in inspect(engine).get_table_names()
    assert "monitor_notification_outbox" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT notification_id, source_type, source_id, title, body, status, "
                "attempt_count FROM notification_outbox ORDER BY notification_id"
            )
        ).all()
    assert rows == [
        ("legacy-event", "MONITOR_EVENT", "event-1", "event title", "event body", "PENDING", 1),
        ("legacy-run", "MONITOR_RUN", "run-1", "run title", "run body", "DELIVERED", 2),
    ]

    command.downgrade(cfg, "0029_dukascopy_light_oil_cfd")
    assert "monitor_notification_outbox" in inspect(engine).get_table_names()
    assert "notification_outbox" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT notification_id, source_event_id, source_run_id, title, body, "
                "status, attempt_count FROM monitor_notification_outbox ORDER BY notification_id"
            )
        ).all()
    assert rows == [
        ("legacy-event", "event-1", None, "event title", "event body", "PENDING", 1),
        ("legacy-run", None, "run-1", "run title", "run body", "DELIVERED", 2),
    ]
    engine.dispose()


def test_generic_notification_outbox_source_metadata_constraints(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "notification-constraints.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, _HEAD_TARGET)
    engine = create_engine(database_url)
    insert_sql = text(
        "INSERT INTO notification_outbox ("
        "notification_id, source_type, source_id, channel, title, body, status, "
        "attempt_count, next_attempt_at, created_at, idempotency_key, confirmed_by, "
        "authorization_note, expires_at"
        ") VALUES ("
        ":notification_id, :source_type, :source_id, :channel, :title, :body, :status, "
        ":attempt_count, :next_attempt_at, :created_at, :idempotency_key, :confirmed_by, "
        ":authorization_note, :expires_at"
        ")"
    )
    valid = {
        "notification_id": "manual-valid",
        "source_type": "MANUAL",
        "source_id": "manual-valid-key",
        "channel": "TELEGRAM",
        "title": "title",
        "body": "body",
        "status": "PENDING",
        "attempt_count": 0,
        "next_attempt_at": "2026-08-06T00:00:00+00:00",
        "created_at": "2026-08-06T00:00:00+00:00",
        "idempotency_key": "manual-valid-key",
        "confirmed_by": "user",
        "authorization_note": "authorized",
        "expires_at": "2026-08-07T00:00:00+00:00",
    }
    with engine.begin() as conn:
        conn.execute(insert_sql, valid)

    invalid_rows = (
        {"idempotency_key": None},
        {"source_id": "different-key"},
        {"confirmed_by": "codex"},
        {"authorization_note": "   "},
        {"expires_at": None},
        {
            "notification_id": "system-auth-invalid",
            "source_type": "SYSTEM",
            "source_id": "system-test",
            "idempotency_key": None,
            "confirmed_by": "user",
            "authorization_note": "not allowed",
            "expires_at": None,
        },
    )
    for index, override in enumerate(invalid_rows):
        candidate = {**valid, **override, "notification_id": f"invalid-{index}"}
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert_sql, candidate)

    constraints = {
        item["name"]
        for item in inspect(engine).get_check_constraints("notification_outbox")
        if item.get("name")
    }
    assert any(name.endswith("_manual_metadata") for name in constraints)
    assert any(name.endswith("_non_manual_authorization") for name in constraints)
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
    assert not _PHASE4_TABLES.intersection(tables_mid)
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
