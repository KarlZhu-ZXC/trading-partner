"""Private helpers for Phase 1C research-memory write services (C4a + C4b2).

Not part of the public service surface. Bootstrap must inject UoW / Clock /
IdGenerator / SecretRedactor explicitly; this module holds pure normalization,
idempotency payload hashes, audit-summary, and Research Subject JSON cache helpers only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import application.services._research_reference_validation as _reference_validation
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import (
    ConfirmationMode,
    DecisionScenario,
    DecisionType,
    JournalEntryType,
)
from domain.common.errors import InputValidationError
from domain.common.time import ensure_utc, require_aware_datetime
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    ResearchSubject,
    canonicalize_research_json_object,
)

EVENT_RELATED_ENTITY_TYPES = _reference_validation.EVENT_RELATED_ENTITY_TYPES
JOURNAL_RELATED_ENTITY_TYPES = _reference_validation.JOURNAL_RELATED_ENTITY_TYPES
validate_decision_references = _reference_validation.validate_decision_references
validate_event_references = _reference_validation.validate_event_references
validate_event_related_entity = _reference_validation.validate_event_related_entity
validate_journal_related_entity = _reference_validation.validate_journal_related_entity
validate_journal_supersedes = _reference_validation.validate_journal_supersedes
validate_report_references = _reference_validation.validate_report_references


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
    """Strip blanks, lower-subject, stable-dedupe preserving first-seen order."""
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


def prepare_topic_tags(values: Sequence[str], redactor: SecretRedactor) -> tuple[str, ...]:
    """Lower/dedupe tags, redact each, then stable-dedupe again for sentinels."""
    base = stable_dedupe_topic_tags(values)
    redacted = tuple(redactor.redact_text(tag) for tag in base)
    return stable_dedupe_strs(redacted)


def redact_optional_text(value: str | None, redactor: SecretRedactor, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputValidationError(
            f"{field} must be a string or None",
            details={"field": field, "type": type(value).__name__},
        )
    return redactor.redact_text(value)


def redact_required_text(value: str, redactor: SecretRedactor, *, field: str) -> str:
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


def prepare_structured_data_json(value: str | None, redactor: SecretRedactor) -> str | None:
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


def normalize_source_url(value: str | None, redactor: SecretRedactor) -> str | None:
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
    rebuilt = urlunparse((scheme, netloc, parsed.path, parsed.params, query, ""))
    return rebuilt


def require_aware_optional(value: datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    return require_aware_datetime(value, field_name=field)


def append_unique_id(existing: tuple[str, ...], new_id: str) -> tuple[str, ...]:
    if new_id in existing:
        return existing
    return existing + (new_id,)


def _rebuild_subject(
    subject: ResearchSubject,
    *,
    updated_at: datetime,
    evidence_ids: tuple[str, ...] | None = None,
    report_ids: tuple[str, ...] | None = None,
    event_ids: tuple[str, ...] | None = None,
    decision_ids: tuple[str, ...] | None = None,
) -> ResearchSubject:
    return ResearchSubject(
        subject_id=subject.subject_id,
        subject_type=subject.subject_type,
        title=subject.title,
        summary=subject.summary,
        status=subject.status,
        primary_instrument_id=subject.primary_instrument_id,
        topic_tags=subject.topic_tags,
        created_at=subject.created_at,
        updated_at=updated_at,
        created_by=subject.created_by,
        archived_at=subject.archived_at,
        archived_reason=subject.archived_reason,
        linked_subject_ids=subject.linked_subject_ids,
        evidence_ids=subject.evidence_ids if evidence_ids is None else evidence_ids,
        report_ids=subject.report_ids if report_ids is None else report_ids,
        event_ids=subject.event_ids if event_ids is None else event_ids,
        decision_ids=subject.decision_ids if decision_ids is None else decision_ids,
        schema_version=subject.schema_version,
    )


def update_subject_evidence_cache(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    evidence_id: str,
    updated_at: datetime,
) -> None:
    """Append evidence_id to Research Subject cache; always set updated_at to write time."""
    require_aware_datetime(updated_at, field_name="updated_at")
    subject = uow.subjects.get(subject_id)
    next_ids = append_unique_id(subject.evidence_ids, evidence_id)
    if next_ids == subject.evidence_ids and subject.updated_at == updated_at:
        return
    uow.subjects.update(_rebuild_subject(subject, updated_at=updated_at, evidence_ids=next_ids))


def update_subject_report_cache(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    report_id: str,
    updated_at: datetime,
) -> None:
    """Append report_id to Research Subject cache; always set updated_at to write time."""
    require_aware_datetime(updated_at, field_name="updated_at")
    subject = uow.subjects.get(subject_id)
    next_ids = append_unique_id(subject.report_ids, report_id)
    if next_ids == subject.report_ids and subject.updated_at == updated_at:
        return
    uow.subjects.update(_rebuild_subject(subject, updated_at=updated_at, report_ids=next_ids))


def update_subject_event_cache(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    event_id: str,
    updated_at: datetime,
) -> None:
    """Append event_id to Research Subject cache; always set updated_at to write time."""
    require_aware_datetime(updated_at, field_name="updated_at")
    subject = uow.subjects.get(subject_id)
    next_ids = append_unique_id(subject.event_ids, event_id)
    if next_ids == subject.event_ids and subject.updated_at == updated_at:
        return
    uow.subjects.update(_rebuild_subject(subject, updated_at=updated_at, event_ids=next_ids))


def update_subject_decision_cache(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    decision_id: str,
    updated_at: datetime,
) -> None:
    """Append decision_id to Research Subject cache; set updated_at to recorded_at."""
    require_aware_datetime(updated_at, field_name="updated_at")
    subject = uow.subjects.get(subject_id)
    next_ids = append_unique_id(subject.decision_ids, decision_id)
    if next_ids == subject.decision_ids and subject.updated_at == updated_at:
        return
    uow.subjects.update(_rebuild_subject(subject, updated_at=updated_at, decision_ids=next_ids))


def audit_summary(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    subject_id: str | None,
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
        "subject_id": subject_id,
        "actor": actor,
        "confirmed_by": confirmed_by,
        "idempotency_key": idempotency_key,
        "content_sha256": content_sha256,
        "linked_entity_ids": (list(linked_entity_ids) if linked_entity_ids is not None else []),
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
    subject_id: str | None,
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
        "subject_id": subject_id,
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
    subject_id: str,
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
    strategy_code: str | None = None,
    strategy_version: str | None = None,
    scenario: DecisionScenario | None = None,
    trade_plan_id: str | None = None,
    trade_plan_version: int | None = None,
    review_due_at: datetime | None = None,
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
        "subject_id": subject_id,
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
    # Preserve the original Phase 1C digest for legacy records that have no
    # Phase 4A fields. Once any structured field is supplied, include the full
    # optional set so changing any one field is an idempotency conflict.
    if any(
        value is not None
        for value in (
            strategy_code,
            strategy_version,
            scenario,
            trade_plan_id,
            trade_plan_version,
            review_due_at,
        )
    ):
        payload.update(
            {
                "review_due_at": (
                    _datetime_to_utc_z(review_due_at) if review_due_at is not None else None
                ),
                "scenario": (
                    scenario.value if isinstance(scenario, DecisionScenario) else scenario
                ),
                "strategy_code": strategy_code,
                "strategy_version": strategy_version,
                "trade_plan_id": trade_plan_id,
                "trade_plan_version": trade_plan_version,
            }
        )
    return _canonical_payload_sha256(payload)


def schema_version() -> int:
    return RESEARCH_SCHEMA_VERSION
