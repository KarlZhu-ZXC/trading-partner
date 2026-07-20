"""Private helpers for Phase 1C research-memory write services (C4a + C4b2).

Not part of the public service surface. Bootstrap must inject UoW / Clock /
IdGenerator / SecretRedactor explicitly; this module holds pure normalization,
idempotency payload hashes, related-entity registries, audit-summary, and Case
JSON cache helpers only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import ConfirmationMode, DecisionType, JournalEntryType
from domain.common.errors import (
    HistoricalVisibilityViolation,
    InputValidationError,
    InvalidResearchLink,
)
from domain.common.time import ensure_utc, require_aware_datetime
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    InvestmentCase,
    canonicalize_research_json_object,
)

# Frozen Event related-entity wire types (Phase 1C C4a §8.2).
# Type ``event`` is intentionally excluded for Event writes (no self-related).
EVENT_RELATED_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "case",
        "thesis",
        "thesis_revision",
        "evidence",
        "report",
        "decision",
        "journal",
    }
)

# Frozen Journal related-entity wire types (Phase 1C C4b2 §8.5).
# ``event`` is allowed after C4b1 added ResearchEventRepository.get.
JOURNAL_RELATED_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "case",
        "thesis",
        "thesis_revision",
        "evidence",
        "report",
        "event",
        "decision",
        "journal",
    }
)


def stable_dedupe_strs(values: Sequence[str]) -> tuple[str, ...]:
    """Strip blanks, drop empties, stable-dedupe preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise InputValidationError(
                "tuple elements must be strings",
                details={"type": type(raw).__name__},
            )
        item = raw.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def stable_dedupe_topic_tags(values: Sequence[str]) -> tuple[str, ...]:
    """Strip blanks, lower-case, stable-dedupe preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise InputValidationError(
                "topic_tags elements must be strings",
                details={"type": type(raw).__name__},
            )
        item = raw.strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def prepare_topic_tags(
    values: Sequence[str], redactor: SecretRedactor
) -> tuple[str, ...]:
    """Lower/dedupe tags, redact each, then stable-dedupe again for sentinels."""
    base = stable_dedupe_topic_tags(values)
    redacted = tuple(redactor.redact_text(tag) for tag in base)
    return stable_dedupe_strs(redacted)


def redact_optional_text(
    value: str | None, redactor: SecretRedactor, *, field: str
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputValidationError(
            f"{field} must be a string or None",
            details={"field": field, "type": type(value).__name__},
        )
    return redactor.redact_text(value)


def redact_required_text(
    value: str, redactor: SecretRedactor, *, field: str
) -> str:
    if not isinstance(value, str):
        raise InputValidationError(
            f"{field} must be a string",
            details={"field": field, "type": type(value).__name__},
        )
    text = value.strip()
    if not text:
        raise InputValidationError(
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    return redactor.redact_text(text)


def prepare_structured_data_json(
    value: str | None, redactor: SecretRedactor
) -> str | None:
    """Parse JSON object, deep-redact mapping, canonicalize for hash/storage."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputValidationError(
            "structured_data_json must be a string or None",
            details={"type": type(value).__name__},
        )
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        raise InputValidationError(
            "structured_data_json is not valid JSON",
            details={"field": "structured_data_json", "rule": "malformed_json"},
        ) from None
    if not isinstance(parsed, dict):
        raise InputValidationError(
            "structured_data_json must be a JSON object",
            details={
                "field": "structured_data_json",
                "rule": "json_object",
                "type": type(parsed).__name__,
            },
        )
    redacted = redactor.redact_mapping(parsed)
    interim = json.dumps(redacted, ensure_ascii=False, allow_nan=False)
    return canonicalize_research_json_object(interim)


def _netloc_without_userinfo(parsed: Any) -> str:
    host = parsed.hostname
    if host is None:
        raise InputValidationError(
            "source_url host is required",
            details={"field": "source_url", "rule": "host_required"},
        )
    # IPv6 hosts need brackets when a port is present.
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if parsed.port is not None:
        return f"{host_part}:{parsed.port}"
    return host_part


def normalize_source_url(
    value: str | None, redactor: SecretRedactor
) -> str | None:
    """Validate scheme/userinfo, drop fragment, redact secret query keys.

    Query pairs are redacted via the injected SecretRedactor (one-key mapping)
    so C4a never duplicates secret-key rules. URL authority userinfo is
    rejected outright rather than redacted-then-stored.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputValidationError(
            "source_url must be a string or None",
            details={"type": type(value).__name__},
        )
    raw = value.strip()
    if not raw:
        raise InputValidationError(
            "source_url must be non-blank when provided",
            details={"field": "source_url"},
        )
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise InputValidationError(
            "source_url scheme must be http or https",
            details={"field": "source_url", "rule": "scheme"},
        )
    if parsed.username is not None or parsed.password is not None:
        raise InputValidationError(
            "source_url must not include username or password",
            details={"field": "source_url", "rule": "userinfo_forbidden"},
        )
    # Defensive: reject residual userinfo markers in netloc.
    if "@" in (parsed.netloc or ""):
        raise InputValidationError(
            "source_url must not include username or password",
            details={"field": "source_url", "rule": "userinfo_forbidden"},
        )

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    redacted_pairs: list[tuple[str, str]] = []
    for key, val in query_pairs:
        # One pair at a time so secret-key detection stays in SecretRedactor.
        redacted_map = redactor.redact_mapping({key: val})
        redacted_val = redacted_map[key]
        if redacted_val is None:
            redacted_pairs.append((key, ""))
        else:
            redacted_pairs.append((key, str(redacted_val)))
    # Stable order: sort by key, then original relative order among ties via enumerate.
    redacted_pairs.sort(key=lambda item: item[0])
    # Keep redaction sentinel asterisks unescaped for readable secret markers.
    query = urlencode(redacted_pairs, doseq=False, safe="*")
    netloc = _netloc_without_userinfo(parsed)
    # Fragment always dropped; params rarely used but preserved.
    rebuilt = urlunparse(
        (scheme, netloc, parsed.path, parsed.params, query, "")
    )
    return rebuilt


def require_aware_optional(
    value: datetime | None, *, field: str
) -> datetime | None:
    if value is None:
        return None
    return require_aware_datetime(value, field_name=field)


def append_unique_id(
    existing: tuple[str, ...], new_id: str
) -> tuple[str, ...]:
    if new_id in existing:
        return existing
    return existing + (new_id,)


def _rebuild_case(
    case: InvestmentCase,
    *,
    updated_at: datetime,
    evidence_ids: tuple[str, ...] | None = None,
    report_ids: tuple[str, ...] | None = None,
    event_ids: tuple[str, ...] | None = None,
    decision_ids: tuple[str, ...] | None = None,
) -> InvestmentCase:
    return InvestmentCase(
        case_id=case.case_id,
        case_type=case.case_type,
        title=case.title,
        summary=case.summary,
        status=case.status,
        primary_instrument_id=case.primary_instrument_id,
        topic_tags=case.topic_tags,
        created_at=case.created_at,
        updated_at=updated_at,
        created_by=case.created_by,
        archived_at=case.archived_at,
        archived_reason=case.archived_reason,
        linked_case_ids=case.linked_case_ids,
        evidence_ids=case.evidence_ids if evidence_ids is None else evidence_ids,
        report_ids=case.report_ids if report_ids is None else report_ids,
        event_ids=case.event_ids if event_ids is None else event_ids,
        decision_ids=case.decision_ids if decision_ids is None else decision_ids,
        schema_version=case.schema_version,
    )


def update_case_evidence_cache(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    evidence_id: str,
    updated_at: datetime,
) -> None:
    """Append evidence_id to Case cache; always set updated_at to write time."""
    require_aware_datetime(updated_at, field_name="updated_at")
    case = uow.cases.get(case_id)
    next_ids = append_unique_id(case.evidence_ids, evidence_id)
    if next_ids == case.evidence_ids and case.updated_at == updated_at:
        return
    uow.cases.update(
        _rebuild_case(case, updated_at=updated_at, evidence_ids=next_ids)
    )


def update_case_report_cache(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    report_id: str,
    updated_at: datetime,
) -> None:
    """Append report_id to Case cache; always set updated_at to write time."""
    require_aware_datetime(updated_at, field_name="updated_at")
    case = uow.cases.get(case_id)
    next_ids = append_unique_id(case.report_ids, report_id)
    if next_ids == case.report_ids and case.updated_at == updated_at:
        return
    uow.cases.update(
        _rebuild_case(case, updated_at=updated_at, report_ids=next_ids)
    )


def update_case_event_cache(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    event_id: str,
    updated_at: datetime,
) -> None:
    """Append event_id to Case cache; always set updated_at to write time."""
    require_aware_datetime(updated_at, field_name="updated_at")
    case = uow.cases.get(case_id)
    next_ids = append_unique_id(case.event_ids, event_id)
    if next_ids == case.event_ids and case.updated_at == updated_at:
        return
    uow.cases.update(
        _rebuild_case(case, updated_at=updated_at, event_ids=next_ids)
    )


def update_case_decision_cache(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    decision_id: str,
    updated_at: datetime,
) -> None:
    """Append decision_id to Case cache; set updated_at to recorded_at."""
    require_aware_datetime(updated_at, field_name="updated_at")
    case = uow.cases.get(case_id)
    next_ids = append_unique_id(case.decision_ids, decision_id)
    if next_ids == case.decision_ids and case.updated_at == updated_at:
        return
    uow.cases.update(
        _rebuild_case(case, updated_at=updated_at, decision_ids=next_ids)
    )


def audit_summary(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    case_id: str | None,
    actor: str,
    confirmed_by: str | None = None,
    content_sha256: str | None = None,
    linked_entity_ids: tuple[str, ...] | list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    """Build §13 audit payload; never include bodies, URLs, or rationale."""
    return {
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "case_id": case_id,
        "actor": actor,
        "confirmed_by": confirmed_by,
        "idempotency_key": idempotency_key,
        "content_sha256": content_sha256,
        "linked_entity_ids": (
            list(linked_entity_ids) if linked_entity_ids is not None else []
        ),
    }


def _datetime_to_utc_z(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def _canonical_payload_sha256(payload: dict[str, object]) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InputValidationError(
            "idempotency payload is not JSON-serializable",
            details={"rule": "json_serialize"},
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_journal_idempotency_payload_sha256(
    *,
    case_id: str | None,
    entry_type: JournalEntryType,
    title: str,
    body_markdown: str,
    authored_by: str,
    confirmed_by: str,
    instrument_ids: tuple[str, ...],
    topic_tags: tuple[str, ...],
    related_entity_type: str | None,
    related_entity_id: str | None,
    supersedes_journal_id: str | None,
) -> str:
    """Canonical SHA-256 of redacted/normalized Journal caller payload (§8.5)."""
    if not isinstance(entry_type, JournalEntryType):
        raise InputValidationError(
            "entry_type must be JournalEntryType",
            details={"type": type(entry_type).__name__},
        )
    # Domain tuples keep first-seen order; hash sorts set fields only (§8.5).
    payload: dict[str, object] = {
        "authored_by": authored_by,
        "body_markdown": body_markdown,
        "case_id": case_id,
        "confirmed_by": confirmed_by,
        "entry_type": entry_type.value,
        "instrument_ids": sorted(instrument_ids),
        "related_entity_id": related_entity_id,
        "related_entity_type": related_entity_type,
        "supersedes_journal_id": supersedes_journal_id,
        "title": title,
        "topic_tags": sorted(topic_tags),
    }
    return _canonical_payload_sha256(payload)


def compute_decision_idempotency_payload_sha256(
    *,
    case_id: str,
    decision_type: DecisionType,
    title: str,
    rationale: str,
    decided_at: datetime,
    decided_by: str,
    confirmation_mode: ConfirmationMode,
    primary_instrument_id: str | None,
    thesis_revision_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    report_ids: tuple[str, ...],
    supersedes_decision_id: str | None,
    position_context_snapshot_id: str | None,
) -> str:
    """Canonical SHA-256 of redacted/normalized Decision caller payload (§8.6)."""
    if not isinstance(decision_type, DecisionType):
        raise InputValidationError(
            "decision_type must be DecisionType",
            details={"type": type(decision_type).__name__},
        )
    if not isinstance(confirmation_mode, ConfirmationMode):
        raise InputValidationError(
            "confirmation_mode must be ConfirmationMode",
            details={"type": type(confirmation_mode).__name__},
        )
    require_aware_datetime(decided_at, field_name="decided_at")
    # Domain tuples keep first-seen order; hash sorts set fields only (§8.6).
    payload: dict[str, object] = {
        "case_id": case_id,
        "confirmation_mode": confirmation_mode.value,
        "decided_at": _datetime_to_utc_z(decided_at),
        "decided_by": decided_by,
        "decision_type": decision_type.value,
        "evidence_ids": sorted(evidence_ids),
        "position_context_snapshot_id": position_context_snapshot_id,
        "primary_instrument_id": primary_instrument_id,
        "rationale": rationale,
        "report_ids": sorted(report_ids),
        "supersedes_decision_id": supersedes_decision_id,
        "thesis_revision_ids": sorted(thesis_revision_ids),
        "title": title,
    }
    return _canonical_payload_sha256(payload)


def validate_report_references(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    as_of: datetime,
    created_at: datetime,
    evidence_ids: tuple[str, ...],
    thesis_revision_ids: tuple[str, ...],
    supersedes_report_id: str | None,
) -> None:
    """Application-level Case membership + historical visibility for Report."""
    require_aware_datetime(as_of, field_name="as_of")
    require_aware_datetime(created_at, field_name="created_at")
    uow.cases.get(case_id)

    for evidence_id in evidence_ids:
        evidence = uow.evidence.get(evidence_id)
        if evidence.observed_at > as_of:
            raise HistoricalVisibilityViolation(
                "report as_of must not precede evidence observed_at",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )
        if not uow.case_evidence_links.exists(case_id, evidence_id):
            raise InvalidResearchLink(
                "evidence must be linked to the report case",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )
        link = uow.case_evidence_links.get(case_id, evidence_id)
        # Frozen rule: Link.linked_at <= report created_at (not as_of).
        if link.linked_at > created_at:
            raise HistoricalVisibilityViolation(
                "report created_at must not precede case evidence link linked_at",
                details={
                    "entity_type": "case_evidence_link",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )

    for revision_id in thesis_revision_ids:
        revision = uow.revisions.get(revision_id)
        if revision.case_id != case_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the report case",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "case_id": case_id,
                },
            )
        if revision.confirmed_at > as_of:
            raise HistoricalVisibilityViolation(
                "report as_of must not precede thesis revision confirmed_at",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "case_id": case_id,
                },
            )

    if supersedes_report_id is not None:
        old = uow.reports.get(supersedes_report_id)
        if old.case_id != case_id:
            raise InvalidResearchLink(
                "superseded report does not belong to the same case",
                details={
                    "entity_type": "report",
                    "supersedes_report_id": supersedes_report_id,
                    "case_id": case_id,
                },
            )
        if old.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "superseded report created_at must be <= new created_at",
                details={
                    "entity_type": "report",
                    "supersedes_report_id": supersedes_report_id,
                },
            )


def validate_event_references(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    recorded_at: datetime,
    evidence_ids: tuple[str, ...],
    report_ids: tuple[str, ...],
) -> None:
    """Reject cross-Case references and future leakage for Event writes."""
    require_aware_datetime(recorded_at, field_name="recorded_at")
    uow.cases.get(case_id)

    for evidence_id in evidence_ids:
        evidence = uow.evidence.get(evidence_id)
        if evidence.observed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede evidence observed_at",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )
        if not uow.case_evidence_links.exists(case_id, evidence_id):
            raise InvalidResearchLink(
                "evidence must be linked to the event case",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )
        link = uow.case_evidence_links.get(case_id, evidence_id)
        if link.linked_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede case evidence link linked_at",
                details={
                    "entity_type": "case_evidence_link",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )

    for report_id in report_ids:
        report = uow.reports.get(report_id)
        if report.case_id != case_id:
            raise InvalidResearchLink(
                "report does not belong to the event case",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "case_id": case_id,
                },
            )
        if report.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede report created_at",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "case_id": case_id,
                },
            )


def validate_event_related_entity(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    recorded_at: datetime,
    related_entity_type: str | None,
    related_entity_id: str | None,
) -> None:
    """Typed registry for Event generic related pair (design §8.2).

    Uses existing repository ports only. Rejects unknown types and ``event``.
    """
    require_aware_datetime(recorded_at, field_name="recorded_at")
    if related_entity_type is None and related_entity_id is None:
        return
    if related_entity_type is None or related_entity_id is None:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must both be set or both null",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )

    rel_type = related_entity_type.strip()
    rel_id = related_entity_id.strip()
    if not rel_type or not rel_id:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must be non-blank when set",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )

    if rel_type == "event":
        raise InvalidResearchLink(
            "event related_entity_type is not allowed in Phase 1C",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
            },
        )
    if rel_type not in EVENT_RELATED_ENTITY_TYPES:
        raise InvalidResearchLink(
            "unknown related_entity_type for research event",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "allowed": sorted(EVENT_RELATED_ENTITY_TYPES),
            },
        )

    if rel_type == "case":
        if rel_id != case_id:
            raise InvalidResearchLink(
                "related case must equal the event case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        uow.cases.get(rel_id)
        return

    if rel_type == "thesis":
        thesis = uow.theses.get(rel_id)
        if thesis.case_id != case_id:
            raise InvalidResearchLink(
                "related thesis does not belong to the event case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if thesis.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede thesis created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "thesis_revision":
        revision = uow.revisions.get(rel_id)
        if revision.case_id != case_id:
            raise InvalidResearchLink(
                "related thesis revision does not belong to the event case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if revision.confirmed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede thesis revision confirmed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "evidence":
        evidence = uow.evidence.get(rel_id)
        if evidence.observed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related evidence observed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if not uow.case_evidence_links.exists(case_id, rel_id):
            raise InvalidResearchLink(
                "related evidence must be linked to the event case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        link = uow.case_evidence_links.get(case_id, rel_id)
        if link.linked_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related evidence link linked_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "report":
        report = uow.reports.get(rel_id)
        if report.case_id != case_id:
            raise InvalidResearchLink(
                "related report does not belong to the event case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if report.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related report created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "decision":
        decision = uow.decisions.get(rel_id)
        if decision.case_id != case_id:
            raise InvalidResearchLink(
                "related decision does not belong to the event case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if decision.recorded_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related decision recorded_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    # journal
    journal = uow.journal.get(rel_id)
    if journal.case_id is None:
        raise InvalidResearchLink(
            "global journal cannot be related to a case-scoped event",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "case_id": case_id,
            },
        )
    if journal.case_id != case_id:
        raise InvalidResearchLink(
            "related journal does not belong to the event case",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "case_id": case_id,
            },
        )
    if journal.created_at > recorded_at:
        raise HistoricalVisibilityViolation(
            "event recorded_at must not precede related journal created_at",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "case_id": case_id,
            },
        )


def _normalize_related_pair(
    related_entity_type: str | None,
    related_entity_id: str | None,
) -> tuple[str | None, str | None]:
    if related_entity_type is None and related_entity_id is None:
        return None, None
    if related_entity_type is None or related_entity_id is None:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must both be set or both null",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )
    rel_type = related_entity_type.strip()
    rel_id = related_entity_id.strip()
    if not rel_type or not rel_id:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must be non-blank when set",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )
    return rel_type, rel_id


def validate_journal_related_entity(
    uow: ResearchUnitOfWork,
    *,
    case_id: str | None,
    created_at: datetime,
    related_entity_type: str | None,
    related_entity_id: str | None,
) -> None:
    """Typed registry for Journal generic related pair (design §8.5).

    Case-scoped Journal: related entity must share the case and be visible at
    ``created_at``. Global Journal (``case_id is None``) may only relate to
    another global Journal. Evidence also requires a visible Case link.
    """
    require_aware_datetime(created_at, field_name="created_at")
    rel_type, rel_id = _normalize_related_pair(
        related_entity_type, related_entity_id
    )
    if rel_type is None or rel_id is None:
        return

    if rel_type not in JOURNAL_RELATED_ENTITY_TYPES:
        raise InvalidResearchLink(
            "unknown related_entity_type for journal entry",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "allowed": sorted(JOURNAL_RELATED_ENTITY_TYPES),
            },
        )

    # Global journal: only other global journals are allowed.
    if case_id is None:
        if rel_type != "journal":
            raise InvalidResearchLink(
                "global journal may only relate to a global journal",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": None,
                },
            )
        journal = uow.journal.get(rel_id)
        if journal.case_id is not None:
            raise InvalidResearchLink(
                "global journal may only relate to a global journal",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "related_case_id": journal.case_id,
                },
            )
        if journal.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related journal created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                },
            )
        return

    # Case-scoped journal related registry.
    if rel_type == "case":
        if rel_id != case_id:
            raise InvalidResearchLink(
                "related case must equal the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        uow.cases.get(rel_id)
        return

    if rel_type == "thesis":
        thesis = uow.theses.get(rel_id)
        if thesis.case_id != case_id:
            raise InvalidResearchLink(
                "related thesis does not belong to the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if thesis.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede thesis created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "thesis_revision":
        revision = uow.revisions.get(rel_id)
        if revision.case_id != case_id:
            raise InvalidResearchLink(
                "related thesis revision does not belong to the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if revision.confirmed_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede thesis revision confirmed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "evidence":
        evidence = uow.evidence.get(rel_id)
        if evidence.observed_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related evidence observed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if not uow.case_evidence_links.exists(case_id, rel_id):
            raise InvalidResearchLink(
                "related evidence must be linked to the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        link = uow.case_evidence_links.get(case_id, rel_id)
        if link.linked_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related evidence link linked_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "report":
        report = uow.reports.get(rel_id)
        if report.case_id != case_id:
            raise InvalidResearchLink(
                "related report does not belong to the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if report.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related report created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "event":
        event = uow.events.get(rel_id)
        if event.case_id != case_id:
            raise InvalidResearchLink(
                "related event does not belong to the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if event.recorded_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related event recorded_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    if rel_type == "decision":
        decision = uow.decisions.get(rel_id)
        if decision.case_id != case_id:
            raise InvalidResearchLink(
                "related decision does not belong to the journal case",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        if decision.recorded_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related decision recorded_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "case_id": case_id,
                },
            )
        return

    # journal
    journal = uow.journal.get(rel_id)
    if journal.case_id is None:
        raise InvalidResearchLink(
            "global journal cannot be related to a case-scoped journal",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "case_id": case_id,
            },
        )
    if journal.case_id != case_id:
        raise InvalidResearchLink(
            "related journal does not belong to the journal case",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "case_id": case_id,
            },
        )
    if journal.created_at > created_at:
        raise HistoricalVisibilityViolation(
            "journal created_at must not precede related journal created_at",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "case_id": case_id,
            },
        )


def validate_journal_supersedes(
    uow: ResearchUnitOfWork,
    *,
    case_id: str | None,
    created_at: datetime,
    supersedes_journal_id: str | None,
) -> None:
    """Superseded journal must exist, share case (incl. both None), and be older."""
    if supersedes_journal_id is None:
        return
    require_aware_datetime(created_at, field_name="created_at")
    old = uow.journal.get(supersedes_journal_id)
    if old.case_id != case_id:
        raise InvalidResearchLink(
            "superseded journal does not share the same case_id",
            details={
                "entity_type": "journal",
                "supersedes_journal_id": supersedes_journal_id,
                "case_id": case_id,
                "old_case_id": old.case_id,
            },
        )
    if old.created_at > created_at:
        raise HistoricalVisibilityViolation(
            "superseded journal created_at must be <= new created_at",
            details={
                "entity_type": "journal",
                "supersedes_journal_id": supersedes_journal_id,
            },
        )


def validate_decision_references(
    uow: ResearchUnitOfWork,
    *,
    case_id: str,
    recorded_at: datetime,
    thesis_revision_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    report_ids: tuple[str, ...],
    supersedes_decision_id: str | None,
) -> None:
    """Application-level Case membership + historical visibility for Decision."""
    require_aware_datetime(recorded_at, field_name="recorded_at")
    uow.cases.get(case_id)

    for revision_id in thesis_revision_ids:
        revision = uow.revisions.get(revision_id)
        if revision.case_id != case_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the decision case",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "case_id": case_id,
                },
            )
        if revision.confirmed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede thesis revision confirmed_at",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "case_id": case_id,
                },
            )

    for evidence_id in evidence_ids:
        evidence = uow.evidence.get(evidence_id)
        if evidence.observed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede evidence observed_at",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )
        if not uow.case_evidence_links.exists(case_id, evidence_id):
            raise InvalidResearchLink(
                "evidence must be linked to the decision case",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )
        link = uow.case_evidence_links.get(case_id, evidence_id)
        if link.linked_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede case evidence link linked_at",
                details={
                    "entity_type": "case_evidence_link",
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                },
            )

    for report_id in report_ids:
        report = uow.reports.get(report_id)
        if report.case_id != case_id:
            raise InvalidResearchLink(
                "report does not belong to the decision case",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "case_id": case_id,
                },
            )
        if report.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede report created_at",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "case_id": case_id,
                },
            )

    if supersedes_decision_id is not None:
        old = uow.decisions.get(supersedes_decision_id)
        if old.case_id != case_id:
            raise InvalidResearchLink(
                "superseded decision does not belong to the same case",
                details={
                    "entity_type": "decision",
                    "supersedes_decision_id": supersedes_decision_id,
                    "case_id": case_id,
                },
            )
        if old.recorded_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "superseded decision recorded_at must be <= new recorded_at",
                details={
                    "entity_type": "decision",
                    "supersedes_decision_id": supersedes_decision_id,
                },
            )


def schema_version() -> int:
    return RESEARCH_SCHEMA_VERSION
