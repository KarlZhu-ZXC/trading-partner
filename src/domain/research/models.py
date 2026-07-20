"""Research domain models (Phase 1B + Phase 1C C1a).

Phase 1A froze the 12-name research registry. Phase 1B keeps that list intact,
implements the dataclass bodies for the 1B subset, and adds a supporting-model
registry for OpenQuestion / CandidateThesisRevision.

Phase 1C C1a implements the six frozen research-memory records plus
CaseEvidenceLink (supporting registry only), canonical JSON/hash helpers, and
mechanical domain invariants. No persistence or MCP surface lives here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from domain.common.enums import (
    AssumptionStatus,
    CandidateKind,
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    InvalidationSeverity,
    InvalidationStatus,
    InvestmentCaseStatus,
    InvestmentCaseType,
    InvestmentRating,
    JournalEntryType,
    Market,
    OpenQuestionStatus,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ThesisRole,
    ThesisStatus,
    VendorId,
    WatchlistItemStatus,
)
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import ensure_utc, require_aware_datetime

# Frozen model names for Investment Case / research state.
# Original 12 names must never be removed (Phase 1C placeholders stay).
FROZEN_RESEARCH_MODEL_NAMES: tuple[str, ...] = (
    "InvestmentCase",
    "Thesis",
    "ThesisRevision",
    "Assumption",
    "InvalidationCondition",
    "Evidence",
    "EvidenceAssessment",
    "ResearchReport",
    "ResearchEvent",
    "DecisionRecord",
    "JournalEntry",
    "WatchlistItem",
)

FROZEN_PHASE1B_SUPPORTING_MODEL_NAMES: tuple[str, ...] = (
    "OpenQuestion",
    "CandidateThesisRevision",
)

# CaseEvidenceLink is not a 13th frozen aggregate; supporting registry only.
FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES: tuple[str, ...] = ("CaseEvidenceLink",)

RESEARCH_SCHEMA_VERSION: int = 1

# Strict run_<uuid7> (lowercase hex + hyphens, version nibble 7, RFC4122 variant).
_UUID7_TOKEN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_RUN_CANDIDATE_ID_RE = re.compile(
    rf"^{re.escape(EntityIdPrefix.RUN.value)}_{_UUID7_TOKEN}$"
)
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

_TITLE_MAX = 300
_SUMMARY_MAX = 8000
_CONTENT_TEXT_MAX = 200_000
_SOURCE_NAME_MAX = 200
_RATIONALE_MAX = 8000
_DECISION_RATIONALE_MAX = 20_000
_JOURNAL_BODY_MAX = 200_000
_CONTENT_MARKDOWN_MAX = 2_000_000

_USER_OR_AGENT = frozenset({"user", "external_agent"})
_USER_AGENT_OR_CODEX = frozenset({"user", "external_agent", "codex"})
_RECORDED_BY_LITERALS = frozenset({"user", "external_agent", "system"})
_PROVIDER_RECORDED_PREFIX = "provider:"

_STRICT_DECISION_TYPES = frozenset(
    {
        DecisionType.INITIATE_INTENT,
        DecisionType.ADD_INTENT,
        DecisionType.HOLD,
        DecisionType.REDUCE_INTENT,
        DecisionType.EXIT_INTENT,
        DecisionType.AVOID,
    }
)
_NORMAL_ONLY_DECISION_TYPES = frozenset(
    {
        DecisionType.WATCH,
        DecisionType.NO_ACTION,
        DecisionType.RESEARCH_MORE,
    }
)


def _require_run_candidate_id(candidate_id: str) -> None:
    if not _RUN_CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise DataContractError(
            "candidate_id must match run_<uuid7> format",
            details={
                "candidate_id": candidate_id,
                "expected_pattern": _RUN_CANDIDATE_ID_RE.pattern,
            },
        )


def _entity_id_pattern(prefix: EntityIdPrefix) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix.value)}_{_UUID7_TOKEN}$")


_ENTITY_ID_PATTERNS: dict[EntityIdPrefix, re.Pattern[str]] = {
    prefix: _entity_id_pattern(prefix) for prefix in EntityIdPrefix
}


def _require_entity_id(value: str, *, field: str, prefix: EntityIdPrefix) -> None:
    pattern = _ENTITY_ID_PATTERNS[prefix]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DataContractError(
            f"{field} must match {prefix.value}_<uuid7> format",
            details={
                "field": field,
                "value": value,
                "expected_pattern": pattern.pattern,
            },
        )


def _require_optional_entity_id(
    value: str | None, *, field: str, prefix: EntityIdPrefix
) -> None:
    if value is None:
        return
    _require_entity_id(value, field=field, prefix=prefix)


def _require_schema_version(schema_version: object) -> None:
    # Exact int only: bool is a subclass of int and must not pass via True == 1.
    if type(schema_version) is not int:
        raise DataContractError(
            "schema_version must be an exact int equal to RESEARCH_SCHEMA_VERSION",
            details={
                "field": "schema_version",
                "type": type(schema_version).__name__,
                "expected": RESEARCH_SCHEMA_VERSION,
            },
        )
    if schema_version != RESEARCH_SCHEMA_VERSION:
        raise DataContractError(
            "schema_version must equal RESEARCH_SCHEMA_VERSION",
            details={
                "field": "schema_version",
                "schema_version": schema_version,
                "expected": RESEARCH_SCHEMA_VERSION,
            },
        )


def _require_non_blank_str(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string",
            details={"field": field, "type": type(value).__name__},
        )
    if not value or not value.strip():
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    return value


def _require_bounded_str(
    value: object,
    *,
    field: str,
    min_len: int,
    max_len: int,
) -> str:
    text = _require_non_blank_str(value, field=field)
    length = len(text)
    if length < min_len or length > max_len:
        raise DataContractError(
            f"{field} length must be in [{min_len}, {max_len}]",
            details={"field": field, "length": length, "min": min_len, "max": max_len},
        )
    return text


def _require_optional_str_max(
    value: str | None, *, field: str, max_len: int
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string or None",
            details={"field": field, "type": type(value).__name__},
        )
    if len(value) > max_len:
        raise DataContractError(
            f"{field} length must be <= {max_len}",
            details={"field": field, "length": len(value), "max": max_len},
        )
    return value


def _require_id_tuple(
    value: object,
    *,
    field: str,
    prefix: EntityIdPrefix | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(
            f"{field} must be a tuple",
            details={"field": field, "type": type(value).__name__},
        )
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item or not item.strip():
            raise DataContractError(
                f"{field} elements must be non-empty strings",
                details={"field": field, "index": index},
            )
        if item in seen:
            raise DataContractError(
                f"{field} must not contain duplicates",
                details={"field": field, "duplicate": item},
            )
        seen.add(item)
        if prefix is not None:
            _require_entity_id(item, field=f"{field}[{index}]", prefix=prefix)
    return value


def _require_string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    return _require_id_tuple(value, field=field, prefix=None)


def _require_unit_interval_decimal(value: object, *, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={
                "field": field,
                "rule": "decimal_type",
                "type": type(value).__name__,
            },
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field, "rule": "finite_decimal"},
        )
    if value < Decimal("0") or value > Decimal("1"):
        raise DataContractError(
            f"{field} must be in [0, 1]",
            details={"field": field, "rule": "unit_interval"},
        )
    return value


def _require_optional_unit_interval_decimal(
    value: object, *, field: str
) -> Decimal | None:
    if value is None:
        return None
    return _require_unit_interval_decimal(value, field=field)


def _require_content_sha256(value: object, *, field: str = "content_sha256") -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.fullmatch(value):
        raise DataContractError(
            f"{field} must be a 64-character lowercase hex digest",
            details={"field": field},
        )
    return value


def _require_actor(value: object, *, field: str, allowed: frozenset[str]) -> str:
    text = _require_non_blank_str(value, field=field)
    if text not in allowed:
        # Do not echo rejected actor free-text (may be secret-shaped).
        raise DataContractError(
            f"{field} must be one of {sorted(allowed)}",
            details={"field": field, "allowed": sorted(allowed)},
        )
    return text


def _require_recorded_by(value: object, *, field: str = "recorded_by") -> str:
    text = _require_non_blank_str(value, field=field)
    if text in _RECORDED_BY_LITERALS:
        return text
    if text.startswith(_PROVIDER_RECORDED_PREFIX):
        vendor_raw = text.removeprefix(_PROVIDER_RECORDED_PREFIX)
        vendor_ok = True
        try:
            VendorId(vendor_raw)
        except ValueError:
            vendor_ok = False
        if not vendor_ok:
            # Raise outside except so ValueError (may echo vendor) is not retained.
            raise DataContractError(
                "recorded_by provider vendor must be a known VendorId value",
                details={"field": field, "rule": "unknown_vendor"},
            )
        return text
    raise DataContractError(
        "recorded_by must be user|external_agent|system|provider:<VendorId>",
        details={"field": field},
    )


def _require_related_entity_pair(
    entity_type: str | None, entity_id: str | None
) -> None:
    if (entity_type is None) ^ (entity_id is None):
        raise DataContractError(
            "related_entity_type and related_entity_id must both be set or both null",
            details={
                "related_entity_type": entity_type,
                "related_entity_id": entity_id,
            },
        )
    if entity_type is not None:
        _require_non_blank_str(entity_type, field="related_entity_type")
        _require_non_blank_str(entity_id, field="related_entity_id")


def _require_not_self_supersede(
    entity_id: str, supersedes_id: str | None, *, field: str
) -> None:
    if supersedes_id is not None and supersedes_id == entity_id:
        raise DataContractError(
            f"{field} must not equal the entity's own id",
            details={"field": field, "entity_id": entity_id},
        )


def _datetime_to_utc_z(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def _decimal_to_canonical_string(value: Decimal) -> str:
    raw = str(value)
    if "E" in raw.upper() or "e" in raw:
        return format(value, "f")
    return raw


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return _datetime_to_utc_z(value)
    if type(value) is Decimal:
        return _decimal_to_canonical_string(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class _StrictJsonError(ValueError):
    """Internal strict JSON parse failure (no payload echo)."""


def _strict_object_pairs_hook(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonError("duplicate object key")
        result[key] = value
    return result


def _strict_parse_constant(_name: str) -> object:
    raise _StrictJsonError("nonstandard constant")


def canonicalize_research_json_object(value: str) -> str:
    """Return canonical JSON object text for research structured data.

    Uses UTF-8 JSON with ``sort_keys=True``, ``ensure_ascii=False``, and
    separators ``(",", ":")``. Rejects duplicate keys, arrays, scalars, NaN,
    and Infinity.
    """
    if not isinstance(value, str):
        raise DataContractError(
            "structured JSON value must be a string",
            details={"type": type(value).__name__},
        )
    # Map failures outside except blocks so raw JSONDecodeError.doc / parse
    # messages (caller content) never remain as __cause__/__context__.
    parse_failure: str | None = None
    parsed: Any = None
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_strict_object_pairs_hook,
            parse_constant=_strict_parse_constant,
        )
    except _StrictJsonError:
        parse_failure = "strict_json"
    except json.JSONDecodeError:
        parse_failure = "malformed_json"
    if parse_failure == "strict_json":
        raise DataContractError(
            "structured JSON must not contain duplicate keys, NaN, or Infinity",
            details={"rule": "strict_json"},
        )
    if parse_failure == "malformed_json":
        raise DataContractError(
            "structured JSON is not valid JSON",
            details={"rule": "malformed_json"},
        )
    if not isinstance(parsed, dict):
        raise DataContractError(
            "structured JSON must be a JSON object",
            details={"rule": "json_object", "type": type(parsed).__name__},
        )
    canonical: str | None = None
    try:
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        canonical = None
    if canonical is None:
        raise DataContractError(
            "structured JSON is not serializable under canonical rules",
            details={"rule": "json_serialize"},
        )
    return canonical


def _canonical_research_payload(payload: dict[str, object]) -> str:
    canonical: str | None = None
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError):
        canonical = None
    if canonical is None:
        raise DataContractError(
            "research content payload is not JSON-serializable",
            details={"rule": "json_serialize"},
        )
    return canonical


def _sha256_hex(canonical_json: str) -> str:
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_evidence_content_sha256(
    *,
    evidence_type: EvidenceType,
    origin: EvidenceOrigin,
    title: str,
    summary: str,
    content_text: str | None,
    structured_data_json: str | None,
    source_name: str,
    source_vendor: str | None,
    source_record_id: str | None,
    published_at: datetime | None,
    effective_from: datetime | None,
    effective_to: datetime | None,
    instrument_ids: tuple[str, ...],
) -> str:
    """Compute Evidence content hash per Phase 1C §11.3 field set."""
    if structured_data_json is None:
        structured_value: object | None = None
    else:
        structured_value = json.loads(
            canonicalize_research_json_object(structured_data_json)
        )
    payload: dict[str, object] = {
        "content_text": content_text,
        "effective_from": (
            None if effective_from is None else _datetime_to_utc_z(effective_from)
        ),
        "effective_to": (
            None if effective_to is None else _datetime_to_utc_z(effective_to)
        ),
        "evidence_type": evidence_type.value,
        "instrument_ids_sorted": sorted(instrument_ids),
        "origin": origin.value,
        "published_at": (
            None if published_at is None else _datetime_to_utc_z(published_at)
        ),
        "source_name": source_name,
        "source_record_id": source_record_id,
        "source_vendor": source_vendor,
        "structured_data_json": structured_value,
        "summary": summary,
        "title": title,
    }
    return _sha256_hex(_canonical_research_payload(payload))


def compute_report_content_sha256(
    *,
    case_id: str,
    report_type: ResearchReportType,
    title: str,
    summary: str,
    content_markdown: str,
    as_of: datetime,
    evidence_ids: tuple[str, ...],
    thesis_revision_ids: tuple[str, ...],
) -> str:
    """Compute ResearchReport content hash per Phase 1C §11.3 field set."""
    payload: dict[str, object] = {
        "as_of": _datetime_to_utc_z(as_of),
        "case_id": case_id,
        "content_markdown": content_markdown,
        "evidence_ids_sorted": sorted(evidence_ids),
        "report_type": report_type.value,
        "summary": summary,
        "thesis_revision_ids_sorted": sorted(thesis_revision_ids),
        "title": title,
    }
    return _sha256_hex(_canonical_research_payload(payload))


def _require_matching_content_sha256(
    provided: str, expected: str, *, field: str = "content_sha256"
) -> None:
    _require_content_sha256(provided, field=field)
    if not hmac.compare_digest(provided, expected):
        raise DataContractError(
            f"{field} does not match recomputed content hash",
            details={"field": field, "rule": "content_sha256_match"},
        )


@dataclass(frozen=True, slots=True)
class InvestmentCase:
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

    def __post_init__(self) -> None:
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.updated_at, field_name="updated_at")
        if self.archived_at is not None:
            require_aware_datetime(self.archived_at, field_name="archived_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must be >= created_at")
        if self.status is InvestmentCaseStatus.ARCHIVED:
            if self.archived_at is None or self.archived_reason is None:
                raise DataContractError("ARCHIVED case requires archived_at and archived_reason")
        else:
            if self.archived_at is not None or self.archived_reason is not None:
                raise DataContractError("non-ARCHIVED case must not set archived_* fields")
        if (
            self.case_type in {InvestmentCaseType.COMPANY, InvestmentCaseType.CATALYST}
            and self.primary_instrument_id is None
        ):
            raise DataContractError(
                "COMPANY/CATALYST case requires primary_instrument_id"
            )


@dataclass(frozen=True, slots=True)
class Thesis:
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
    case_id: str
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
                "non-CLOSED_WITHOUT_ANSWER open question must not set "
                "closed_without_answer_reason"
            )


@dataclass(frozen=True, slots=True)
class WatchlistItem:
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

    def __post_init__(self) -> None:
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.updated_at, field_name="updated_at")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, field_name="expires_at")
        if self.triggered_at is not None:
            require_aware_datetime(self.triggered_at, field_name="triggered_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must be >= created_at")
        if self.status is WatchlistItemStatus.PROMOTED_TO_CASE:
            if self.promoted_to_case_id is None:
                raise DataContractError("PROMOTED_TO_CASE requires promoted_to_case_id")
        elif self.promoted_to_case_id is not None:
            raise DataContractError(
                "non-PROMOTED_TO_CASE watchlist item must not set promoted_to_case_id"
            )
        if self.status is WatchlistItemStatus.TRIGGERED:
            if self.triggered_at is None or self.triggered_reason is None:
                raise DataContractError(
                    "TRIGGERED requires triggered_at and triggered_reason"
                )
        elif self.triggered_at is not None or self.triggered_reason is not None:
            raise DataContractError(
                "non-TRIGGERED watchlist item must not set triggered_at/triggered_reason"
            )


@dataclass(frozen=True, slots=True)
class CandidateThesisRevision:
    candidate_id: str
    case_id: str | None
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
        if self.kind is not CandidateKind.WATCHLIST_ITEM and self.case_id is None:
            raise DataContractError(
                "case_id is required for non-watchlist candidates",
                details={"kind": self.kind.value},
            )
        if self.kind in {
            CandidateKind.ASSUMPTION,
            CandidateKind.INVALIDATION_CONDITION,
        } and self.thesis_id is None:
            raise DataContractError(
                "thesis_id is required for assumption/invalidation candidates",
                details={"kind": self.kind.value},
            )
        if self.status is CandidateStatus.PROPOSED:
            if self.reviewed_at is not None or self.reviewed_by is not None:
                raise DataContractError(
                    "PROPOSED candidate must not set reviewed_at/reviewed_by"
                )
        elif self.status is CandidateStatus.CONFIRMED:
            if self.reviewed_at is None or self.reviewed_by is None:
                raise DataContractError(
                    "CONFIRMED candidate requires reviewed_at and reviewed_by"
                )
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
            if (
                self.reviewed_at is None
                or self.reviewed_by is None
                or self.review_note is None
            ):
                raise DataContractError(
                    "WITHDRAWN candidate requires reviewed_at, reviewed_by, review_note"
                )
        elif self.status is CandidateStatus.EXPIRED and (
            self.reviewed_at is not None or self.reviewed_by is not None
        ):
            raise DataContractError(
                "EXPIRED candidate must not set reviewed_at/reviewed_by"
            )
        if (
            self.status is not CandidateStatus.REJECTED
            and self.rejection_reason is not None
        ):
            raise DataContractError(
                "non-REJECTED candidate must not set rejection_reason"
            )
        if (
            self.status in {CandidateStatus.PROPOSED, CandidateStatus.EXPIRED}
            and self.review_note is not None
        ):
            raise DataContractError(
                "PROPOSED/EXPIRED candidate must not set review_note"
            )


# --- Phase 1C research-memory models ---


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
        _require_entity_id(
            self.evidence_id, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE
        )
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
        _require_bounded_str(
            self.summary, field="summary", min_len=1, max_len=_SUMMARY_MAX
        )
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
        if (
            self.evidence_type is EvidenceType.CORRECTION
            and self.supersedes_evidence_id is None
        ):
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
class CaseEvidenceLink:
    link_id: str
    case_id: str
    evidence_id: str
    linked_at: datetime
    linked_by: str
    schema_version: int

    def __post_init__(self) -> None:
        _require_entity_id(self.link_id, field="link_id", prefix=EntityIdPrefix.REV)
        _require_entity_id(self.case_id, field="case_id", prefix=EntityIdPrefix.CASE)
        _require_entity_id(
            self.evidence_id, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE
        )
        require_aware_datetime(self.linked_at, field_name="linked_at")
        _require_non_blank_str(self.linked_by, field="linked_by")
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    assessment_id: str
    evidence_id: str
    case_id: str
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
        _require_entity_id(
            self.assessment_id, field="assessment_id", prefix=EntityIdPrefix.REV
        )
        _require_entity_id(
            self.evidence_id, field="evidence_id", prefix=EntityIdPrefix.EVIDENCE
        )
        _require_entity_id(self.case_id, field="case_id", prefix=EntityIdPrefix.CASE)
        _require_optional_entity_id(
            self.thesis_id, field="thesis_id", prefix=EntityIdPrefix.THESIS
        )
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
        _require_bounded_str(
            self.rationale, field="rationale", min_len=1, max_len=_RATIONALE_MAX
        )
        require_aware_datetime(self.assessed_at, field_name="assessed_at")
        _require_actor(
            self.assessed_by, field="assessed_by", allowed=_USER_AGENT_OR_CODEX
        )
        _require_actor(self.confirmed_by, field="confirmed_by", allowed=_USER_OR_AGENT)
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class ResearchReport:
    report_id: str
    case_id: str
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
        _require_entity_id(
            self.report_id, field="report_id", prefix=EntityIdPrefix.REPORT
        )
        _require_entity_id(self.case_id, field="case_id", prefix=EntityIdPrefix.CASE)
        if not isinstance(self.report_type, ResearchReportType):
            raise DataContractError(
                "report_type must be ResearchReportType",
                details={"type": type(self.report_type).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(
            self.summary, field="summary", min_len=1, max_len=_SUMMARY_MAX
        )
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
        _require_id_tuple(
            self.evidence_ids, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE
        )
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
            case_id=self.case_id,
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
    case_id: str
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
        _require_entity_id(self.case_id, field="case_id", prefix=EntityIdPrefix.CASE)
        if not isinstance(self.event_type, ResearchEventType):
            raise DataContractError(
                "event_type must be ResearchEventType",
                details={"type": type(self.event_type).__name__},
            )
        _require_bounded_str(self.title, field="title", min_len=1, max_len=_TITLE_MAX)
        _require_bounded_str(
            self.summary, field="summary", min_len=1, max_len=_SUMMARY_MAX
        )
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_string_tuple(self.instrument_ids, field="instrument_ids")
        _require_id_tuple(
            self.evidence_ids, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE
        )
        _require_id_tuple(
            self.report_ids, field="report_ids", prefix=EntityIdPrefix.REPORT
        )
        _require_related_entity_pair(self.related_entity_type, self.related_entity_id)
        _require_bounded_str(
            self.source_name, field="source_name", min_len=1, max_len=_SOURCE_NAME_MAX
        )
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    case_id: str
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
        _require_entity_id(
            self.decision_id, field="decision_id", prefix=EntityIdPrefix.DECISION
        )
        _require_entity_id(self.case_id, field="case_id", prefix=EntityIdPrefix.CASE)
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
            _require_non_blank_str(
                self.primary_instrument_id, field="primary_instrument_id"
            )
        _require_id_tuple(
            self.thesis_revision_ids,
            field="thesis_revision_ids",
            prefix=EntityIdPrefix.REV,
        )
        _require_id_tuple(
            self.evidence_ids, field="evidence_ids", prefix=EntityIdPrefix.EVIDENCE
        )
        _require_id_tuple(
            self.report_ids, field="report_ids", prefix=EntityIdPrefix.REPORT
        )
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
    case_id: str | None
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
        _require_entity_id(
            self.journal_id, field="journal_id", prefix=EntityIdPrefix.JOURNAL
        )
        _require_optional_entity_id(
            self.case_id, field="case_id", prefix=EntityIdPrefix.CASE
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
        _require_actor(
            self.authored_by, field="authored_by", allowed=_USER_AGENT_OR_CODEX
        )
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
