"""Research-memory DTOs, search query, page, and timeline contracts (Phase 1C C1b)."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSearchEntityType,
    ResearchTimelineEntityType,
)
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    DecisionRecord,
    Evidence,
    EvidenceAssessment,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    SubjectEvidenceLink,
)

# Strict uuid7 token: lowercase hex, version nibble 7, RFC4122 variant.
_UUID7_TOKEN = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def _entity_id_re(prefix: EntityIdPrefix) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix.value)}_{_UUID7_TOKEN}$")


_ENTITY_ID_RE: dict[EntityIdPrefix, re.Pattern[str]] = {
    prefix: _entity_id_re(prefix) for prefix in EntityIdPrefix
}

_SEARCH_ENTITY_ID_PREFIX: dict[str, EntityIdPrefix] = {
    ResearchSearchEntityType.EVIDENCE.value: EntityIdPrefix.EVIDENCE,
    ResearchSearchEntityType.REPORT.value: EntityIdPrefix.REPORT,
    ResearchSearchEntityType.EVENT.value: EntityIdPrefix.EVENT,
    ResearchSearchEntityType.DECISION.value: EntityIdPrefix.DECISION,
    ResearchSearchEntityType.JOURNAL.value: EntityIdPrefix.JOURNAL,
}

_TIMELINE_ENTITY_ID_PREFIX: dict[str, EntityIdPrefix] = {
    ResearchTimelineEntityType.EVIDENCE.value: EntityIdPrefix.EVIDENCE,
    ResearchTimelineEntityType.REPORT.value: EntityIdPrefix.REPORT,
    ResearchTimelineEntityType.EVENT.value: EntityIdPrefix.EVENT,
    ResearchTimelineEntityType.DECISION.value: EntityIdPrefix.DECISION,
    ResearchTimelineEntityType.JOURNAL.value: EntityIdPrefix.JOURNAL,
    ResearchTimelineEntityType.THESIS_REVISION.value: EntityIdPrefix.REV,
    ResearchTimelineEntityType.CANDIDATE_RESOLUTION.value: EntityIdPrefix.RUN,
}


def _wire_enum_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(getattr(value, "value", value))


def _require_entity_id(value: str, *, field: str, prefix: EntityIdPrefix) -> str:
    pattern = _ENTITY_ID_RE[prefix]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} must match {prefix.value}_<uuid7> format")
    return value


def _require_optional_entity_id(
    value: str | None, *, field: str, prefix: EntityIdPrefix
) -> str | None:
    if value is None:
        return None
    return _require_entity_id(value, field=field, prefix=prefix)


def _require_unique_str_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field}[{index}] must be a string")
        if not item:
            raise ValueError(f"{field}[{index}] must be a non-empty string")
        if item in seen:
            raise ValueError(f"{field} must not contain duplicates")
        seen.add(item)
    return value


def _require_unique_id_tuple(
    value: object, *, field: str, prefix: EntityIdPrefix
) -> tuple[str, ...]:
    items = _require_unique_str_tuple(value, field=field)
    for index, item in enumerate(items):
        _require_entity_id(item, field=f"{field}[{index}]", prefix=prefix)
    return items


def _require_unique_enum_tuple(value: object, *, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple")
    seen: set[str] = set()
    for item in value:
        wire = _wire_enum_value(item)
        if wire in seen:
            raise ValueError(f"{field} must not contain duplicates")
        seen.add(wire)
    return value


def _require_finite_decimal(value: object, *, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise ValueError(f"{field} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")
    return value


def _require_optional_finite_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _require_finite_decimal(value, field=field)


def _require_optional_unit_interval_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    dec = _require_finite_decimal(value, field=field)
    if dec < Decimal("0") or dec > Decimal("1"):
        raise ValueError(f"{field} must be in [0, 1]")
    return dec


def _require_unit_interval_decimal(value: object, *, field: str) -> Decimal:
    dec = _require_finite_decimal(value, field=field)
    if dec < Decimal("0") or dec > Decimal("1"):
        raise ValueError(f"{field} must be in [0, 1]")
    return dec


def _aware(value: datetime, *, field: str) -> datetime:
    try:
        return require_aware_datetime(value, field_name=field)
    except DataContractError as exc:
        # Pydantic validators surface ValueError as ValidationError.
        raise ValueError(str(exc)) from None


def _optional_aware(value: datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    return _aware(value, field=field)


def _require_schema_version(value: object) -> int:
    """Exact int equal to RESEARCH_SCHEMA_VERSION; reject bool and other int-like."""
    # bool is a subclass of int; type(value) is int rejects True/False.
    if type(value) is not int:
        raise ValueError("schema_version must be an exact int equal to RESEARCH_SCHEMA_VERSION")
    if value != RESEARCH_SCHEMA_VERSION:
        raise ValueError("schema_version must equal RESEARCH_SCHEMA_VERSION")
    return value


def _validate_page_invariants(
    *,
    items_len: int,
    total: int,
    limit: int,
    offset: int,
    has_more: bool,
) -> None:
    if total < 0:
        raise ValueError("total must be >= 0")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if items_len > limit:
        raise ValueError("len(items) must be <= limit")
    # Non-empty pages must not claim more items than the remaining total.
    # Empty pages may have offset past total (end-of-results / over-seek).
    if items_len > 0 and offset + items_len > total:
        raise ValueError("offset + len(items) must be <= total when items is non-empty")
    expected_has_more = offset + items_len < total
    if has_more is not expected_has_more:
        raise ValueError("has_more must equal (offset + len(items) < total)")


class _BaseResearchMemoryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


# ---------------------------------------------------------------------------
# Entity DTOs
# ---------------------------------------------------------------------------


class EvidenceDTO(_BaseResearchMemoryDTO):
    evidence_id: str
    evidence_type: EvidenceType
    origin: EvidenceOrigin
    title: str
    summary: str
    content_text: str | None
    structured_data_json: str | None
    source_name: str
    source_vendor: str | None
    source_record_id: str | None
    source_url: str | None
    published_at: datetime | None
    observed_at: datetime
    effective_from: datetime | None
    effective_to: datetime | None
    instrument_ids: tuple[str, ...]
    topic_tags: tuple[str, ...]
    quality: EvidenceQuality
    reliability: ReliabilityLevel
    confidence: Decimal | None
    content_sha256: str
    supersedes_evidence_id: str | None
    recorded_by: str
    schema_version: int

    @field_validator("evidence_id")
    @classmethod
    def _evidence_id(cls, value: str) -> str:
        return _require_entity_id(value, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE)

    @field_validator("supersedes_evidence_id")
    @classmethod
    def _supersedes(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="supersedes_evidence_id", prefix=EntityIdPrefix.EVIDENCE
        )

    @field_validator("published_at", "effective_from", "effective_to")
    @classmethod
    def _optional_times(cls, value: datetime | None) -> datetime | None:
        return _optional_aware(value, field="datetime")

    @field_validator("observed_at")
    @classmethod
    def _observed_at(cls, value: datetime) -> datetime:
        return _aware(value, field="observed_at")

    @field_validator("instrument_ids", "topic_tags")
    @classmethod
    def _str_tuples(cls, value: object) -> tuple[str, ...]:
        return _require_unique_str_tuple(value, field="tuple")

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> Decimal | None:
        return _require_optional_unit_interval_decimal(value, field="confidence")

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, evidence: Evidence) -> EvidenceDTO:
        return cls(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.evidence_type,
            origin=evidence.origin,
            title=evidence.title,
            summary=evidence.summary,
            content_text=evidence.content_text,
            structured_data_json=evidence.structured_data_json,
            source_name=evidence.source_name,
            source_vendor=evidence.source_vendor,
            source_record_id=evidence.source_record_id,
            source_url=evidence.source_url,
            published_at=evidence.published_at,
            observed_at=evidence.observed_at,
            effective_from=evidence.effective_from,
            effective_to=evidence.effective_to,
            instrument_ids=evidence.instrument_ids,
            topic_tags=evidence.topic_tags,
            quality=evidence.quality,
            reliability=evidence.reliability,
            confidence=evidence.confidence,
            content_sha256=evidence.content_sha256,
            supersedes_evidence_id=evidence.supersedes_evidence_id,
            recorded_by=evidence.recorded_by,
            schema_version=evidence.schema_version,
        )


class SubjectEvidenceLinkDTO(_BaseResearchMemoryDTO):
    link_id: str
    subject_id: str
    evidence_id: str
    linked_at: datetime
    linked_by: str
    schema_version: int

    @field_validator("link_id")
    @classmethod
    def _link_id(cls, value: str) -> str:
        return _require_entity_id(value, field="link_id", prefix=EntityIdPrefix.REV)

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("evidence_id")
    @classmethod
    def _evidence_id(cls, value: str) -> str:
        return _require_entity_id(value, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE)

    @field_validator("linked_at")
    @classmethod
    def _linked_at(cls, value: datetime) -> datetime:
        return _aware(value, field="linked_at")

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, link: SubjectEvidenceLink) -> SubjectEvidenceLinkDTO:
        return cls(
            link_id=link.link_id,
            subject_id=link.subject_id,
            evidence_id=link.evidence_id,
            linked_at=link.linked_at,
            linked_by=link.linked_by,
            schema_version=link.schema_version,
        )


class EvidenceAssessmentDTO(_BaseResearchMemoryDTO):
    assessment_id: str
    evidence_id: str
    subject_id: str
    thesis_id: str | None
    thesis_revision_id: str | None
    stance: EvidenceStance
    materiality: Decimal
    rationale: str
    assessed_at: datetime
    assessed_by: str
    confirmed_by: str
    schema_version: int

    @field_validator("assessment_id")
    @classmethod
    def _assessment_id(cls, value: str) -> str:
        return _require_entity_id(value, field="assessment_id", prefix=EntityIdPrefix.REV)

    @field_validator("evidence_id")
    @classmethod
    def _evidence_id(cls, value: str) -> str:
        return _require_entity_id(value, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE)

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("thesis_id")
    @classmethod
    def _thesis_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(value, field="thesis_id", prefix=EntityIdPrefix.THESIS)

    @field_validator("thesis_revision_id")
    @classmethod
    def _thesis_revision_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="thesis_revision_id", prefix=EntityIdPrefix.REV
        )

    @field_validator("materiality", mode="before")
    @classmethod
    def _materiality(cls, value: object) -> Decimal:
        return _require_unit_interval_decimal(value, field="materiality")

    @field_validator("assessed_at")
    @classmethod
    def _assessed_at(cls, value: datetime) -> datetime:
        return _aware(value, field="assessed_at")

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, assessment: EvidenceAssessment) -> EvidenceAssessmentDTO:
        return cls(
            assessment_id=assessment.assessment_id,
            evidence_id=assessment.evidence_id,
            subject_id=assessment.subject_id,
            thesis_id=assessment.thesis_id,
            thesis_revision_id=assessment.thesis_revision_id,
            stance=assessment.stance,
            materiality=assessment.materiality,
            rationale=assessment.rationale,
            assessed_at=assessment.assessed_at,
            assessed_by=assessment.assessed_by,
            confirmed_by=assessment.confirmed_by,
            schema_version=assessment.schema_version,
        )


class ResearchReportDTO(_BaseResearchMemoryDTO):
    report_id: str
    subject_id: str
    report_type: ResearchReportType
    title: str
    summary: str
    content_markdown: str
    as_of: datetime
    created_at: datetime
    created_by: str
    research_run_id: str | None
    evidence_ids: tuple[str, ...]
    thesis_revision_ids: tuple[str, ...]
    supersedes_report_id: str | None
    content_sha256: str
    model_name: str | None
    prompt_version: str | None
    schema_version: int

    @field_validator("report_id")
    @classmethod
    def _report_id(cls, value: str) -> str:
        return _require_entity_id(value, field="report_id", prefix=EntityIdPrefix.REPORT)

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("research_run_id")
    @classmethod
    def _research_run_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="research_run_id", prefix=EntityIdPrefix.RUN
        )

    @field_validator("supersedes_report_id")
    @classmethod
    def _supersedes(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="supersedes_report_id", prefix=EntityIdPrefix.REPORT
        )

    @field_validator("as_of", "created_at")
    @classmethod
    def _times(cls, value: datetime) -> datetime:
        return _aware(value, field="datetime")

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(value, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE)

    @field_validator("thesis_revision_ids")
    @classmethod
    def _thesis_revision_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(
            value, field="thesis_revision_ids", prefix=EntityIdPrefix.REV
        )

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, report: ResearchReport) -> ResearchReportDTO:
        return cls(
            report_id=report.report_id,
            subject_id=report.subject_id,
            report_type=report.report_type,
            title=report.title,
            summary=report.summary,
            content_markdown=report.content_markdown,
            as_of=report.as_of,
            created_at=report.created_at,
            created_by=report.created_by,
            research_run_id=report.research_run_id,
            evidence_ids=report.evidence_ids,
            thesis_revision_ids=report.thesis_revision_ids,
            supersedes_report_id=report.supersedes_report_id,
            content_sha256=report.content_sha256,
            model_name=report.model_name,
            prompt_version=report.prompt_version,
            schema_version=report.schema_version,
        )


class ResearchEventDTO(_BaseResearchMemoryDTO):
    event_id: str
    subject_id: str
    event_type: ResearchEventType
    title: str
    summary: str
    occurred_at: datetime
    recorded_at: datetime
    published_at: datetime | None
    instrument_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    related_entity_type: str | None
    related_entity_id: str | None
    source_name: str
    schema_version: int

    @field_validator("event_id")
    @classmethod
    def _event_id(cls, value: str) -> str:
        return _require_entity_id(value, field="event_id", prefix=EntityIdPrefix.EVENT)

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _times(cls, value: datetime) -> datetime:
        return _aware(value, field="datetime")

    @field_validator("published_at")
    @classmethod
    def _published_at(cls, value: datetime | None) -> datetime | None:
        return _optional_aware(value, field="published_at")

    @field_validator("instrument_ids")
    @classmethod
    def _instrument_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_str_tuple(value, field="instrument_ids")

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(value, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE)

    @field_validator("report_ids")
    @classmethod
    def _report_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(value, field="report_ids", prefix=EntityIdPrefix.REPORT)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, event: ResearchEvent) -> ResearchEventDTO:
        return cls(
            event_id=event.event_id,
            subject_id=event.subject_id,
            event_type=event.event_type,
            title=event.title,
            summary=event.summary,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            published_at=event.published_at,
            instrument_ids=event.instrument_ids,
            evidence_ids=event.evidence_ids,
            report_ids=event.report_ids,
            related_entity_type=event.related_entity_type,
            related_entity_id=event.related_entity_id,
            source_name=event.source_name,
            schema_version=event.schema_version,
        )


class DecisionRecordDTO(_BaseResearchMemoryDTO):
    decision_id: str
    subject_id: str
    decision_type: DecisionType
    title: str
    rationale: str
    decided_at: datetime
    recorded_at: datetime
    decided_by: str
    confirmation_mode: ConfirmationMode
    primary_instrument_id: str | None
    thesis_revision_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    supersedes_decision_id: str | None
    position_context_snapshot_id: str | None
    schema_version: int
    execution_effect: Literal[False] = False

    @field_validator("decision_id")
    @classmethod
    def _decision_id(cls, value: str) -> str:
        return _require_entity_id(value, field="decision_id", prefix=EntityIdPrefix.DECISION)

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("decided_at", "recorded_at")
    @classmethod
    def _times(cls, value: datetime) -> datetime:
        return _aware(value, field="datetime")

    @field_validator("primary_instrument_id")
    @classmethod
    def _primary_instrument_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("primary_instrument_id must be a non-empty string")
        return value

    @field_validator("thesis_revision_ids")
    @classmethod
    def _thesis_revision_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(
            value, field="thesis_revision_ids", prefix=EntityIdPrefix.REV
        )

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(value, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE)

    @field_validator("report_ids")
    @classmethod
    def _report_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(value, field="report_ids", prefix=EntityIdPrefix.REPORT)

    @field_validator("supersedes_decision_id")
    @classmethod
    def _supersedes(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="supersedes_decision_id", prefix=EntityIdPrefix.DECISION
        )

    @field_validator("position_context_snapshot_id")
    @classmethod
    def _snapshot(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="position_context_snapshot_id", prefix=EntityIdPrefix.SNAPSHOT
        )

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, decision: DecisionRecord) -> DecisionRecordDTO:
        return cls(
            decision_id=decision.decision_id,
            subject_id=decision.subject_id,
            decision_type=decision.decision_type,
            title=decision.title,
            rationale=decision.rationale,
            decided_at=decision.decided_at,
            recorded_at=decision.recorded_at,
            decided_by=decision.decided_by,
            confirmation_mode=decision.confirmation_mode,
            primary_instrument_id=decision.primary_instrument_id,
            thesis_revision_ids=decision.thesis_revision_ids,
            evidence_ids=decision.evidence_ids,
            report_ids=decision.report_ids,
            supersedes_decision_id=decision.supersedes_decision_id,
            position_context_snapshot_id=decision.position_context_snapshot_id,
            schema_version=decision.schema_version,
            execution_effect=False,
        )


class JournalEntryDTO(_BaseResearchMemoryDTO):
    journal_id: str
    subject_id: str | None
    entry_type: JournalEntryType
    title: str
    body_markdown: str
    created_at: datetime
    authored_by: str
    confirmed_by: str
    instrument_ids: tuple[str, ...]
    topic_tags: tuple[str, ...]
    related_entity_type: str | None
    related_entity_id: str | None
    supersedes_journal_id: str | None
    schema_version: int

    @field_validator("journal_id")
    @classmethod
    def _journal_id(cls, value: str) -> str:
        return _require_entity_id(value, field="journal_id", prefix=EntityIdPrefix.JOURNAL)

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("created_at")
    @classmethod
    def _created_at(cls, value: datetime) -> datetime:
        return _aware(value, field="created_at")

    @field_validator("instrument_ids", "topic_tags")
    @classmethod
    def _str_tuples(cls, value: object) -> tuple[str, ...]:
        return _require_unique_str_tuple(value, field="tuple")

    @field_validator("supersedes_journal_id")
    @classmethod
    def _supersedes(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(
            value, field="supersedes_journal_id", prefix=EntityIdPrefix.JOURNAL
        )

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: object) -> int:
        return _require_schema_version(value)

    @classmethod
    def from_domain(cls, entry: JournalEntry) -> JournalEntryDTO:
        return cls(
            journal_id=entry.journal_id,
            subject_id=entry.subject_id,
            entry_type=entry.entry_type,
            title=entry.title,
            body_markdown=entry.body_markdown,
            created_at=entry.created_at,
            authored_by=entry.authored_by,
            confirmed_by=entry.confirmed_by,
            instrument_ids=entry.instrument_ids,
            topic_tags=entry.topic_tags,
            related_entity_type=entry.related_entity_type,
            related_entity_id=entry.related_entity_id,
            supersedes_journal_id=entry.supersedes_journal_id,
            schema_version=entry.schema_version,
        )


# ---------------------------------------------------------------------------
# Search query / hit / page
# ---------------------------------------------------------------------------


class ResearchSearchQuery(_BaseResearchMemoryDTO):
    text: str | None = None
    subject_id: str | None = None
    thesis_id: str | None = None
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
    def _normalize_blank_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("text must be a string or None")
        if not value.strip():
            return None
        return value

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("thesis_id")
    @classmethod
    def _thesis_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(value, field="thesis_id", prefix=EntityIdPrefix.THESIS)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("instrument_id must be a non-empty string")
        return value

    @field_validator("entity_types", "evidence_types", "journal_entry_types", "stances")
    @classmethod
    def _unique_enum_filters(cls, value: object) -> tuple[object, ...]:
        return _require_unique_enum_tuple(value, field="filter")

    @field_validator("topic_tags")
    @classmethod
    def _topic_tags(cls, value: object) -> tuple[str, ...]:
        return _require_unique_str_tuple(value, field="topic_tags")

    @field_validator("visible_from", "visible_to", "as_of")
    @classmethod
    def _optional_times(cls, value: datetime | None) -> datetime | None:
        return _optional_aware(value, field="datetime")

    @model_validator(mode="after")
    def _query_rules(self) -> Self:
        has_filter = any(
            (
                self.text is not None,
                self.subject_id is not None,
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
                "ResearchSearchQuery requires at least one effective filter "
                "(text/subject_id/thesis_id/instrument_id/entity_types/evidence_types/"
                "journal_entry_types/stances/topic_tags/visible_from/visible_to/as_of)"
            )
        if (
            self.visible_from is not None
            and self.visible_to is not None
            and self.visible_to < self.visible_from
        ):
            raise ValueError("visible_to must be >= visible_from")
        if self.stances:
            if self.subject_id is None and self.thesis_id is None:
                raise ValueError("stances filter requires subject_id or thesis_id")
            if self.entity_types and any(
                _wire_enum_value(t) != ResearchSearchEntityType.EVIDENCE.value
                for t in self.entity_types
            ):
                raise ValueError(
                    "stances filter requires entity_types to be empty or only EVIDENCE"
                )
        if (
            self.journal_entry_types
            and self.entity_types
            and any(
                _wire_enum_value(t) != ResearchSearchEntityType.JOURNAL.value
                for t in self.entity_types
            )
        ):
            raise ValueError(
                "journal_entry_types filter requires entity_types to be empty or only JOURNAL"
            )
        if self.journal_entry_types and self.evidence_types:
            raise ValueError("journal_entry_types must not be combined with evidence_types")
        if self.journal_entry_types and self.stances:
            raise ValueError("journal_entry_types must not be combined with stances")
        if self.journal_entry_types and self.thesis_id is not None:
            raise ValueError("journal_entry_types must not be combined with thesis_id")
        return self


class ResearchSearchHitDTO(_BaseResearchMemoryDTO):
    entity_type: ResearchSearchEntityType
    entity_id: str
    subject_id: str | None
    title: str
    snippet: str
    visible_at: datetime
    occurred_at: datetime | None
    instrument_ids: tuple[str, ...]
    topic_tags: tuple[str, ...]
    matched_stances: tuple[EvidenceStance, ...]
    matched_assessment_ids: tuple[str, ...]
    score: Decimal | None
    source_name: str | None

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str | None) -> str | None:
        return _require_optional_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("visible_at")
    @classmethod
    def _visible_at(cls, value: datetime) -> datetime:
        return _aware(value, field="visible_at")

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at(cls, value: datetime | None) -> datetime | None:
        return _optional_aware(value, field="occurred_at")

    @field_validator("instrument_ids", "topic_tags")
    @classmethod
    def _str_tuples(cls, value: object) -> tuple[str, ...]:
        return _require_unique_str_tuple(value, field="tuple")

    @field_validator("matched_stances")
    @classmethod
    def _matched_stances(cls, value: object) -> tuple[object, ...]:
        return _require_unique_enum_tuple(value, field="matched_stances")

    @field_validator("matched_assessment_ids")
    @classmethod
    def _matched_assessment_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_id_tuple(
            value, field="matched_assessment_ids", prefix=EntityIdPrefix.REV
        )

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, value: object) -> Decimal | None:
        return _require_optional_finite_decimal(value, field="score")

    @model_validator(mode="after")
    def _hit_rules(self) -> Self:
        entity_type = _wire_enum_value(self.entity_type)
        prefix = _SEARCH_ENTITY_ID_PREFIX.get(entity_type)
        if prefix is None:
            raise ValueError(f"unsupported entity_type: {entity_type}")
        _require_entity_id(self.entity_id, field="entity_id", prefix=prefix)
        if entity_type != ResearchSearchEntityType.EVIDENCE.value and (
            self.matched_stances or self.matched_assessment_ids
        ):
            raise ValueError(
                "non-Evidence hits must have empty matched_stances and matched_assessment_ids"
            )
        return self


class ResearchSearchPageDTO(_BaseResearchMemoryDTO):
    items: tuple[ResearchSearchHitDTO, ...]
    total: int
    limit: int
    offset: int
    has_more: bool

    @model_validator(mode="after")
    def _pagination(self) -> Self:
        _validate_page_invariants(
            items_len=len(self.items),
            total=self.total,
            limit=self.limit,
            offset=self.offset,
            has_more=self.has_more,
        )
        return self


class JournalSearchPageDTO(_BaseResearchMemoryDTO):
    items: tuple[JournalEntryDTO, ...]
    total: int
    limit: int
    offset: int
    has_more: bool

    @model_validator(mode="after")
    def _pagination(self) -> Self:
        _validate_page_invariants(
            items_len=len(self.items),
            total=self.total,
            limit=self.limit,
            offset=self.offset,
            has_more=self.has_more,
        )
        return self


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class ResearchTimelineItemDTO(_BaseResearchMemoryDTO):
    entity_type: ResearchTimelineEntityType
    entity_id: str
    subject_id: str
    title: str
    summary: str
    occurred_at: datetime
    visible_at: datetime
    instrument_ids: tuple[str, ...]
    source_name: str | None

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("occurred_at", "visible_at")
    @classmethod
    def _times(cls, value: datetime) -> datetime:
        return _aware(value, field="datetime")

    @field_validator("instrument_ids")
    @classmethod
    def _instrument_ids(cls, value: object) -> tuple[str, ...]:
        return _require_unique_str_tuple(value, field="instrument_ids")

    @model_validator(mode="after")
    def _entity_id(self) -> Self:
        entity_type = _wire_enum_value(self.entity_type)
        prefix = _TIMELINE_ENTITY_ID_PREFIX.get(entity_type)
        if prefix is None:
            raise ValueError(f"unsupported entity_type: {entity_type}")
        _require_entity_id(self.entity_id, field="entity_id", prefix=prefix)
        return self


class ResearchTimelineDTO(_BaseResearchMemoryDTO):
    subject_id: str
    as_of: datetime
    items: tuple[ResearchTimelineItemDTO, ...]
    total: int

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _require_entity_id(value, field="subject_id", prefix=EntityIdPrefix.SUBJECT)

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: datetime) -> datetime:
        return _aware(value, field="as_of")

    @model_validator(mode="after")
    def _order_and_total(self) -> Self:
        if self.total < 0:
            raise ValueError("total must be >= 0")
        if self.total < len(self.items):
            raise ValueError("total must be >= len(items)")
        for index in range(len(self.items) - 1):
            a = self.items[index]
            b = self.items[index + 1]
            if a.occurred_at != b.occurred_at:
                if a.occurred_at < b.occurred_at:
                    raise ValueError(
                        "timeline items must be ordered by "
                        "occurred_at DESC, visible_at DESC, entity_id ASC"
                    )
                continue
            if a.visible_at != b.visible_at:
                if a.visible_at < b.visible_at:
                    raise ValueError(
                        "timeline items must be ordered by "
                        "occurred_at DESC, visible_at DESC, entity_id ASC"
                    )
                continue
            if a.entity_id > b.entity_id:
                raise ValueError(
                    "timeline items must be ordered by "
                    "occurred_at DESC, visible_at DESC, entity_id ASC"
                )
        return self
