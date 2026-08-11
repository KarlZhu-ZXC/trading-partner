"""Append-only, deterministic Judgment Scorecard records.

The scorecard deliberately has no aggregate score.  It reports only bounded,
machine-auditable dimensions and preserves the exact Thesis revision used.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.scorecard.enums import ScorecardDimensionStatus, ScorecardStatus

JUDGMENT_SCORECARD_S0_SCHEMA_VERSION = 1
JUDGMENT_SCORECARD_S0_ALGORITHM_VERSION = "judgment-scorecard-s0-v1"
JUDGMENT_SCORECARD_S0_DIMENSION_CODES: tuple[str, ...] = (
    "REVISION_DEFINITION_COVERAGE",
    "REVISION_EVIDENCE_BALANCE",
    "EVIDENCE_RECENCY",
    "ASSUMPTION_OUTCOME",
    "THESIS_INVALIDATION_OUTCOME",
    "PLAN_MONITOR_COVERAGE",
    "PLAN_BEFORE_ACTION_INTENT",
    "TRADE_RETRO_DISCIPLINE",
)
# S1 adds one immutable dimension without changing the persisted row shape.
# The algorithm version distinguishes the canonical eight- and nine-dimension
# contracts while historical S0 rows remain readable under schema version 1.
JUDGMENT_SCORECARD_S1_SCHEMA_VERSION = 1
JUDGMENT_SCORECARD_S1_ALGORITHM_VERSION = "judgment-scorecard-s1-v1"
JUDGMENT_SCORECARD_S1_DIMENSION_CODES: tuple[str, ...] = (
    *JUDGMENT_SCORECARD_S0_DIMENSION_CODES,
    "CATALYST_OUTCOME_CALIBRATION",
)

# The unsuffixed names describe the current public contract.  Historical S0
# values use the suffixed constants and remain readable by the run invariant.
JUDGMENT_SCORECARD_DIMENSION_CODES = JUDGMENT_SCORECARD_S1_DIMENSION_CODES
JUDGMENT_SCORECARD_SCHEMA_VERSION = JUDGMENT_SCORECARD_S1_SCHEMA_VERSION
JUDGMENT_SCORECARD_ALGORITHM_VERSION = JUDGMENT_SCORECARD_S1_ALGORITHM_VERSION

_UUID7 = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_ID_RE = re.compile(rf"^(?:case|thesis|rev|scorecard)_{_UUID7}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    value = value.strip()
    if len(value) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return value


def _id(value: object, field: str) -> str:
    normalized = _text(value, field, 128)
    if _ID_RE.fullmatch(normalized) is None:
        raise DataContractError(f"{field} has an invalid entity id")
    return normalized


def _code(value: object, field: str) -> str:
    normalized = _text(value, field, 128)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", normalized):
        raise DataContractError(f"{field} must be an uppercase machine code")
    return normalized


@dataclass(frozen=True, slots=True)
class ScorecardSourceRef:
    kind: str
    entity_id: str
    version: int | None = None

    def __post_init__(self) -> None:
        _code(self.kind.upper(), "source_ref.kind")
        _text(self.entity_id, "source_ref.entity_id", 256)
        if self.version is not None and (type(self.version) is not int or self.version < 1):
            raise DataContractError("source_ref.version must be a positive integer")


@dataclass(frozen=True, slots=True)
class ScorecardDimension:
    code: str
    status: ScorecardDimensionStatus
    result_code: str
    title: str
    summary: str
    facts: tuple[tuple[str, str], ...] = ()
    source_refs: tuple[ScorecardSourceRef, ...] = ()
    limitation_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        code = _code(self.code, "dimension.code")
        if code not in JUDGMENT_SCORECARD_S1_DIMENSION_CODES:
            raise DataContractError("dimension.code is not part of the Judgment Scorecard contract")
        if not isinstance(self.status, ScorecardDimensionStatus):
            raise DataContractError("dimension.status is invalid")
        _code(self.result_code, "dimension.result_code")
        _text(self.title, "dimension.title", 200)
        _text(self.summary, "dimension.summary", 2_000)
        if len(self.facts) > 30:
            raise DataContractError("dimension supports at most 30 facts")
        keys: set[str] = set()
        for key, value in self.facts:
            normalized_key = _code(key.upper(), "dimension.fact.key")
            _text(value, "dimension.fact.value", 1_000)
            if normalized_key in keys:
                raise DataContractError("dimension fact keys must be unique")
            keys.add(normalized_key)
        if len(self.source_refs) > 50:
            raise DataContractError("dimension supports at most 50 source refs")
        if len(self.limitation_codes) > 20:
            raise DataContractError("dimension supports at most 20 limitation codes")
        for value in self.limitation_codes:
            _code(value, "dimension.limitation_code")


@dataclass(frozen=True, slots=True)
class JudgmentScorecardRun:
    scorecard_id: str
    subject_id: str
    subject_title: str
    thesis_id: str
    thesis_title: str
    thesis_revision_id: str
    thesis_revision_no: int
    generated_at: datetime
    status: ScorecardStatus
    dimensions: tuple[ScorecardDimension, ...]
    warning_codes: tuple[str, ...]
    input_fingerprint: str
    idempotency_key: str
    algorithm_version: str = JUDGMENT_SCORECARD_ALGORITHM_VERSION
    schema_version: int = JUDGMENT_SCORECARD_SCHEMA_VERSION
    execution_effect: bool = False

    def __post_init__(self) -> None:
        if not self.scorecard_id.startswith("scorecard_"):
            raise DataContractError("scorecard_id must use scorecard_ prefix")
        _id(self.subject_id, "subject_id")
        if not self.subject_id.startswith("case_"):
            raise DataContractError("subject_id must use case_ compatibility prefix")
        _id(self.thesis_id, "thesis_id")
        if not self.thesis_id.startswith("thesis_"):
            raise DataContractError("thesis_id must use thesis_ prefix")
        _id(self.thesis_revision_id, "thesis_revision_id")
        if not self.thesis_revision_id.startswith("rev_"):
            raise DataContractError("thesis_revision_id must use rev_ prefix")
        _text(self.subject_title, "subject_title", 500)
        _text(self.thesis_title, "thesis_title", 500)
        if type(self.thesis_revision_no) is not int or self.thesis_revision_no < 1:
            raise DataContractError("thesis_revision_no must be positive")
        require_aware_datetime(self.generated_at, field_name="generated_at")
        if not isinstance(self.status, ScorecardStatus):
            raise DataContractError("scorecard status is invalid")
        if self.algorithm_version == JUDGMENT_SCORECARD_S0_ALGORITHM_VERSION:
            expected_codes = JUDGMENT_SCORECARD_S0_DIMENSION_CODES
            expected_schema_version = JUDGMENT_SCORECARD_S0_SCHEMA_VERSION
            version_label = "S0"
        elif self.algorithm_version == JUDGMENT_SCORECARD_S1_ALGORITHM_VERSION:
            expected_codes = JUDGMENT_SCORECARD_S1_DIMENSION_CODES
            expected_schema_version = JUDGMENT_SCORECARD_S1_SCHEMA_VERSION
            version_label = "S1"
        else:
            raise DataContractError("unsupported Judgment Scorecard algorithm version")
        if self.schema_version != expected_schema_version:
            raise DataContractError(
                f"{version_label} scorecard requires schema version {expected_schema_version}"
            )
        if len(self.dimensions) != len(expected_codes):
            expected_count = len(expected_codes)
            raise DataContractError(
                f"{version_label} scorecard must contain exactly {expected_count} dimensions"
            )
        codes = tuple(item.code for item in self.dimensions)
        if codes != expected_codes:
            raise DataContractError(
                f"{version_label} scorecard dimensions must use canonical order"
            )
        for code in self.warning_codes:
            _code(code, "warning_code")
        if _SHA256_RE.fullmatch(self.input_fingerprint) is None:
            raise DataContractError("input_fingerprint must be a lowercase SHA-256")
        _text(self.idempotency_key, "idempotency_key", 200)
        if self.execution_effect:
            raise DataContractError("Judgment Scorecard cannot have execution effect")


def scorecard_input_fingerprint(payload: object) -> str:
    """Hash deterministic input references, never free-form provider payloads."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
