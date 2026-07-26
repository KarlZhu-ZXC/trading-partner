"""Research-state DTOs and closed CandidateRevisionPayload union (Phase 1B)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from application.dto.trade_plan import TradePlanDTO
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
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.research.models import (
    Assumption,
    CandidateThesisRevision,
    InvalidationCondition,
    InvestmentCase,
    OpenQuestion,
    Thesis,
    ThesisRevision,
    WatchlistItem,
)
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlanCondition


class _BaseResearchDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


# ---------------------------------------------------------------------------
# Nested payloads inside thesis_revision candidates
# ---------------------------------------------------------------------------


class AssumptionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    statement: str = Field(min_length=1, max_length=4000)
    basis: str = Field(min_length=1, max_length=4000)
    falsifiability: str = Field(min_length=1, max_length=4000)


class InvalidationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    description: str = Field(min_length=1, max_length=4000)
    observable: str = Field(min_length=1, max_length=4000)
    severity: InvalidationSeverity


# ---------------------------------------------------------------------------
# Closed discriminated CandidateRevisionPayload
# ---------------------------------------------------------------------------


class ThesisRevisionCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["thesis_revision"] = "thesis_revision"
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=8000)
    rationale: str = Field(min_length=1, max_length=16000)
    confidence_band: ConfidenceBand
    rating: InvestmentRating
    invalidation_check_note: str = Field(min_length=1, max_length=4000)
    observation_window_start: date | None = None
    observation_window_end: date | None = None
    assumptions: tuple[AssumptionPayload, ...] = ()
    invalidations: tuple[InvalidationPayload, ...] = ()
    thesis_role: ThesisRole | None = None
    parent_thesis_id: str | None = None
    rival_thesis_ids: tuple[str, ...] = ()
    replaces_revision_no: int | None = None
    thesis_status: ThesisStatus | None = None

    @model_validator(mode="after")
    def _role_parent_rules(self) -> Self:
        role = self.thesis_role
        if role == ThesisRole.SUB and self.parent_thesis_id is None:
            raise ValueError("SUB thesis_role requires parent_thesis_id")
        if role is not None and role != ThesisRole.SUB and self.parent_thesis_id is not None:
            raise ValueError("non-SUB thesis_role must not set parent_thesis_id")
        if (
            self.observation_window_start is not None
            and self.observation_window_end is not None
            and self.observation_window_end < self.observation_window_start
        ):
            raise ValueError("observation_window_end must be >= observation_window_start")
        return self


class AssumptionCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["assumption"] = "assumption"
    thesis_id: str = Field(min_length=1)
    revision_no: int = Field(ge=1)
    statement: str = Field(min_length=1, max_length=4000)
    basis: str = Field(min_length=1, max_length=4000)
    falsifiability: str = Field(min_length=1, max_length=4000)


class InvalidationCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["invalidation_condition"] = "invalidation_condition"
    thesis_id: str = Field(min_length=1)
    revision_no: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=4000)
    observable: str = Field(min_length=1, max_length=4000)
    severity: InvalidationSeverity
    # When set, this proposal relaxes an existing HARD condition (must STRICT_REVIEW).
    relaxes_invalidation_id: str | None = None


class OpenQuestionCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["open_question"] = "open_question"
    action: Literal["create", "answer", "mark_stale", "close"]
    question_id: str | None = None
    text: str | None = Field(default=None, max_length=2000)
    answer_summary: str | None = Field(default=None, max_length=4000)
    closed_reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _action_fields(self) -> Self:
        if self.action == "create":
            if self.text is None or not self.text.strip():
                raise ValueError("create open_question requires non-empty text")
            if self.question_id is not None:
                raise ValueError("create open_question must not set question_id")
        elif self.action == "answer":
            if self.question_id is None:
                raise ValueError("answer open_question requires question_id")
            if self.answer_summary is None or not self.answer_summary.strip():
                raise ValueError("answer open_question requires answer_summary")
        elif self.action == "mark_stale":
            if self.question_id is None:
                raise ValueError("mark_stale open_question requires question_id")
        elif self.action == "close":
            if self.question_id is None:
                raise ValueError("close open_question requires question_id")
            if self.closed_reason is None or not self.closed_reason.strip():
                raise ValueError("close open_question requires closed_reason")
        return self


class WatchlistCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["watchlist_item"] = "watchlist_item"
    action: Literal["create", "update_status"]
    item_id: str | None = None
    market: Market | None = None
    symbol: str | None = Field(default=None, max_length=32)
    display_name: str | None = Field(default=None, max_length=128)
    thesis_hint: str | None = Field(default=None, max_length=1000)
    triggers: tuple[str, ...] = ()
    case_id: str | None = None
    expires_at: datetime | None = None
    new_status: WatchlistItemStatus | None = None
    promoted_to_case_id: str | None = None
    triggered_reason: str | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware_expires(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="expires_at")
        return value

    @model_validator(mode="after")
    def _action_fields(self) -> Self:
        if self.action == "create":
            if self.market is None:
                raise ValueError("create watchlist_item requires market")
            if self.symbol is None or not self.symbol.strip():
                raise ValueError("create watchlist_item requires symbol")
            if self.display_name is None or not self.display_name.strip():
                raise ValueError("create watchlist_item requires display_name")
            if self.thesis_hint is None or not self.thesis_hint.strip():
                raise ValueError("create watchlist_item requires thesis_hint")
            if self.item_id is not None:
                raise ValueError("create watchlist_item must not set item_id")
            if self.new_status is not None:
                raise ValueError("create watchlist_item must not set new_status")
        elif self.action == "update_status":
            if self.item_id is None:
                raise ValueError("update_status watchlist_item requires item_id")
            if self.new_status is None:
                raise ValueError("update_status watchlist_item requires new_status")
            if (
                self.new_status == WatchlistItemStatus.PROMOTED_TO_CASE
                and self.promoted_to_case_id is None
            ):
                raise ValueError("PROMOTED_TO_CASE requires promoted_to_case_id")
            if self.new_status == WatchlistItemStatus.TRIGGERED and (
                self.triggered_reason is None or not self.triggered_reason.strip()
            ):
                raise ValueError("TRIGGERED requires triggered_reason")
        return self


class CaseUpdateCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["case_status_change"] = "case_status_change"
    action: Literal["create", "archive", "update"]
    case_type: InvestmentCaseType | None = None
    new_status: InvestmentCaseStatus | None = None
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    primary_instrument_id: str | None = None
    topic_tags: tuple[str, ...] | None = None
    linked_case_ids: tuple[str, ...] | None = None
    archived_reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _action_fields(self) -> Self:
        if self.action == "create":
            if self.case_type is None:
                raise ValueError("create case_status_change requires case_type")
            if self.title is None or not self.title.strip():
                raise ValueError("create case_status_change requires title")
            if self.summary is None or not self.summary.strip():
                raise ValueError("create case_status_change requires summary")
        elif self.action == "archive":
            if self.archived_reason is None or not self.archived_reason.strip():
                raise ValueError("archive case_status_change requires archived_reason")
            if self.new_status is not None and self.new_status != InvestmentCaseStatus.ARCHIVED:
                raise ValueError("archive action new_status must be archived when set")
        elif self.action == "update":
            if (
                self.title is None
                and self.summary is None
                and self.topic_tags is None
                and self.linked_case_ids is None
                and self.new_status is None
                and self.primary_instrument_id is None
            ):
                raise ValueError("update case_status_change requires at least one field")
        return self


class TradePlanConditionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    condition_code: str = Field(min_length=1, max_length=64)
    phase: TradePlanConditionPhase
    mode: TradePlanConditionMode
    description: str = Field(min_length=1, max_length=2000)
    severity: Literal["INFO", "MEDIUM", "HIGH"] = "MEDIUM"
    fact_type: TradePlanFactType | None = None
    metric_key: str | None = Field(default=None, min_length=1, max_length=128)
    comparator: TradePlanComparator | None = None
    threshold: Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    instrument_id: str | None = None
    max_fact_age_seconds: int | None = Field(default=None, gt=0)
    event_after: datetime | None = None

    @field_validator("event_after")
    @classmethod
    def _aware_event_after(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="event_after")
        return value

    @field_validator("instrument_id")
    @classmethod
    def _instrument(cls, value: str | None) -> str | None:
        if value is not None:
            parse_instrument_id(value)
        return value

    @model_validator(mode="after")
    def _condition_contract(self) -> Self:
        # Reuse the domain invariant at the public proposal boundary so an
        # invalid condition cannot linger as a confirm-time-only failure.
        try:
            TradePlanCondition(
                condition_code=self.condition_code,
                phase=TradePlanConditionPhase(self.phase),
                mode=TradePlanConditionMode(self.mode),
                description=self.description,
                severity=self.severity,
                fact_type=(
                    TradePlanFactType(self.fact_type) if self.fact_type else None
                ),
                metric_key=self.metric_key,
                comparator=(
                    TradePlanComparator(self.comparator) if self.comparator else None
                ),
                threshold=self.threshold,
                unit=self.unit,
                instrument_id=self.instrument_id,
                max_fact_age_seconds=self.max_fact_age_seconds,
                event_after=self.event_after,
            )
        except DataContractError as exc:
            raise ValueError(str(exc)) from exc
        return self


class TradePlanCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    kind: Literal["trade_plan"] = "trade_plan"
    plan_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    thesis_id: str = Field(min_length=1, max_length=128)
    instrument_id: str = Field(min_length=1, max_length=160)
    status: TradePlanStatus
    valid_from: datetime
    valid_until: datetime | None = None
    currency: str = Field(min_length=1, max_length=16)
    reference_price: Decimal = Field(gt=0)
    reference_price_at: datetime
    target_position_percent: Decimal = Field(ge=0, le=100)
    max_position_percent: Decimal = Field(ge=0, le=100)
    risk_budget_percent: Decimal = Field(ge=0, le=100)
    stop_price: Decimal | None = Field(default=None, gt=0)
    conditions: tuple[TradePlanConditionPayload, ...] = Field(default=(), max_length=100)
    notes: str = Field(min_length=1, max_length=8000)

    @field_validator("valid_from", "valid_until", "reference_price_at")
    @classmethod
    def _aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @field_validator("instrument_id")
    @classmethod
    def _plan_instrument(cls, value: str) -> str:
        parse_instrument_id(value)
        return value

    @model_validator(mode="after")
    def _plan_rules(self) -> Self:
        if (self.plan_id is None) != (self.expected_version is None):
            raise ValueError("plan_id and expected_version must be provided together")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must follow valid_from")
        if self.target_position_percent > self.max_position_percent:
            raise ValueError("target_position_percent must not exceed max_position_percent")
        if self.status == TradePlanStatus.ACTIVE and not self.conditions:
            raise ValueError("ACTIVE trade plan requires conditions")
        codes = [item.condition_code for item in self.conditions]
        if len(codes) != len(set(codes)):
            raise ValueError("condition_code values must be unique")
        return self


CandidateRevisionPayload = Annotated[
    ThesisRevisionCandidatePayload
    | AssumptionCandidatePayload
    | InvalidationCandidatePayload
    | OpenQuestionCandidatePayload
    | WatchlistCandidatePayload
    | CaseUpdateCandidatePayload
    | TradePlanCandidatePayload,
    Field(discriminator="kind"),
]

_CANDIDATE_PAYLOAD_ADAPTER: TypeAdapter[
    ThesisRevisionCandidatePayload
    | AssumptionCandidatePayload
    | InvalidationCandidatePayload
    | OpenQuestionCandidatePayload
    | WatchlistCandidatePayload
    | CaseUpdateCandidatePayload
    | TradePlanCandidatePayload
] = TypeAdapter(CandidateRevisionPayload)


def parse_candidate_payload(payload_json: str) -> (
    ThesisRevisionCandidatePayload
    | AssumptionCandidatePayload
    | InvalidationCandidatePayload
    | OpenQuestionCandidatePayload
    | WatchlistCandidatePayload
    | CaseUpdateCandidatePayload
    | TradePlanCandidatePayload
):
    """Parse and validate closed candidate payload JSON."""
    return _CANDIDATE_PAYLOAD_ADAPTER.validate_json(payload_json)


def candidate_payload_to_json(
    payload: (
        ThesisRevisionCandidatePayload
        | AssumptionCandidatePayload
        | InvalidationCandidatePayload
        | OpenQuestionCandidatePayload
        | WatchlistCandidatePayload
        | CaseUpdateCandidatePayload
        | TradePlanCandidatePayload
    ),
) -> str:
    """Canonical JSON serialization for payload storage and idempotency compare."""
    return payload.model_dump_json()


def candidate_payload_canonical_dict(
    payload: (
        ThesisRevisionCandidatePayload
        | AssumptionCandidatePayload
        | InvalidationCandidatePayload
        | OpenQuestionCandidatePayload
        | WatchlistCandidatePayload
        | CaseUpdateCandidatePayload
        | TradePlanCandidatePayload
    ),
) -> dict[str, Any]:
    return payload.model_dump(mode="json")


def payloads_equal_json(left_json: str, right_json: str) -> bool:
    """Compare two payload_json strings by parsed canonical content."""
    left: object = json.loads(left_json)
    right: object = json.loads(right_json)
    return bool(left == right)


# Proposed* payloads used by service propose_* APIs (mirrors domain; times optional).
# Prefer the candidate payload models; aliases keep design naming stable.


ProposedRevisionPayload = ThesisRevisionCandidatePayload
ProposedAssumptionPayload = AssumptionCandidatePayload
ProposedInvalidationPayload = InvalidationCandidatePayload


# ---------------------------------------------------------------------------
# Entity DTOs
# ---------------------------------------------------------------------------


class InvestmentCaseDTO(_BaseResearchDTO):
    case_id: str
    case_type: InvestmentCaseType
    title: str
    summary: str
    status: InvestmentCaseStatus
    primary_instrument_id: str | None
    topic_tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    created_by: str
    archived_at: datetime | None
    archived_reason: str | None
    linked_case_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    schema_version: int

    @field_validator("created_at", "updated_at", "archived_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, case: InvestmentCase) -> InvestmentCaseDTO:
        return cls(
            case_id=case.case_id,
            case_type=case.case_type,
            title=case.title,
            summary=case.summary,
            status=case.status,
            primary_instrument_id=case.primary_instrument_id,
            topic_tags=case.topic_tags,
            created_at=case.created_at,
            updated_at=case.updated_at,
            created_by=case.created_by,
            archived_at=case.archived_at,
            archived_reason=case.archived_reason,
            linked_case_ids=case.linked_case_ids,
            evidence_ids=case.evidence_ids,
            report_ids=case.report_ids,
            event_ids=case.event_ids,
            decision_ids=case.decision_ids,
            schema_version=case.schema_version,
        )

    @classmethod
    def from_domain_list(cls, items: tuple[InvestmentCase, ...]) -> tuple[InvestmentCaseDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class InvestmentCaseListDTO(_BaseResearchDTO):
    items: tuple[InvestmentCaseDTO, ...]
    total: int


class ThesisDTO(_BaseResearchDTO):
    thesis_id: str
    case_id: str
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

    @field_validator("created_at", "updated_at", "archived_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, thesis: Thesis) -> ThesisDTO:
        return cls(
            thesis_id=thesis.thesis_id,
            case_id=thesis.case_id,
            title=thesis.title,
            role=thesis.role,
            status=thesis.status,
            current_revision_no=thesis.current_revision_no,
            latest_revision_id=thesis.latest_revision_id,
            parent_thesis_id=thesis.parent_thesis_id,
            rival_thesis_ids=thesis.rival_thesis_ids,
            created_at=thesis.created_at,
            updated_at=thesis.updated_at,
            archived_at=thesis.archived_at,
        )

    @classmethod
    def from_domain_list(cls, items: tuple[Thesis, ...]) -> tuple[ThesisDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class ThesisListDTO(_BaseResearchDTO):
    items: tuple[ThesisDTO, ...]


class ThesisRevisionDTO(_BaseResearchDTO):
    revision_id: str
    thesis_id: str
    case_id: str
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

    @field_validator("proposed_at", "confirmed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, revision: ThesisRevision) -> ThesisRevisionDTO:
        return cls(
            revision_id=revision.revision_id,
            thesis_id=revision.thesis_id,
            case_id=revision.case_id,
            revision_no=revision.revision_no,
            supersedes_revision_no=revision.supersedes_revision_no,
            statement=revision.statement,
            rationale=revision.rationale,
            confidence_band=revision.confidence_band,
            rating=revision.rating,
            confirmation_mode=revision.confirmation_mode,
            proposed_by=revision.proposed_by,
            confirmed_by=revision.confirmed_by,
            proposed_at=revision.proposed_at,
            confirmed_at=revision.confirmed_at,
            observation_window_start=revision.observation_window_start,
            observation_window_end=revision.observation_window_end,
            invalidation_check_note=revision.invalidation_check_note,
            schema_version=revision.schema_version,
        )

    @classmethod
    def from_domain_list(
        cls, items: tuple[ThesisRevision, ...]
    ) -> tuple[ThesisRevisionDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class AssumptionDTO(_BaseResearchDTO):
    assumption_id: str
    thesis_id: str
    case_id: str
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

    @field_validator("proposed_at", "confirmed_at", "retired_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, assumption: Assumption) -> AssumptionDTO:
        return cls(
            assumption_id=assumption.assumption_id,
            thesis_id=assumption.thesis_id,
            case_id=assumption.case_id,
            revision_no=assumption.revision_no,
            statement=assumption.statement,
            basis=assumption.basis,
            falsifiability=assumption.falsifiability,
            status=assumption.status,
            proposed_at=assumption.proposed_at,
            confirmed_at=assumption.confirmed_at,
            proposed_by=assumption.proposed_by,
            confirmed_by=assumption.confirmed_by,
            retired_at=assumption.retired_at,
            retired_reason=assumption.retired_reason,
        )

    @classmethod
    def from_domain_list(cls, items: tuple[Assumption, ...]) -> tuple[AssumptionDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class InvalidationConditionDTO(_BaseResearchDTO):
    invalidation_id: str
    thesis_id: str
    case_id: str
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

    @field_validator("proposed_at", "confirmed_at", "last_checked_at", "triggered_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, inv: InvalidationCondition) -> InvalidationConditionDTO:
        return cls(
            invalidation_id=inv.invalidation_id,
            thesis_id=inv.thesis_id,
            case_id=inv.case_id,
            revision_no=inv.revision_no,
            description=inv.description,
            observable=inv.observable,
            severity=inv.severity,
            status=inv.status,
            proposed_at=inv.proposed_at,
            confirmed_at=inv.confirmed_at,
            last_checked_at=inv.last_checked_at,
            triggered_at=inv.triggered_at,
            triggered_reason=inv.triggered_reason,
            proposed_by=inv.proposed_by,
            confirmed_by=inv.confirmed_by,
        )

    @classmethod
    def from_domain_list(
        cls, items: tuple[InvalidationCondition, ...]
    ) -> tuple[InvalidationConditionDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class OpenQuestionDTO(_BaseResearchDTO):
    question_id: str
    case_id: str
    text: str
    status: OpenQuestionStatus
    asked_at: datetime
    answered_at: datetime | None
    answer_summary: str | None
    closed_without_answer_reason: str | None
    proposed_by: str

    @field_validator("asked_at", "answered_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, question: OpenQuestion) -> OpenQuestionDTO:
        return cls(
            question_id=question.question_id,
            case_id=question.case_id,
            text=question.text,
            status=question.status,
            asked_at=question.asked_at,
            answered_at=question.answered_at,
            answer_summary=question.answer_summary,
            closed_without_answer_reason=question.closed_without_answer_reason,
            proposed_by=question.proposed_by,
        )

    @classmethod
    def from_domain_list(cls, items: tuple[OpenQuestion, ...]) -> tuple[OpenQuestionDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class OpenQuestionListDTO(_BaseResearchDTO):
    items: tuple[OpenQuestionDTO, ...]


class WatchlistItemDTO(_BaseResearchDTO):
    item_id: str
    market: Market
    symbol: str
    display_name: str
    thesis_hint: str
    triggers: tuple[str, ...]
    case_id: str | None
    status: WatchlistItemStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    promoted_to_case_id: str | None
    triggered_at: datetime | None
    triggered_reason: str | None

    @field_validator("created_at", "updated_at", "expires_at", "triggered_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @classmethod
    def from_domain(cls, item: WatchlistItem) -> WatchlistItemDTO:
        return cls(
            item_id=item.item_id,
            market=item.market,
            symbol=item.symbol,
            display_name=item.display_name,
            thesis_hint=item.thesis_hint,
            triggers=item.triggers,
            case_id=item.case_id,
            status=item.status,
            created_at=item.created_at,
            updated_at=item.updated_at,
            expires_at=item.expires_at,
            promoted_to_case_id=item.promoted_to_case_id,
            triggered_at=item.triggered_at,
            triggered_reason=item.triggered_reason,
        )

    @classmethod
    def from_domain_list(cls, items: tuple[WatchlistItem, ...]) -> tuple[WatchlistItemDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class WatchlistListDTO(_BaseResearchDTO):
    items: tuple[WatchlistItemDTO, ...]


class CandidateRevisionDTO(_BaseResearchDTO):
    candidate_id: str
    case_id: str | None
    thesis_id: str | None
    target_revision_no: int | None
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
    payload: (
        ThesisRevisionCandidatePayload
        | AssumptionCandidatePayload
        | InvalidationCandidatePayload
        | OpenQuestionCandidatePayload
        | WatchlistCandidatePayload
        | CaseUpdateCandidatePayload
        | TradePlanCandidatePayload
    )

    @field_validator("proposed_at", "expires_at", "reviewed_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value)
        return value

    @model_validator(mode="after")
    def _kind_matches_payload(self) -> Self:
        payload_kind = self.payload.kind
        kind_value = self.kind.value if isinstance(self.kind, CandidateKind) else str(self.kind)
        if payload_kind != kind_value:
            raise ValueError(
                f"candidate kind {kind_value!r} must match payload.kind {payload_kind!r}"
            )
        return self

    @classmethod
    def from_domain(cls, candidate: CandidateThesisRevision) -> CandidateRevisionDTO:
        payload = parse_candidate_payload(candidate.payload_json)
        return cls(
            candidate_id=candidate.candidate_id,
            case_id=candidate.case_id,
            thesis_id=candidate.thesis_id,
            target_revision_no=candidate.target_revision_no,
            kind=candidate.kind,
            confirmation_mode=candidate.confirmation_mode,
            status=candidate.status,
            proposed_at=candidate.proposed_at,
            expires_at=candidate.expires_at,
            proposed_by=candidate.proposed_by,
            proposed_by_rationale=candidate.proposed_by_rationale,
            reviewed_at=candidate.reviewed_at,
            reviewed_by=candidate.reviewed_by,
            review_note=candidate.review_note,
            rejection_reason=candidate.rejection_reason,
            idempotency_key=candidate.idempotency_key,
            payload=payload,
        )

    @classmethod
    def from_domain_list(
        cls, items: tuple[CandidateThesisRevision, ...]
    ) -> tuple[CandidateRevisionDTO, ...]:
        return tuple(cls.from_domain(i) for i in items)


class CandidateRevisionListDTO(_BaseResearchDTO):
    items: tuple[CandidateRevisionDTO, ...]


class ThesisHistoryDTO(_BaseResearchDTO):
    thesis: ThesisDTO
    revisions: tuple[ThesisRevisionDTO, ...]
    supersedes_edges: tuple[tuple[int, int | None], ...]


class ResearchStateDTO(_BaseResearchDTO):
    case: InvestmentCaseDTO
    theses: tuple[ThesisDTO, ...]
    latest_revisions: tuple[ThesisRevisionDTO, ...]
    assumptions: tuple[AssumptionDTO, ...]
    invalidations: tuple[InvalidationConditionDTO, ...]
    open_questions: tuple[OpenQuestionDTO, ...]
    watchlist_items: tuple[WatchlistItemDTO, ...]
    pending_candidates: tuple[CandidateRevisionDTO, ...]
    current_trade_plan: TradePlanDTO | None = None
    trade_plan_versions: tuple[TradePlanDTO, ...] = ()


class ConfirmedRevisionDTO(_BaseResearchDTO):
    thesis: ThesisDTO
    revision: ThesisRevisionDTO
    assumptions: tuple[AssumptionDTO, ...]
    invalidations: tuple[InvalidationConditionDTO, ...]
    case: InvestmentCaseDTO


class ConfirmedStateUpdateDTO(_BaseResearchDTO):
    candidate: CandidateRevisionDTO
    research_state: ResearchStateDTO | None
    affected_entity_type: str
    affected_entity_id: str | None


def kind_from_payload(
    payload: (
        ThesisRevisionCandidatePayload
        | AssumptionCandidatePayload
        | InvalidationCandidatePayload
        | OpenQuestionCandidatePayload
        | WatchlistCandidatePayload
        | CaseUpdateCandidatePayload
        | TradePlanCandidatePayload
    ),
) -> CandidateKind:
    return CandidateKind(payload.kind)
