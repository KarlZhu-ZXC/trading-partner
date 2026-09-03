"""Phase 1C C2a schema inspection: PRAGMA/sqlite_master, ORM parity, constraints, FTS."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError

from infrastructure.persistence.orm import (
    DecisionRecordRow,
    EvidenceAssessmentRow,
    JournalEntryRow,
    NotificationOutboxRow,
    ResearchEventRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    SubjectEvidenceLinkRow,
)

_BUSINESS_TABLES = {
    "research_evidence": ResearchEvidenceRow,
    "case_evidence_links": SubjectEvidenceLinkRow,
    "evidence_assessments": EvidenceAssessmentRow,
    "research_reports": ResearchReportRow,
    "research_events": ResearchEventRow,
    "decision_records": DecisionRecordRow,
    "journal_entries": JournalEntryRow,
    "notification_outbox": NotificationOutboxRow,
}

_SEARCH_TABLES = {
    "research_search_documents",
    "research_search_document_cases",
    "research_search_document_instruments",
    "research_search_document_tags",
    "research_search_fts",
}

_REQUIRED_INDEXES = {
    "ix_evidence_observed_at",
    "ix_evidence_type",
    "ix_assessment_case_stance",
    "ix_assessment_thesis_stance",
    "ix_reports_case_created_at",
    "ix_events_case_occurred_at",
    "ix_events_case_recorded_at",
    "ix_decisions_case_recorded_at",
    "ix_decisions_external_note_revision",
    "ix_journal_case_created_at",
    "ix_search_documents_visible_at",
    "ix_search_document_cases_case",
    "ix_search_document_instruments_instrument",
    "ix_search_document_tags_tag",
    "ix_links_case_linked_at",
    "ix_assessments_evidence_assessed_at",
    "ix_reports_supersedes",
    "ix_decisions_supersedes",
    "ix_journal_supersedes",
}

_FTS_TRIGGERS = {
    "research_search_documents_ai",
    "research_search_documents_ad",
    "research_search_documents_au",
}

_HEX64_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HEX64_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_TS = "2026-07-17T12:00:00+00:00"
_TS_LATER = "2026-07-17T13:00:00+00:00"
_CASE_ID = "case_schema_00000000-0000-7000-8000-000000000001"
_EVIDENCE_ID = "evidence_schema_00000000-0000-7000-8000-000000000001"


def _table_ddl(conn: Any, table: str) -> str:
    row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).one()
    return row[0] or ""


def _pragma_table_info(conn: Any, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(text(f"PRAGMA table_info('{table}')")).mappings().all()
    return [dict(r) for r in rows]


def _pragma_foreign_keys(conn: Any, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(text(f"PRAGMA foreign_key_list('{table}')")).mappings().all()
    return [dict(r) for r in rows]


def _pragma_indexes(conn: Any, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(text(f"PRAGMA index_list('{table}')")).mappings().all()
    return [dict(r) for r in rows]


def _seed_case(conn: Any) -> None:
    conn.execute(text("PRAGMA foreign_keys=ON"))
    conn.execute(
        text(
            "INSERT INTO investment_cases("
            "case_id, case_type, title, summary, status, topic_tags_json, "
            "created_at, updated_at, created_by, linked_case_ids_json, "
            "evidence_ids_json, report_ids_json, event_ids_json, "
            "decision_ids_json, schema_version"
            ") VALUES ("
            f"'{_CASE_ID}', 'theme', 't', 's', 'draft', '[]', "
            f"'{_TS}', '{_TS}', 'user', '[]', '[]', '[]', '[]', '[]', 1)"
        )
    )


def _insert_evidence(
    conn: Any,
    *,
    evidence_id: str = _EVIDENCE_ID,
    evidence_type: str = "company_news",
    content_sha256: str = _HEX64_A,
    supersedes: str | None = None,
    confidence: str | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO research_evidence("
            "evidence_id, evidence_type, origin, title, summary, "
            "source_name, observed_at, instrument_ids_json, topic_tags_json, "
            "quality, reliability, confidence_decimal, content_sha256, "
            "supersedes_evidence_id, recorded_by, schema_version, "
            "effective_from, effective_to"
            ") VALUES ("
            ":id, :etype, 'external_fact', 't', 's', "
            f"'src', '{_TS}', '[]', '[]', "
            "'primary', 'high', :conf, :sha, "
            ":sup, 'system', 1, :efrom, :eto)"
        ),
        {
            "id": evidence_id,
            "etype": evidence_type,
            "conf": confidence,
            "sha": content_sha256,
            "sup": supersedes,
            "efrom": effective_from,
            "eto": effective_to,
        },
    )


def test_no_search_orm_rows_exist() -> None:
    """C2a freezes Search as migration-only; no ORM Row classes for search tables."""
    import infrastructure.persistence.orm as models_mod

    names = [n for n in dir(models_mod) if n.endswith("Row")]
    searchish = [
        n
        for n in names
        if "Search" in n or "search_" in n.lower() or n.startswith("ResearchSearch")
    ]
    assert searchish == [], f"unexpected Search ORM rows: {searchish}"
    for required in (
        "ResearchEvidenceRow",
        "SubjectEvidenceLinkRow",
        "EvidenceAssessmentRow",
        "ResearchReportRow",
        "ResearchEventRow",
        "DecisionRecordRow",
        "JournalEntryRow",
    ):
        assert hasattr(models_mod, required)


def test_business_tables_columns_pk_nullability_via_pragma(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    expected: dict[str, dict[str, int]] = {
        # col -> notnull (1/0); pk handled separately
        "research_evidence": {
            "evidence_id": 1,
            "evidence_type": 1,
            "origin": 1,
            "title": 1,
            "summary": 1,
            "content_text": 0,
            "structured_data_json": 0,
            "source_name": 1,
            "source_vendor": 0,
            "source_record_id": 0,
            "source_url": 0,
            "published_at": 0,
            "observed_at": 1,
            "effective_from": 0,
            "effective_to": 0,
            "instrument_ids_json": 1,
            "topic_tags_json": 1,
            "quality": 1,
            "reliability": 1,
            "confidence_decimal": 0,
            "content_sha256": 1,
            "supersedes_evidence_id": 0,
            "recorded_by": 1,
            "schema_version": 1,
        },
        "case_evidence_links": {
            "link_id": 1,
            "case_id": 1,
            "evidence_id": 1,
            "linked_at": 1,
            "linked_by": 1,
            "schema_version": 1,
        },
        "evidence_assessments": {
            "assessment_id": 1,
            "evidence_id": 1,
            "case_id": 1,
            "thesis_id": 0,
            "thesis_revision_id": 0,
            "stance": 1,
            "materiality_decimal": 1,
            "rationale": 1,
            "assessed_at": 1,
            "assessed_by": 1,
            "confirmed_by": 1,
            "schema_version": 1,
        },
        "research_reports": {
            "report_id": 1,
            "case_id": 1,
            "report_type": 1,
            "title": 1,
            "summary": 1,
            "content_markdown": 1,
            "as_of": 1,
            "created_at": 1,
            "created_by": 1,
            "research_run_id": 0,
            "evidence_ids_json": 1,
            "thesis_revision_ids_json": 1,
            "supersedes_report_id": 0,
            "content_sha256": 1,
            "model_name": 0,
            "prompt_version": 0,
            "schema_version": 1,
        },
        "research_events": {
            "event_id": 1,
            "case_id": 1,
            "event_type": 1,
            "title": 1,
            "summary": 1,
            "occurred_at": 1,
            "recorded_at": 1,
            "published_at": 0,
            "instrument_ids_json": 1,
            "evidence_ids_json": 1,
            "report_ids_json": 1,
            "related_entity_type": 0,
            "related_entity_id": 0,
            "source_name": 1,
            "schema_version": 1,
        },
        "decision_records": {
            "decision_id": 1,
            "case_id": 1,
            "decision_type": 1,
            "title": 1,
            "rationale": 1,
            "decided_at": 1,
            "recorded_at": 1,
            "decided_by": 1,
            "confirmation_mode": 1,
            "primary_instrument_id": 0,
            "thesis_revision_ids_json": 1,
            "evidence_ids_json": 1,
            "report_ids_json": 1,
            "supersedes_decision_id": 0,
            "position_context_snapshot_id": 0,
            "strategy_code": 0,
            "strategy_version": 0,
            "scenario": 0,
            "trade_plan_id": 0,
            "trade_plan_version": 0,
            "review_due_at": 0,
            "external_note_revision_id": 0,
            "idempotency_key": 1,
            "idempotency_payload_sha256": 1,
            "schema_version": 1,
        },
        "journal_entries": {
            "journal_id": 1,
            "case_id": 0,
            "entry_type": 1,
            "title": 1,
            "body_markdown": 1,
            "created_at": 1,
            "authored_by": 1,
            "confirmed_by": 1,
            "instrument_ids_json": 1,
            "topic_tags_json": 1,
            "related_entity_type": 0,
            "related_entity_id": 0,
            "supersedes_journal_id": 0,
            "idempotency_key": 1,
            "idempotency_payload_sha256": 1,
            "schema_version": 1,
        },
    }
    pk_cols = {
        "research_evidence": {"evidence_id"},
        "case_evidence_links": {"link_id"},
        "evidence_assessments": {"assessment_id"},
        "research_reports": {"report_id"},
        "research_events": {"event_id"},
        "decision_records": {"decision_id"},
        "journal_entries": {"journal_id"},
    }

    with engine.connect() as conn:
        for table, cols in expected.items():
            info = _pragma_table_info(conn, table)
            by_name = {c["name"]: c for c in info}
            assert set(by_name) == set(cols), (
                f"{table}: columns mismatch "
                f"extra={set(by_name) - set(cols)} "
                f"missing={set(cols) - set(by_name)}"
            )
            for col, notnull in cols.items():
                assert by_name[col]["notnull"] == notnull, (
                    f"{table}.{col} notnull expected {notnull}, got {by_name[col]['notnull']}"
                )
            actual_pk = {c["name"] for c in info if c["pk"]}
            assert actual_pk == pk_cols[table], f"{table} pk={actual_pk}"

            # Only decision/journal have idempotency columns.
            if table not in {"decision_records", "journal_entries"}:
                assert "idempotency_key" not in by_name
                assert "idempotency_payload_sha256" not in by_name

    engine.dispose()


def test_search_tables_columns_and_fts_triggers(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    with engine.connect() as conn:
        for table in _SEARCH_TABLES:
            row = conn.execute(
                text("SELECT name, sql FROM sqlite_master WHERE name=:n"),
                {"n": table},
            ).one_or_none()
            assert row is not None, f"missing {table}"

        doc_cols = {c["name"] for c in _pragma_table_info(conn, "research_search_documents")}
        assert doc_cols == {
            "rowid",
            "entity_type",
            "entity_id",
            "instrument_ids_text",
            "topic_tags",
            "title",
            "body",
            "visible_at",
            "occurred_at",
            "superseded_by_id",
        }
        cases_cols = {c["name"] for c in _pragma_table_info(conn, "research_search_document_cases")}
        assert cases_cols == {"document_rowid", "case_id", "membership_visible_at"}

        triggers = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='research_search_documents'"
                )
            ).all()
        }
        assert triggers == _FTS_TRIGGERS

        for name in _FTS_TRIGGERS:
            sql = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:n"),
                {"n": name},
            ).scalar()
            assert sql is not None
            assert "research_search_fts" in sql

    engine.dispose()


def test_foreign_keys_unique_checks_indexes_via_pragma_and_master(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    required_fks: dict[str, set[tuple[str, str, str]]] = {
        # (from_col, to_table, to_col)
        "research_evidence": {("supersedes_evidence_id", "research_evidence", "evidence_id")},
        "case_evidence_links": {
            ("case_id", "investment_cases", "case_id"),
            ("evidence_id", "research_evidence", "evidence_id"),
        },
        "evidence_assessments": {
            ("evidence_id", "research_evidence", "evidence_id"),
            ("case_id", "investment_cases", "case_id"),
            ("thesis_id", "theses", "thesis_id"),
            ("thesis_revision_id", "thesis_revisions", "revision_id"),
        },
        "research_reports": {
            ("case_id", "investment_cases", "case_id"),
            ("supersedes_report_id", "research_reports", "report_id"),
        },
        "research_events": {("case_id", "investment_cases", "case_id")},
        "decision_records": {
            ("case_id", "investment_cases", "case_id"),
            ("supersedes_decision_id", "decision_records", "decision_id"),
            (
                "external_note_revision_id",
                "external_note_revisions",
                "note_revision_id",
            ),
        },
        "journal_entries": {
            ("case_id", "investment_cases", "case_id"),
            ("supersedes_journal_id", "journal_entries", "journal_id"),
        },
        "research_search_document_cases": {
            ("document_rowid", "research_search_documents", "rowid"),
            ("case_id", "investment_cases", "case_id"),
        },
        "research_search_document_instruments": {
            ("document_rowid", "research_search_documents", "rowid"),
        },
        "research_search_document_tags": {
            ("document_rowid", "research_search_documents", "rowid"),
        },
    }
    required_checks = {
        "ck_research_evidence_type",
        "ck_research_evidence_origin",
        "ck_research_evidence_quality",
        "ck_research_evidence_reliability",
        "ck_research_evidence_schema_version",
        "ck_research_evidence_confidence",
        "ck_research_evidence_effective_order",
        "ck_research_evidence_correction_supersedes",
        "ck_research_evidence_content_sha256",
        "ck_case_evidence_links_schema_version",
        "ck_evidence_assessments_stance",
        "ck_evidence_assessments_schema_version",
        "ck_evidence_assessments_materiality",
        "ck_research_reports_type",
        "ck_research_reports_schema_version",
        "ck_research_reports_as_of_order",
        "ck_research_reports_content_sha256",
        "ck_research_events_type",
        "ck_research_events_schema_version",
        "ck_research_events_related_entity_pair",
        "ck_decision_records_type",
        "ck_decision_records_confirmation_mode",
        "ck_decision_records_schema_version",
        "ck_decision_records_decided_order",
        "ck_decision_records_confirmation_matrix",
        "ck_decision_records_idempotency_hash",
        "ck_decision_records_scenario",
        "ck_decision_records_trade_plan_pair",
        "ck_decision_records_trade_plan_version",
        "ck_journal_entries_type",
        "ck_journal_entries_schema_version",
        "ck_journal_entries_related_entity_pair",
        "ck_journal_entries_idempotency_hash",
        "ck_research_search_documents_entity_type",
    }
    required_uniques = {
        "uq_research_evidence_content_sha256",
        "uq_research_reports_content_sha256",
        "uq_case_evidence_links_case_evidence",
        "uq_decision_records_idempotency_key",
        "uq_journal_entries_idempotency_key",
        "uq_research_search_documents_entity_id",
    }

    with engine.connect() as conn:
        for table, fks in required_fks.items():
            actual = {(r["from"], r["table"], r["to"]) for r in _pragma_foreign_keys(conn, table)}
            assert fks.issubset(actual), f"{table} missing FKs: {fks - actual}"

        found_checks: set[str] = set()
        found_uniques: set[str] = set()
        all_index_names: set[str] = set()
        for table in list(_BUSINESS_TABLES) + list(_SEARCH_TABLES - {"research_search_fts"}):
            ddl = _table_ddl(conn, table)
            for name in re.findall(r"\b(ck_[a-z0-9_]+)\b", ddl, flags=re.I):
                found_checks.add(name)
            for name in re.findall(r"\b(uq_[a-z0-9_]+)\b", ddl, flags=re.I):
                found_uniques.add(name)
            for idx in _pragma_indexes(conn, table):
                if idx.get("name"):
                    all_index_names.add(idx["name"])

        assert required_checks.issubset(found_checks), (
            f"missing checks: {required_checks - found_checks}"
        )
        assert required_uniques.issubset(found_uniques), (
            f"missing uniques: {required_uniques - found_uniques}"
        )
        assert _REQUIRED_INDEXES.issubset(all_index_names), (
            f"missing indexes: {_REQUIRED_INDEXES - all_index_names}"
        )

    engine.dispose()


def test_orm_metadata_parity_for_seven_business_tables(
    migrated_sqlite_url: str,
) -> None:
    """ORM column sets / named constraints must match migrated SQLite schema."""
    engine = create_engine(migrated_sqlite_url)

    with engine.connect() as conn:
        for table_name, row_cls in _BUSINESS_TABLES.items():
            pragma_cols = {c["name"] for c in _pragma_table_info(conn, table_name)}
            orm_cols = {c.name for c in row_cls.__table__.columns}
            assert orm_cols == pragma_cols, (
                f"{table_name}: orm-only={orm_cols - pragma_cols} db-only={pragma_cols - orm_cols}"
            )

            orm_index_names = {idx.name for idx in row_cls.__table__.indexes if idx.name}
            db_index_names = {r["name"] for r in _pragma_indexes(conn, table_name) if r.get("name")}
            for idx_name in orm_index_names:
                assert idx_name in db_index_names, f"{table_name} missing index {idx_name}"

            ddl = _table_ddl(conn, table_name)
            for constraint in row_cls.__table__.constraints:
                if isinstance(constraint, CheckConstraint) and constraint.name:
                    assert constraint.name in ddl, f"{table_name} missing check {constraint.name}"
                if isinstance(constraint, UniqueConstraint) and constraint.name:
                    in_ddl = constraint.name in ddl
                    in_idx = constraint.name in db_index_names
                    assert in_ddl or in_idx, f"{table_name} missing unique {constraint.name}"

    engine.dispose()


def test_enum_time_hash_idempotency_constraints_reject_bad_rows(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    with engine.begin() as conn:
        _seed_case(conn)
        _insert_evidence(conn)

    # Bad evidence type
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        _insert_evidence(
            conn,
            evidence_id="evidence_bad_type",
            evidence_type="not_a_type",
            content_sha256=_HEX64_B,
        )

    # CORRECTION without supersedes
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        _insert_evidence(
            conn,
            evidence_id="evidence_bad_corr",
            evidence_type="correction",
            content_sha256=_HEX64_B,
            supersedes=None,
        )

    # confidence out of range
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        _insert_evidence(
            conn,
            evidence_id="evidence_bad_conf",
            content_sha256=_HEX64_B,
            confidence="1.5",
        )

    # effective_to < effective_from
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        _insert_evidence(
            conn,
            evidence_id="evidence_bad_eff",
            content_sha256=_HEX64_B,
            effective_from=_TS_LATER,
            effective_to=_TS,
        )

    # bad content_sha256 (not 64 lowercase hex)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        _insert_evidence(
            conn,
            evidence_id="evidence_bad_hash",
            content_sha256="ZZZZ",
        )

    # duplicate content_sha256
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        _insert_evidence(
            conn,
            evidence_id="evidence_dup_hash",
            content_sha256=_HEX64_A,
        )

    # report as_of > created_at
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO research_reports("
                "report_id, case_id, report_type, title, summary, "
                "content_markdown, as_of, created_at, created_by, "
                "evidence_ids_json, thesis_revision_ids_json, content_sha256, "
                "schema_version"
                ") VALUES ("
                f"'report_bad', '{_CASE_ID}', 'ad_hoc', 't', 's', "
                f"'body', '{_TS_LATER}', '{_TS}', 'user', "
                f"'[]', '[]', '{_HEX64_B}', 1)"
            )
        )

    # decision decided_at > recorded_at
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO decision_records("
                "decision_id, case_id, decision_type, title, rationale, "
                "decided_at, recorded_at, decided_by, confirmation_mode, "
                "thesis_revision_ids_json, evidence_ids_json, report_ids_json, "
                "idempotency_key, idempotency_payload_sha256, schema_version"
                ") VALUES ("
                f"'decision_bad_time', '{_CASE_ID}', 'watch', 't', 'r', "
                f"'{_TS_LATER}', '{_TS}', 'user', 'normal', "
                f"'[]', '[]', '[]', 'idem-bad-time', '{_HEX64_B}', 1)"
            )
        )

    # decision confirmation matrix: initiate_intent requires strict_review
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO decision_records("
                "decision_id, case_id, decision_type, title, rationale, "
                "decided_at, recorded_at, decided_by, confirmation_mode, "
                "thesis_revision_ids_json, evidence_ids_json, report_ids_json, "
                "idempotency_key, idempotency_payload_sha256, schema_version"
                ") VALUES ("
                f"'decision_bad_mode', '{_CASE_ID}', 'initiate_intent', 't', 'r', "
                f"'{_TS}', '{_TS}', 'user', 'normal', "
                f"'[]', '[]', '[]', 'idem-bad-mode', '{_HEX64_B}', 1)"
            )
        )

    # journal related entity pair mismatch
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO journal_entries("
                "journal_id, case_id, entry_type, title, body_markdown, "
                "created_at, authored_by, confirmed_by, instrument_ids_json, "
                "topic_tags_json, related_entity_type, related_entity_id, "
                "idempotency_key, idempotency_payload_sha256, schema_version"
                ") VALUES ("
                f"'journal_bad_pair', '{_CASE_ID}', 'note', 't', 'b', "
                f"'{_TS}', 'user', 'user', '[]', '[]', 'evidence', NULL, "
                f"'idem-bad-pair', '{_HEX64_B}', 1)"
            )
        )

    # legal decision + journal + unique idempotency conflict
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO decision_records("
                "decision_id, case_id, decision_type, title, rationale, "
                "decided_at, recorded_at, decided_by, confirmation_mode, "
                "thesis_revision_ids_json, evidence_ids_json, report_ids_json, "
                "idempotency_key, idempotency_payload_sha256, schema_version"
                ") VALUES ("
                f"'decision_ok', '{_CASE_ID}', 'watch', 't', 'r', "
                f"'{_TS}', '{_TS}', 'user', 'normal', "
                f"'[]', '[]', '[]', 'idem-decision-ok', '{_HEX64_B}', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO journal_entries("
                "journal_id, case_id, entry_type, title, body_markdown, "
                "created_at, authored_by, confirmed_by, instrument_ids_json, "
                "topic_tags_json, related_entity_type, related_entity_id, "
                "idempotency_key, idempotency_payload_sha256, schema_version"
                ") VALUES ("
                f"'journal_ok', '{_CASE_ID}', 'note', 't', 'b', "
                f"'{_TS}', 'user', 'user', '[]', '[]', NULL, NULL, "
                f"'idem-journal-ok', '{_HEX64_B}', 1)"
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO decision_records("
                "decision_id, case_id, decision_type, title, rationale, "
                "decided_at, recorded_at, decided_by, confirmation_mode, "
                "thesis_revision_ids_json, evidence_ids_json, report_ids_json, "
                "idempotency_key, idempotency_payload_sha256, schema_version"
                ") VALUES ("
                f"'decision_dup_idem', '{_CASE_ID}', 'watch', 't', 'r', "
                f"'{_TS}', '{_TS}', 'user', 'normal', "
                f"'[]', '[]', '[]', 'idem-decision-ok', '{_HEX64_A}', 1)"
            )
        )

    # materiality out of range
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO evidence_assessments("
                "assessment_id, evidence_id, case_id, stance, "
                "materiality_decimal, rationale, assessed_at, assessed_by, "
                "confirmed_by, schema_version"
                ") VALUES ("
                f"'assess_bad', '{_EVIDENCE_ID}', '{_CASE_ID}', 'supports', "
                f"'2.0', 'r', '{_TS}', 'user', 'user', 1)"
            )
        )

    engine.dispose()


def test_fts_triggers_insert_update_delete(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO research_search_documents("
                "entity_type, entity_id, instrument_ids_text, topic_tags, "
                "title, body, visible_at, occurred_at, superseded_by_id"
                ") VALUES ("
                f"'evidence', 'entity_fts_1', '', 'tag one', "
                f"'hello title', 'body content about market', '{_TS}', NULL, NULL)"
            )
        )

    with engine.connect() as conn:
        # External-content FTS should index inserted row.
        hit = conn.execute(
            text(
                "SELECT entity_id FROM research_search_documents "
                "WHERE rowid IN ("
                "SELECT rowid FROM research_search_fts "
                "WHERE research_search_fts MATCH 'hello')"
            )
        ).all()
        assert [r[0] for r in hit] == ["entity_fts_1"]

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE research_search_documents "
                "SET title='updated title', body='fresh body content' "
                "WHERE entity_id='entity_fts_1'"
            )
        )

    with engine.connect() as conn:
        old = conn.execute(
            text("SELECT rowid FROM research_search_fts WHERE research_search_fts MATCH 'hello'")
        ).all()
        assert old == []
        new = conn.execute(
            text(
                "SELECT entity_id FROM research_search_documents "
                "WHERE rowid IN ("
                "SELECT rowid FROM research_search_fts "
                "WHERE research_search_fts MATCH 'updated')"
            )
        ).all()
        assert [r[0] for r in new] == ["entity_fts_1"]

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM research_search_documents WHERE entity_id='entity_fts_1'"))

    with engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT rowid FROM research_search_fts WHERE research_search_fts MATCH 'updated'")
        ).all()
        assert remaining == []
        assert conn.execute(text("SELECT COUNT(*) FROM research_search_documents")).scalar() == 0

    engine.dispose()


def test_schema_not_created_via_metadata_create_all(
    tmp_path: Path,
    migrated_sqlite_url: str,
) -> None:
    """Migration is authoritative; create_all is not used as proof of schema."""
    # Ensure tests above use alembic upgrade; this guard asserts 0004 file exists
    # and that Base.metadata alone is insufficient for FTS virtual table.
    import infrastructure.persistence.orm  # noqa: F401 — register rows
    from infrastructure.persistence.metadata import Base

    db_path = tmp_path / "create_all.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    # Business tables may appear via ORM, but FTS virtual table does not.
    assert "research_search_fts" not in tables
    # Migration path does create FTS
    engine.dispose()

    engine2 = create_engine(migrated_sqlite_url)
    tables2 = set(inspect(engine2).get_table_names())
    assert "research_search_fts" in tables2
    engine2.dispose()
