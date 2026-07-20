"""Phase 1B research domain model invariants."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from domain.common.enums import (
    AssumptionStatus,
    CandidateKind,
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    InvalidationSeverity,
    InvalidationStatus,
    InvestmentCaseStatus,
    InvestmentCaseType,
    InvestmentRating,
    Market,
    OpenQuestionStatus,
    ThesisRole,
    ThesisStatus,
    WatchlistItemStatus,
)
from domain.common.errors import DataContractError
from domain.research.models import (
    FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES,
    FROZEN_RESEARCH_MODEL_NAMES,
    RESEARCH_SCHEMA_VERSION,
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    InvestmentCase,
    OpenQuestion,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


def _case(**overrides: object) -> InvestmentCase:
    base: dict[str, object] = {
        "case_id": "case_00000000-0000-7000-8000-000000000001",
        "case_type": InvestmentCaseType.COMPANY,
        "title": "NVDA long",
        "summary": "Structural GPU demand",
        "status": InvestmentCaseStatus.DRAFT,
        "primary_instrument_id": "equity:US:NVDA",
        "topic_tags": ("ai", "semiconductors"),
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": "user",
        "archived_at": None,
        "archived_reason": None,
        "linked_case_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "event_ids": (),
        "decision_ids": (),
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return InvestmentCase(**base)  # type: ignore[arg-type]


def _thesis(**overrides: object) -> Thesis:
    base: dict[str, object] = {
        "thesis_id": "thesis_00000000-0000-7000-8000-000000000001",
        "case_id": "case_00000000-0000-7000-8000-000000000001",
        "title": "Primary demand",
        "role": ThesisRole.PRIMARY,
        "status": ThesisStatus.ACTIVE,
        "current_revision_no": 1,
        "latest_revision_id": "rev_00000000-0000-7000-8000-000000000001",
        "parent_thesis_id": None,
        "rival_thesis_ids": (),
        "created_at": NOW,
        "updated_at": NOW,
        "archived_at": None,
    }
    base.update(overrides)
    return Thesis(**base)  # type: ignore[arg-type]


def _revision(**overrides: object) -> ThesisRevision:
    base: dict[str, object] = {
        "revision_id": "rev_00000000-0000-7000-8000-000000000001",
        "thesis_id": "thesis_00000000-0000-7000-8000-000000000001",
        "case_id": "case_00000000-0000-7000-8000-000000000001",
        "revision_no": 1,
        "supersedes_revision_no": None,
        "statement": "GPU demand remains structural",
        "rationale": "Data center capex",
        "confidence_band": ConfidenceBand.MEDIUM,
        "rating": InvestmentRating.BUY,
        "confirmation_mode": ConfirmationMode.STRICT_REVIEW,
        "proposed_by": "codex",
        "confirmed_by": "user",
        "proposed_at": NOW,
        "confirmed_at": LATER,
        "observation_window_start": date(2026, 1, 1),
        "observation_window_end": date(2026, 12, 31),
        "invalidation_check_note": "Watch gross margin",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return ThesisRevision(**base)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> CandidateThesisRevision:
    base: dict[str, object] = {
        "candidate_id": "run_00000000-0000-7000-8000-000000000001",
        "case_id": "case_00000000-0000-7000-8000-000000000001",
        "thesis_id": None,
        "target_revision_no": None,
        "payload_json": '{"kind":"thesis_revision"}',
        "kind": CandidateKind.THESIS_REVISION,
        "confirmation_mode": ConfirmationMode.STRICT_REVIEW,
        "status": CandidateStatus.PROPOSED,
        "proposed_at": NOW,
        "expires_at": NOW + timedelta(days=7),
        "proposed_by": "codex",
        "proposed_by_rationale": "User asked to draft",
        "reviewed_at": None,
        "reviewed_by": None,
        "review_note": None,
        "rejection_reason": None,
        "idempotency_key": "idem-1",
    }
    base.update(overrides)
    return CandidateThesisRevision(**base)  # type: ignore[arg-type]


def test_frozen_research_registry_keeps_original_twelve_names() -> None:
    assert len(FROZEN_RESEARCH_MODEL_NAMES) == 12
    assert FROZEN_RESEARCH_MODEL_NAMES[0] == "InvestmentCase"
    assert "Evidence" in FROZEN_RESEARCH_MODEL_NAMES
    assert "WatchlistItem" in FROZEN_RESEARCH_MODEL_NAMES
    assert FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES == (
        "OpenQuestion",
        "CandidateThesisRevision",
    )


def test_investment_case_archived_fields_sync() -> None:
    with pytest.raises(DataContractError, match="ARCHIVED case requires"):
        _case(status=InvestmentCaseStatus.ARCHIVED)
    with pytest.raises(DataContractError, match="non-ARCHIVED"):
        _case(archived_at=NOW, archived_reason="done")
    archived = _case(
        status=InvestmentCaseStatus.ARCHIVED,
        archived_at=LATER,
        archived_reason="no longer tracked",
        updated_at=LATER,
    )
    assert archived.archived_reason == "no longer tracked"


def test_investment_case_company_requires_instrument() -> None:
    with pytest.raises(DataContractError, match="primary_instrument_id"):
        _case(primary_instrument_id=None)


def test_investment_case_rejects_naive_datetime() -> None:
    with pytest.raises(DataContractError, match="timezone-aware"):
        _case(created_at=datetime(2026, 7, 16, 12, 0, 0))


def test_thesis_role_parent_sync() -> None:
    with pytest.raises(DataContractError, match="SUB thesis requires"):
        _thesis(role=ThesisRole.SUB, parent_thesis_id=None)
    with pytest.raises(DataContractError, match="non-SUB"):
        _thesis(parent_thesis_id="thesis_other")


def test_thesis_archived_fields_sync() -> None:
    with pytest.raises(DataContractError, match="ARCHIVED thesis requires"):
        _thesis(status=ThesisStatus.ARCHIVED, archived_at=None)
    with pytest.raises(DataContractError, match="non-ARCHIVED thesis"):
        _thesis(status=ThesisStatus.ACTIVE, archived_at=LATER)


def test_thesis_revision_no_1_supersedes_none() -> None:
    with pytest.raises(DataContractError, match="supersedes_revision_no=None"):
        _revision(supersedes_revision_no=0)
    with pytest.raises(DataContractError, match="requires supersedes_revision_no"):
        _revision(revision_no=2, supersedes_revision_no=None)
    with pytest.raises(DataContractError, match="must be < revision_no"):
        _revision(revision_no=2, supersedes_revision_no=2)
    ok = _revision(revision_no=2, supersedes_revision_no=1)
    assert ok.supersedes_revision_no == 1


def test_hard_invalidation_allows_triggered_recovery() -> None:
    """HARD recovery semantics: domain may hydrate HARD + TRIGGERED from storage."""
    recovered = InvalidationCondition(
        invalidation_id="rev_00000000-0000-7000-8000-000000000099",
        thesis_id="thesis_00000000-0000-7000-8000-000000000001",
        case_id="case_00000000-0000-7000-8000-000000000001",
        revision_no=1,
        description="Gross margin collapse",
        observable="GM < 50%",
        severity=InvalidationSeverity.HARD,
        status=InvalidationStatus.TRIGGERED,
        proposed_at=NOW,
        confirmed_at=LATER,
        last_checked_at=None,
        triggered_at=LATER,
        triggered_reason="GM printed 48%",
        proposed_by="codex",
        confirmed_by="user",
    )
    assert recovered.severity is InvalidationSeverity.HARD
    assert recovered.status is InvalidationStatus.TRIGGERED

    rearmed = InvalidationCondition(
        invalidation_id="rev_00000000-0000-7000-8000-000000000098",
        thesis_id="thesis_00000000-0000-7000-8000-000000000001",
        case_id="case_00000000-0000-7000-8000-000000000001",
        revision_no=1,
        description="Gross margin collapse",
        observable="GM < 50%",
        severity=InvalidationSeverity.HARD,
        status=InvalidationStatus.REARMED,
        proposed_at=NOW,
        confirmed_at=LATER,
        last_checked_at=None,
        triggered_at=None,
        triggered_reason=None,
        proposed_by="codex",
        confirmed_by="user",
    )
    assert rearmed.status is InvalidationStatus.REARMED


def test_triggered_invalidation_requires_fields() -> None:
    with pytest.raises(DataContractError, match="TRIGGERED invalidation"):
        InvalidationCondition(
            invalidation_id="rev_00000000-0000-7000-8000-000000000097",
            thesis_id="thesis_00000000-0000-7000-8000-000000000001",
            case_id="case_00000000-0000-7000-8000-000000000001",
            revision_no=1,
            description="x",
            observable="y",
            severity=InvalidationSeverity.SOFT,
            status=InvalidationStatus.TRIGGERED,
            proposed_at=NOW,
            confirmed_at=LATER,
            last_checked_at=None,
            triggered_at=None,
            triggered_reason=None,
            proposed_by="codex",
            confirmed_by="user",
        )


def test_non_triggered_invalidation_rejects_residual_fields() -> None:
    with pytest.raises(DataContractError, match="non-TRIGGERED invalidation"):
        InvalidationCondition(
            invalidation_id="rev_00000000-0000-7000-8000-000000000096",
            thesis_id="thesis_00000000-0000-7000-8000-000000000001",
            case_id="case_00000000-0000-7000-8000-000000000001",
            revision_no=1,
            description="x",
            observable="y",
            severity=InvalidationSeverity.SOFT,
            status=InvalidationStatus.ARMED,
            proposed_at=NOW,
            confirmed_at=LATER,
            last_checked_at=None,
            triggered_at=LATER,
            triggered_reason="stale residual",
            proposed_by="codex",
            confirmed_by="user",
        )


def test_assumption_retired_fields() -> None:
    with pytest.raises(DataContractError, match="RETIRED assumption"):
        Assumption(
            assumption_id="rev_a",
            thesis_id="thesis_t",
            case_id="case_c",
            revision_no=1,
            statement="s",
            basis="b",
            falsifiability="f",
            status=AssumptionStatus.RETIRED,
            proposed_at=NOW,
            confirmed_at=LATER,
            proposed_by="codex",
            confirmed_by="user",
            retired_at=None,
            retired_reason=None,
        )


def test_open_question_status_fields() -> None:
    with pytest.raises(DataContractError, match="ANSWERED requires"):
        OpenQuestion(
            question_id="rev_q",
            case_id="case_c",
            text="Why?",
            status=OpenQuestionStatus.ANSWERED,
            asked_at=NOW,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=None,
            proposed_by="user",
        )
    with pytest.raises(DataContractError, match="CLOSED_WITHOUT_ANSWER"):
        OpenQuestion(
            question_id="rev_q",
            case_id="case_c",
            text="Why?",
            status=OpenQuestionStatus.CLOSED_WITHOUT_ANSWER,
            asked_at=NOW,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason=None,
            proposed_by="user",
        )
    with pytest.raises(DataContractError, match="non-ANSWERED"):
        OpenQuestion(
            question_id="rev_q",
            case_id="case_c",
            text="Why?",
            status=OpenQuestionStatus.OPEN,
            asked_at=NOW,
            answered_at=LATER,
            answer_summary="leftover",
            closed_without_answer_reason=None,
            proposed_by="user",
        )
    with pytest.raises(DataContractError, match="non-CLOSED_WITHOUT_ANSWER"):
        OpenQuestion(
            question_id="rev_q",
            case_id="case_c",
            text="Why?",
            status=OpenQuestionStatus.STALE,
            asked_at=NOW,
            answered_at=None,
            answer_summary=None,
            closed_without_answer_reason="leftover",
            proposed_by="user",
        )


def test_watchlist_status_fields() -> None:
    with pytest.raises(DataContractError, match="PROMOTED_TO_CASE"):
        WatchlistItem(
            item_id="snapshot_1",
            market=Market.US,
            symbol="NVDA",
            display_name="NVIDIA",
            thesis_hint="watch earnings",
            triggers=("eps miss",),
            case_id=None,
            status=WatchlistItemStatus.PROMOTED_TO_CASE,
            created_at=NOW,
            updated_at=NOW,
            expires_at=None,
            promoted_to_case_id=None,
            triggered_at=None,
            triggered_reason=None,
        )
    with pytest.raises(DataContractError, match="non-PROMOTED_TO_CASE"):
        WatchlistItem(
            item_id="snapshot_1",
            market=Market.US,
            symbol="NVDA",
            display_name="NVIDIA",
            thesis_hint="watch earnings",
            triggers=("eps miss",),
            case_id=None,
            status=WatchlistItemStatus.WATCHING,
            created_at=NOW,
            updated_at=NOW,
            expires_at=None,
            promoted_to_case_id="case_x",
            triggered_at=None,
            triggered_reason=None,
        )
    with pytest.raises(DataContractError, match="non-TRIGGERED watchlist"):
        WatchlistItem(
            item_id="snapshot_1",
            market=Market.US,
            symbol="NVDA",
            display_name="NVIDIA",
            thesis_hint="watch earnings",
            triggers=("eps miss",),
            case_id=None,
            status=WatchlistItemStatus.WATCHING,
            created_at=NOW,
            updated_at=NOW,
            expires_at=None,
            promoted_to_case_id=None,
            triggered_at=LATER,
            triggered_reason="leftover",
        )


def test_candidate_requires_run_prefix() -> None:
    with pytest.raises(DataContractError, match="run_<uuid7>"):
        _candidate(candidate_id="case_00000000-0000-7000-8000-000000000001")
    with pytest.raises(DataContractError, match="run_<uuid7>"):
        _candidate(candidate_id="run_x")
    with pytest.raises(DataContractError, match="run_<uuid7>"):
        _candidate(candidate_id="run_00000000-0000-4000-8000-000000000001")


def test_candidate_status_field_sync() -> None:
    with pytest.raises(DataContractError, match="PROPOSED candidate"):
        _candidate(reviewed_at=LATER, reviewed_by="user")
    with pytest.raises(DataContractError, match="CONFIRMED candidate"):
        _candidate(status=CandidateStatus.CONFIRMED)
    with pytest.raises(DataContractError, match="REJECTED candidate"):
        _candidate(
            status=CandidateStatus.REJECTED,
            reviewed_at=LATER,
            reviewed_by="user",
        )
    with pytest.raises(DataContractError, match="WITHDRAWN candidate"):
        _candidate(
            status=CandidateStatus.WITHDRAWN,
            reviewed_at=LATER,
            reviewed_by="codex",
        )
    with pytest.raises(DataContractError, match="non-REJECTED"):
        _candidate(
            status=CandidateStatus.CONFIRMED,
            reviewed_at=LATER,
            reviewed_by="user",
            rejection_reason="leftover",
        )
    with pytest.raises(DataContractError, match="PROPOSED/EXPIRED"):
        _candidate(review_note="leftover")
    confirmed = _candidate(
        status=CandidateStatus.CONFIRMED,
        reviewed_at=LATER,
        reviewed_by="user",
        review_note="ok",
    )
    assert confirmed.status is CandidateStatus.CONFIRMED


def test_candidate_case_scope() -> None:
    with pytest.raises(DataContractError, match="case_id is required"):
        _candidate(case_id=None, kind=CandidateKind.THESIS_REVISION)
    watch = _candidate(
        case_id=None,
        kind=CandidateKind.WATCHLIST_ITEM,
        payload_json='{"kind":"watchlist_item"}',
    )
    assert watch.case_id is None


def test_candidate_thesis_scope_for_assumption() -> None:
    with pytest.raises(DataContractError, match="thesis_id is required"):
        _candidate(
            kind=CandidateKind.ASSUMPTION,
            thesis_id=None,
            payload_json='{"kind":"assumption"}',
        )
