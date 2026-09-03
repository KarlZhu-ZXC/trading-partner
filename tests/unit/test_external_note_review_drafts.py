from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.ports.agent_model_provider import ModelResponse, ModelToolCall
from application.services.external_note_review_draft_service import (
    ExternalNoteReviewDraftService,
)
from domain.external_note.enums import ExternalNoteReviewStatus, NoteCoverage, NoteSpeakerKind
from domain.external_note.models import (
    AttributedNoteBlock,
    ExternalNoteInterpretation,
    ExternalNoteReview,
    ExternalNoteReviewDraft,
    ExternalNoteRevision,
)

NOW = datetime(2026, 9, 3, 9, tzinfo=UTC)
REVISION_ID = "external_note_revision_deep"
REVIEW_ID = "external_note_review_deep"


def _scenarios() -> list[dict[str, object]]:
    return [
        {
            "scenario": scenario,
            "action": "NO_ACTION",
            "condition": "原文没有新增确认条件",
            "confirmation": "等待原文明确确认",
            "loss_boundary": "原文未定义",
        }
        for scenario in ("UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION")
    ]


class _Provider:
    def __init__(
        self,
        *,
        invented_time_condition: bool = False,
        forced_action: tuple[str, str] | None = None,
    ) -> None:
        self.requests = []
        self.invented_time_condition = invented_time_condition
        self.forced_action = forced_action

    async def complete(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        payload = {
            "change_relation": "SUPERSEDES",
            "material_change_summary": "用户撤回此前加仓计划。",
            "viewpoints": [
                {
                    "speaker_kind": "USER",
                    "speaker_label": "USER",
                    "source_block_ordinals": [0],
                    "summary": "撤回加仓，等待新证据。",
                    "holding_horizon": "POSITION",
                    "direction": "SIDEWAYS",
                    "structure": "当前不采取行动。",
                }
            ],
            "user_scenarios": [
                {
                    **item,
                    **(
                        {"confirmation": "等待收盘突破"}
                        if self.invented_time_condition and index == 0
                        else {}
                    ),
                    **(
                        {"action": self.forced_action[1]}
                        if self.forced_action is not None
                        and item["scenario"] == self.forced_action[0]
                        else {}
                    ),
                }
                for index, item in enumerate(_scenarios())
            ],
            "catalysts": [],
            "key_levels": [],
            "missing_evidence": ["新的确认条件"],
            "contradictions": ["此前加仓计划已经撤回"],
            "suggested_next_step": "PROPOSE_DECISION",
        }
        return ModelResponse(
            tool_calls=(
                ModelToolCall(
                    id="deep-review",
                    name="submit_note_interpretation",
                    arguments=json.dumps(payload, ensure_ascii=False),
                ),
            )
        )


class _RepairProvider(_Provider):
    async def complete(self, request):  # type: ignore[no-untyped-def]
        if not self.requests:
            self.requests.append(request)
            return ModelResponse(text="invalid plain text")
        return await super().complete(request)


class _RepairingProvider(_Provider):
    async def complete(self, request):  # type: ignore[no-untyped-def]
        if not self.requests:
            self.requests.append(request)
            invalid = {
                "change_relation": "SUPERSEDES",
                "material_change_summary": "用户撤回此前加仓计划。",
                "viewpoints": [
                    {
                        "speaker_kind": "USER",
                        "speaker_label": "USER",
                        "source_block_ordinals": [0],
                        "summary": "撤回加仓。",
                        "holding_horizon": "POSITION",
                        "direction": "SIDEWAYS",
                        "structure": "等待新证据。",
                    }
                ],
                "user_scenarios": _scenarios()[:3],
                "catalysts": [],
                "key_levels": [],
                "missing_evidence": [],
                "contradictions": [],
                "suggested_next_step": "REVIEW",
            }
            return ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="invalid-review",
                        name="submit_note_interpretation",
                        arguments=json.dumps(invalid, ensure_ascii=False),
                    ),
                )
            )
        return await super().complete(request)


class _Reviews:
    def __init__(self) -> None:
        self.review = ExternalNoteReview(
            review_id=REVIEW_ID,
            note_revision_id=REVISION_ID,
            note_id="external_note_deep",
            version=1,
            status=ExternalNoteReviewStatus.PENDING,
            subject_id="case_deep",
            decision_id=None,
            due_at=None,
            actor="system",
            authorization_note="Await review.",
            idempotency_key="deep-pending",
            created_at=NOW,
        )
        self.drafts: list[ExternalNoteReviewDraft] = []

    def latest_for_revision(self, _revision_id: str):
        return self.review

    def latest_draft(self, _revision_id: str):
        return self.drafts[-1] if self.drafts else None

    def append_draft(self, value: ExternalNoteReviewDraft):
        self.drafts.append(value)
        return value


class _Notes:
    def __init__(self) -> None:
        self.revision = ExternalNoteRevision(
            note_revision_id=REVISION_ID,
            note_id="external_note_deep",
            version=2,
            content_sha256="a" * 64,
            source_revision_key="source:deep-review",
            title="AMD",
            summary="撤回加仓",
            full_body="撤回此前加仓计划，等待新证据。",
            coverage=NoteCoverage.FULL,
            source_timestamp=NOW,
            observed_at=NOW,
            visibility="SELF",
            related_provider_stock_ids=(),
            related_provider_codes=("US.AMD",),
            blocks=(
                AttributedNoteBlock(
                    ordinal=0,
                    speaker_kind=NoteSpeakerKind.USER,
                    speaker_label="USER",
                    body="撤回此前加仓计划，等待新证据。",
                ),
            ),
        )
        self.interpretation = ExternalNoteInterpretation(
            interpretation_id="external_note_interpretation_deep",
            note_revision_id=REVISION_ID,
            status="SUCCEEDED",
            provider="opencode_go",
            model="qwen3.8-flash",
            reasoning_effort="max",
            schema_version="moomoo-note-interpretation-v1",
            payload_json=json.dumps({"change_relation": "SUPERSEDES"}),
            error_code=None,
            created_at=NOW,
        )

    def revision_by_id(self, _revision_id: str):
        return self.revision

    def interpretation_for_revision(self, _revision_id: str):
        return self.interpretation


@pytest.mark.asyncio
async def test_max_review_draft_is_separate_strict_and_idempotent(
    fixed_clock, id_generator
) -> None:
    fixed_clock.set(NOW)
    provider = _Provider()
    reviews = _Reviews()
    package = SimpleNamespace(
        requires_deep_review=True,
        note_revision_id=REVISION_ID,
        escalation_reasons=("CHANGE_SUPERSEDES", "EXPLICIT_USER_REVIEW"),
        model_dump=lambda **_kwargs: {
            "material_change_summary": "撤回此前加仓计划。",
            "thesis": {"statement": "此前计划等待回调加仓。"},
        },
    )
    service = ExternalNoteReviewDraftService(
        provider,  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="qwen3.8-max",
        reviews=reviews,  # type: ignore[arg-type]
        notes=_Notes(),  # type: ignore[arg-type]
        view_reviews=SimpleNamespace(get=lambda *_a, **_k: package),  # type: ignore[arg-type]
        clock=fixed_clock,
        id_generator=id_generator,
        reasoning_effort="high",
    )

    first = await service.review(REVISION_ID, explicit_review=True)
    repeated = await service.review(REVISION_ID, explicit_review=True)

    assert first is not None and first.status == "SUCCEEDED"
    assert first.model == "qwen3.8-max"
    assert first.trigger_codes == ("CHANGE_SUPERSEDES", "EXPLICIT_USER_REVIEW")
    assert first.payload["suggested_next_step"] == "PROPOSE_DECISION"
    assert repeated == first
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.model == "qwen3.8-max"
    assert request.reasoning_effort == "high"
    assert request.native_web_search is False
    assert len(reviews.drafts) == 1


@pytest.mark.asyncio
async def test_max_review_gets_one_bounded_schema_repair(
    fixed_clock, id_generator
) -> None:
    fixed_clock.set(NOW)
    provider = _RepairProvider()
    reviews = _Reviews()
    package = SimpleNamespace(
        requires_deep_review=True,
        note_revision_id=REVISION_ID,
        escalation_reasons=("EXPLICIT_USER_REVIEW",),
        model_dump=lambda **_kwargs: {},
    )
    service = ExternalNoteReviewDraftService(
        provider,  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="qwen3.8-max",
        reviews=reviews,  # type: ignore[arg-type]
        notes=_Notes(),  # type: ignore[arg-type]
        view_reviews=SimpleNamespace(get=lambda *_a, **_k: package),  # type: ignore[arg-type]
        clock=fixed_clock,
        id_generator=id_generator,
    )

    result = await service.review(REVISION_ID, explicit_review=True)

    assert result is not None and result.status == "SUCCEEDED"
    assert len(provider.requests) == 2
    assert "上一响应未通过严格结构校验" in provider.requests[1].messages[-1]["content"]


@pytest.mark.asyncio
async def test_max_review_rejects_an_invented_time_confirmation(
    fixed_clock, id_generator
) -> None:
    fixed_clock.set(NOW)
    provider = _Provider(invented_time_condition=True)
    reviews = _Reviews()
    package = SimpleNamespace(
        requires_deep_review=True,
        note_revision_id=REVISION_ID,
        escalation_reasons=("EXPLICIT_USER_REVIEW",),
        model_dump=lambda **_kwargs: {},
    )
    service = ExternalNoteReviewDraftService(
        provider,  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="qwen3.8-max",
        reviews=reviews,  # type: ignore[arg-type]
        notes=_Notes(),  # type: ignore[arg-type]
        view_reviews=SimpleNamespace(get=lambda *_a, **_k: package),  # type: ignore[arg-type]
        clock=fixed_clock,
        id_generator=id_generator,
    )

    result = await service.review(REVISION_ID, explicit_review=True)

    assert result is not None and result.status == "FAILED"
    assert result.error_code == "NOTE_REVIEW_UNGROUNDED_TIME_CONDITION"
    assert len(provider.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forced_action", "error_code"),
    (
        (("SIDEWAYS", "HOLD"), "NOTE_REVIEW_UNGROUNDED_ACTION"),
        (("UPSIDE", "ADD"), "NOTE_REVIEW_WITHDRAWN_ACTION_REINTRODUCED"),
    ),
)
async def test_max_review_rejects_ungrounded_or_withdrawn_actions(
    fixed_clock,
    id_generator,
    forced_action: tuple[str, str],
    error_code: str,
) -> None:
    fixed_clock.set(NOW)
    provider = _Provider(forced_action=forced_action)
    reviews = _Reviews()
    package = SimpleNamespace(
        requires_deep_review=True,
        note_revision_id=REVISION_ID,
        escalation_reasons=("CHANGE_SUPERSEDES",),
        model_dump=lambda **_kwargs: {},
    )
    service = ExternalNoteReviewDraftService(
        provider,  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="qwen3.8-max",
        reviews=reviews,  # type: ignore[arg-type]
        notes=_Notes(),  # type: ignore[arg-type]
        view_reviews=SimpleNamespace(get=lambda *_a, **_k: package),  # type: ignore[arg-type]
        clock=fixed_clock,
        id_generator=id_generator,
    )

    result = await service.review(REVISION_ID)

    assert result is not None and result.status == "FAILED"
    assert result.error_code == error_code


@pytest.mark.asyncio
async def test_review_draft_repairs_one_invalid_structure(fixed_clock, id_generator) -> None:
    fixed_clock.set(NOW)
    provider = _RepairingProvider()
    reviews = _Reviews()
    package = SimpleNamespace(
        requires_deep_review=True,
        note_revision_id=REVISION_ID,
        escalation_reasons=("CHANGE_SUPERSEDES",),
        model_dump=lambda **_kwargs: {"material_change_summary": "撤回此前加仓计划。"},
    )
    service = ExternalNoteReviewDraftService(
        provider,  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="muse-spark-1.3-contributor",
        reviews=reviews,  # type: ignore[arg-type]
        notes=_Notes(),  # type: ignore[arg-type]
        view_reviews=SimpleNamespace(get=lambda *_a, **_k: package),  # type: ignore[arg-type]
        clock=fixed_clock,
        id_generator=id_generator,
        reasoning_effort="high",
    )

    result = await service.review(REVISION_ID, explicit_review=True)

    assert result is not None and result.status == "SUCCEEDED"
    assert len(provider.requests) == 2
    repair = provider.requests[1]
    assert repair.reasoning_effort == "high"
    assert repair.messages[-1]["role"] == "user"
    assert "补齐四个 USER 场景" in repair.messages[-1]["content"]


def test_sanitized_model_benchmark_contract_covers_known_failure_modes(
    project_root: Path,
) -> None:
    payload = json.loads(
        (
            project_root
            / "tests"
            / "fixtures"
            / "observation-review-benchmarks.v1.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == 1
    cases = {item["id"]: item for item in payload["cases"]}
    assert set(cases) == {
        "withdrawn-add-must-not-propagate",
        "explicit-invalidation-stays-scenario-scoped",
    }
    withdrawal = cases["withdrawn-add-must-not-propagate"]
    assert {"ADD", "HOLD", "EXIT"} <= set(
        withdrawal["forbidden_propagated_actions"]
    )
    assert all(
        item["forbid_invented_timing"]
        and item["forbid_invented_confirmation"]
        for item in cases.values()
    )
