"""Shared validation and canonical content helpers for research domain models."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from domain.common.enums import (
    DecisionType,
    EvidenceOrigin,
    EvidenceType,
    ResearchReportType,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import ensure_utc

# Frozen model names for Research Subject / research state.
# Original 12 names must never be removed (Phase 1C placeholders stay).
FROZEN_RESEARCH_MODEL_NAMES: tuple[str, ...] = (
    "ResearchSubject",
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

# SubjectEvidenceLink is not a 13th frozen aggregate; supporting registry only.
FROZEN_PHASE1C_SUPPORTING_MODEL_NAMES: tuple[str, ...] = ("SubjectEvidenceLink",)

RESEARCH_SCHEMA_VERSION: int = 1

# Strict run_<uuid7> (lowercase hex + hyphens, version nibble 7, RFC4122 variant).
_UUID7_TOKEN = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_RUN_CANDIDATE_ID_RE = re.compile(rf"^{re.escape(EntityIdPrefix.RUN.value)}_{_UUID7_TOKEN}$")
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


def _require_optional_entity_id(value: str | None, *, field: str, prefix: EntityIdPrefix) -> None:
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


def _require_optional_str_max(value: str | None, *, field: str, max_len: int) -> str | None:
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


def _require_optional_unit_interval_decimal(value: object, *, field: str) -> Decimal | None:
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


def _require_related_entity_pair(entity_type: str | None, entity_id: str | None) -> None:
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


def _require_not_self_supersede(entity_id: str, supersedes_id: str | None, *, field: str) -> None:
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
        structured_value = json.loads(canonicalize_research_json_object(structured_data_json))
    payload: dict[str, object] = {
        "content_text": content_text,
        "effective_from": (None if effective_from is None else _datetime_to_utc_z(effective_from)),
        "effective_to": (None if effective_to is None else _datetime_to_utc_z(effective_to)),
        "evidence_type": evidence_type.value,
        "instrument_ids_sorted": sorted(instrument_ids),
        "origin": origin.value,
        "published_at": (None if published_at is None else _datetime_to_utc_z(published_at)),
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
    subject_id: str,
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
        # Hash ABI: historical report digests used the storage-era key name.
        "case_id": subject_id,
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
