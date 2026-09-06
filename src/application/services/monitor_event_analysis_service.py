"""Bounded model interpretation appended to deterministic Monitor events."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from application.ports.agent_model_provider import AgentModelProvider, ModelRequest, ModelResponse
from domain.common.errors import TradingPartnerError
from domain.monitoring.models import MonitorDefinition, MonitorEvent, MonitorRunObservation

MONITOR_EVENT_ANALYSIS_MAX_CHARS = 160
_TOOL_NAME = "submit_monitor_event_analysis"
_UNAVAILABLE = "模型分析暂不可用；确定性规则结果仍然有效。"
_SYSTEM_PROMPT = """你是只读 Monitor 事件解释器。
只能使用用户 JSON 中的确定性事件、规则和数据质量事实。用简体中文概括事件含义与下一项关注，
最多两句；不要复述整条通知，不要虚构价格、成交或原因，不要修改事件状态，不要建议下单、仓位
数量或自动操作。RECOVERED 只表示条件解除，不代表行情转好；NOT_EVALUATED 不是规则通过。
必须调用 submit_monitor_event_analysis 一次，不要输出普通文本。"""


class _AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: str = Field(min_length=1, max_length=300)

    @field_validator("analysis")
    @classmethod
    def _require_chinese(cls, value: str) -> str:
        if re.search(r"[\u3400-\u9fff]", value) is None:
            raise ValueError("Monitor event analysis must contain Chinese")
        return value


@dataclass(frozen=True, slots=True)
class MonitorEventAnalysisResult:
    analysis: str
    warning_codes: tuple[str, ...] = ()


class MonitorEventAnalysisService:
    """Interpret one Monitor's transition batch without changing its facts."""

    def __init__(
        self,
        provider: AgentModelProvider,
        *,
        timeout_seconds: float = 80.0,
        max_chars: int = MONITOR_EVENT_ANALYSIS_MAX_CHARS,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = max(1.0, min(timeout_seconds, 120.0))
        self._max_chars = max(40, min(max_chars, 300))

    async def analyze(
        self,
        monitor: MonitorDefinition,
        events: tuple[MonitorEvent, ...],
        observations: tuple[MonitorRunObservation, ...],
    ) -> MonitorEventAnalysisResult:
        if not events:
            return MonitorEventAnalysisResult(analysis=_UNAVAILABLE)
        rules = {item.rule_code: item for item in monitor.rules}
        observation_by_code = {item.rule_code: item for item in observations}
        payload = {
            "monitor": monitor.name,
            "primary_instrument_id": monitor.primary_instrument_id,
            "events": [
                {
                    "rule_code": event.rule_code,
                    "event_type": event.event_type.value,
                    "severity": event.severity.value,
                    "description": (
                        rules[event.rule_code].description
                        if event.rule_code in rules
                        else event.message
                    ),
                    "observed_value": _wire(event.observed_value),
                    "threshold_value": _wire(event.threshold_value),
                    "fact_as_of": (
                        event.fact_as_of.isoformat() if event.fact_as_of is not None else None
                    ),
                    "warning_codes": list(
                        observation_by_code[event.rule_code].warning_codes
                        if event.rule_code in observation_by_code
                        else ()
                    ),
                    "error_codes": list(
                        observation_by_code[event.rule_code].error_codes
                        if event.rule_code in observation_by_code
                        else ()
                    ),
                }
                for event in events[:12]
            ],
            "output_contract": {
                "language": "zh-CN",
                "max_chars": self._max_chars,
                "sentences": 2,
            },
        }
        schema = _AnalysisResponse.model_json_schema()
        request = ModelRequest(
            messages=(
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ),
            session_id=monitor.monitor_id,
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": _TOOL_NAME,
                        "description": "Submit one short read-only Monitor event analysis.",
                        "strict": True,
                        "parameters": schema,
                    },
                },
            ),
            model=getattr(self._provider, "model", None),
            reasoning_mode=_reasoning_mode(self._provider),
            reasoning_effort="max",
            max_output_tokens=384,
            native_web_search=False,
        )
        try:
            response = await asyncio.wait_for(
                self._provider.complete(request),
                timeout=self._timeout_seconds,
            )
            parsed = _parse(response)
            return MonitorEventAnalysisResult(
                analysis=_bounded_text(parsed.analysis, self._max_chars)
            )
        except (TimeoutError, TradingPartnerError, ValidationError, ValueError, TypeError):
            return MonitorEventAnalysisResult(
                analysis=_UNAVAILABLE,
                warning_codes=("MONITOR_EVENT_ANALYSIS_UNAVAILABLE",),
            )


def _parse(response: ModelResponse) -> _AnalysisResponse:
    if response.tool_calls:
        if len(response.tool_calls) != 1 or response.tool_calls[0].name != _TOOL_NAME:
            raise ValueError("unexpected Monitor event analysis tool call")
        raw = response.tool_calls[0].arguments
    else:
        raw = response.text.strip()
        if raw.startswith("```json") and raw.endswith("```"):
            raw = raw[7:-3].strip()
        elif raw.startswith("```") and raw.endswith("```"):
            raw = raw[3:-3].strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("Monitor event analysis must be an object")
    projected = {field: value[field] for field in _AnalysisResponse.model_fields if field in value}
    return _AnalysisResponse.model_validate(projected)


def _bounded_text(value: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(1, limit - 1)].rstrip("，。；、 ") + "…"


def _reasoning_mode(
    provider: AgentModelProvider,
) -> Literal["none", "effort", "thinking"]:
    value = getattr(provider, "reasoning_mode", "none")
    if value == "effort":
        return "effort"
    if value == "thinking":
        return "thinking"
    return "none"


def _wire(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "MONITOR_EVENT_ANALYSIS_MAX_CHARS",
    "MonitorEventAnalysisResult",
    "MonitorEventAnalysisService",
]
