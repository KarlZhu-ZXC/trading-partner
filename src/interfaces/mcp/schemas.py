"""MCP tool input schemas (Phase 1A + Phase 1B + Phase 1C + Phase 1D)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.research import (
    CandidateRevisionPayload,
    ThesisRevisionCandidatePayload,
)
from domain.common.enums import (
    AssetType,
    ConfirmationMode,
    DecisionType,
    EvidenceStance,
    EvidenceType,
    InvestmentCaseStatus,
    InvestmentCaseType,
    JournalEntryType,
    Market,
    ResearchSearchEntityType,
    ResearchTimelineEntityType,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# UUID7-compatible token (version nibble 7, RFC variant 8/9/a/b).
_UUID7 = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
# Design §11 uses a slightly looser pattern for archive / run ids.
_UUID_FLEX = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

CASE_ID_UUID7_PATTERN = rf"^case_{_UUID7}$"
CASE_ID_FLEX_PATTERN = rf"^case_{_UUID_FLEX}$"
THESIS_ID_UUID7_PATTERN = rf"^thesis_{_UUID7}$"
RUN_ID_FLEX_PATTERN = rf"^run_{_UUID_FLEX}$"
REPORT_ID_UUID7_PATTERN = rf"^report_{_UUID7}$"
JOURNAL_ID_UUID7_PATTERN = rf"^journal_{_UUID7}$"
DECISION_ID_UUID7_PATTERN = rf"^decision_{_UUID7}$"
EVIDENCE_ID_UUID7_PATTERN = rf"^evidence_{_UUID7}$"
EVENT_ID_UUID7_PATTERN = rf"^event_{_UUID7}$"
REV_ID_UUID7_PATTERN = rf"^rev_{_UUID7}$"
SNAPSHOT_ID_UUID7_PATTERN = rf"^snapshot_{_UUID7}$"

# Frozen Journal related-entity wire types → strict ``<prefix>_<uuid7>`` patterns.
# Type strings mirror C4b2 ``JOURNAL_RELATED_ENTITY_TYPES``; no domain enum exists.
JournalRelatedEntityType = Literal[
    "case",
    "thesis",
    "thesis_revision",
    "evidence",
    "report",
    "event",
    "decision",
    "journal",
]
_JOURNAL_RELATED_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "case": re.compile(CASE_ID_UUID7_PATTERN),
    "thesis": re.compile(THESIS_ID_UUID7_PATTERN),
    "thesis_revision": re.compile(REV_ID_UUID7_PATTERN),
    "evidence": re.compile(EVIDENCE_ID_UUID7_PATTERN),
    "report": re.compile(REPORT_ID_UUID7_PATTERN),
    "event": re.compile(EVENT_ID_UUID7_PATTERN),
    "decision": re.compile(DECISION_ID_UUID7_PATTERN),
    "journal": re.compile(JOURNAL_ID_UUID7_PATTERN),
}


class MarketGetMockSnapshotInput(BaseModel):
    """Input for market_get_mock_snapshot.

    Only exact Market wire values ``A_SHARE`` / ``US`` are accepted.
    Symbol is required and non-empty; business whitelist is enforced in application.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        use_enum_values=False,
    )

    market: Market
    symbol: str = Field(min_length=1)
    as_of: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def _symbol_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("symbol must be non-empty")
        return stripped

    @field_validator("as_of")
    @classmethod
    def _as_of_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        try:
            return require_aware_datetime(value, field_name="as_of")
        except DataContractError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("market", mode="before")
    @classmethod
    def _market_normalized(cls, value: object) -> object:
        if isinstance(value, Market):
            return value
        if isinstance(value, str):
            # Case-sensitive exact match only.
            for member in Market:
                if member.value == value:
                    return member
            raise ValueError(f"market must be exactly one of {[m.value for m in Market]}")
        raise ValueError("market must be a string enum value")


# ---------------------------------------------------------------------------
# Phase 1B research tool inputs
# ---------------------------------------------------------------------------


class InvestmentCaseCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_type: InvestmentCaseType
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    primary_instrument_id: str | None = None
    topic_tags: tuple[str, ...] = ()
    linked_case_ids: tuple[str, ...] = ()
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _company_catalyst_requires_instrument(self) -> Self:
        # Domain INV: COMPANY/CATALYST require primary_instrument_id (design §4).
        if self.case_type in {
            InvestmentCaseType.COMPANY,
            InvestmentCaseType.CATALYST,
        }:
            instrument = self.primary_instrument_id
            if instrument is None or not instrument.strip():
                raise ValueError("COMPANY/CATALYST case requires non-empty primary_instrument_id")
        return self


class InvestmentCaseGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=CASE_ID_UUID7_PATTERN)


class InvestmentCaseListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_type: InvestmentCaseType | None = None
    status: InvestmentCaseStatus | None = None
    primary_instrument_id: str | None = None
    topic_tag: str | None = None
    include_archived: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class InvestmentCaseArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=CASE_ID_FLEX_PATTERN)
    archived_reason: str = Field(min_length=1, max_length=1000)
    reviewed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ResearchStateGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=CASE_ID_UUID7_PATTERN)
    include_archived_theses: bool = False
    include_watchlist: bool = True


class ResearchStateUpdateInput(BaseModel):
    """research_state_update — always produces a PROPOSED candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str | None = Field(default=None, pattern=CASE_ID_UUID7_PATTERN)
    payload: CandidateRevisionPayload
    confirmation_mode: ConfirmationMode = ConfirmationMode.STRICT_REVIEW
    proposed_by: Literal["user", "external_agent", "codex"]
    proposed_by_rationale: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _case_scope(self) -> Self:
        kind = self.payload.kind
        if self.case_id is None and kind != "watchlist_item":
            raise ValueError("case_id is required unless payload.kind is watchlist_item")
        return self


class ThesisRevisionProposeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=CASE_ID_UUID7_PATTERN)
    thesis_id: str | None = Field(default=None, pattern=THESIS_ID_UUID7_PATTERN)
    payload: ThesisRevisionCandidatePayload
    confirmation_mode: ConfirmationMode = ConfirmationMode.STRICT_REVIEW
    proposed_by: Literal["user", "external_agent", "codex"]
    proposed_by_rationale: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ThesisRevisionConfirmInput(BaseModel):
    """Collapsed confirm | reject | withdraw on a candidate (candidate_id uses RUN prefix)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=RUN_ID_FLEX_PATTERN)
    action: Literal["confirm", "reject", "withdraw"] = "confirm"
    reviewed_by: Literal["user", "external_agent", "codex"]
    submitted_via: Literal["direct", "codex_chat"] = "direct"
    authorization_note: str | None = Field(default=None, min_length=1, max_length=4000)
    review_note: str | None = Field(default=None, max_length=4000)
    rejection_reason: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _action_rules(self) -> Self:
        if self.action == "reject" and (
            self.rejection_reason is None or not self.rejection_reason.strip()
        ):
            raise ValueError("action=reject requires non-empty rejection_reason")
        if self.action == "withdraw" and (self.review_note is None or not self.review_note.strip()):
            raise ValueError("action=withdraw requires non-empty review_note")
        if self.reviewed_by == "codex" and self.action in {"confirm", "reject"}:
            raise ValueError("reviewed_by=codex only allows action=withdraw")
        if self.submitted_via == "codex_chat":
            if self.reviewed_by != "user":
                raise ValueError("submitted_via=codex_chat requires reviewed_by=user")
            if self.authorization_note is None or not self.authorization_note.strip():
                raise ValueError("submitted_via=codex_chat requires authorization_note")
        elif self.authorization_note is not None:
            raise ValueError("authorization_note requires submitted_via=codex_chat")
        return self


class ThesisHistoryGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thesis_id: str = Field(pattern=THESIS_ID_UUID7_PATTERN)


# ---------------------------------------------------------------------------
# Phase 1D instrument_resolve
# ---------------------------------------------------------------------------


class InstrumentResolveInput(BaseModel):
    """Input for instrument_resolve (Phase 1D design §14.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    market: Market
    query: str = Field(min_length=1)
    asset_type: AssetType | None = None
    as_of: datetime | None = None

    @field_validator("query")
    @classmethod
    def _query_nonblank_stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must be non-empty")
        return stripped

    @field_validator("as_of")
    @classmethod
    def _as_of_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        try:
            return require_aware_datetime(value, field_name="as_of")
        except DataContractError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("market", mode="before")
    @classmethod
    def _market_exact(cls, value: object) -> object:
        if isinstance(value, Market):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            for member in Market:
                if member.value.casefold() == normalized:
                    return member
            raise ValueError(f"market must be one of {[m.value for m in Market]}")
        raise ValueError("market must be a string enum value")

    @field_validator("asset_type", mode="before")
    @classmethod
    def _asset_type_normalized(cls, value: object) -> object:
        if value is None or isinstance(value, AssetType):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            for member in AssetType:
                if member.value.casefold() == normalized:
                    return member
            raise ValueError(f"asset_type must be one of {[m.value for m in AssetType]}")
        raise ValueError("asset_type must be a string enum value or null")


# ---------------------------------------------------------------------------
# Phase 1C research memory tools (exact public surface; no Evidence create MCP)
# ---------------------------------------------------------------------------


def _optional_aware_datetime(value: datetime | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return require_aware_datetime(value, field_name=field_name)
    except DataContractError as exc:
        raise ValueError(exc.message) from exc


def _coerce_tuple(value: object) -> object:
    """Accept list wire values from MCP as tuples for frozen schemas."""
    if isinstance(value, list):
        return tuple(value)
    return value


class ResearchSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    text: str | None = Field(default=None, max_length=500)
    case_id: str | None = Field(default=None, pattern=CASE_ID_UUID7_PATTERN)
    thesis_id: str | None = Field(default=None, pattern=THESIS_ID_UUID7_PATTERN)
    instrument_id: str | None = None
    entity_types: tuple[ResearchSearchEntityType, ...] = ()
    evidence_types: tuple[EvidenceType, ...] = ()
    journal_entry_types: tuple[JournalEntryType, ...] = ()
    stances: tuple[EvidenceStance, ...] = ()
    topic_tags: tuple[str, ...] = ()
    visible_from: datetime | None = None
    visible_to: datetime | None = None
    as_of: datetime | None = None
    include_superseded: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("text", mode="before")
    @classmethod
    def _blank_text_as_none(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("text must be a string or null")
        stripped = value.strip()
        return stripped if stripped else None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id_nonblank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("instrument_id must be non-empty when provided")
        return stripped

    @field_validator(
        "entity_types",
        "evidence_types",
        "journal_entry_types",
        "stances",
        "topic_tags",
        mode="before",
    )
    @classmethod
    def _tuple_filters(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("visible_from", "visible_to", "as_of")
    @classmethod
    def _aware_optional(cls, value: datetime | None) -> datetime | None:
        return _optional_aware_datetime(value, field_name="datetime")

    @model_validator(mode="after")
    def _search_rules(self) -> Self:
        has_filter = any(
            (
                self.text is not None,
                self.case_id is not None,
                self.thesis_id is not None,
                self.instrument_id is not None,
                len(self.entity_types) > 0,
                len(self.evidence_types) > 0,
                len(self.journal_entry_types) > 0,
                len(self.stances) > 0,
                len(self.topic_tags) > 0,
                self.visible_from is not None,
                self.visible_to is not None,
                self.as_of is not None,
            )
        )
        if not has_filter:
            raise ValueError(
                "research_search requires at least one effective filter "
                "(non-blank text, case_id, thesis_id, instrument_id, entity_types, "
                "evidence_types, journal_entry_types, stances, topic_tags, "
                "visible_from, visible_to, or as_of)"
            )
        if (
            self.visible_from is not None
            and self.visible_to is not None
            and self.visible_to < self.visible_from
        ):
            raise ValueError("visible_to must be >= visible_from")
        if self.stances and self.case_id is None and self.thesis_id is None:
            raise ValueError("stances filter requires case_id or thesis_id")
        return self


class ResearchReportGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(pattern=REPORT_ID_UUID7_PATTERN)


class ResearchTimelineGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    case_id: str = Field(pattern=CASE_ID_UUID7_PATTERN)
    entity_types: tuple[ResearchTimelineEntityType, ...] = ()
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    as_of: datetime | None = None
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("entity_types", mode="before")
    @classmethod
    def _entity_types_tuple(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("occurred_from", "occurred_to", "as_of")
    @classmethod
    def _aware_optional(cls, value: datetime | None) -> datetime | None:
        return _optional_aware_datetime(value, field_name="datetime")

    @model_validator(mode="after")
    def _window_order(self) -> Self:
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_to < self.occurred_from
        ):
            raise ValueError("occurred_to must be >= occurred_from")
        return self


class JournalSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    text: str | None = Field(default=None, max_length=500)
    case_id: str | None = Field(default=None, pattern=CASE_ID_UUID7_PATTERN)
    instrument_id: str | None = None
    entry_types: tuple[JournalEntryType, ...] = ()
    as_of: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("text", mode="before")
    @classmethod
    def _blank_text_as_none(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("text must be a string or null")
        stripped = value.strip()
        return stripped if stripped else None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id_nonblank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("instrument_id must be non-empty when provided")
        return stripped

    @field_validator("entry_types", mode="before")
    @classmethod
    def _entry_types_tuple(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("as_of")
    @classmethod
    def _as_of_aware(cls, value: datetime | None) -> datetime | None:
        return _optional_aware_datetime(value, field_name="as_of")

    @model_validator(mode="after")
    def _at_least_one_filter(self) -> Self:
        has_filter = any(
            (
                self.text is not None,
                self.case_id is not None,
                self.instrument_id is not None,
                len(self.entry_types) > 0,
                self.as_of is not None,
            )
        )
        if not has_filter:
            raise ValueError(
                "journal_search requires at least one effective filter "
                "(non-blank text, case_id, instrument_id, entry_types, or as_of)"
            )
        return self


class JournalAppendInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    case_id: str | None = Field(default=None, pattern=CASE_ID_UUID7_PATTERN)
    entry_type: JournalEntryType
    title: str = Field(min_length=1, max_length=300)
    body_markdown: str = Field(min_length=1, max_length=200_000)
    authored_by: Literal["user", "external_agent", "codex"]
    confirmed_by: Literal["user", "external_agent"]
    instrument_ids: tuple[str, ...] = ()
    topic_tags: tuple[str, ...] = ()
    related_entity_type: JournalRelatedEntityType | None = None
    related_entity_id: str | None = None
    supersedes_journal_id: str | None = Field(default=None, pattern=JOURNAL_ID_UUID7_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("instrument_ids", "topic_tags", mode="before")
    @classmethod
    def _tuple_fields(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("title", "body_markdown", "idempotency_key")
    @classmethod
    def _nonblank_stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must be non-blank")
        return stripped

    @model_validator(mode="after")
    def _related_pair(self) -> Self:
        has_type = self.related_entity_type is not None
        has_id = self.related_entity_id is not None
        if has_type != has_id:
            raise ValueError(
                "related_entity_type and related_entity_id must both be set or both null"
            )
        if self.related_entity_type is not None and self.related_entity_id is not None:
            # Type is schema-constrained; id must match frozen type→prefix map.
            pattern = _JOURNAL_RELATED_ID_PATTERNS[self.related_entity_type]
            if not pattern.fullmatch(self.related_entity_id):
                raise ValueError(
                    "related_entity_id must match the frozen "
                    f"{self.related_entity_type!r} id prefix pattern "
                    f"(thesis_revision → rev_<uuid7>), got {self.related_entity_id!r}"
                )
        return self


class DecisionRecordAppendInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    case_id: str = Field(pattern=CASE_ID_UUID7_PATTERN)
    decision_type: DecisionType
    title: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=20_000)
    decided_at: datetime
    decided_by: Literal["user", "external_agent"]
    confirmation_mode: ConfirmationMode = ConfirmationMode.STRICT_REVIEW
    primary_instrument_id: str | None = None
    thesis_revision_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    report_ids: tuple[str, ...] = ()
    supersedes_decision_id: str | None = Field(default=None, pattern=DECISION_ID_UUID7_PATTERN)
    position_context_snapshot_id: str | None = Field(
        default=None, pattern=SNAPSHOT_ID_UUID7_PATTERN
    )
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("thesis_revision_ids", "evidence_ids", "report_ids", mode="before")
    @classmethod
    def _id_tuples(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("title", "rationale", "idempotency_key")
    @classmethod
    def _nonblank_stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must be non-blank")
        return stripped

    @field_validator("decided_at")
    @classmethod
    def _decided_at_aware(cls, value: datetime) -> datetime:
        try:
            return require_aware_datetime(value, field_name="decided_at")
        except DataContractError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("thesis_revision_ids")
    @classmethod
    def _rev_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        pattern = re.compile(REV_ID_UUID7_PATTERN)
        for item in value:
            if not pattern.fullmatch(item):
                raise ValueError(f"thesis_revision_ids items must match rev_<uuid7>, got {item!r}")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        pattern = re.compile(EVIDENCE_ID_UUID7_PATTERN)
        for item in value:
            if not pattern.fullmatch(item):
                raise ValueError(f"evidence_ids items must match evidence_<uuid7>, got {item!r}")
        return value

    @field_validator("report_ids")
    @classmethod
    def _report_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        pattern = re.compile(REPORT_ID_UUID7_PATTERN)
        for item in value:
            if not pattern.fullmatch(item):
                raise ValueError(f"report_ids items must match report_<uuid7>, got {item!r}")
        return value

    @field_validator("primary_instrument_id")
    @classmethod
    def _primary_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("primary_instrument_id must be non-empty when provided")
        return stripped
