"""Migration 0073 preservation and downgrade safety regressions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

CASE_ID = "case_00000000-0000-7000-8000-000000000301"
QUESTION_ID = "rev_00000000-0000-7000-8000-000000000301"
CANDIDATE_ID = "run_00000000-0000-7000-8000-000000000301"
REVIEW_ITEM_ID = "review_item_00000000-0000-7000-8000-000000000301"


def _config(database_url: str, project_root: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.attributes["database_url"] = database_url
    return config


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "research-evidence-migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "research-evidence-migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _seed_pre_0073_rows(engine) -> None:  # type: ignore[no-untyped-def]
    timestamp = "2026-09-20T12:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO investment_cases("
                "case_id,case_type,title,summary,status,topic_tags_json,created_at,updated_at,"
                "created_by,linked_case_ids_json,evidence_ids_json,report_ids_json,event_ids_json,"
                "decision_ids_json,schema_version) VALUES ("
                ":case_id,'theme','Historical subject','scope','draft','[]',:timestamp,:timestamp,"
                "'user','[]','[]','[]','[]','[]',1)"
            ),
            {"case_id": CASE_ID, "timestamp": timestamp},
        )
        connection.execute(
            text(
                "INSERT INTO open_questions("
                "question_id,case_id,text,status,asked_at,answered_at,answer_summary,"
                "closed_without_answer_reason,proposed_by) VALUES ("
                ":question_id,:case_id,'Legacy question','open',:timestamp,NULL,NULL,NULL,'user')"
            ),
            {"question_id": QUESTION_ID, "case_id": CASE_ID, "timestamp": timestamp},
        )
        connection.execute(
            text(
                "INSERT INTO candidate_thesis_revisions("
                "candidate_id,case_id,thesis_id,target_revision_no,payload_json,kind,"
                "confirmation_mode,status,proposed_at,expires_at,proposed_by,"
                "proposed_by_rationale,reviewed_at,reviewed_by,review_note,rejection_reason,"
                "idempotency_key) VALUES ("
                ":candidate_id,:case_id,NULL,NULL,'{}','open_question','normal','proposed',"
                ":timestamp,:expires_at,'codex','legacy',NULL,NULL,NULL,NULL,:idempotency)"
            ),
            {
                "candidate_id": CANDIDATE_ID,
                "case_id": CASE_ID,
                "timestamp": timestamp,
                "expires_at": "2026-09-21T12:00:00+00:00",
                "idempotency": "migration-legacy-candidate",
            },
        )
        connection.execute(
            text(
                "INSERT INTO review_items("
                "review_item_id,source_key,source_type,source_ref,subject_id,title,detail,"
                "severity,recommended_action,href,status,active_at_source,first_seen_at,"
                "last_seen_at,due_at,resolved_at,resolved_by,resolution_note,resolution_ref,"
                "occurrence_count,version) VALUES ("
                ":review_item_id,'legacy-agenda','CATALYST_AGENDA','agenda_legacy',:case_id,"
                "'Legacy review','keep this row','INFO','review','/research', 'OPEN',1,"
                ":timestamp,:timestamp,NULL,NULL,NULL,NULL,NULL,1,1)"
            ),
            {"review_item_id": REVIEW_ITEM_ID, "case_id": CASE_ID, "timestamp": timestamp},
        )


def test_upgrade_0073_preserves_legacy_research_rows_and_adds_defaults(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    _set_test_env(monkeypatch, database_url)
    config = _config(database_url, project_root)
    command.upgrade(config, "0072_external_note_review_drafts")
    engine = create_engine(database_url)
    _seed_pre_0073_rows(engine)

    command.upgrade(config, "0073_research_evidence_links")
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(open_questions)"))}
        assert {
            "version",
            "evidence_requirements_json",
            "due_at",
            "progress_note",
            "source_refs_json",
            "decision_ids_json",
        } <= columns
        question = connection.execute(
            text(
                "SELECT version,evidence_requirements_json,due_at,progress_note,"
                "source_refs_json,decision_ids_json FROM open_questions WHERE question_id=:id"
            ),
            {"id": QUESTION_ID},
        ).one()
        assert question == (1, "[]", None, None, "[]", "[]")
        assert connection.execute(
            text("SELECT kind FROM candidate_thesis_revisions WHERE candidate_id=:id"),
            {"id": CANDIDATE_ID},
        ).scalar_one() == "open_question"
        assert connection.execute(
            text("SELECT source_type,title FROM review_items WHERE review_item_id=:id"),
            {"id": REVIEW_ITEM_ID},
        ).one() == ("CATALYST_AGENDA", "Legacy review")
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0073_research_evidence_links"
        )
    assert "evidence_impacts" in set(inspect(engine).get_table_names())
    engine.dispose()


def test_downgrade_with_new_rows_refuses_before_any_drop(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'refuse.db'}"
    _set_test_env(monkeypatch, database_url)
    config = _config(database_url, project_root)
    command.upgrade(config, "0072_external_note_review_drafts")
    engine = create_engine(database_url)
    _seed_pre_0073_rows(engine)
    command.upgrade(config, "0073_research_evidence_links")

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE open_questions SET version=2,evidence_requirements_json='[\"new\"]' "
                "WHERE question_id=:id"
            ),
            {"id": QUESTION_ID},
        )
        connection.execute(
            text(
                "UPDATE candidate_thesis_revisions SET kind='evidence_impact' "
                "WHERE candidate_id=:id"
            ),
            {"id": CANDIDATE_ID},
        )

    with pytest.raises(RuntimeError, match="restore a verified backup"):
        command.downgrade(config, "0072_external_note_review_drafts")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0073_research_evidence_links"
        )
        assert connection.execute(
            text(
                "SELECT version,evidence_requirements_json FROM open_questions "
                "WHERE question_id=:id"
            ),
            {"id": QUESTION_ID},
        ).one() == (2, '["new"]')
        assert connection.execute(
            text("SELECT kind FROM candidate_thesis_revisions WHERE candidate_id=:id"),
            {"id": CANDIDATE_ID},
        ).scalar_one() == "evidence_impact"
    assert "evidence_impacts" in set(inspect(engine).get_table_names())
    engine.dispose()


def test_empty_feature_downgrade_is_supported(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"
    _set_test_env(monkeypatch, database_url)
    config = _config(database_url, project_root)
    command.upgrade(config, "0073_research_evidence_links")
    engine = create_engine(database_url)
    assert "evidence_impacts" in set(inspect(engine).get_table_names())

    command.downgrade(config, "0072_external_note_review_drafts")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0072_external_note_review_drafts"
        )
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(open_questions)"))}
        assert "version" not in columns
    assert "evidence_impacts" not in set(inspect(engine).get_table_names())
    engine.dispose()
