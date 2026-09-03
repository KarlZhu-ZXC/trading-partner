"""Decision links to one exact immutable external observation revision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.services._research_memory_write_support import (
    compute_decision_idempotency_payload_sha256,
)
from application.services.decision_record_service import DecisionRecordService
from application.services.research_timeline_service import ResearchTimelineService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ResearchTimelineEntityType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject
from infrastructure.persistence.orm import (
    DecisionRecordRow,
    ExternalNoteIdentityRow,
    ExternalNoteRevisionRow,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.mcp.schemas import DecisionRecordAppendInput

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=1)
SUBJECT_INSTRUMENT = "equity:US:NVDA"
NOTE_ID = "external_note_00000000-0000-7000-8000-000000000001"
NOTE_REVISION_ID = "external_note_revision_00000000-0000-7000-8000-000000000001"


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _subject(factory: object, ids: SequentialIdGenerator, clock: FixedClock) -> str:
    value = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA research",
        summary="Long-horizon research scope",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=SUBJECT_INSTRUMENT,
        topic_tags=(),
        created_at=clock.now(),
        updated_at=clock.now(),
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_subject_ids=(),
        evidence_ids=(),
        report_ids=(),
        event_ids=(),
        decision_ids=(),
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:  # type: ignore[operator]
        uow.subjects.add(value)
        uow.commit()
    return value.subject_id


def _insert_note(engine: Engine, *, observed_at: datetime, instrument_id: str | None) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            ExternalNoteIdentityRow(
                note_id=NOTE_ID,
                source="LOCAL_OBSERVATION_BRIDGE",
                external_id="nvda-note",
                title="NVDA note",
                primary_instrument_id=instrument_id,
                created_at=EARLIER.isoformat(),
                last_seen_at=observed_at.isoformat(),
            )
        )
        session.add(
            ExternalNoteRevisionRow(
                note_revision_id=NOTE_REVISION_ID,
                note_id=NOTE_ID,
                version=1,
                content_sha256="a" * 64,
                source_revision_key="bridge:nvda:1",
                title="NVDA note",
                summary="Observation",
                full_body="Observation",
                coverage="FULL",
                source_timestamp=observed_at.isoformat(),
                observed_at=observed_at.isoformat(),
                visibility="SELF",
                related_stock_ids=(),
                related_codes=(),
                blocks_json="[]",
            )
        )


def _append_base(subject_id: str, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "subject_id": subject_id,
        "decision_type": DecisionType.WATCH,
        "title": "Watch NVDA",
        "rationale": "Need more evidence",
        "decided_at": EARLIER,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "primary_instrument_id": SUBJECT_INSTRUMENT,
        "thesis_revision_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "supersedes_decision_id": None,
        "position_context_snapshot_id": None,
        "idempotency_key": "decision-note-1",
    }
    result.update(overrides)
    return result


def test_decision_append_schema_accepts_only_exact_external_revision_ids() -> None:
    payload = {
        "case_id": "case_00000000-0000-7000-8000-000000000001",
        "decision_type": DecisionType.WATCH,
        "title": "Watch",
        "rationale": "Need more evidence",
        "decided_at": EARLIER,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "idempotency_key": "schema-note",
        "external_note_revision_id": NOTE_REVISION_ID,
    }
    value = DecisionRecordAppendInput.model_validate(payload)
    assert value.external_note_revision_id == NOTE_REVISION_ID
    with pytest.raises(ValidationError):
        DecisionRecordAppendInput.model_validate(
            {**payload, "external_note_revision_id": "external_note_revision_legacy"}
        )


def test_omitted_observation_reference_preserves_legacy_idempotency_digest() -> None:
    common: dict[str, object] = {
        "subject_id": "case_00000000-0000-7000-8000-000000000001",
        "decision_type": DecisionType.WATCH,
        "title": "Watch",
        "rationale": "Need more evidence",
        "decided_at": EARLIER,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "primary_instrument_id": None,
        "thesis_revision_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "supersedes_decision_id": None,
        "position_context_snapshot_id": None,
        "strategy_code": "strategy_v1",
        "strategy_version": "1",
        "scenario": None,
        "trade_plan_id": None,
        "trade_plan_version": None,
        "review_due_at": None,
    }
    legacy = compute_decision_idempotency_payload_sha256(**common)  # type: ignore[arg-type]
    omitted = compute_decision_idempotency_payload_sha256(
        **common, external_note_revision_id=None  # type: ignore[arg-type]
    )
    linked = compute_decision_idempotency_payload_sha256(
        **common,
        external_note_revision_id=NOTE_REVISION_ID,  # type: ignore[arg-type]
    )
    assert omitted == legacy
    assert linked != legacy


def test_decision_note_revision_round_trips_and_appears_in_timeline(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    _enable_fk(engine)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator(start=10_000)
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    subject_id = _subject(factory, ids, clock)
    _insert_note(engine, observed_at=EARLIER, instrument_id=SUBJECT_INSTRUMENT)
    service = DecisionRecordService(factory, clock, ids, redactor)
    result = service.append(
        **_append_base(
            subject_id,
            external_note_revision_id=NOTE_REVISION_ID,
        )
    )
    assert result.ok and result.data is not None
    assert result.data.external_note_revision_id == NOTE_REVISION_ID

    with Session(engine) as session:
        row = session.get(DecisionRecordRow, result.data.decision_id)
        assert row is not None
        assert row.external_note_revision_id == NOTE_REVISION_ID

    timeline = ResearchTimelineService(factory, clock, ids, redactor).get_timeline(
        subject_id=subject_id,
        entity_types=(ResearchTimelineEntityType.DECISION,),
    )
    assert timeline.ok and timeline.data is not None
    assert timeline.data.items[0].external_note_revision_id == NOTE_REVISION_ID
    engine.dispose()


def test_decision_note_revision_must_match_subject_and_point_in_time(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    _enable_fk(engine)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator(start=20_000)
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    subject_id = _subject(factory, ids, clock)
    _insert_note(engine, observed_at=EARLIER, instrument_id="equity:US:OTHER")
    service = DecisionRecordService(factory, clock, ids, redactor)
    mismatched = service.append(
        **_append_base(
            subject_id,
            external_note_revision_id=NOTE_REVISION_ID,
            idempotency_key="decision-note-mismatch",
        )
    )
    assert not mismatched.ok
    assert any(error.code == "INVALID_RESEARCH_LINK" for error in mismatched.errors)

    with Session(engine) as session:
        session.execute(
            ExternalNoteIdentityRow.__table__.update()
            .where(ExternalNoteIdentityRow.note_id == NOTE_ID)
            .values(primary_instrument_id=SUBJECT_INSTRUMENT)
        )
        session.execute(
            ExternalNoteRevisionRow.__table__.update()
            .where(ExternalNoteRevisionRow.note_revision_id == NOTE_REVISION_ID)
            .values(observed_at=FUTURE.isoformat())
        )
        session.commit()
    future = service.append(
        **_append_base(
            subject_id,
            external_note_revision_id=NOTE_REVISION_ID,
            idempotency_key="decision-note-future",
        )
    )
    assert not future.ok
    assert any(error.code == "INVALID_RESEARCH_LINK" for error in future.errors)
    engine.dispose()
