from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from sqlalchemy import create_engine

from application.dto.quick_review import QuickReviewInput
from application.dto.research_changes import ResearchChangesBaselineDTO, ResearchChangesDTO
from application.services.decision_record_service import DecisionRecordService
from application.services.quick_review_service import QuickReviewService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    ResearchSubjectStatus,
    ResearchSubjectType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor


def test_quick_decision_versions_and_retry_use_existing_durable_gates(migrated_sqlite_url):
    engine = create_engine(migrated_sqlite_url)
    now = datetime(2026, 9, 15, tzinfo=UTC)
    clock, ids, redactor = FixedClock(now), SequentialIdGenerator(), DefaultSecretRedactor()

    def factory():
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    subject = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="Synthetic quick review",
        summary="Synthetic",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        created_at=now,
        updated_at=now,
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
    with factory() as uow:
        uow.subjects.add(subject)
        uow.commit()
    writer = DecisionRecordService(factory, clock, ids, redactor)
    first = writer.append(
        subject_id=subject.subject_id,
        decision_type=DecisionType.NO_ACTION,
        title="Original",
        rationale="Original judgment",
        decided_at=now,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=subject.primary_instrument_id,
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        idempotency_key="baseline",
    )
    assert first.ok

    def changes(*args, **kwargs):
        with factory() as uow:
            rows = uow.decisions.list_by_subject(subject.subject_id)
        prior = max(rows, key=lambda r: (r.recorded_at, r.decision_id))
        return ResearchChangesDTO(
            subject_id=subject.subject_id,
            as_of=clock.now(),
            baseline=ResearchChangesBaselineDTO(
                decision_id=prior.decision_id,
                title=prior.title,
                rationale=prior.rationale,
                decided_at=prior.decided_at,
                recorded_at=prior.recorded_at,
                theses=[],
                plan=None,
            ),
            coverage={},
            warning_codes=[],
            total=0,
            offset=0,
            limit=100,
            has_more=False,
            items=[],
        )

    def service():
        return QuickReviewService(
            factory,
            NS(get_for_review=changes),
            NS(get=lambda _: []),
            NS(latest_accounts=lambda: ()),
            NS(list_latest=lambda **k: (), latest_for_revision=lambda _: None),
            writer,
            clock,
            redactor,
        )

    svc = service()
    card = svc.get(subject.subject_id)
    clock.advance(30)
    request = QuickReviewInput(
        review_token=card["review_token"],
        baseline_decision_id=card["baseline"]["decision_id"],
        action="maintain",
        rationale="Maintain after reviewing",
        review_due_at=None,
        idempotency_key="once",
        confirmed=True,
    )
    saved = svc.submit(subject.subject_id, request)
    assert service().submit(subject.subject_id, request) == saved
    with factory() as uow:
        rows = uow.decisions.list_by_subject(subject.subject_id)
        assert len(rows) == 2
        current = uow.decisions.get(saved["decision_id"])
        assert current.supersedes_decision_id == first.data.decision_id
        assert current.decided_at == clock.now()
    card = svc.get(subject.subject_id)
    follow = svc.submit(
        subject.subject_id,
        request.model_copy(
            update={
                "review_token": card["review_token"],
                "baseline_decision_id": saved["decision_id"],
                "idempotency_key": "follow",
                "action": "defer",
                "rationale": "Need later evidence",
                "review_due_at": now + timedelta(days=2),
            }
        ),
    )
    with factory() as uow:
        current = uow.decisions.get(follow["decision_id"])
        assert current.decision_type is DecisionType.RESEARCH_MORE
        assert current.review_due_at == now + timedelta(days=2)
        assert len(uow.decisions.list_by_subject(subject.subject_id)) == 3
    engine.dispose()
