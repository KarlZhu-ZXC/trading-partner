"""Research Subject aggregate, thesis, and candidate models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

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
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.research.validation import _require_run_candidate_id


@dataclass(frozen=True, slots=True)
class ResearchSubject:
    subject_id: str
    subject_type: ResearchSubjectType
    title: str
    summary: str
    status: ResearchSubjectStatus
    primary_instrument_id: str | None
    topic_tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    created_by: str
    archived_at: datetime | None
    archived_reason: str | None
    linked_subject_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    schema_version: int

    def __post_init__(self) -> None:
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.updated_at, field_name="updated_at")
        if self.archived_at is not None:
            require_aware_datetime(self.archived_at, field_name="archived_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must be >= created_at")
        if self.status is ResearchSubjectStatus.ARCHIVED:
            if self.archived_at is None or self.archived_reason is None:
                raise DataContractError(
                    "ARCHIVED research subject requires archived_at and archived_reason"
                )
        else:
            if self.archived_at is not None or self.archived_reason is not None:
                raise DataContractError(
                    "non-ARCHIVED research subject must not set archived_* fields"
                )
        if (
            self.subject_type in {ResearchSubjectType.COMPANY, ResearchSubjectType.CATALYST}
            and self.primary_instrument_id is None
        ):
            raise DataContractError(
                "COMPANY/CATALYST research subject requires primary_instrument_id"
            )


@dataclass(frozen=True, slots=True)
class Thesis:
    thesis_id: str
    subject_id: str
    title: str
    role: ThesisRole
    status: ThesisStatus
    current_revision_no: int
    latest_revision_id: str
    parent_thesis_id: str | None
    rival_thesis_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None

    def __post_init__(self) -> None:
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.updated_at, field_name="updated_at")
        if self.archived_at is not None:
            require_aware_datetime(self.archived_at, field_name="archived_at")
        if self.current_revision_no < 1:
            raise DataContractError("current_revision_no must be >= 1")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must be >= created_at")
        if self.status is ThesisStatus.ARCHIVED and self.archived_at is None:
            raise DataContractError("ARCHIVED thesis requires archived_at")
        if self.status is not ThesisStatus.ARCHIVED and self.archived_at is not None:
            raise DataContractError("non-ARCHIVED thesis must not set archived_at")
        if self.role is ThesisRole.SUB and self.parent_thesis_id is None:
            raise DataContractError("SUB thesis requires parent_thesis_id")
        if self.role is not ThesisRole.SUB and self.parent_thesis_id is not None:
            raise DataContractError("non-SUB thesis must not set parent_thesis_id")


@dataclass(frozen=True, slots=True)
class ThesisRevision:
    revision_id: str
    thesis_id: str
    subject_id: str
    revision_no: int
    supersedes_revision_no: int | None
    statement: str
    rationale: str
    confidence_band: ConfidenceBand
    rating: InvestmentRating
    confirmation_mode: ConfirmationMode
    proposed_by: str
    confirmed_by: str
    proposed_at: datetime
    confirmed_at: datetime
    observation_window_start: date | None
    observation_window_end: date | None
    invalidation_check_note: str
    schema_version: int

    def __post_init__(self) -> None:
        require_aware_datetime(self.proposed_at, field_name="proposed_at")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")
        if self.confirmed_at < self.proposed_at:
            raise DataContractError("confirmed_at must be >= proposed_at")
        if self.revision_no < 1:
            raise DataContractError("revision_no must be >= 1")
        if self.revision_no == 1:
            if self.supersedes_revision_no is not None:
                raise DataContractError("revision_no=1 must have supersedes_revision_no=None")
        else:
            if self.supersedes_revision_no is None:
                raise DataContractError("revision_no>1 requires supersedes_revision_no")
            if self.supersedes_revision_no >= self.revision_no:
                raise DataContractError("supersedes_revision_no must be < revision_no")


@dataclass(frozen=True, slots=True)
class Assumption:
    assumption_id: str
    thesis_id: str
    subject_id: str
    revision_no: int
    statement: str
    basis: str
    falsifiability: str
    status: AssumptionStatus
    proposed_at: datetime
    confirmed_at: datetime
    proposed_by: str
    confirmed_by: str
    retired_at: datetime | None
    retired_reason: str | None

    def __post_init__(self) -> None:
        require_aware_datetime(self.proposed_at, field_name="proposed_at")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")
        if self.confirmed_at < self.proposed_at:
            raise DataContractError("confirmed_at must be >= proposed_at")
        if self.retired_at is not None:
            require_aware_datetime(self.retired_at, field_name="retired_at")
        if self.status is AssumptionStatus.RETIRED:
            if self.retired_at is None or self.retired_reason is None:
                raise DataContractError("RETIRED assumption requires retired_at and retired_reason")
        else:
            if self.retired_at is not None or self.retired_reason is not None:
                raise DataContractError("non-RETIRED assumption must not set retired_* fields")


@dataclass(frozen=True, slots=True)
class InvalidationCondition:
    """Invalidation condition attached to a thesis revision.

    HARD recovery semantics: the domain model allows reconstructing
    HARD conditions in TRIGGERED / REARMED / RETIRED states from storage.
    Application services must require status=ARMED when *creating* a new HARD
    condition; that gate is not enforced here.
    """

    invalidation_id: str
    thesis_id: str
    subject_id: str
    revision_no: int
    description: str
    observable: str
    severity: InvalidationSeverity
    status: InvalidationStatus
    proposed_at: datetime
    confirmed_at: datetime
    last_checked_at: datetime | None
    triggered_at: datetime | None
    triggered_reason: str | None
    proposed_by: str
    confirmed_by: str

    def __post_init__(self) -> None:
        require_aware_datetime(self.proposed_at, field_name="proposed_at")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")
        if self.confirmed_at < self.proposed_at:
            raise DataContractError("confirmed_at must be >= proposed_at")
        if self.last_checked_at is not None:
            require_aware_datetime(self.last_checked_at, field_name="last_checked_at")
        if self.triggered_at is not None:
            require_aware_datetime(self.triggered_at, field_name="triggered_at")
        if self.status is InvalidationStatus.TRIGGERED:
            if self.triggered_at is None or self.triggered_reason is None:
                raise DataContractError(
                    "TRIGGERED invalidation requires triggered_at and triggered_reason"
                )
        elif self.triggered_at is not None or self.triggered_reason is not None:
            raise DataContractError(
                "non-TRIGGERED invalidation must not set triggered_at/triggered_reason"
            )


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    question_id: str
    subject_id: str
    text: str
    status: OpenQuestionStatus
    asked_at: datetime
    answered_at: datetime | None
    answer_summary: str | None
    closed_without_answer_reason: str | None
    proposed_by: str

    def __post_init__(self) -> None:
        require_aware_datetime(self.asked_at, field_name="asked_at")
        if self.answered_at is not None:
            require_aware_datetime(self.answered_at, field_name="answered_at")
            if self.answered_at < self.asked_at:
                raise DataContractError("answered_at must be >= asked_at")
        if self.status is OpenQuestionStatus.ANSWERED:
            if self.answered_at is None or self.answer_summary is None:
                raise DataContractError("ANSWERED requires answered_at and answer_summary")
        elif self.answered_at is not None or self.answer_summary is not None:
            raise DataContractError(
                "non-ANSWERED open question must not set answered_at/answer_summary"
            )
        if self.status is OpenQuestionStatus.CLOSED_WITHOUT_ANSWER:
            if self.closed_without_answer_reason is None:
                raise DataContractError(
                    "CLOSED_WITHOUT_ANSWER requires closed_without_answer_reason"
                )
        elif self.closed_without_answer_reason is not None:
            raise DataContractError(
                "non-CLOSED_WITHOUT_ANSWER open question must not set closed_without_answer_reason"
            )


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    item_id: str
    market: Market
    symbol: str
    display_name: str
    thesis_hint: str
    triggers: tuple[str, ...]
    subject_id: str | None
    status: WatchlistItemStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    promoted_to_subject_id: str | None
    triggered_at: datetime | None
    triggered_reason: str | None
    instrument_id: str | None = None
    selection_reason: str | None = None

    def __post_init__(self) -> None:
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.updated_at, field_name="updated_at")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, field_name="expires_at")
        if self.triggered_at is not None:
            require_aware_datetime(self.triggered_at, field_name="triggered_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must be >= created_at")
        if self.status is WatchlistItemStatus.PROMOTED_TO_SUBJECT:
            if self.promoted_to_subject_id is None:
                raise DataContractError("PROMOTED_TO_SUBJECT requires promoted_to_subject_id")
        elif self.promoted_to_subject_id is not None:
            raise DataContractError(
                "non-PROMOTED_TO_SUBJECT watchlist item must not set promoted_to_subject_id"
            )
        if self.status is WatchlistItemStatus.TRIGGERED:
            if self.triggered_at is None or self.triggered_reason is None:
                raise DataContractError("TRIGGERED requires triggered_at and triggered_reason")
        elif self.triggered_at is not None or self.triggered_reason is not None:
            raise DataContractError(
                "non-TRIGGERED watchlist item must not set triggered_at/triggered_reason"
            )
        if self.instrument_id is not None:
            from domain.common.values import parse_instrument_id

            _, instrument_market, instrument_symbol = parse_instrument_id(self.instrument_id)
            if instrument_market is not self.market or instrument_symbol != self.symbol:
                raise DataContractError("candidate instrument_id must match market and symbol")
        if self.status in {WatchlistItemStatus.SELECTED, WatchlistItemStatus.REJECTED}:
            if self.subject_id is None:
                raise DataContractError("selected/rejected candidate requires Research Subject")
            if self.instrument_id is None:
                raise DataContractError("selected/rejected candidate requires instrument_id")
            if self.selection_reason is None or not self.selection_reason.strip():
                raise DataContractError("selected/rejected candidate requires selection_reason")
        elif self.selection_reason is not None:
            raise DataContractError(
                "selection_reason is only valid for selected/rejected candidate"
            )


@dataclass(frozen=True, slots=True)
class CandidateThesisRevision:
    candidate_id: str
    subject_id: str | None
    thesis_id: str | None
    target_revision_no: int | None
    payload_json: str
    kind: CandidateKind
    confirmation_mode: ConfirmationMode
    status: CandidateStatus
    proposed_at: datetime
    expires_at: datetime
    proposed_by: str
    proposed_by_rationale: str
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_note: str | None
    rejection_reason: str | None
    idempotency_key: str

    def __post_init__(self) -> None:
        require_aware_datetime(self.proposed_at, field_name="proposed_at")
        require_aware_datetime(self.expires_at, field_name="expires_at")
        if self.reviewed_at is not None:
            require_aware_datetime(self.reviewed_at, field_name="reviewed_at")
        _require_run_candidate_id(self.candidate_id)
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise DataContractError("idempotency_key must be non-empty")
        if self.kind is not CandidateKind.WATCHLIST_ITEM and self.subject_id is None:
            raise DataContractError(
                "subject_id is required for non-watchlist candidates",
                details={"kind": self.kind.value},
            )
        if (
            self.kind
            in {
                CandidateKind.ASSUMPTION,
                CandidateKind.INVALIDATION_CONDITION,
            }
            and self.thesis_id is None
        ):
            raise DataContractError(
                "thesis_id is required for assumption/invalidation candidates",
                details={"kind": self.kind.value},
            )
        if self.status is CandidateStatus.PROPOSED:
            if self.reviewed_at is not None or self.reviewed_by is not None:
                raise DataContractError("PROPOSED candidate must not set reviewed_at/reviewed_by")
        elif self.status is CandidateStatus.CONFIRMED:
            if self.reviewed_at is None or self.reviewed_by is None:
                raise DataContractError("CONFIRMED candidate requires reviewed_at and reviewed_by")
        elif self.status is CandidateStatus.REJECTED:
            if (
                self.reviewed_at is None
                or self.reviewed_by is None
                or self.rejection_reason is None
            ):
                raise DataContractError(
                    "REJECTED candidate requires reviewed_at, reviewed_by, rejection_reason"
                )
        elif self.status is CandidateStatus.WITHDRAWN:
            if self.reviewed_at is None or self.reviewed_by is None or self.review_note is None:
                raise DataContractError(
                    "WITHDRAWN candidate requires reviewed_at, reviewed_by, review_note"
                )
        elif self.status is CandidateStatus.EXPIRED and (
            self.reviewed_at is not None or self.reviewed_by is not None
        ):
            raise DataContractError("EXPIRED candidate must not set reviewed_at/reviewed_by")
        if self.status is not CandidateStatus.REJECTED and self.rejection_reason is not None:
            raise DataContractError("non-REJECTED candidate must not set rejection_reason")
        if (
            self.status in {CandidateStatus.PROPOSED, CandidateStatus.EXPIRED}
            and self.review_note is not None
        ):
            raise DataContractError("PROPOSED/EXPIRED candidate must not set review_note")


# --- Phase 1C research-memory models ---
