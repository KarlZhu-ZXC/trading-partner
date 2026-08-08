"""Phase 1B Alembic migration round-trip and candidate SQL CHECK coverage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, create_engine, inspect, text
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
    monkeypatch.setenv("APP_NAME", "research-migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "research-migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


_RESEARCH_TABLES = {
    "investment_cases",
    "theses",
    "thesis_revisions",
    "assumptions",
    "invalidation_conditions",
    "open_questions",
    "watchlist_items",
    "candidate_thesis_revisions",
}


def test_phase1b_migration_round_trip(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research_migrate.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert _RESEARCH_TABLES.issubset(tables)
    assert "schema_versions" in tables
    assert "system_audit_log" in tables

    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert "phase1a_foundation" in versions
        assert "phase1b_research_state" in versions
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        # Head may advance past 0002 (e.g. Phase 1D 0003); research tables remain.
        # Compare to the configured script head — never lexicographically.
        expected_head = ScriptDirectory.from_config(cfg).get_current_head()
        assert rev[0] == expected_head

    command.downgrade(cfg, "0001_phase1a_foundation")
    tables_mid = set(inspect(engine).get_table_names())
    assert not _RESEARCH_TABLES.intersection(tables_mid)
    assert "schema_versions" in tables_mid
    with engine.connect() as conn:
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert versions == {"phase1a_foundation"}

    command.downgrade(cfg, "base")
    tables_base = set(inspect(engine).get_table_names())
    assert "schema_versions" not in tables_base
    assert "system_audit_log" not in tables_base

    command.upgrade(cfg, "head")
    tables_again = set(inspect(engine).get_table_names())
    assert tables_again == tables
    engine.dispose()


def test_phase1b_upgrade_from_phase1a_only(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "from_1a.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, "0001_phase1a_foundation")
    engine = create_engine(database_url)
    assert "investment_cases" not in set(inspect(engine).get_table_names())

    command.upgrade(cfg, "0002_phase1b_research_state")
    tables = set(inspect(engine).get_table_names())
    assert _RESEARCH_TABLES.issubset(tables)
    engine.dispose()


def test_head_restricts_research_subject_to_lifecycle_statuses(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "subject_lifecycle.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    with engine.connect() as conn:
        ddl = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='investment_cases'"
            )
        ).scalar_one()

    assert "status IN ('draft','active','archived')" in ddl
    assert "strengthened" not in ddl
    assert "weakened" not in ddl
    assert "invalidated" not in ddl
    engine.dispose()


def test_candidate_sql_checks_reject_illegal_rows(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "candidate_checks.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
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
                "'2026-07-16T12:00:00+00:00', '2026-07-16T12:00:00+00:00', "
                "'user', '[]', '[]', '[]', '[]', '[]', 1)"
            )
        )

    def _insert_candidate(**cols: str) -> None:
        defaults = {
            "candidate_id": "'run_00000000-0000-7000-8000-000000000001'",
            "case_id": "'case_00000000-0000-7000-8000-000000000001'",
            "thesis_id": "NULL",
            "payload_json": "'{}'",
            "kind": "'thesis_revision'",
            "confirmation_mode": "'normal'",
            "status": "'proposed'",
            "proposed_at": "'2026-07-16T12:00:00+00:00'",
            "expires_at": "'2026-07-23T12:00:00+00:00'",
            "proposed_by": "'codex'",
            "proposed_by_rationale": "'r'",
            "reviewed_at": "NULL",
            "reviewed_by": "NULL",
            "review_note": "NULL",
            "rejection_reason": "NULL",
            "idempotency_key": "'idem-check-1'",
        }
        defaults.update(cols)
        sql = (
            "INSERT INTO candidate_thesis_revisions("
            "candidate_id, case_id, thesis_id, payload_json, kind, "
            "confirmation_mode, status, proposed_at, expires_at, "
            "proposed_by, proposed_by_rationale, reviewed_at, reviewed_by, "
            "review_note, rejection_reason, idempotency_key"
            ") VALUES ("
            f"{defaults['candidate_id']}, {defaults['case_id']}, "
            f"{defaults['thesis_id']}, {defaults['payload_json']}, "
            f"{defaults['kind']}, {defaults['confirmation_mode']}, "
            f"{defaults['status']}, {defaults['proposed_at']}, "
            f"{defaults['expires_at']}, {defaults['proposed_by']}, "
            f"{defaults['proposed_by_rationale']}, {defaults['reviewed_at']}, "
            f"{defaults['reviewed_by']}, {defaults['review_note']}, "
            f"{defaults['rejection_reason']}, {defaults['idempotency_key']})"
        )
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.execute(text(sql))

    # case_scope: non-watchlist without case_id
    with pytest.raises(IntegrityError):
        _insert_candidate(
            candidate_id="'run_00000000-0000-7000-8000-0000000000a1'",
            case_id="NULL",
            kind="'thesis_revision'",
            idempotency_key="'idem-case-scope'",
        )

    # thesis_scope: assumption without thesis_id
    with pytest.raises(IntegrityError):
        _insert_candidate(
            candidate_id="'run_00000000-0000-7000-8000-0000000000a2'",
            kind="'assumption'",
            thesis_id="NULL",
            idempotency_key="'idem-thesis-scope'",
        )

    # review_state: confirmed without reviewed_*
    with pytest.raises(IntegrityError):
        _insert_candidate(
            candidate_id="'run_00000000-0000-7000-8000-0000000000a3'",
            status="'confirmed'",
            reviewed_at="NULL",
            reviewed_by="NULL",
            idempotency_key="'idem-review-state'",
        )

    # rejection_reason required for rejected
    with pytest.raises(IntegrityError):
        _insert_candidate(
            candidate_id="'run_00000000-0000-7000-8000-0000000000a4'",
            status="'rejected'",
            reviewed_at="'2026-07-16T13:00:00+00:00'",
            reviewed_by="'user'",
            rejection_reason="NULL",
            idempotency_key="'idem-reject'",
        )

    # legal watchlist candidate with case_id NULL
    _insert_candidate(
        candidate_id="'run_00000000-0000-7000-8000-0000000000b1'",
        case_id="NULL",
        kind="'watchlist_item'",
        idempotency_key="'idem-watch-ok'",
    )

    # legal confirmed
    _insert_candidate(
        candidate_id="'run_00000000-0000-7000-8000-0000000000b2'",
        status="'confirmed'",
        reviewed_at="'2026-07-16T13:00:00+00:00'",
        reviewed_by="'user'",
        review_note="'ok'",
        idempotency_key="'idem-confirm-ok'",
    )
    engine.dispose()


def test_thesis_revision_sql_checks(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "rev_checks.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO investment_cases("
                "case_id, case_type, title, summary, status, topic_tags_json, "
                "created_at, updated_at, created_by, linked_case_ids_json, "
                "evidence_ids_json, report_ids_json, event_ids_json, "
                "decision_ids_json, schema_version"
                ") VALUES ("
                "'case_00000000-0000-7000-8000-000000000001', 'company', 't', 's', "
                "'active', '[]', "
                "'2026-07-16T12:00:00+00:00', '2026-07-16T12:00:00+00:00', "
                "'user', '[]', '[]', '[]', '[]', '[]', 1)"
            )
        )
        # company case requires instrument at domain layer only — SQL allows null
        conn.execute(
            text(
                "INSERT INTO theses("
                "thesis_id, case_id, title, role, status, current_revision_no, "
                "latest_revision_id, rival_thesis_ids_json, created_at, updated_at"
                ") VALUES ("
                "'thesis_00000000-0000-7000-8000-000000000001', "
                "'case_00000000-0000-7000-8000-000000000001', "
                "'P', 'primary', 'active', 1, "
                "'rev_00000000-0000-7000-8000-000000000001', '[]', "
                "'2026-07-16T12:00:00+00:00', '2026-07-16T12:00:00+00:00')"
            )
        )

    # revision_no=1 with supersedes set must fail
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO thesis_revisions("
                "revision_id, thesis_id, case_id, revision_no, "
                "supersedes_revision_no, statement, rationale, confidence_band, "
                "rating, confirmation_mode, proposed_by, confirmed_by, "
                "proposed_at, confirmed_at, invalidation_check_note, schema_version"
                ") VALUES ("
                "'rev_00000000-0000-7000-8000-000000000001', "
                "'thesis_00000000-0000-7000-8000-000000000001', "
                "'case_00000000-0000-7000-8000-000000000001', "
                "1, 0, 's', 'r', 'low', 'watch', 'normal', 'codex', 'user', "
                "'2026-07-16T12:00:00+00:00', '2026-07-16T12:00:00+00:00', "
                "'n', 1)"
            )
        )

    # valid first revision
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO thesis_revisions("
                "revision_id, thesis_id, case_id, revision_no, "
                "supersedes_revision_no, statement, rationale, confidence_band, "
                "rating, confirmation_mode, proposed_by, confirmed_by, "
                "proposed_at, confirmed_at, invalidation_check_note, schema_version"
                ") VALUES ("
                "'rev_00000000-0000-7000-8000-000000000001', "
                "'thesis_00000000-0000-7000-8000-000000000001', "
                "'case_00000000-0000-7000-8000-000000000001', "
                "1, NULL, 's', 'r', 'low', 'watch', 'normal', 'codex', 'user', "
                "'2026-07-16T12:00:00+00:00', '2026-07-16T12:00:00+00:00', "
                "'n', 1)"
            )
        )
    engine.dispose()


def _orm_named_objects() -> tuple[set[str], set[str], set[str], set[str]]:
    """Collect ORM table / index / check / unique names for research tables."""
    from infrastructure.persistence.metadata import Base

    tables: set[str] = set()
    indexes: set[str] = set()
    checks: set[str] = set()
    uniques: set[str] = set()
    for table in Base.metadata.tables.values():
        if table.name not in _RESEARCH_TABLES:
            continue
        tables.add(table.name)
        for idx in table.indexes:
            if isinstance(idx, Index) and idx.name:
                indexes.add(idx.name)
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and constraint.name:
                checks.add(constraint.name)
            if isinstance(constraint, UniqueConstraint) and constraint.name:
                uniques.add(constraint.name)
    return tables, indexes, checks, uniques


def test_orm_migration_schema_parity(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORM Index/Check/Unique names must match 0002 migration objects."""
    db_path = tmp_path / "schema_parity.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    insp = inspect(engine)
    migrated_tables = set(insp.get_table_names()) & _RESEARCH_TABLES

    orm_tables, orm_indexes, orm_checks, orm_uniques = _orm_named_objects()
    assert orm_tables == _RESEARCH_TABLES
    assert migrated_tables == _RESEARCH_TABLES

    migrated_indexes: set[str] = set()
    for table_name in sorted(_RESEARCH_TABLES):
        for idx in insp.get_indexes(table_name):
            name = idx.get("name")
            if name:
                migrated_indexes.add(name)

    # SQLite stores CHECK names in sqlite_master SQL text.
    migrated_checks: set[str] = set()
    migrated_uniques: set[str] = set()
    with engine.connect() as conn:
        for table_name in sorted(_RESEARCH_TABLES):
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table_name},
            ).one()
            ddl = row[0] or ""
            for token in ddl.replace("(", " ").replace(")", " ").replace(",", " ").split():
                if token.startswith("ck_"):
                    migrated_checks.add(token)
                if token.startswith("uq_"):
                    migrated_uniques.add(token)
            # UNIQUE may appear as CONSTRAINT uq_... UNIQUE
            for piece in ddl.split("CONSTRAINT "):
                name = piece.split()[0] if piece.strip() else ""
                if name.startswith("uq_"):
                    migrated_uniques.add(name.rstrip(","))
                if name.startswith("ck_"):
                    migrated_checks.add(name.rstrip(","))

    assert orm_indexes == migrated_indexes, (
        f"index mismatch orm-only={orm_indexes - migrated_indexes} "
        f"migration-only={migrated_indexes - orm_indexes}"
    )
    assert orm_checks == migrated_checks, (
        f"check mismatch orm-only={orm_checks - migrated_checks} "
        f"migration-only={migrated_checks - orm_checks}"
    )
    assert orm_uniques == migrated_uniques, (
        f"unique mismatch orm-only={orm_uniques - migrated_uniques} "
        f"migration-only={migrated_uniques - orm_uniques}"
    )
    engine.dispose()
