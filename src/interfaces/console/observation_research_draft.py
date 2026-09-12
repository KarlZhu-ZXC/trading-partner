"""Pure projection for one exact Observation research draft."""

from __future__ import annotations

import json
from typing import Any

from application.dto.external_note_review import ExternalNoteReviewDraftDTO
from application.services.external_note_interpretation_service import (
    NoteInterpretationDraft,
    validate_note_interpretation_attribution,
)
from application.services.external_note_sync_service import ExternalNoteInboxItem
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteInterpretation, ExternalNoteRevision


def _source(
    item: ExternalNoteInboxItem,
    *,
    analysis_kind: str | None = None,
    analysis_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    revision = item.revision
    return {
        "note_revision_id": revision.note_revision_id,
        "note_id": revision.note_id,
        "note_version": revision.version,
        "title": revision.title,
        "instrument_id": item.identity.primary_instrument_id,
        "observed_at": revision.observed_at.isoformat(),
        "analysis_kind": analysis_kind,
        "analysis_id": analysis_id,
        "provider": provider,
        "model": model,
        "created_at": created_at,
    }


def _parse_payload(
    payload: object,
    revision: ExternalNoteRevision,
) -> dict[str, Any] | None:
    parsed = NoteInterpretationDraft.model_validate(payload)
    validate_note_interpretation_attribution(parsed, revision)
    return parsed.model_dump(mode="json")


def _draft_metadata(
    item: ExternalNoteInboxItem,
    draft: ExternalNoteReviewDraftDTO | None,
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    revision = item.revision
    if revision.coverage is not NoteCoverage.FULL:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_SUMMARY_ONLY")
        return _source(item), None
    if draft is None:
        return _source(item), None
    if draft.note_revision_id != revision.note_revision_id:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_ESCALATED_REVISION_MISMATCH")
        return _source(item), None
    if draft.status != "SUCCEEDED":
        warnings.append("OBSERVATION_RESEARCH_DRAFT_ESCALATED_NOT_READY")
        return _source(item), None
    try:
        payload = _parse_payload(draft.payload, revision)
    except (TypeError, ValueError, json.JSONDecodeError):
        warnings.append("OBSERVATION_RESEARCH_DRAFT_ESCALATED_PAYLOAD_INVALID")
        return _source(item), None
    return (
        _source(
            item,
            analysis_kind="ESCALATED_REVIEW",
            analysis_id=draft.draft_id,
            provider=draft.provider,
            model=draft.model,
            created_at=draft.created_at.isoformat(),
        ),
        payload,
    )


def _interpretation_metadata(
    item: ExternalNoteInboxItem,
    interpretation: ExternalNoteInterpretation | None,
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    revision = item.revision
    if revision.coverage is not NoteCoverage.FULL:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_SUMMARY_ONLY")
        return _source(item), None
    if interpretation is None:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_INTERPRETATION_NOT_READY")
        return _source(item), None
    if interpretation.note_revision_id != revision.note_revision_id:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_INTERPRETATION_REVISION_MISMATCH")
        return _source(item), None
    if interpretation.status != "SUCCEEDED":
        warnings.append("OBSERVATION_RESEARCH_DRAFT_INTERPRETATION_NOT_READY")
        return _source(item), None
    try:
        raw = json.loads(interpretation.payload_json)
        payload = _parse_payload(raw, revision)
    except (TypeError, ValueError, json.JSONDecodeError):
        warnings.append("OBSERVATION_RESEARCH_DRAFT_INTERPRETATION_PAYLOAD_INVALID")
        return _source(item), None
    return (
        _source(
            item,
            analysis_kind="INTERPRETATION",
            analysis_id=interpretation.interpretation_id,
            provider=interpretation.provider,
            model=interpretation.model,
            created_at=interpretation.created_at.isoformat(),
        ),
        payload,
    )


def project_observation_research_draft(
    item: ExternalNoteInboxItem,
    *,
    escalated_review: ExternalNoteReviewDraftDTO | None = None,
) -> dict[str, Any]:
    """Project exact durable metadata and a validated private draft payload."""

    warnings: list[str] = []
    if item.identity.note_id != item.revision.note_id:
        return {
            "source": _source(item),
            "payload": None,
            "warnings": ["OBSERVATION_RESEARCH_DRAFT_IDENTITY_MISMATCH"],
        }

    source, payload = _draft_metadata(item, escalated_review, warnings)
    if payload is None and item.revision.coverage is NoteCoverage.FULL:
        source, payload = _interpretation_metadata(item, item.interpretation, warnings)
    if item.identity.primary_instrument_id is None:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_INSTRUMENT_UNRESOLVED")
    if payload is None and not warnings:
        warnings.append("OBSERVATION_RESEARCH_DRAFT_ANALYSIS_UNAVAILABLE")
    return {
        "source": source,
        "payload": payload,
        "warnings": list(dict.fromkeys(warnings)),
    }


__all__ = ["project_observation_research_draft"]
