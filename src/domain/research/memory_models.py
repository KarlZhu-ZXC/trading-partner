"""Research evidence, report, event, decision, and journal models."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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
    VendorId,
)
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.validation import (
    _CONTENT_MARKDOWN_MAX,
    _CONTENT_TEXT_MAX,
    _DECISION_RATIONALE_MAX,
    _JOURNAL_BODY_MAX,
    _NORMAL_ONLY_DECISION_TYPES,
    _RATIONALE_MAX,
    _SOURCE_NAME_MAX,
    _STRICT_DECISION_TYPES,
    _SUMMARY_MAX,
    _TITLE_MAX,
    _USER_AGENT_OR_CODEX,
    _USER_OR_AGENT,
    _require_actor,
    _require_bounded_str,
    _require_entity_id,
    _require_id_tuple,
    _require_matching_content_sha256,
    _require_non_blank_str,
    _require_not_self_supersede,
    _require_optional_entity_id,
    _require_optional_str_max,
    _require_optional_unit_interval_decimal,
    _require_recorded_by,
    _require_related_entity_pair,
    _require_schema_version,
    _require_string_tuple,
    _require_unit_interval_decimal,
    canonicalize_research_json_object,
    compute_evidence_content_sha256,
    compute_report_content_sha256,
)


@dataclass(frozen=True, slots=True)
class Evidence:
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

    def __post_init__(self) -> None:
        _require_entity_id(self.evidence_id, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE)
        if not isinstance(self.evidence_type, EvidenceType):
            raise DataContractError(
                "evidence_type must be EvidenceType",
                details={"type": type(self.evidence_type).__name__},
            )
        if not isinstance(self.origin, EvidenceOrigin):
            raise DataContractError(
                "origin must be EvidenceOrigin",
                details={"type": type(self.origin).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(self.summary, field="summary", min_len=1, max_len=_SUMMARY_MAX)
        _require_optional_str_max(
            self.content_text, field="content_text", max_len=_CONTENT_TEXT_MAX
        )
        if self.structured_data_json is not None:
            canonical = canonicalize_research_json_object(self.structured_data_json)
            if not hmac.compare_digest(self.structured_data_json, canonical):
                raise DataContractError(
                    "structured_data_json must already be canonical JSON object text",
                    details={"field": "structured_data_json", "rule": "canonical_json"},
                )
        _require_bounded_str(
            self.source_name, field="source_name", min_len=1, max_len=_SOURCE_NAME_MAX
        )
        if self.source_vendor is not None:
            vendor_ok = True
            try:
                VendorId(self.source_vendor)
            except ValueError:
                vendor_ok = False
            if not vendor_ok:
                raise DataContractError(
                    "source_vendor must be a known VendorId value when set",
                    details={"field": "source_vendor", "rule": "unknown_vendor"},
                )
        if self.source_record_id is not None:
            _require_non_blank_str(self.source_record_id, field="source_record_id")
        if self.source_url is not None:
            _require_non_blank_str(self.source_url, field="source_url")
        require_aware_datetime(self.observed_at, field_name="observed_at")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        if self.effective_from is not None:
            require_aware_datetime(self.effective_from, field_name="effective_from")
        if self.effective_to is not None:
            require_aware_datetime(self.effective_to, field_name="effective_to")
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise DataContractError(
                "effective_to must be >= effective_from",
                details={"field": "effective_to"},
            )
        _require_string_tuple(self.instrument_ids, field="instrument_ids")
        _require_string_tuple(self.topic_tags, field="topic_tags")
        if not isinstance(self.quality, EvidenceQuality):
            raise DataContractError(
                "quality must be EvidenceQuality",
                details={"type": type(self.quality).__name__},
            )
        if not isinstance(self.reliability, ReliabilityLevel):
            raise DataContractError(
                "reliability must be ReliabilityLevel",
                details={"type": type(self.reliability).__name__},
            )
        _require_optional_unit_interval_decimal(self.confidence, field="confidence")
        expected_hash = compute_evidence_content_sha256(
            evidence_type=self.evidence_type,
            origin=self.origin,
            title=self.title,
            summary=self.summary,
            content_text=self.content_text,
            structured_data_json=self.structured_data_json,
            source_name=self.source_name,
            source_vendor=self.source_vendor,
            source_record_id=self.source_record_id,
            published_at=self.published_at,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            instrument_ids=self.instrument_ids,
        )
        _require_matching_content_sha256(self.content_sha256, expected_hash)
        _require_optional_entity_id(
            self.supersedes_evidence_id,
            field="supersedes_evidence_id",
            prefix=EntityIdPrefix.EVIDENCE,
        )
        _require_not_self_supersede(
            self.evidence_id,
            self.supersedes_evidence_id,
            field="supersedes_evidence_id",
        )
        if self.evidence_type is EvidenceType.CORRECTION and self.supersedes_evidence_id is None:
            raise DataContractError(
                "CORRECTION evidence requires supersedes_evidence_id",
                details={"field": "supersedes_evidence_id"},
            )
        recorded_by = _require_recorded_by(self.recorded_by)
        if self.origin is EvidenceOrigin.USER_OBSERVATION and recorded_by not in _USER_OR_AGENT:
            raise DataContractError(
                "USER_OBSERVATION recorded_by must be user or external_agent",
                details={"field": "recorded_by"},
            )
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class SubjectEvidenceLink:
    link_id: str
    subject_id: str
    evidence_id: str
    linked_at: datetime
    linked_by: str
    schema_version: int

    def __post_init__(self) -> None:
        _require_entity_id(self.link_id, field="link_id", prefix=EntityIdPrefix.REV)
        _require_entity_id(self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT)
        _require_entity_id(self.evidence_id, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE)
        require_aware_datetime(self.linked_at, field_name="linked_at")
        _require_non_blank_str(self.linked_by, field="linked_by")
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
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

    def __post_init__(self) -> None:
        _require_entity_id(self.assessment_id, field="assessment_id", prefix=EntityIdPrefix.REV)
        _require_entity_id(self.evidence_id, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE)
        _require_entity_id(self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT)
        _require_optional_entity_id(self.thesis_id, field="thesis_id", prefix=EntityIdPrefix.THESIS)
        _require_optional_entity_id(
            self.thesis_revision_id,
            field="thesis_revision_id",
            prefix=EntityIdPrefix.REV,
        )
        if not isinstance(self.stance, EvidenceStance):
            raise DataContractError(
                "stance must be EvidenceStance",
                details={"type": type(self.stance).__name__},
            )
        _require_unit_interval_decimal(self.materiality, field="materiality")
        _require_bounded_str(self.rationale, field="rationale", min_len=1, max_len=_RATIONALE_MAX)
        require_aware_datetime(self.assessed_at, field_name="assessed_at")
        _require_actor(self.assessed_by, field="assessed_by", allowed=_USER_AGENT_OR_CODEX)
        _require_actor(self.confirmed_by, field="confirmed_by", allowed=_USER_OR_AGENT)
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class ResearchReport:
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

    def __post_init__(self) -> None:
        _require_entity_id(self.report_id, field="report_id", prefix=EntityIdPrefix.REPORT)
        _require_entity_id(self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT)
        if not isinstance(self.report_type, ResearchReportType):
            raise DataContractError(
                "report_type must be ResearchReportType",
                details={"type": type(self.report_type).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(self.summary, field="summary", min_len=1, max_len=_SUMMARY_MAX)
        _require_bounded_str(
            self.content_markdown,
            field="content_markdown",
            min_len=1,
            max_len=_CONTENT_MARKDOWN_MAX,
        )
        require_aware_datetime(self.as_of, field_name="as_of")
        require_aware_datetime(self.created_at, field_name="created_at")
        if self.as_of > self.created_at:
            raise DataContractError(
                "as_of must be <= created_at",
                details={"field": "as_of"},
            )
        _require_non_blank_str(self.created_by, field="created_by")
        _require_optional_entity_id(
            self.research_run_id, field="research_run_id", prefix=EntityIdPrefix.RUN
        )
        _require_id_tuple(self.evidence_ids, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE)
        _require_id_tuple(
            self.thesis_revision_ids,
            field="thesis_revision_ids",
            prefix=EntityIdPrefix.REV,
        )
        _require_optional_entity_id(
            self.supersedes_report_id,
            field="supersedes_report_id",
            prefix=EntityIdPrefix.REPORT,
        )
        _require_not_self_supersede(
            self.report_id,
            self.supersedes_report_id,
            field="supersedes_report_id",
        )
        expected_hash = compute_report_content_sha256(
            subject_id=self.subject_id,
            report_type=self.report_type,
            title=self.title,
            summary=self.summary,
            content_markdown=self.content_markdown,
            as_of=self.as_of,
            evidence_ids=self.evidence_ids,
            thesis_revision_ids=self.thesis_revision_ids,
        )
        _require_matching_content_sha256(self.content_sha256, expected_hash)
        if self.model_name is not None:
            _require_non_blank_str(self.model_name, field="model_name")
        if self.prompt_version is not None:
            _require_non_blank_str(self.prompt_version, field="prompt_version")
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class ResearchEvent:
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

    def __post_init__(self) -> None:
        _require_entity_id(self.event_id, field="event_id", prefix=EntityIdPrefix.EVENT)
        _require_entity_id(self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT)
        if not isinstance(self.event_type, ResearchEventType):
            raise DataContractError(
                "event_type must be ResearchEventType",
                details={"type": type(self.event_type).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(self.summary, field="summary", min_len=1, max_len=_SUMMARY_MAX)
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_string_tuple(self.instrument_ids, field="instrument_ids")
        _require_id_tuple(self.evidence_ids, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE)
        _require_id_tuple(self.report_ids, field="report_ids", prefix=EntityIdPrefix.REPORT)
        _require_related_entity_pair(self.related_entity_type, self.related_entity_id)
        _require_bounded_str(
            self.source_name, field="source_name", min_len=1, max_len=_SOURCE_NAME_MAX
        )
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
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

    def __post_init__(self) -> None:
        _require_entity_id(self.decision_id, field="decision_id", prefix=EntityIdPrefix.DECISION)
        _require_entity_id(self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT)
        if not isinstance(self.decision_type, DecisionType):
            raise DataContractError(
                "decision_type must be DecisionType",
                details={"type": type(self.decision_type).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(
            self.rationale,
            field="rationale",
            min_len=1,
            max_len=_DECISION_RATIONALE_MAX,
        )
        require_aware_datetime(self.decided_at, field_name="decided_at")
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        if self.decided_at > self.recorded_at:
            raise DataContractError(
                "decided_at must be <= recorded_at",
                details={"field": "decided_at"},
            )
        _require_actor(self.decided_by, field="decided_by", allowed=_USER_OR_AGENT)
        if not isinstance(self.confirmation_mode, ConfirmationMode):
            raise DataContractError(
                "confirmation_mode must be ConfirmationMode",
                details={"type": type(self.confirmation_mode).__name__},
            )
        if (
            self.decision_type in _STRICT_DECISION_TYPES
            and self.confirmation_mode is not ConfirmationMode.STRICT_REVIEW
        ):
            raise DataContractError(
                "trading-intent decision types require STRICT_REVIEW",
                details={
                    "decision_type": self.decision_type.value,
                    "confirmation_mode": self.confirmation_mode.value,
                },
            )
        if (
            self.decision_type in _NORMAL_ONLY_DECISION_TYPES
            and self.confirmation_mode is not ConfirmationMode.NORMAL
        ):
            raise DataContractError(
                "WATCH/NO_ACTION/RESEARCH_MORE require NORMAL confirmation_mode",
                details={
                    "decision_type": self.decision_type.value,
                    "confirmation_mode": self.confirmation_mode.value,
                },
            )
        if self.primary_instrument_id is not None:
            _require_non_blank_str(self.primary_instrument_id, field="primary_instrument_id")
        _require_id_tuple(
            self.thesis_revision_ids,
            field="thesis_revision_ids",
            prefix=EntityIdPrefix.REV,
        )
        _require_id_tuple(self.evidence_ids, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE)
        _require_id_tuple(self.report_ids, field="report_ids", prefix=EntityIdPrefix.REPORT)
        _require_optional_entity_id(
            self.supersedes_decision_id,
            field="supersedes_decision_id",
            prefix=EntityIdPrefix.DECISION,
        )
        _require_not_self_supersede(
            self.decision_id,
            self.supersedes_decision_id,
            field="supersedes_decision_id",
        )
        _require_optional_entity_id(
            self.position_context_snapshot_id,
            field="position_context_snapshot_id",
            prefix=EntityIdPrefix.SNAPSHOT,
        )
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class JournalEntry:
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

    def __post_init__(self) -> None:
        _require_entity_id(self.journal_id, field="journal_id", prefix=EntityIdPrefix.JOURNAL)
        _require_optional_entity_id(
            self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT
        )
        if not isinstance(self.entry_type, JournalEntryType):
            raise DataContractError(
                "entry_type must be JournalEntryType",
                details={"type": type(self.entry_type).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(
            self.body_markdown,
            field="body_markdown",
            min_len=1,
            max_len=_JOURNAL_BODY_MAX,
        )
        require_aware_datetime(self.created_at, field_name="created_at")
        _require_actor(self.authored_by, field="authored_by", allowed=_USER_AGENT_OR_CODEX)
        _require_actor(self.confirmed_by, field="confirmed_by", allowed=_USER_OR_AGENT)
        _require_string_tuple(self.instrument_ids, field="instrument_ids")
        _require_string_tuple(self.topic_tags, field="topic_tags")
        _require_related_entity_pair(self.related_entity_type, self.related_entity_id)
        _require_optional_entity_id(
            self.supersedes_journal_id,
            field="supersedes_journal_id",
            prefix=EntityIdPrefix.JOURNAL,
        )
        _require_not_self_supersede(
            self.journal_id,
            self.supersedes_journal_id,
            field="supersedes_journal_id",
        )
        _require_schema_version(self.schema_version)
