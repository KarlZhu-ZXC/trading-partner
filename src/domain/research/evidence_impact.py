"""Durable evidence-to-assumption references.

The source reference deliberately contains identifiers only.  Source metadata is
resolved by the persistence port at a bounded historical ``as_of`` time so a
later edit to a note, monitor definition, or provider payload cannot rewrite a
confirmed impact record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.validation import _require_entity_id

_SOURCE_KINDS = frozenset({"EVIDENCE", "OBSERVATION", "MONITOR"})
_STANCES = frozenset({"supports", "contradicts", "uncertain"})
_STATES = frozenset({"ACTIVE", "RETRACTED"})
_ACTORS = frozenset({"user", "external_agent"})


def _bounded_text(value: object, *, field: str, maximum: int, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string",
            details={"field": field, "type": type(value).__name__},
        )
    if not allow_blank and not value.strip():
        raise DataContractError(f"{field} must be non-blank", details={"field": field})
    if len(value) > maximum:
        raise DataContractError(
            f"{field} length must be <= {maximum}",
            details={"field": field, "length": len(value), "maximum": maximum},
        )
    return value


def _bounded_codes(value: object, *, field: str, maximum_count: int = 100) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(
            f"{field} must be a tuple",
            details={"field": field, "type": type(value).__name__},
        )
    if len(value) > maximum_count:
        raise DataContractError(
            f"{field} has too many items",
            details={"field": field, "maximum": maximum_count},
        )
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or len(item) > 128:
            raise DataContractError(
                f"{field} items must be bounded non-blank strings",
                details={"field": field, "index": index},
            )
        if item in seen:
            raise DataContractError(
                f"{field} must not contain duplicates",
                details={"field": field, "duplicate": item},
            )
        seen.add(item)
    return value


@dataclass(frozen=True, slots=True)
class ResearchSourceRef:
    """Immutable identifier for one historical research source.

    ``MONITOR`` references are three-part references because a run may contain
    several rules and a monitor definition may have multiple historical
    versions.  For the other source kinds the monitor-specific fields are
    forbidden by construction.
    """

    kind: str
    source_id: str
    monitor_id: str | None = None
    rule_code: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _SOURCE_KINDS:
            raise DataContractError(
                "ResearchSourceRef.kind is invalid",
                details={"kind": self.kind, "allowed": sorted(_SOURCE_KINDS)},
            )
        if self.kind == "EVIDENCE":
            _require_entity_id(self.source_id, field="source_id", prefix=EntityIdPrefix.EVIDENCE)
        elif self.kind == "OBSERVATION":
            _require_entity_id(
                self.source_id,
                field="source_id",
                prefix=EntityIdPrefix.EXTERNAL_NOTE_REVISION,
            )
        else:
            _require_entity_id(
                self.source_id,
                field="source_id",
                prefix=EntityIdPrefix.MONITOR_RUN,
            )
            if self.monitor_id is None or self.rule_code is None:
                raise DataContractError(
                    "MONITOR source requires monitor_id and rule_code"
                )
            _require_entity_id(
                self.monitor_id,
                field="monitor_id",
                prefix=EntityIdPrefix.MONITOR,
            )
            _bounded_text(self.rule_code, field="rule_code", maximum=128)
        if self.kind != "MONITOR" and (self.monitor_id is not None or self.rule_code is not None):
            raise DataContractError(
                "monitor_id and rule_code are only valid for MONITOR sources"
            )


@dataclass(frozen=True, slots=True)
class ResearchSourceSnapshot:
    """Metadata returned for a source visible at one historical time."""

    source: ResearchSourceRef
    title: str
    fact_at: datetime | None
    recorded_at: datetime
    origin: str
    warning_codes: tuple[str, ...] = ()
    source_name: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, ResearchSourceRef):
            raise DataContractError("ResearchSourceSnapshot.source is invalid")
        _bounded_text(self.title, field="title", maximum=500)
        if self.fact_at is not None:
            require_aware_datetime(self.fact_at, field_name="fact_at")
        require_aware_datetime(self.recorded_at, field_name="recorded_at")
        _bounded_text(self.origin, field="origin", maximum=200)
        _bounded_codes(self.warning_codes, field="warning_codes")
        if self.source_name is not None:
            _bounded_text(self.source_name, field="source_name", maximum=500)
        if self.source_url is not None:
            _bounded_text(self.source_url, field="source_url", maximum=4000)


@dataclass(frozen=True, slots=True)
class EvidenceImpact:
    """One confirmed, append-only interpretation of source impact."""

    impact_id: str
    version: int
    subject_id: str
    thesis_id: str
    thesis_revision_id: str
    assumption_id: str
    source: ResearchSourceRef
    stance: str
    rationale: str
    state: str
    confirmed_at: datetime
    confirmed_by: str
    candidate_id: str

    def __post_init__(self) -> None:
        _require_entity_id(self.impact_id, field="impact_id", prefix=EntityIdPrefix.EVIDENCE_IMPACT)
        if type(self.version) is not int or self.version < 1:
            raise DataContractError("version must be an integer >= 1")
        _require_entity_id(self.subject_id, field="subject_id", prefix=EntityIdPrefix.SUBJECT)
        _require_entity_id(self.thesis_id, field="thesis_id", prefix=EntityIdPrefix.THESIS)
        _require_entity_id(
            self.thesis_revision_id,
            field="thesis_revision_id",
            prefix=EntityIdPrefix.REV,
        )
        _bounded_text(self.assumption_id, field="assumption_id", maximum=128)
        if not isinstance(self.source, ResearchSourceRef):
            raise DataContractError("source must be ResearchSourceRef")
        if self.stance not in _STANCES:
            raise DataContractError("stance must be supports, contradicts, or uncertain")
        _bounded_text(self.rationale, field="rationale", maximum=4_000)
        if self.state not in _STATES:
            raise DataContractError("state must be ACTIVE or RETRACTED")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")
        if self.confirmed_by not in _ACTORS:
            raise DataContractError("confirmed_by must be user or external_agent")
        _require_entity_id(self.candidate_id, field="candidate_id", prefix=EntityIdPrefix.RUN)


__all__ = ["EvidenceImpact", "ResearchSourceRef", "ResearchSourceSnapshot"]
