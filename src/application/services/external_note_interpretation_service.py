"""Strict model extraction for private external-note revisions."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from application.ports.agent_model_provider import (
    AgentModelProvider,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.external_note.attribution import attributed_blocks, detect_section_order
from domain.external_note.models import ExternalNoteInterpretation, ExternalNoteRevision

_TOOL_NAME = "submit_note_interpretation"
_SCHEMA_VERSION = "moomoo-note-interpretation-v1"
_SYSTEM_PROMPT = """你是 Trading Partner 的私有笔记结构化层。
只使用用户提供的 Moomoo 笔记 revision、已确定的段落作者归属和上一版结构化结果。
段落作者归属已经由确定性 explicit-speaker section state 给出：每个日期开始默认 USER。
行首 @speaker 可引入任意有界新 speaker；无 @ 的旧格式只识别 boss墨、宝总、姜汁汽水。
无署名段落会继承该作者，直到下一日期或另一明确作者重置；不得改成 UNKNOWN 或重新猜测。
“财报看法、整体观点、风险、结论”等结构标题继承当前作者且不是人名。
外部观点不是 USER 的 Thesis、Decision 或市场事实。
blocks 保持笔记原顺序。section_order 是确定性日期序列识别结果；按其解释前后关系。
若为 MIXED 或 UNKNOWN，只按各段明确日期理解，不得根据段落位置猜测时间先后。
分别概括每位作者的观点，但只为 USER 观点输出一组 strategy_v1 的 UPSIDE、SIDEWAYS、
PULLBACK、INVALIDATION 四种情景；价格降低本身不能成为 ADD，缺少周期、确认、损失边界或
收益风险时使用 REVIEW/NO_ACTION。
识别本 revision 相对上一版是 NO_MATERIAL_CHANGE、CORRECTION、REVISION、SUPERSEDES、
INVALIDATES、REMOVED_FROM_NOTE 或 NEW_THREAD，但这只是草案，不能修改任何已确认对象。
所有结论必须引用 source_block_ordinals。调用 submit_note_interpretation 一次，不要输出普通文本。"""


class _InterpretationOutputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION"]
    action: Literal["ADD", "HOLD", "REDUCE", "EXIT", "NO_ACTION", "REVIEW"]
    condition: str = Field(min_length=1, max_length=500)
    confirmation: str = Field(min_length=1, max_length=500)
    loss_boundary: str = Field(min_length=1, max_length=500)


class _Viewpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_kind: Literal["USER", "NAMED_PERSON"]
    speaker_label: str = Field(min_length=1, max_length=80)
    source_block_ordinals: tuple[int, ...] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=1000)
    holding_horizon: Literal["INTRADAY", "SWING", "POSITION", "LONG_TERM", "UNKNOWN"]
    direction: Literal["UP", "DOWN", "SIDEWAYS", "MIXED", "UNKNOWN"]
    structure: str = Field(min_length=1, max_length=500)


class _Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_relation: Literal[
        "NO_MATERIAL_CHANGE",
        "CORRECTION",
        "REVISION",
        "SUPERSEDES",
        "INVALIDATES",
        "REMOVED_FROM_NOTE",
        "NEW_THREAD",
    ]
    material_change_summary: str = Field(min_length=1, max_length=1000)
    viewpoints: tuple[_Viewpoint, ...] = Field(min_length=1, max_length=12)
    user_scenarios: tuple[_Scenario, ...] = Field(min_length=4, max_length=4)
    catalysts: tuple[str, ...] = Field(max_length=20)
    key_levels: tuple[str, ...] = Field(max_length=30)
    missing_evidence: tuple[str, ...] = Field(max_length=30)
    contradictions: tuple[str, ...] = Field(max_length=20)
    suggested_next_step: Literal["WATCH", "NO_ACTION", "REVIEW", "PROPOSE_DECISION", "PROPOSE_PLAN"]

    @field_validator("user_scenarios")
    @classmethod
    def _four_scenarios(cls, value: tuple[_Scenario, ...]) -> tuple[_Scenario, ...]:
        expected = {"UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION"}
        if {item.scenario for item in value} != expected:
            raise ValueError("all four USER strategy scenarios are required")
        return value


class ExternalNoteInterpretationService:
    def __init__(
        self,
        provider: AgentModelProvider,
        *,
        provider_name: str,
        model: str,
        clock: Clock,
        id_generator: IdGenerator,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._clock = clock
        self._ids = id_generator
        self._timeout_seconds = max(1.0, min(timeout_seconds, 120.0))

    async def analyze(
        self,
        revision: ExternalNoteRevision,
        previous_payload_json: str | None,
    ) -> ExternalNoteInterpretation:
        effective_revision = (
            replace(revision, blocks=attributed_blocks(revision.full_body))
            if revision.full_body
            else revision
        )
        payload = {
            "note_revision_id": revision.note_revision_id,
            "title": revision.title,
            "coverage": revision.coverage.value,
            "section_order": detect_section_order(revision.full_body or ""),
            "related_provider_codes": revision.related_provider_codes,
            "blocks": [
                {
                    "ordinal": block.ordinal,
                    "speaker_kind": block.speaker_kind.value,
                    "speaker_label": block.speaker_label,
                    "body": block.body,
                    "section_date": block.section_date,
                }
                for block in effective_revision.blocks
            ],
            "previous_interpretation": (
                json.loads(previous_payload_json) if previous_payload_json else None
            ),
        }
        schema = _Interpretation.model_json_schema()
        base_messages = (
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        )
        request = ModelRequest(
            messages=base_messages,
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": _TOOL_NAME,
                        "description": "Submit one bounded Moomoo note interpretation draft.",
                        "strict": True,
                        "parameters": schema,
                    },
                },
            ),
            model=self._model,
            reasoning_mode="thinking",
            reasoning_effort="max",
            max_output_tokens=5000,
            native_web_search=False,
        )
        try:
            value: _Interpretation | None = None
            validation_error: Exception | None = None
            repair_context: tuple[ModelMessage, ...] = ()
            for attempt in range(2):
                candidate = request
                response = ModelResponse()
                if attempt == 1:
                    candidate = replace(
                        request,
                        messages=(
                            *base_messages,
                            *repair_context,
                            {
                                "role": "user",
                                "content": (
                                    "上一响应的工具参数不是完整有效 JSON。保持事实不变，"
                                    "重新调用同一工具一次；不得输出普通文本或额外字段。"
                                ),
                            },
                        ),
                    )
                try:
                    response = await asyncio.wait_for(
                        self._provider.complete(candidate), timeout=self._timeout_seconds
                    )
                    value = _parse(response, revision)
                    _validate_attribution(value, effective_revision)
                    break
                except (ValidationError, TypeError, ValueError) as error:
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
                                        "validation": _validation_hint(error),
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            ),
                        )
            if value is None:
                raise _InterpretationOutputError(
                    _output_error_code(validation_error)
                ) from validation_error
            return ExternalNoteInterpretation(
                interpretation_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_INTERPRETATION),
                note_revision_id=revision.note_revision_id,
                status="SUCCEEDED",
                provider=self._provider_name,
                model=self._model,
                reasoning_effort="max",
                schema_version=_SCHEMA_VERSION,
                payload_json=value.model_dump_json(),
                error_code=None,
                created_at=self._clock.now(),
            )
        except TimeoutError:
            error_code = "PROVIDER_TIMEOUT_ERROR"
        except TradingPartnerError as error:
            error_code = error.code
        except _InterpretationOutputError as error:
            error_code = error.code
        except (ValidationError, TypeError, ValueError):
            error_code = "NOTE_INTERPRETATION_INVALID_OUTPUT"
        return ExternalNoteInterpretation(
            interpretation_id=self._ids.new(EntityIdPrefix.EXTERNAL_NOTE_INTERPRETATION),
            note_revision_id=revision.note_revision_id,
            status="FAILED",
            provider=self._provider_name,
            model=self._model,
            reasoning_effort="max",
            schema_version=_SCHEMA_VERSION,
            payload_json="{}",
            error_code=error_code,
            created_at=self._clock.now(),
        )


def _parse(response: ModelResponse, revision: ExternalNoteRevision) -> _Interpretation:
    if len(response.tool_calls) != 1 or response.tool_calls[0].name != _TOOL_NAME:
        raise _InterpretationOutputError("NOTE_INTERPRETATION_TOOL_CALL_MISSING")
    try:
        raw = json.loads(response.tool_calls[0].arguments)
    except json.JSONDecodeError:
        raise _InterpretationOutputError("NOTE_INTERPRETATION_JSON_INVALID") from None
    if isinstance(raw, dict) and "interpretation" in raw:
        allowed = {"interpretation", "note_revision_id", "title", "coverage"}
        if not set(raw).issubset(allowed):
            raise _InterpretationOutputError("NOTE_INTERPRETATION_WRAPPER_INVALID")
        expected = {
            "note_revision_id": revision.note_revision_id,
            "title": revision.title,
            "coverage": revision.coverage.value,
        }
        if any(key in raw and raw[key] != value for key, value in expected.items()):
            raise _InterpretationOutputError("NOTE_INTERPRETATION_SOURCE_METADATA_CHANGED")
        raw = raw["interpretation"]
    return _Interpretation.model_validate(raw)


def _validate_attribution(value: _Interpretation, revision: ExternalNoteRevision) -> None:
    blocks = {item.ordinal: item for item in revision.blocks}
    used: set[int] = set()
    for viewpoint in value.viewpoints:
        for ordinal in viewpoint.source_block_ordinals:
            block = blocks.get(ordinal)
            if block is None:
                raise _InterpretationOutputError(
                    "NOTE_INTERPRETATION_ATTRIBUTION_UNKNOWN_BLOCK"
                )
            if viewpoint.speaker_kind != block.speaker_kind.value:
                raise _InterpretationOutputError(
                    "NOTE_INTERPRETATION_ATTRIBUTION_KIND_CHANGED"
                )
            if viewpoint.speaker_label != block.speaker_label:
                raise _InterpretationOutputError(
                    "NOTE_INTERPRETATION_ATTRIBUTION_LABEL_CHANGED"
                )
            used.add(ordinal)
    if not used:
        raise _InterpretationOutputError("NOTE_INTERPRETATION_NO_CITATIONS")


def _output_error_code(error: Exception | None) -> str:
    if isinstance(error, _InterpretationOutputError):
        return error.code
    if isinstance(error, ValidationError):
        issues = error.errors(include_input=False, include_url=False)
        if issues:
            issue = issues[0]
            location = [str(item) for item in issue["loc"] if not isinstance(item, int)]
            field = "_".join(location[-2:] or ["ROOT"])
            field = re.sub(r"[^A-Z0-9_]+", "_", field.upper()).strip("_") or "ROOT"
            issue_type = str(issue["type"])
            category = {
                "missing": "MISSING",
                "extra_forbidden": "EXTRA",
                "literal_error": "ENUM",
                "string_too_long": "TOO_LONG",
                "too_long": "TOO_MANY",
                "too_short": "TOO_FEW",
            }.get(issue_type, "INVALID")
            return f"NOTE_INTERPRETATION_SCHEMA_{category}_{field}"[:128]
        return "NOTE_INTERPRETATION_SCHEMA_INVALID"
    if isinstance(error, TypeError):
        return "NOTE_INTERPRETATION_ARGUMENT_TYPE_INVALID"
    return "NOTE_INTERPRETATION_INVALID_OUTPUT"


def _validation_hint(error: Exception) -> list[dict[str, object]]:
    if isinstance(error, ValidationError):
        return [
            {"location": list(item["loc"]), "type": item["type"]}
            for item in error.errors(include_input=False, include_url=False)[:20]
        ]
    return [{"location": [], "type": type(error).__name__}]


__all__ = ["ExternalNoteInterpretationService"]
