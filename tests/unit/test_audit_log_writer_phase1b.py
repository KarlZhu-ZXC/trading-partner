"""Session-bound audit writer: no self-commit; same UoW session as business rows."""

from __future__ import annotations

import json

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import InvestmentCaseStatus, InvestmentCaseType
from domain.research.models import RESEARCH_SCHEMA_VERSION, InvestmentCase
from infrastructure.persistence.audit_log_writer import SqlAlchemySessionAuditLogWriter
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def test_session_audit_writer_does_not_commit_itself(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "session_audit.db"
    engine = create_engine(f"sqlite:///{db}")
    _enable_fk(engine)
    Base.metadata.create_all(engine)
    clock = FixedClock()
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    with Session(engine) as session:
        writer = SqlAlchemySessionAuditLogWriter(session, clock, ids, redactor)
        audit_id = writer.append(
            event_type="phase1b.test",
            payload={"api_key": "sk-secret", "ok": True},
        )
        assert audit_id.startswith("audit_")
        # Flushed but not committed: visible in this session, gone after rollback.
        count = session.execute(text("SELECT count(*) FROM system_audit_log")).scalar()
        assert count == 1
        session.rollback()

    with Session(engine) as session:
        count = session.execute(text("SELECT count(*) FROM system_audit_log")).scalar()
        assert count == 0
    engine.dispose()


def test_uow_commits_business_and_audit_together(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "uow_audit.db"
    engine = create_engine(f"sqlite:///{db}")
    _enable_fk(engine)
    Base.metadata.create_all(engine)
    clock = FixedClock()
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    from domain.common.ids import EntityIdPrefix

    case = InvestmentCase(
        case_id=ids.new(EntityIdPrefix.CASE),
        case_type=InvestmentCaseType.THEME,
        title="AI infra",
        summary="Theme case without primary instrument",
        status=InvestmentCaseStatus.DRAFT,
        primary_instrument_id=None,
        topic_tags=("ai",),
        created_at=clock.now(),
        updated_at=clock.now(),
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_case_ids=(),
        evidence_ids=(),
        report_ids=(),
        event_ids=(),
        decision_ids=(),
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    with SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor) as uow:
        uow.cases.add(case)
        audit_id = uow.audit.append(
            event_type="phase1b.case.created",
            payload={"case_id": case.case_id, "token": "secret-token-value"},
            request_id="req_test",
        )
        uow.commit()

    with Session(engine) as session:
        cases = session.execute(text("SELECT case_id FROM investment_cases")).all()
        assert len(cases) == 1
        row = session.execute(
            text("SELECT audit_id, payload_json FROM system_audit_log WHERE audit_id = :aid"),
            {"aid": audit_id},
        ).one()
        payload = json.loads(row[1])
        assert "secret-token-value" not in row[1]
        assert payload["case_id"] == case.case_id
    engine.dispose()


def test_uow_rollback_drops_audit_and_business(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "uow_rollback.db"
    engine = create_engine(f"sqlite:///{db}")
    _enable_fk(engine)
    Base.metadata.create_all(engine)
    clock = FixedClock()
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()
    from domain.common.ids import EntityIdPrefix

    with SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor) as uow:
        uow.cases.add(
            InvestmentCase(
                case_id=ids.new(EntityIdPrefix.CASE),
                case_type=InvestmentCaseType.MACRO,
                title="Rates",
                summary="Macro view",
                status=InvestmentCaseStatus.DRAFT,
                primary_instrument_id=None,
                topic_tags=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                created_by="user",
                archived_at=None,
                archived_reason=None,
                linked_case_ids=(),
                evidence_ids=(),
                report_ids=(),
                event_ids=(),
                decision_ids=(),
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.audit.append(event_type="phase1b.case.created", payload={"x": 1})
        uow.rollback()

    with Session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM investment_cases")).scalar() == 0
        assert session.execute(text("SELECT count(*) FROM system_audit_log")).scalar() == 0
    engine.dispose()
