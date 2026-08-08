"""ThesisRevisionService / candidate lifecycle unit tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research import (
    AssumptionPayload,
    InvalidationPayload,
    SubjectUpdateCandidatePayload,
    ThesisRevisionCandidatePayload,
    WatchlistCandidatePayload,
)
from application.services.research_subject_service import ResearchSubjectService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.actor import ActorContext
from domain.common.enums import (
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    InvalidationSeverity,
    InvestmentRating,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
    WatchlistItemStatus,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("field", ["subject_type", "primary_instrument_id"])
def test_case_update_candidate_cannot_change_subject_identity(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        SubjectUpdateCandidatePayload.model_validate(
            {
                "action": "update",
                "title": "NVDA company research",
                field: "company" if field == "subject_type" else "equity:US:TSLA",
            }
        )


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def harness(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "cand.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    subjects = ResearchSubjectService(factory, clock, ids, redactor)
    thesis = ThesisRevisionService(factory, clock, ids, redactor)
    yield subjects, thesis, factory, clock, ids, eng
    eng.dispose()


def _create_subject(
    subjects: ResearchSubjectService,
    thesis: ThesisRevisionService,
    key: str = "case-1",
    *,
    activate: bool = True,
) -> str:
    env = subjects.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA",
        summary="GPU demand",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=("ai",),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key=key,
    )
    assert env.ok and env.data is not None
    subject_id = env.data.subject_id
    if activate:
        proposed = thesis.propose_state_update(
            subject_id=subject_id,
            payload=SubjectUpdateCandidatePayload(
                action="update",
                new_status=ResearchSubjectStatus.ACTIVE,
            ),
            confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            proposed_by="codex",
            proposed_by_rationale="Activate case for live judgment tests",
            idempotency_key=f"{key}-activate",
        )
        assert proposed.ok and proposed.data is not None
        confirmed = thesis.confirm_candidate(
            proposed.data.candidate_id,
            reviewed_by="user",
        )
        assert confirmed.ok
    return subject_id


@pytest.mark.parametrize(
    "legacy_status",
    ["strengthened", "weakened", "invalidated"],
)
def test_subject_candidate_schema_rejects_thesis_only_statuses(
    legacy_status: str,
) -> None:
    with pytest.raises(ValidationError, match="new_status"):
        SubjectUpdateCandidatePayload.model_validate(
            {"action": "update", "new_status": legacy_status}
        )


def _revision_payload() -> ThesisRevisionCandidatePayload:
    return ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title="Primary demand thesis",
        statement="Demand is structural",
        rationale="Capex cycle continues",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        invalidation_check_note="Watch gross margin",
        assumptions=(
            AssumptionPayload(
                statement="AI spend continues",
                basis="Hyperscaler guidance",
                falsifiability="Capex cuts 2 quarters",
            ),
        ),
        invalidations=(
            InvalidationPayload(
                description="GM collapse",
                observable="Gross margin < 50%",
                severity=InvalidationSeverity.HARD,
            ),
        ),
        thesis_role=ThesisRole.PRIMARY,
    )


def test_propose_idempotent_same_payload_warning(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    payload = _revision_payload()
    first = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="initial",
        idempotency_key="prop-1",
    )
    second = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="initial",
        idempotency_key="prop-1",
    )
    assert first.ok and second.ok
    assert first.data is not None and second.data is not None
    assert first.data.candidate_id == second.data.candidate_id
    assert any(w.code == "DUPLICATE_IDEMPOTENCY_KEY" for w in second.warnings)


def test_propose_idempotent_different_payload_conflict(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    payload = _revision_payload()
    first = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="a",
        idempotency_key="prop-diff",
    )
    assert first.ok
    other = ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title="Different",
        statement="Different statement long enough",
        rationale="Different rationale long enough",
        confidence_band=ConfidenceBand.LOW,
        rating=InvestmentRating.HOLD,
        invalidation_check_note="Other note",
        thesis_role=ThesisRole.PRIMARY,
    )
    second = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=other,
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="b",
        idempotency_key="prop-diff",
    )
    assert second.ok is False
    assert second.errors[0].code == "DUPLICATE_IDEMPOTENCY_KEY"


def test_codex_cannot_confirm(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="no-self",
    )
    assert proposed.data is not None
    env = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="codex",
        review_note="self",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_confirm_writes_revision_assumptions_invalidations(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, factory, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="confirm-ok",
    )
    assert proposed.data is not None
    confirmed = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
        review_note="looks good",
    )
    assert confirmed.ok is True
    assert confirmed.data is not None
    assert confirmed.data.affected_entity_type == "thesis_revision"
    assert confirmed.data.research_state is not None
    assert len(confirmed.data.research_state.theses) == 1
    assert confirmed.data.research_state.theses[0].current_revision_no == 1
    assert len(confirmed.data.research_state.assumptions) == 1
    assert len(confirmed.data.research_state.invalidations) == 1
    inv = confirmed.data.research_state.invalidations[0]
    assert inv.severity in {"hard", InvalidationSeverity.HARD}
    assert inv.status in {"armed", "ARMED"} or str(inv.status) == "armed"

    with factory() as uow:
        theses = uow.theses.list_by_subject(subject_id)
        assert len(theses) == 1
        revs = uow.revisions.list_by_thesis(theses[0].thesis_id)
        assert len(revs) == 1
        assert theses[0].current_revision_no == revs[0].revision_no


def test_subject_supports_parallel_sub_theses_but_one_live_primary(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, factory, *_ = harness
    subject_id = _create_subject(subjects, thesis, key="parallel-theses")
    primary_proposal = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        proposed_by="codex",
        proposed_by_rationale="Create aggregate judgment",
        idempotency_key="parallel-primary",
    )
    assert primary_proposal.data is not None
    primary_result = thesis.confirm_candidate(
        primary_proposal.data.candidate_id, reviewed_by="user"
    )
    assert primary_result.ok and primary_result.data is not None
    assert primary_result.data.research_state is not None
    primary_id = primary_result.data.research_state.theses[0].thesis_id

    sub_ids: list[str] = []
    for index in (1, 2):
        sub_payload = _revision_payload().model_copy(
            update={
                "title": f"Parallel driver {index}",
                "thesis_role": ThesisRole.SUB,
                "parent_thesis_id": primary_id,
                "rival_thesis_ids": (),
            }
        )
        proposed = thesis.propose_revision(
            subject_id=subject_id,
            thesis_id=None,
            payload=sub_payload,
            proposed_by="codex",
            proposed_by_rationale="Create sibling driver",
            idempotency_key=f"parallel-sub-{index}",
        )
        assert proposed.data is not None
        confirmed = thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="user")
        assert confirmed.ok and confirmed.data is not None
        assert confirmed.data.research_state is not None
        sub_ids.append(
            next(
                item.thesis_id
                for item in confirmed.data.research_state.theses
                if item.title == f"Parallel driver {index}"
            )
        )

    relation_update = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=sub_ids[0],
        payload=_revision_payload().model_copy(
            update={
                "title": "Parallel driver 1 refined",
                "thesis_role": ThesisRole.SUB,
                "parent_thesis_id": primary_id,
                "rival_thesis_ids": (sub_ids[1],),
            }
        ),
        proposed_by="codex",
        proposed_by_rationale="Maintain explicit sibling relationship",
        idempotency_key="parallel-sub-update",
    )
    assert relation_update.data is not None
    assert thesis.confirm_candidate(relation_update.data.candidate_id, reviewed_by="user").ok

    parent_retirement = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=primary_id,
        payload=_revision_payload().model_copy(
            update={"title": "Retire aggregate", "thesis_status": ThesisStatus.ARCHIVED}
        ),
        proposed_by="codex",
        proposed_by_rationale="Try retiring the parent before its live children",
        idempotency_key="parallel-parent-retirement",
    )
    assert not parent_retirement.ok
    assert parent_retirement.errors[0].code == "RESEARCH_STATE_CONFLICT"

    parent_role_change = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=primary_id,
        payload=_revision_payload().model_copy(
            update={"title": "Alternative", "thesis_role": ThesisRole.COMPETITOR}
        ),
        proposed_by="codex",
        proposed_by_rationale="Try removing the parent role",
        idempotency_key="parallel-parent-role-change",
    )
    assert not parent_role_change.ok
    assert parent_role_change.errors[0].code == "RESEARCH_STATE_CONFLICT"

    role_conflict = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=sub_ids[0],
        payload=_revision_payload().model_copy(
            update={
                "title": "Improper second primary",
                "thesis_role": ThesisRole.PRIMARY,
                "parent_thesis_id": None,
                "rival_thesis_ids": (),
            }
        ),
        proposed_by="codex",
        proposed_by_rationale="Try promoting a live sibling",
        idempotency_key="parallel-role-conflict",
    )
    assert role_conflict.data is not None
    role_rejected = thesis.confirm_candidate(
        role_conflict.data.candidate_id, reviewed_by="user"
    )
    assert not role_rejected.ok
    assert role_rejected.errors[0].code == "RESEARCH_STATE_CONFLICT"

    duplicate_primary = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload().model_copy(
            update={
                "title": "Conflicting aggregate judgment",
                "thesis_status": ThesisStatus.STRENGTHENED,
            }
        ),
        proposed_by="codex",
        proposed_by_rationale="Try another live primary",
        idempotency_key="parallel-primary-conflict",
    )
    assert duplicate_primary.data is not None
    rejected = thesis.confirm_candidate(
        duplicate_primary.data.candidate_id, reviewed_by="user"
    )

    assert not rejected.ok
    assert rejected.errors[0].code == "RESEARCH_STATE_CONFLICT"
    with factory() as uow:
        all_theses = uow.theses.list_by_subject(subject_id)
        assert [item.role for item in all_theses].count(ThesisRole.PRIMARY) == 1
        assert [item.parent_thesis_id for item in all_theses if item.role is ThesisRole.SUB] == [
            primary_id,
            primary_id,
        ]
        refined = uow.theses.get(sub_ids[0])
        assert refined.title == "Parallel driver 1 refined"
        assert refined.rival_thesis_ids == (sub_ids[1],)


def test_sub_parent_must_belong_to_same_subject_at_proposal(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    first_subject = _create_subject(subjects, thesis, key="parent-owner")
    primary = thesis.propose_revision(
        subject_id=first_subject,
        thesis_id=None,
        payload=_revision_payload(),
        proposed_by="codex",
        proposed_by_rationale="Create foreign parent",
        idempotency_key="foreign-primary",
    )
    assert primary.data is not None
    confirmed = thesis.confirm_candidate(primary.data.candidate_id, reviewed_by="user")
    assert confirmed.data is not None and confirmed.data.research_state is not None
    parent_id = confirmed.data.research_state.theses[0].thesis_id
    second_subject = _create_subject(subjects, thesis, key="child-owner")

    proposed = thesis.propose_revision(
        subject_id=second_subject,
        thesis_id=None,
        payload=_revision_payload().model_copy(
            update={
                "title": "Foreign child",
                "thesis_role": ThesisRole.SUB,
                "parent_thesis_id": parent_id,
            }
        ),
        proposed_by="codex",
        proposed_by_rationale="Invalid cross-subject relationship",
        idempotency_key="foreign-child",
    )

    assert not proposed.ok
    assert proposed.errors[0].code == "INPUT_VALIDATION_ERROR"


def test_live_sub_requires_live_primary_at_proposal(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis, key="inactive-parent")
    primary = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload().model_copy(update={"thesis_status": ThesisStatus.DRAFT}),
        proposed_by="codex",
        proposed_by_rationale="Create draft aggregate judgment",
        idempotency_key="inactive-parent-primary",
    )
    assert primary.data is not None
    confirmed = thesis.confirm_candidate(primary.data.candidate_id, reviewed_by="user")
    assert confirmed.data is not None and confirmed.data.research_state is not None
    parent_id = confirmed.data.research_state.theses[0].thesis_id

    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload().model_copy(
            update={
                "title": "Premature live driver",
                "thesis_role": ThesisRole.SUB,
                "parent_thesis_id": parent_id,
            }
        ),
        proposed_by="codex",
        proposed_by_rationale="Try live child against draft parent",
        idempotency_key="inactive-parent-sub",
    )

    assert not proposed.ok
    assert proposed.errors[0].code == "RESEARCH_STATE_CONFLICT"


def test_confirm_live_thesis_rejected_for_draft_case(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, factory, *_ = harness
    subject_id = _create_subject(subjects, thesis, key="draft-live-thesis", activate=False)
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Attempt live judgment before case activation",
        idempotency_key="draft-live-thesis-candidate",
    )
    assert proposed.ok and proposed.data is not None

    confirmed = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
    )

    assert not confirmed.ok
    assert confirmed.errors[0].code == "RESEARCH_STATE_CONFLICT"
    assert confirmed.errors[0].retryable is False
    assert confirmed.errors[0].details == {
        "subject_id": subject_id,
        "subject_status": "draft",
        "attempted_child_status": "active",
    }
    with factory() as uow:
        assert uow.theses.list_by_subject(subject_id) == ()


def test_chat_authorized_confirm_persists_user_decision_and_relay_provenance(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_, eng = harness
    subject_id = _create_subject(subjects, thesis, key="case-chat-auth")
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="chat-auth-confirm",
    )
    assert proposed.data is not None
    authorization = "我确认采用这个候选"

    confirmed = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
        review_note="explicit chat authorization",
        actor_context=ActorContext.codex_chat_authorized(
            request_id="req_chat_confirm",
            authorization_note=authorization,
        ),
    )

    assert confirmed.ok is True
    with Session(eng) as session:
        payload_json = session.execute(
            text(
                "SELECT payload_json FROM system_audit_log "
                "WHERE event_type = 'phase1b.candidate.confirmed' "
                "AND payload_json LIKE '%user_instruction%'"
            )
        ).scalar_one()
    payload = json.loads(payload_json)
    assert payload["reviewed_by"] == "user"
    assert payload["actor_type"] == "user"
    assert payload["actor_assurance"] == "caller_asserted"
    assert payload["submitted_via"] == "codex_chat"
    assert payload["user_instruction"] == authorization


def test_confirm_advances_revision_no(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    p1 = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p1",
        idempotency_key="rev-1",
    )
    assert p1.data is not None
    c1 = thesis.confirm_candidate(p1.data.candidate_id, reviewed_by="user")
    assert c1.ok and c1.data is not None
    thesis_id = c1.data.research_state.theses[0].thesis_id  # type: ignore[union-attr]

    p2 = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=thesis_id,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p2",
        idempotency_key="rev-2",
    )
    assert p2.data is not None
    c2 = thesis.confirm_candidate(p2.data.candidate_id, reviewed_by="user")
    assert c2.ok and c2.data is not None
    assert c2.data.research_state is not None
    assert c2.data.research_state.theses[0].current_revision_no == 2


def test_reject_does_not_write_formal_rows(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, factory, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="rej-1",
    )
    assert proposed.data is not None
    rejected = thesis.reject_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
        rejection_reason="not ready",
    )
    assert rejected.ok is True
    assert rejected.data is not None
    assert rejected.data.status in {CandidateStatus.REJECTED, "rejected"}

    with factory() as uow:
        assert uow.theses.list_by_subject(subject_id) == ()
        assert uow.candidates.get(proposed.data.candidate_id).payload_json


def test_withdraw_only_by_proposer_or_user(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="wd-1",
    )
    assert proposed.data is not None
    # other agent cannot withdraw
    bad = thesis.withdraw_candidate(
        proposed.data.candidate_id,
        reviewed_by="other_agent",
        review_note="nope",
    )
    assert bad.ok is False
    assert bad.errors[0].code == "UNAUTHORIZED_REVIEWER"

    ok = thesis.withdraw_candidate(
        proposed.data.candidate_id,
        reviewed_by="codex",
        review_note="changed mind",
    )
    assert ok.ok is True
    assert ok.data is not None
    assert ok.data.status in {CandidateStatus.WITHDRAWN, "withdrawn"}


def test_codex_cannot_withdraw_strict_review(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="wd-strict",
    )
    assert proposed.data is not None
    env = thesis.withdraw_candidate(
        proposed.data.candidate_id,
        reviewed_by="codex",
        review_note="try",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_live_primary_unique(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, *_ = harness
    subject_id = _create_subject(subjects, thesis)
    p1 = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p1",
        idempotency_key="pri-1",
    )
    assert p1.data is not None
    assert thesis.confirm_candidate(p1.data.candidate_id, reviewed_by="user").ok

    p2 = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p2",
        idempotency_key="pri-2",
    )
    assert p2.data is not None
    c2 = thesis.confirm_candidate(p2.data.candidate_id, reviewed_by="user")
    assert c2.ok is False
    assert c2.errors[0].code == "RESEARCH_STATE_CONFLICT"


def test_theme_subject_instrument_selection_candidate_lifecycle(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, factory, *_ = harness
    created = subjects.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="A股创新药 ETF 选择",
        summary="比较候选 ETF 后确定最终执行载体",
        primary_instrument_id=None,
        topic_tags=("创新药", "ETF"),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="theme-selection",
    )
    assert created.ok and created.data is not None
    subject_id = created.data.subject_id

    proposed = thesis.propose_state_update(
        subject_id=subject_id,
        payload=WatchlistCandidatePayload(
            action="create",
            instrument_id="etf:A_SHARE:159992",
            display_name="创新药 ETF",
            thesis_hint="比较指数覆盖、流动性和跟踪质量",
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="建立候选池",
        idempotency_key="selection-add-159992",
    )
    assert proposed.ok and proposed.data is not None
    assert thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="user").ok

    with factory() as uow:
        item = uow.watchlist.list(subject_id=subject_id, limit=10)[0]
        assert item.instrument_id == "etf:A_SHARE:159992"
        assert item.market.value == "A_SHARE"
        assert item.symbol == "159992"

    selected = thesis.propose_state_update(
        subject_id=subject_id,
        payload=WatchlistCandidatePayload(
            action="update_status",
            item_id=item.item_id,
            new_status=WatchlistItemStatus.SELECTED,
            selection_reason="流动性与指数覆盖更符合执行要求",
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="记录最终选择",
        idempotency_key="selection-confirm-159992",
    )
    assert selected.ok and selected.data is not None
    assert thesis.confirm_candidate(selected.data.candidate_id, reviewed_by="user").ok

    with factory() as uow:
        item = uow.watchlist.get(item.item_id)
        assert item.status is WatchlistItemStatus.SELECTED
        assert item.selection_reason == "流动性与指数覆盖更符合执行要求"


def test_subject_rejects_second_selected_instrument(harness) -> None:  # type: ignore[no-untyped-def]
    subjects, thesis, factory, *_ = harness
    created = subjects.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="ETF selection",
        summary="Compare two execution vehicles",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="theme-two",
    )
    assert created.data is not None
    subject_id = created.data.subject_id
    item_ids: list[str] = []
    for symbol in ("159992", "516060"):
        candidate = thesis.propose_state_update(
            subject_id=subject_id,
            payload=WatchlistCandidatePayload(
                action="create",
                instrument_id=f"etf:A_SHARE:{symbol}",
                display_name=symbol,
                thesis_hint="candidate",
            ),
            proposed_by="codex",
            proposed_by_rationale="candidate",
            idempotency_key=f"add-{symbol}",
        )
        assert candidate.data is not None
        assert thesis.confirm_candidate(candidate.data.candidate_id, reviewed_by="user").ok
    with factory() as uow:
        item_ids = [item.item_id for item in uow.watchlist.list(subject_id=subject_id)]

    for index, item_id in enumerate(item_ids):
        candidate = thesis.propose_state_update(
            subject_id=subject_id,
            payload=WatchlistCandidatePayload(
                action="update_status",
                item_id=item_id,
                new_status=WatchlistItemStatus.SELECTED,
                selection_reason=f"choice {index}",
            ),
            proposed_by="codex",
            proposed_by_rationale="select",
            idempotency_key=f"select-{index}",
        )
        assert candidate.data is not None
        confirmed = thesis.confirm_candidate(candidate.data.candidate_id, reviewed_by="user")
        if index == 0:
            assert confirmed.ok
        else:
            assert not confirmed.ok
            assert confirmed.errors[0].code == "RESEARCH_STATE_CONFLICT"
