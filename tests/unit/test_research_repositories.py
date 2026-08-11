"""Phase 1B research repository unit tests (session UoW + append-only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AssumptionStatus,
    CandidateKind,
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    InvalidationSeverity,
    InvalidationStatus,
    InvestmentRating,
    Market,
    OpenQuestionStatus,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
    WatchlistItemStatus,
)
from domain.common.errors import (
    AppendOnlyViolation,
    CandidateAlreadyResolved,
    DataContractError,
    ResearchSubjectNotFound,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    OpenQuestion,
    ResearchSubject,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm import ThesisRevisionRow
from infrastructure.persistence.repositories.thesis_revision import (
    SqlAlchemyThesisRevisionRepository,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(orm_sqlite_url: str):  # type: ignore[no-untyped-def]
    eng = create_engine(orm_sqlite_url)
    _enable_fk(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def uow_factory(engine: Engine):  # type: ignore[no-untyped-def]
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    factory.clock = clock  # type: ignore[attr-defined]
    factory.ids = ids  # type: ignore[attr-defined]
    return factory


def _make_case(ids: SequentialIdGenerator, clock: FixedClock) -> ResearchSubject:
    return ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA structural",
        summary="Long-horizon GPU demand",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id="equity:US:NVDA",
        topic_tags=("ai", "gpu"),
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


def test_case_round_trip_and_list(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.commit()

    with uow_factory() as uow:
        loaded = uow.subjects.get(subject.subject_id)
        assert loaded.title == subject.title
        assert loaded.topic_tags == ("ai", "gpu")
        listed = uow.subjects.list(topic_tag="ai")
        assert len(listed) == 1
        assert listed[0].subject_id == subject.subject_id


def test_case_not_found(uow_factory) -> None:  # type: ignore[no-untyped-def]
    with uow_factory() as uow, pytest.raises(ResearchSubjectNotFound):
        uow.subjects.get("case_missing")


def test_thesis_revision_append_and_advance(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev1_id = ids.new(EntityIdPrefix.REV)

    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="Primary",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev1_id,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev1_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="Demand is structural",
                rationale="Capex cycle",
                confidence_band=ConfidenceBand.HIGH,
                rating=InvestmentRating.BUY,
                confirmation_mode=ConfirmationMode.STRICT_REVIEW,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="Watch GM",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.commit()

    rev2_id = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        assert uow.revisions.next_revision_no(thesis_id) == 2
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev2_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=2,
                supersedes_revision_no=1,
                statement="Demand is still structural",
                rationale="Updated",
                confidence_band=ConfidenceBand.MEDIUM,
                rating=InvestmentRating.BUY,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="Watch GM",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.theses.advance_current_revision(
            thesis_id,
            new_revision_no=2,
            new_latest_revision_id=rev2_id,
        )
        uow.commit()

    with uow_factory() as uow:
        thesis = uow.theses.get(thesis_id)
        assert thesis.current_revision_no == 2
        assert thesis.latest_revision_id == rev2_id
        history = uow.revisions.list_by_thesis(thesis_id)
        assert [r.revision_no for r in history] == [1, 2]


def test_advance_revision_rejects_non_monotonic(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev_id = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="Primary",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev_id,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.commit()

    with (
        uow_factory() as uow,
        pytest.raises(DataContractError, match="current_revision_no \\+ 1"),
    ):
        uow.theses.advance_current_revision(
            thesis_id,
            new_revision_no=3,
            new_latest_revision_id=ids.new(EntityIdPrefix.REV),
        )


def test_thesis_revision_repository_has_no_update_or_delete_methods() -> None:
    methods = {name for name in dir(SqlAlchemyThesisRevisionRepository) if not name.startswith("_")}
    assert "update" not in methods
    assert "delete" not in methods
    assert "append" in methods
    assert "get" in methods


def test_thesis_revision_append_only_listener_blocks_update(engine: Engine, uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev_id = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="Primary",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev_id,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="s",
                rationale="r",
                confidence_band=ConfidenceBand.LOW,
                rating=InvestmentRating.WATCH,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.commit()

    with Session(engine) as session:
        row = session.get(ThesisRevisionRow, rev_id)
        assert row is not None
        row.statement = "mutated"
        with pytest.raises(AppendOnlyViolation, match="append-only"):
            session.flush()
        session.rollback()


def test_thesis_revision_append_only_listener_blocks_delete(engine: Engine, uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev_id = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="Primary",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev_id,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="s",
                rationale="r",
                confidence_band=ConfidenceBand.LOW,
                rating=InvestmentRating.WATCH,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.commit()

    with Session(engine) as session:
        row = session.get(ThesisRevisionRow, rev_id)
        assert row is not None
        session.delete(row)
        with pytest.raises(AppendOnlyViolation, match="append-only"):
            session.flush()
        session.rollback()


def test_assumption_and_invalidation_lifecycle(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev_id = ids.new(EntityIdPrefix.REV)
    assumption_id = ids.new(EntityIdPrefix.REV)
    inv_id = ids.new(EntityIdPrefix.REV)

    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="Primary",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev_id,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="s",
                rationale="r",
                confidence_band=ConfidenceBand.MEDIUM,
                rating=InvestmentRating.BUY,
                confirmation_mode=ConfirmationMode.STRICT_REVIEW,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.assumptions.add(
            Assumption(
                assumption_id=assumption_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                statement="Capex grows",
                basis="Guidance",
                falsifiability="Capex guide cut",
                status=AssumptionStatus.ACCEPTED,
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                proposed_by="codex",
                confirmed_by="user",
                retired_at=None,
                retired_reason=None,
            )
        )
        uow.invalidations.add(
            InvalidationCondition(
                invalidation_id=inv_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                description="GM collapse",
                observable="GM < 50%",
                severity=InvalidationSeverity.HARD,
                status=InvalidationStatus.ARMED,
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                last_checked_at=None,
                triggered_at=None,
                triggered_reason=None,
                proposed_by="codex",
                confirmed_by="user",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        active = uow.assumptions.list_active(thesis_id, 1)
        assert len(active) == 1
        armed = uow.invalidations.list_armed(thesis_id, 1)
        assert len(armed) == 1
        uow.assumptions.retire(
            assumption_id,
            retired_at=clock.now() + timedelta(seconds=1),
            retired_reason="superseded",
        )
        uow.invalidations.transition_status(
            inv_id,
            new_status=InvalidationStatus.TRIGGERED,
            triggered_at=clock.now() + timedelta(seconds=1),
            triggered_reason="GM 48%",
            last_checked_at=clock.now() + timedelta(seconds=1),
        )
        uow.commit()

    with uow_factory() as uow:
        assert uow.assumptions.list_active(thesis_id, 1) == ()
        inv = uow.invalidations.get(inv_id)
        assert inv.status is InvalidationStatus.TRIGGERED
        # HARD recovery: re-read HARD+TRIGGERED from storage
        assert inv.severity is InvalidationSeverity.HARD


def test_open_question_and_watchlist(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    q_id = ids.new(EntityIdPrefix.REV)
    item_id = ids.new(EntityIdPrefix.SNAPSHOT)

    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.questions.add(
            OpenQuestion(
                question_id=q_id,
                subject_id=subject.subject_id,
                text="What is 2027 HBM supply?",
                status=OpenQuestionStatus.OPEN,
                asked_at=clock.now(),
                answered_at=None,
                answer_summary=None,
                closed_without_answer_reason=None,
                proposed_by="user",
            )
        )
        uow.watchlist.add(
            WatchlistItem(
                item_id=item_id,
                market=Market.US,
                symbol="NVDA",
                display_name="NVIDIA",
                thesis_hint="earnings watch",
                triggers=("eps miss",),
                subject_id=None,
                status=WatchlistItemStatus.WATCHING,
                created_at=clock.now(),
                updated_at=clock.now(),
                expires_at=None,
                promoted_to_subject_id=None,
                triggered_at=None,
                triggered_reason=None,
            )
        )
        uow.commit()

    with uow_factory() as uow:
        uow.questions.answer(
            q_id,
            answered_at=clock.now() + timedelta(seconds=1),
            answer_summary="Tight through 2027",
        )
        uow.watchlist.update_status(
            item_id,
            new_status=WatchlistItemStatus.PROMOTED_TO_SUBJECT,
            triggered_at=None,
            triggered_reason=None,
            promoted_to_subject_id=subject.subject_id,
            expires_at=None,
        )
        uow.commit()

    with uow_factory() as uow:
        q = uow.questions.get(q_id)
        assert q.status is OpenQuestionStatus.ANSWERED
        item = uow.watchlist.get(item_id)
        assert item.status is WatchlistItemStatus.PROMOTED_TO_SUBJECT
        assert item.promoted_to_subject_id == subject.subject_id


def test_candidate_payload_frozen_after_status_change(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    candidate_id = ids.new(EntityIdPrefix.RUN)
    payload = '{"kind":"thesis_revision","statement":"original"}'

    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.candidates.add(
            CandidateThesisRevision(
                candidate_id=candidate_id,
                subject_id=subject.subject_id,
                thesis_id=None,
                target_revision_no=None,
                payload_json=payload,
                kind=CandidateKind.THESIS_REVISION,
                confirmation_mode=ConfirmationMode.STRICT_REVIEW,
                status=CandidateStatus.PROPOSED,
                proposed_at=clock.now(),
                expires_at=clock.now() + timedelta(days=7),
                proposed_by="codex",
                proposed_by_rationale="draft",
                reviewed_at=None,
                reviewed_by=None,
                review_note=None,
                rejection_reason=None,
                idempotency_key="idem-freeze-1",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        uow.candidates.update_status(
            candidate_id,
            new_status=CandidateStatus.CONFIRMED,
            reviewed_at=clock.now() + timedelta(seconds=1),
            reviewed_by="user",
            review_note="lgtm",
            rejection_reason=None,
        )
        uow.commit()

    with uow_factory() as uow:
        cand = uow.candidates.get(candidate_id)
        assert cand.status is CandidateStatus.CONFIRMED
        assert cand.payload_json == payload
        with pytest.raises(CandidateAlreadyResolved):
            uow.candidates.update_status(
                candidate_id,
                new_status=CandidateStatus.REJECTED,
                reviewed_at=clock.now(),
                reviewed_by="user",
                review_note=None,
                rejection_reason="nope",
            )


def test_candidate_expire_due(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    candidate_id = ids.new(EntityIdPrefix.RUN)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.candidates.add(
            CandidateThesisRevision(
                candidate_id=candidate_id,
                subject_id=subject.subject_id,
                thesis_id=None,
                target_revision_no=None,
                payload_json='{"kind":"thesis_revision"}',
                kind=CandidateKind.THESIS_REVISION,
                confirmation_mode=ConfirmationMode.NORMAL,
                status=CandidateStatus.PROPOSED,
                proposed_at=clock.now(),
                expires_at=clock.now() - timedelta(seconds=1),
                proposed_by="codex",
                proposed_by_rationale="old",
                reviewed_at=None,
                reviewed_by=None,
                review_note=None,
                rejection_reason=None,
                idempotency_key="idem-expire-1",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        expired = uow.candidates.expire_due(now=clock.now())
        assert expired == (candidate_id,)
        uow.commit()

    with uow_factory() as uow:
        cand = uow.candidates.get(candidate_id)
        assert cand.status is CandidateStatus.EXPIRED
        assert cand.payload_json == '{"kind":"thesis_revision"}'


def test_candidate_list_tie_break_proposed_at_desc_candidate_id_asc(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """Offset pagination is safe when proposed_at ties: candidate_id ASC breaks ties."""
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    tied_at = clock.now()
    # Deliberately non-monotonic insert order vs id order to prove SQL ordering.
    id_high = "run_00000000-0000-7000-8000-000000000099"
    id_low = "run_00000000-0000-7000-8000-000000000001"
    id_mid = "run_00000000-0000-7000-8000-000000000050"
    earlier = tied_at - timedelta(hours=1)

    def _cand(candidate_id: str, *, proposed_at: datetime, key: str) -> CandidateThesisRevision:
        return CandidateThesisRevision(
            candidate_id=candidate_id,
            subject_id=subject.subject_id,
            thesis_id=None,
            target_revision_no=None,
            payload_json='{"kind":"thesis_revision"}',
            kind=CandidateKind.THESIS_REVISION,
            confirmation_mode=ConfirmationMode.NORMAL,
            status=CandidateStatus.PROPOSED,
            proposed_at=proposed_at,
            expires_at=tied_at + timedelta(days=7),
            proposed_by="codex",
            proposed_by_rationale="tie-break",
            reviewed_at=None,
            reviewed_by=None,
            review_note=None,
            rejection_reason=None,
            idempotency_key=key,
        )

    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.candidates.add(_cand(id_high, proposed_at=tied_at, key="tie-high"))
        uow.candidates.add(_cand(id_low, proposed_at=tied_at, key="tie-low"))
        uow.candidates.add(_cand(id_mid, proposed_at=tied_at, key="tie-mid"))
        uow.candidates.add(
            _cand(
                "run_00000000-0000-7000-8000-000000000200",
                proposed_at=earlier,
                key="earlier",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        page1 = uow.candidates.list(subject_id=subject.subject_id, limit=2, offset=0)
        page2 = uow.candidates.list(subject_id=subject.subject_id, limit=2, offset=2)
        full = uow.candidates.list(subject_id=subject.subject_id, limit=50, offset=0)

    # proposed_at DESC then candidate_id ASC among ties.
    assert [c.candidate_id for c in full] == [
        id_low,
        id_mid,
        id_high,
        "run_00000000-0000-7000-8000-000000000200",
    ]
    assert [c.candidate_id for c in page1] == [id_low, id_mid]
    assert [c.candidate_id for c in page2] == [
        id_high,
        "run_00000000-0000-7000-8000-000000000200",
    ]
    # Pages partition full without gaps or duplicates.
    assert {c.candidate_id for c in page1} | {c.candidate_id for c in page2} == {
        c.candidate_id for c in full
    }
    assert not {c.candidate_id for c in page1} & {c.candidate_id for c in page2}


def test_list_live_primary_thesis_ids(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    t1 = ids.new(EntityIdPrefix.THESIS)
    t2 = ids.new(EntityIdPrefix.THESIS)
    rev = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=t1,
                subject_id=subject.subject_id,
                title="P1",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.STRENGTHENED,
                current_revision_no=1,
                latest_revision_id=rev,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.theses.add(
            Thesis(
                thesis_id=t2,
                subject_id=subject.subject_id,
                title="Bear",
                role=ThesisRole.BEAR,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.commit()

    with uow_factory() as uow:
        live_primary = uow.subjects.list_live_primary_thesis_ids(subject.subject_id)
        assert live_primary == (t1,)


def test_orm_models_registered() -> None:
    tables = set(Base.metadata.tables.keys())
    for name in (
        "investment_cases",
        "theses",
        "thesis_revisions",
        "assumptions",
        "invalidation_conditions",
        "open_questions",
        "watchlist_items",
        "candidate_thesis_revisions",
        "schema_versions",
        "system_audit_log",
        "industry_metric_observations",
    ):
        assert name in tables


def test_candidate_sql_scope_checks_via_uow(engine: Engine, uow_factory) -> None:  # type: ignore[no-untyped-def]
    """Candidate scope CHECKs: non-watchlist requires subject_id (domain + SQL)."""
    from sqlalchemy.exc import IntegrityError

    clock = uow_factory.clock
    ids = uow_factory.ids
    # Domain rejects before SQL for missing subject_id on thesis_revision.
    with pytest.raises(DataContractError, match="subject_id is required"):
        CandidateThesisRevision(
            candidate_id=ids.new(EntityIdPrefix.RUN),
            subject_id=None,
            thesis_id=None,
            target_revision_no=None,
            payload_json="{}",
            kind=CandidateKind.THESIS_REVISION,
            confirmation_mode=ConfirmationMode.NORMAL,
            status=CandidateStatus.PROPOSED,
            proposed_at=clock.now(),
            expires_at=clock.now() + timedelta(days=1),
            proposed_by="codex",
            proposed_by_rationale="x",
            reviewed_at=None,
            reviewed_by=None,
            review_note=None,
            rejection_reason=None,
            idempotency_key="idem-scope",
        )
    # SQL CHECK for thesis_scope: assumption without thesis_id
    with Session(engine) as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        # insert bare case for FK
        session.execute(
            text(
                "INSERT INTO investment_cases("
                "case_id, case_type, title, summary, status, topic_tags_json, "
                "created_at, updated_at, created_by, linked_case_ids_json, "
                "evidence_ids_json, report_ids_json, event_ids_json, "
                "decision_ids_json, schema_version"
                ") VALUES ("
                "'case_x', 'theme', 't', 's', 'draft', '[]', "
                ":t, :t, 'user', '[]', '[]', '[]', '[]', '[]', 1)"
            ),
            {"t": clock.now().isoformat()},
        )
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO candidate_thesis_revisions("
                    "candidate_id, case_id, thesis_id, payload_json, kind, "
                    "confirmation_mode, status, proposed_at, expires_at, "
                    "proposed_by, proposed_by_rationale, idempotency_key"
                    ") VALUES ("
                    "'run_00000000-0000-7000-8000-0000000000fe', "
                    "'case_x', NULL, '{}', 'assumption', "
                    "'normal', 'proposed', :t, :t, 'codex', 'r', 'idem-sql-scope')"
                ),
                {"t": clock.now().isoformat()},
            )
            session.commit()
        session.rollback()


def test_open_question_mark_stale_only_from_open(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    q_id = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.questions.add(
            OpenQuestion(
                question_id=q_id,
                subject_id=subject.subject_id,
                text="Q?",
                status=OpenQuestionStatus.OPEN,
                asked_at=clock.now(),
                answered_at=None,
                answer_summary=None,
                closed_without_answer_reason=None,
                proposed_by="user",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        uow.questions.mark_stale(q_id)
        uow.commit()

    with uow_factory() as uow:
        q = uow.questions.get(q_id)
        assert q.status is OpenQuestionStatus.STALE
        assert q.answered_at is None
        assert q.closed_without_answer_reason is None
        with pytest.raises(DataContractError, match="OPEN→STALE"):
            uow.questions.mark_stale(q_id)


def test_open_question_answer_and_close_only_from_open(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    q1 = ids.new(EntityIdPrefix.REV)
    q2 = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        for qid in (q1, q2):
            uow.questions.add(
                OpenQuestion(
                    question_id=qid,
                    subject_id=subject.subject_id,
                    text="Q?",
                    status=OpenQuestionStatus.OPEN,
                    asked_at=clock.now(),
                    answered_at=None,
                    answer_summary=None,
                    closed_without_answer_reason=None,
                    proposed_by="user",
                )
            )
        uow.commit()

    with uow_factory() as uow:
        uow.questions.answer(
            q1,
            answered_at=clock.now() + timedelta(seconds=1),
            answer_summary="A",
        )
        uow.commit()

    with uow_factory() as uow, pytest.raises(DataContractError, match="only allowed from OPEN"):
        uow.questions.answer(
            q1,
            answered_at=clock.now() + timedelta(seconds=2),
            answer_summary="again",
        )

    with uow_factory() as uow:
        uow.questions.close_without_answer(q2, closed_reason="n/a")
        uow.commit()

    with uow_factory() as uow, pytest.raises(DataContractError, match="only allowed from OPEN"):
        uow.questions.close_without_answer(q2, closed_reason="again")


def test_revision_append_rejects_gap(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev1 = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="P",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev1,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev1,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="s",
                rationale="r",
                confidence_band=ConfidenceBand.LOW,
                rating=InvestmentRating.WATCH,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.commit()

    with uow_factory() as uow, pytest.raises(DataContractError, match="no gaps"):
        uow.revisions.append(
            ThesisRevision(
                revision_id=ids.new(EntityIdPrefix.REV),
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=3,
                supersedes_revision_no=1,
                statement="gap",
                rationale="r",
                confidence_band=ConfidenceBand.LOW,
                rating=InvestmentRating.WATCH,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )


def test_advance_revision_requires_existing_revision_for_thesis(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev1 = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="P",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev1,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev1,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="s",
                rationale="r",
                confidence_band=ConfidenceBand.LOW,
                rating=InvestmentRating.WATCH,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.commit()

    missing = ids.new(EntityIdPrefix.REV)
    with (
        uow_factory() as uow,
        pytest.raises(DataContractError, match="existing revision_id"),
    ):
        uow.theses.advance_current_revision(
            thesis_id,
            new_revision_no=2,
            new_latest_revision_id=missing,
        )


def test_case_update_forces_updated_at_from_clock(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.commit()

    later = NOW + timedelta(hours=2)
    clock.set(later)

    with uow_factory() as uow:
        loaded = uow.subjects.get(subject.subject_id)
        stale_updated = loaded.updated_at
        updated = ResearchSubject(
            subject_id=loaded.subject_id,
            subject_type=loaded.subject_type,
            title="Updated title",
            summary=loaded.summary,
            status=loaded.status,
            primary_instrument_id=loaded.primary_instrument_id,
            topic_tags=loaded.topic_tags,
            created_at=loaded.created_at,
            updated_at=stale_updated,  # deliberately stale
            created_by=loaded.created_by,
            archived_at=None,
            archived_reason=None,
            linked_subject_ids=loaded.linked_subject_ids,
            evidence_ids=loaded.evidence_ids,
            report_ids=loaded.report_ids,
            event_ids=loaded.event_ids,
            decision_ids=loaded.decision_ids,
            schema_version=loaded.schema_version,
        )
        uow.subjects.update(updated)
        uow.commit()

    with uow_factory() as uow:
        again = uow.subjects.get(subject.subject_id)
        assert again.title == "Updated title"
        assert again.updated_at == later


def test_topic_tag_list_pages_after_full_filter(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    # Create more subjects than a 4x over-fetch of limit=1 would cover incorrectly
    # if it applied SQL limit first.
    subjects: list[ResearchSubject] = []
    for i in range(6):
        c = ResearchSubject(
            subject_id=ids.new(EntityIdPrefix.SUBJECT),
            subject_type=ResearchSubjectType.THEME,
            title=f"case-{i}",
            summary="s",
            status=ResearchSubjectStatus.ACTIVE,
            primary_instrument_id=None,
            topic_tags=("ai",) if i % 2 == 0 else ("other",),
            created_at=clock.now() + timedelta(seconds=i),
            updated_at=clock.now() + timedelta(seconds=i),
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
        subjects.append(c)

    with uow_factory() as uow:
        for c in subjects:
            uow.subjects.add(c)
        uow.commit()

    with uow_factory() as uow:
        page0 = uow.subjects.list(topic_tag="ai", limit=1, offset=0)
        page1 = uow.subjects.list(topic_tag="ai", limit=1, offset=1)
        page2 = uow.subjects.list(topic_tag="ai", limit=1, offset=2)
        all_ai = uow.subjects.list(topic_tag="ai", limit=50, offset=0)
        assert len(all_ai) == 3
        assert len(page0) == 1
        assert len(page1) == 1
        assert len(page2) == 1
        assert {page0[0].subject_id, page1[0].subject_id, page2[0].subject_id} == {
            c.subject_id for c in all_ai
        }


def test_candidate_payload_immutable_after_leaving_proposed(engine: Engine, uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    candidate_id = ids.new(EntityIdPrefix.RUN)
    payload = '{"kind":"thesis_revision","statement":"original"}'
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.candidates.add(
            CandidateThesisRevision(
                candidate_id=candidate_id,
                subject_id=subject.subject_id,
                thesis_id=None,
                target_revision_no=None,
                payload_json=payload,
                kind=CandidateKind.THESIS_REVISION,
                confirmation_mode=ConfirmationMode.NORMAL,
                status=CandidateStatus.PROPOSED,
                proposed_at=clock.now(),
                expires_at=clock.now() + timedelta(days=1),
                proposed_by="codex",
                proposed_by_rationale="draft",
                reviewed_at=None,
                reviewed_by=None,
                review_note=None,
                rejection_reason=None,
                idempotency_key="idem-payload-guard",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        uow.candidates.update_status(
            candidate_id,
            new_status=CandidateStatus.CONFIRMED,
            reviewed_at=clock.now() + timedelta(seconds=1),
            reviewed_by="user",
            review_note="ok",
            rejection_reason=None,
        )
        uow.commit()

    from infrastructure.persistence.orm import CandidateThesisRevisionRow

    with Session(engine) as session:
        row = session.get(CandidateThesisRevisionRow, candidate_id)
        assert row is not None
        row.payload_json = '{"mutated":true}'
        with pytest.raises(AppendOnlyViolation, match="payload_json"):
            session.flush()
        session.rollback()


def test_watchlist_clears_residuals_when_leaving_triggered(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    item_id = ids.new(EntityIdPrefix.SNAPSHOT)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.watchlist.add(
            WatchlistItem(
                item_id=item_id,
                market=Market.US,
                symbol="NVDA",
                display_name="NVIDIA",
                thesis_hint="hint",
                triggers=("t",),
                subject_id=None,
                status=WatchlistItemStatus.WATCHING,
                created_at=clock.now(),
                updated_at=clock.now(),
                expires_at=None,
                promoted_to_subject_id=None,
                triggered_at=None,
                triggered_reason=None,
            )
        )
        uow.commit()

    with uow_factory() as uow:
        uow.watchlist.update_status(
            item_id,
            new_status=WatchlistItemStatus.TRIGGERED,
            triggered_at=clock.now() + timedelta(seconds=1),
            triggered_reason="hit",
            promoted_to_subject_id=None,
            expires_at=None,
        )
        uow.commit()

    with uow_factory() as uow:
        uow.watchlist.update_status(
            item_id,
            new_status=WatchlistItemStatus.ARCHIVED,
            triggered_at=clock.now() + timedelta(seconds=2),  # residual attempt
            triggered_reason="should clear",
            promoted_to_subject_id=subject.subject_id,  # residual attempt
            expires_at=None,
        )
        uow.commit()

    with uow_factory() as uow:
        item = uow.watchlist.get(item_id)
        assert item.status is WatchlistItemStatus.ARCHIVED
        assert item.triggered_at is None
        assert item.triggered_reason is None
        assert item.promoted_to_subject_id is None


def test_invalidation_clears_residuals_when_leaving_triggered(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    subject = _make_case(ids, clock)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    rev_id = ids.new(EntityIdPrefix.REV)
    inv_id = ids.new(EntityIdPrefix.REV)
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(
            Thesis(
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                title="P",
                role=ThesisRole.PRIMARY,
                status=ThesisStatus.ACTIVE,
                current_revision_no=1,
                latest_revision_id=rev_id,
                parent_thesis_id=None,
                rival_thesis_ids=(),
                created_at=clock.now(),
                updated_at=clock.now(),
                archived_at=None,
            )
        )
        uow.revisions.append(
            ThesisRevision(
                revision_id=rev_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="s",
                rationale="r",
                confidence_band=ConfidenceBand.MEDIUM,
                rating=InvestmentRating.BUY,
                confirmation_mode=ConfirmationMode.NORMAL,
                proposed_by="codex",
                confirmed_by="user",
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="n",
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
        )
        uow.invalidations.add(
            InvalidationCondition(
                invalidation_id=inv_id,
                thesis_id=thesis_id,
                subject_id=subject.subject_id,
                revision_no=1,
                description="d",
                observable="o",
                severity=InvalidationSeverity.SOFT,
                status=InvalidationStatus.ARMED,
                proposed_at=clock.now(),
                confirmed_at=clock.now(),
                last_checked_at=None,
                triggered_at=None,
                triggered_reason=None,
                proposed_by="codex",
                confirmed_by="user",
            )
        )
        uow.commit()

    with uow_factory() as uow:
        uow.invalidations.transition_status(
            inv_id,
            new_status=InvalidationStatus.TRIGGERED,
            triggered_at=clock.now() + timedelta(seconds=1),
            triggered_reason="hit",
            last_checked_at=clock.now() + timedelta(seconds=1),
        )
        uow.commit()

    with uow_factory() as uow:
        uow.invalidations.transition_status(
            inv_id,
            new_status=InvalidationStatus.REARMED,
            triggered_at=clock.now() + timedelta(seconds=2),
            triggered_reason="should clear",
            last_checked_at=clock.now() + timedelta(seconds=2),
        )
        uow.commit()

    with uow_factory() as uow:
        inv = uow.invalidations.get(inv_id)
        assert inv.status is InvalidationStatus.REARMED
        assert inv.triggered_at is None
        assert inv.triggered_reason is None
