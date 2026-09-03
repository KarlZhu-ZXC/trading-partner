"""Escalated provider-neutral draft for a deterministic Observation review package."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import date

from pydantic import ValidationError

from application.dto.external_note_review import ExternalNoteReviewDraftDTO
from application.ports.agent_model_provider import (
    AgentModelProvider,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from application.ports.id_generator import IdGenerator
from application.services.external_note_interpretation_service import (
    NOTE_INTERPRETATION_TOOL_NAME,
    NoteInterpretationDraft,
    parse_note_interpretation_response,
    validate_note_interpretation_attribution,
)
from application.services.view_review_service import ViewReviewService
from domain.common.errors import ExternalNoteReviewNotFound, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.external_note.enums import NoteSpeakerKind
from domain.external_note.models import ExternalNoteReviewDraft

_SCHEMA_VERSION = "external-note-review-draft-v1"
_SYSTEM_PROMPT = """你是 Trading Partner 的第二层私有观点复核模型。
输入包含精确 Moomoo Note Revision、第一层 Flash 草稿，以及只读的 confirmed Thesis、
Trade Plan、Decision、Position、Monitor 和 coverage。外部说话人的观点不是 USER 判断。
不得把上一版已经撤回的 ADD/HOLD/EXIT 延续到当前版；不得补造日期、时间窗口、价格、
确认条件、止损或行动。缺少原文依据时保持 REVIEW/NO_ACTION，并在 missing_evidence 中说明。
四情景只描述 USER 当前文本能够支持的 UPSIDE、SIDEWAYS、PULLBACK、INVALIDATION；每个
结论必须引用 source_block_ordinals。confirmed baseline 是对照事实，不得被改写，也不代表
本次草稿已经确认。只调用 submit_note_interpretation 一次，不输出普通文本。"""

_TIME_QUALIFIERS = (
    "收盘",
    "连续",
    "日线",
    "周线",
    "小时",
    "分钟",
    "交易日",
    "上周",
    "本周",
    "两周",
)
_ACTION_TERMS = {
    "ADD": ("ADD", "加仓", "建仓", "买入"),
    "HOLD": ("HOLD", "持有", "维持仓位"),
    "REDUCE": ("REDUCE", "减仓", "降低仓位"),
    "EXIT": ("EXIT", "退出", "清仓", "卖出", "止损"),
}


class _ReviewFidelityError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _section_date(value: str | None) -> date | None:
    if value is None:
        return None
    normalized = value.strip().replace("/", "-").replace(".", "-")
    parts = normalized.split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            return date(2000, int(parts[0]), int(parts[1]))
    except ValueError:
        return None
    return None


def _current_user_text(revision: object) -> str:
    blocks = [
        item
        for item in getattr(revision, "blocks", ())
        if item.speaker_kind is NoteSpeakerKind.USER
    ]
    dated = [(parsed, item) for item in blocks if (parsed := _section_date(item.section_date))]
    if dated:
        latest = max(item[0] for item in dated)
        blocks = [item for parsed, item in dated if parsed == latest]
    return "\n".join(
        f"{item.section_date or ''} {item.body}".strip() for item in blocks
    )


def _validate_fidelity(value: NoteInterpretationDraft, revision: object) -> None:
    current_text = _current_user_text(revision)
    if not current_text:
        raise _ReviewFidelityError("NOTE_REVIEW_CURRENT_USER_TEXT_MISSING")
    rendered = "\n".join(
        (
            value.material_change_summary,
            *(item.summary for item in value.viewpoints),
            *(item.structure for item in value.viewpoints),
            *(item.condition for item in value.user_scenarios),
            *(item.confirmation for item in value.user_scenarios),
            *(item.loss_boundary for item in value.user_scenarios),
            *value.catalysts,
            *value.key_levels,
            *value.missing_evidence,
            *value.contradictions,
        )
    )
    for qualifier in _TIME_QUALIFIERS:
        if qualifier in rendered and qualifier not in current_text:
            raise _ReviewFidelityError("NOTE_REVIEW_UNGROUNDED_TIME_CONDITION")
    for numeric in set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", rendered)):
        if numeric not in current_text:
            raise _ReviewFidelityError("NOTE_REVIEW_UNGROUNDED_NUMERIC_CONDITION")
    upper_source = current_text.upper()
    for scenario in value.user_scenarios:
        action = scenario.action
        if action in {"REVIEW", "NO_ACTION"}:
            continue
        terms = _ACTION_TERMS[action]
        if not any(term.upper() in upper_source for term in terms):
            raise _ReviewFidelityError("NOTE_REVIEW_UNGROUNDED_ACTION")
        if action == "ADD" and re.search(
            r"(?:撤回|取消|不再|停止|暂不|不能).{0,48}(?:ADD|加仓|建仓|买入)|"
            r"不(?:再)?加仓",
            current_text,
            re.IGNORECASE,
        ):
            raise _ReviewFidelityError("NOTE_REVIEW_WITHDRAWN_ACTION_REINTRODUCED")
        if action == "EXIT" and scenario.scenario != "INVALIDATION" and not re.search(
            r"(?:回调|回落|下跌).{0,80}(?:EXIT|退出|清仓|卖出|止损)",
            current_text,
            re.IGNORECASE,
        ):
            raise _ReviewFidelityError("NOTE_REVIEW_ACTION_SCENARIO_MISMATCH")


class ExternalNoteReviewDraftService:
    """Create an append-only deep-review draft without any write authority."""

    def __init__(
        self,
        provider: AgentModelProvider | None,
        *,
        provider_name: str,
        model: str,
        reviews: ExternalNoteReviewRepository,
        notes: ExternalNoteRepository,
        view_reviews: ViewReviewService,
        clock: Clock,
        id_generator: IdGenerator,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 5000,
        reasoning_effort: str = "max",
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._reviews = reviews
        self._notes = notes
        self._view_reviews = view_reviews
        self._clock = clock
        self._ids = id_generator
        self._timeout_seconds = max(1.0, min(timeout_seconds, 120.0))
        self._max_output_tokens = max(512, min(max_output_tokens, 8000))
        self._reasoning_effort = reasoning_effort

    async def review(
        self,
        note_revision_id: str,
        *,
        explicit_review: bool = False,
        force: bool = False,
    ) -> ExternalNoteReviewDraftDTO | None:
        package = self._view_reviews.get(
            note_revision_id,
            explicit_review=explicit_review,
        )
        if not package.requires_deep_review:
            return None
        review = self._reviews.latest_for_revision(package.note_revision_id)
        revision = self._notes.revision_by_id(package.note_revision_id)
        interpretation = self._notes.interpretation_for_revision(package.note_revision_id)
        if review is None or revision is None or interpretation is None:
            raise ExternalNoteReviewNotFound(
                "Escalated review requires the exact review, revision, and first-pass draft"
            )
        existing = self._reviews.latest_draft(package.note_revision_id)
        if (
            not force
            and existing is not None
            and existing.status == "SUCCEEDED"
            and existing.review_id == review.review_id
            and existing.model == self._model
        ):
            return ExternalNoteReviewDraftDTO.from_domain(existing)
        if self._provider is None:
            return None
        payload = {
            "note_revision_id": revision.note_revision_id,
            "title": revision.title,
            "coverage": revision.coverage.value,
            "blocks": [
                {
                    "ordinal": item.ordinal,
                    "speaker_kind": item.speaker_kind.value,
                    "speaker_label": item.speaker_label,
                    "body": item.body,
                    "section_date": item.section_date,
                }
                for item in revision.blocks
            ],
            "first_pass_interpretation": json.loads(interpretation.payload_json),
            "confirmed_context": package.model_dump(
                mode="json",
                exclude={
                    "review",
                    "deep_review",
                    "user_scenarios",
                    "external_viewpoints",
                },
            ),
            "escalation_reasons": list(package.escalation_reasons),
        }
        request = ModelRequest(
            messages=(
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": NOTE_INTERPRETATION_TOOL_NAME,
                        "description": "Submit one bounded escalated view-review draft.",
                        "strict": True,
                        "parameters": NoteInterpretationDraft.model_json_schema(),
                    },
                },
            ),
            model=self._model,
            reasoning_mode="thinking",
            reasoning_effort=self._reasoning_effort,
            max_output_tokens=self._max_output_tokens,
            native_web_search=False,
        )
        try:
            parsed = None
            validation_error: Exception | None = None
            repair_context: tuple[ModelMessage, ...] = ()
            for attempt in range(2):
                candidate = request
                response = ModelResponse()
                if attempt == 1:
                    candidate = replace(
                        request,
                        messages=(
                            *request.messages,
                            *repair_context,
                            {
                                "role": "user",
                                "content": (
                                    "上一响应未通过严格结构校验。保持事实、作者归属和当前/"
                                    "历史判断边界不变，补齐四个 USER 场景并重新调用同一工具"
                                    "一次；不得输出普通文本或额外字段。"
                                ),
                            },
                        ),
                    )
                try:
                    response = await asyncio.wait_for(
                        self._provider.complete(candidate),
                        timeout=self._timeout_seconds,
                    )
                    parsed = parse_note_interpretation_response(response, revision)
                    validate_note_interpretation_attribution(parsed, revision)
                    _validate_fidelity(parsed, revision)
                    break
                except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
                    parsed = None
                    validation_error = error
                    if response.tool_calls:
                        call = response.tool_calls[0]
                        repair_context = (
                            ModelMessage(
                                role="assistant",
                                content=response.text or None,
                                tool_calls=response.tool_calls,
                            ),
                            ModelMessage(
                                role="tool",
                                name=call.name,
                                tool_call_id=call.id,
                                content=json.dumps(
                                    {
                                        "ok": False,
                                        "error": "SCHEMA_INVALID",
                                        "validation_type": type(error).__name__,
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            ),
                        )
            if parsed is None:
                assert validation_error is not None
                raise validation_error
            status = "SUCCEEDED"
            payload_json = parsed.model_dump_json()
            error_code = None
        except TimeoutError:
            status = "FAILED"
            payload_json = "{}"
            error_code = "NOTE_REVIEW_PROVIDER_TIMEOUT"
        except TradingPartnerError as error:
            status = "FAILED"
            payload_json = "{}"
            error_code = f"NOTE_REVIEW_{error.code}"[:128]
        except _ReviewFidelityError as error:
            status = "FAILED"
            payload_json = "{}"
            error_code = error.code
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError):
            status = "FAILED"
            payload_json = "{}"
            error_code = "NOTE_REVIEW_INVALID_OUTPUT"
        draft_id = self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_REVIEW_DRAFT)
        draft = ExternalNoteReviewDraft(
            draft_id=draft_id,
            review_id=review.review_id,
            note_revision_id=revision.note_revision_id,
            status=status,
            provider=self._provider_name,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            schema_version=_SCHEMA_VERSION,
            trigger_codes=package.escalation_reasons,
            payload_json=payload_json,
            error_code=error_code,
            idempotency_key=(
                f"review-draft:{review.review_id}:v{review.version}:{self._model}"
                if not force and existing is None
                else f"review-draft-retry:{draft_id}"
            ),
            created_at=self._clock.now(),
        )
        return ExternalNoteReviewDraftDTO.from_domain(self._reviews.append_draft(draft))

    def latest(self, note_revision_id: str) -> ExternalNoteReviewDraftDTO | None:
        value = self._reviews.latest_draft(note_revision_id.strip())
        return ExternalNoteReviewDraftDTO.from_domain(value) if value is not None else None
