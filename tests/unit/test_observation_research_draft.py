"""Read-only exact Observation research-draft projection contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from application.dto.external_note_review import ExternalNoteReviewDraftDTO
from application.services.external_note_interpretation_service import NoteInterpretationDraft
from application.services.external_note_sync_service import (
    ExternalNoteInboxItem,
    ExternalNoteSyncService,
)
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import (
    AttributedNoteBlock,
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteRevision,
)
from interfaces.console.api import observation_research_draft
from interfaces.console.observation_research_draft import (
    project_observation_research_draft,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
REVISION_ID = "external_note_revision_00000000000000000000000000000001"
NOTE_ID = "external_note_00000000000000000000000000000001"


def _payload(*, speaker_kind: str = "USER", speaker_label: str = "USER") -> dict[str, object]:
    return {
        "change_relation": "SUPERSEDES",
        "material_change_summary": "The user view changed.",
        "viewpoints": [
            {
                "speaker_kind": speaker_kind,
                "speaker_label": speaker_label,
                "source_block_ordinals": [0],
                "summary": "The user view remains bounded.",
                "holding_horizon": "POSITION",
                "direction": "UP",
                "structure": "The structure is constructive.",
            }
        ],
        "user_scenarios": [
            {
                "scenario": scenario,
                "action": "NO_ACTION",
                "condition": f"Review {scenario.lower()} condition.",
                "confirmation": "Wait for confirmation.",
                "loss_boundary": "Respect the invalidation boundary.",
            }
            for scenario in ("UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION")
        ],
        "catalysts": ["A bounded catalyst"],
        "key_levels": ["A referenced level"],
        "missing_evidence": [],
        "contradictions": [],
        "suggested_next_step": "REVIEW",
    }


def _revision(*, coverage: NoteCoverage = NoteCoverage.FULL) -> ExternalNoteRevision:
    block = AttributedNoteBlock(
        ordinal=0,
        speaker_kind=NoteSpeakerKind.USER,
        speaker_label="USER",
        body="A private observation body that must never be returned.",
        section_date="2026-09-08",
    )
    return ExternalNoteRevision(
        note_revision_id=REVISION_ID,
        note_id=NOTE_ID,
        version=2,
        content_sha256="a" * 64,
        source_revision_key="source-revision-2",
        title="Immutable revision title",
        summary="Summary-only observation",
        full_body=(
            "A private observation body that must never be returned."
            if coverage is NoteCoverage.FULL
            else None
        ),
        coverage=coverage,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="SELF",
        related_provider_stock_ids=(),
        related_provider_codes=(),
        blocks=(block,) if coverage is NoteCoverage.FULL else (),
    )


def _identity() -> ExternalNoteIdentity:
    return ExternalNoteIdentity(
        note_id=NOTE_ID,
        source="MOOMOO_NOTE",
        external_id="note-1",
        title="Current mutable identity title",
        primary_instrument_id="equity:US:ABC",
        created_at=NOW,
        last_seen_at=NOW,
    )


def _item(
    *,
    revision: ExternalNoteRevision | None = None,
    interpretation: ExternalNoteInterpretation | None = None,
) -> ExternalNoteInboxItem:
    return ExternalNoteInboxItem(
        identity=_identity(),
        revision=revision or _revision(),
        interpretation=interpretation,
    )


def _interpretation(
    payload: dict[str, object], *, status: str = "SUCCEEDED"
) -> ExternalNoteInterpretation:
    return ExternalNoteInterpretation(
        interpretation_id="interpretation-1",
        note_revision_id=REVISION_ID,
        status=status,
        provider="flash-provider",
        model="flash-model",
        reasoning_effort="max",
        schema_version="interpretation-v1",
        payload_json=json.dumps(payload),
        error_code=None if status == "SUCCEEDED" else "FAILED",
        created_at=NOW,
    )


def _deep(payload: dict[str, object], *, status: str = "SUCCEEDED") -> ExternalNoteReviewDraftDTO:
    return ExternalNoteReviewDraftDTO(
        draft_id="external_note_review_draft_1",
        review_id="external_note_review_1",
        note_revision_id=REVISION_ID,
        status=status,
        provider="deep-provider",
        model="deep-model",
        reasoning_effort="high",
        schema_version="review-v1",
        trigger_codes=("MATERIAL_CHANGE",),
        payload=payload if status == "SUCCEEDED" else {},
        error_code=None if status == "SUCCEEDED" else "FAILED",
        created_at=NOW,
    )


def test_deep_review_is_preferred_and_source_title_is_revision_immutable() -> None:
    item = _item(interpretation=_interpretation(_payload()))
    result = project_observation_research_draft(
        item,
        escalated_review=_deep(_payload()),
    )

    assert result["source"]["analysis_kind"] == "ESCALATED_REVIEW"
    assert result["source"]["analysis_id"] == "external_note_review_draft_1"
    assert result["source"]["title"] == "Immutable revision title"
    assert result["payload"] == NoteInterpretationDraft.model_validate(_payload()).model_dump(
        mode="json"
    )
    assert "Current mutable identity title" not in json.dumps(result)


def test_invalid_or_unready_deep_review_falls_back_to_exact_first_pass() -> None:
    item = _item(interpretation=_interpretation(_payload()))
    invalid = _deep({"unexpected": "payload"})
    result = project_observation_research_draft(item, escalated_review=invalid)

    assert result["source"]["analysis_kind"] == "INTERPRETATION"
    assert result["source"]["analysis_id"] == "interpretation-1"
    assert result["payload"] is not None
    assert "OBSERVATION_RESEARCH_DRAFT_ESCALATED_PAYLOAD_INVALID" in result["warnings"]


def test_summary_only_and_missing_analysis_are_closed_without_payload() -> None:
    summary = _item(revision=_revision(coverage=NoteCoverage.SUMMARY_ONLY))
    summary_result = project_observation_research_draft(
        summary,
        escalated_review=_deep(_payload()),
    )
    assert summary_result["payload"] is None
    assert "OBSERVATION_RESEARCH_DRAFT_SUMMARY_ONLY" in summary_result["warnings"]

    missing_result = project_observation_research_draft(_item())
    assert missing_result["payload"] is None
    assert "OBSERVATION_RESEARCH_DRAFT_INTERPRETATION_NOT_READY" in missing_result["warnings"]


def test_mixed_speaker_attribution_is_fail_closed_without_body_leak() -> None:
    item = _item(interpretation=_interpretation(_payload(speaker_label="Other Speaker")))
    result = project_observation_research_draft(item)
    encoded = json.dumps(result)

    assert result["payload"] is None
    assert "OBSERVATION_RESEARCH_DRAFT_INTERPRETATION_PAYLOAD_INVALID" in result["warnings"]
    assert "private observation body" not in encoded


def test_exact_service_read_does_not_use_latest_or_provider_or_write() -> None:
    repository = MagicMock()
    revision = _revision()
    repository.revision_by_id.return_value = revision
    repository.get.return_value = _identity()
    repository.previous_revision.return_value = None
    repository.interpretation_for_revision.return_value = _interpretation(_payload())
    service = object.__new__(ExternalNoteSyncService)
    service._repository = repository

    item = service.read_revision(REVISION_ID)

    assert item is not None
    repository.revision_by_id.assert_called_once_with(REVISION_ID)
    repository.get.assert_called_once_with(NOTE_ID)
    repository.latest_revision.assert_not_called()
    repository.append_revision.assert_not_called()
    repository.append_interpretation.assert_not_called()


def test_exact_read_never_substitutes_an_older_full_revision() -> None:
    repository = MagicMock()
    current = _revision(coverage=NoteCoverage.SUMMARY_ONLY)
    previous = replace(_revision(), note_revision_id="external_note_revision_previous", version=1)
    repository.revision_by_id.return_value = current
    repository.get.return_value = _identity()
    repository.previous_revision.return_value = previous
    service = object.__new__(ExternalNoteSyncService)
    service._repository = repository

    item = service.read_revision(REVISION_ID)

    assert item is not None
    assert item.revision == current
    assert item.interpretation is None
    repository.interpretation_for_revision.assert_not_called()


def test_exact_read_keeps_proven_full_promotion_for_the_same_revision() -> None:
    repository = MagicMock()
    summary = "USER: Hold.\nWatch support at 210."
    current = replace(_revision(coverage=NoteCoverage.SUMMARY_ONLY), summary=summary)
    previous = replace(
        _revision(),
        note_revision_id="external_note_revision_previous",
        version=1,
        summary=summary,
        full_body="USER: Hold.",
    )
    repository.revision_by_id.return_value = current
    repository.get.return_value = _identity()
    repository.previous_revision.return_value = previous
    repository.interpretation_for_revision.return_value = _interpretation(_payload())
    service = object.__new__(ExternalNoteSyncService)
    service._repository = repository

    item = service.read_revision(REVISION_ID)

    assert item is not None
    assert item.revision.note_revision_id == REVISION_ID
    assert item.revision.coverage is NoteCoverage.FULL
    assert item.interpretation is not None
    repository.interpretation_for_revision.assert_called_once_with(REVISION_ID)


@pytest.mark.asyncio
async def test_console_endpoint_is_no_store_and_returns_exact_projection() -> None:
    revision = _revision()
    item = _item(revision=revision, interpretation=_interpretation(_payload()))
    container = MagicMock()
    container.services.external_notes.read_revision.return_value = item
    container.services.external_note_review_drafts.latest.return_value = None
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))

    response = await observation_research_draft(request, REVISION_ID)

    assert response.headers["cache-control"] == "no-store"
    body = json.loads(response.body)
    assert body["data"]["source"]["note_revision_id"] == REVISION_ID
    assert body["data"]["payload"] is not None


@pytest.mark.asyncio
async def test_console_endpoint_returns_404_for_missing_exact_revision() -> None:
    container = MagicMock()
    container.services.external_notes.read_revision.return_value = None
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))

    with pytest.raises(HTTPException) as error:
        await observation_research_draft(request, REVISION_ID)

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_exact_review_revision_is_scoped_and_read_only(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import interfaces.console.api as api

    container = MagicMock()
    container.settings.observation_review_workflow_enabled = False
    item = _item(revision=_revision(), interpretation=_interpretation(_payload()))
    container.services.external_notes.read_revision.return_value = item
    container.services.external_note_reviews.get_for_revision.return_value = None
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))
    read = AsyncMock(
        return_value={
            "ok": True,
            "data": {
                "subject": {
                    "primary_instrument_id": item.identity.primary_instrument_id,
                }
            },
        }
    )
    monkeypatch.setattr(api, "_invoke_capability", read)
    response = await api.observation_exact_revision(request, REVISION_ID, "case_synthetic")
    assert response.headers["cache-control"] == "no-store"
    assert json.loads(response.body)["data"]["review_workflow_enabled"] is False
    assert json.loads(response.body)["data"]["revision"]["note_revision_id"] == REVISION_ID
    assert read.call_args.kwargs["preserve_full_result"] is True
    container.services.external_notes.inbox.assert_not_called()
    container.services.external_notes.analyze_revision.assert_not_called()
    container.services.external_note_reviews.ensure_pending.assert_not_called()
    read.return_value = {
        "ok": True,
        "data": {
            "subject": {
                "primary_instrument_id": "equity:US:DIFFERENT",
            }
        },
    }
    with pytest.raises(HTTPException) as failure:
        await api.observation_exact_revision(request, REVISION_ID, "case_other")
    assert failure.value.status_code == 422


@pytest.mark.asyncio
async def test_changes_endpoint_retains_full_projection_without_mutation() -> None:
    import interfaces.console.api as api

    container = MagicMock()
    payload = {
        "subject_id": "case_synthetic",
        "total": 45,
        "items": [{"change_id": str(i)} for i in range(25)],
    }
    container.services.research_changes.get.return_value.model_dump.return_value = payload
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))
    response = await api.research_changes(request, "case_synthetic", "decision_exact", None, 0, 25)
    assert response.headers["cache-control"] == "no-store"
    assert json.loads(response.body)["data"] == payload
    container.services.research_changes.get.assert_called_once_with(
        "case_synthetic",
        baseline_decision_id="decision_exact",
        change_id=None,
        offset=0,
        limit=25,
    )
