"""Shared, secret-safe Agent conversation cost and lifecycle aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from pydantic import ValidationError

from application.dto.copilot_research import CopilotResearchReceipt
from application.ports.agent_conversation_repository import AgentConversationRepository
from domain.agent.enums import AgentTurnStatus

_MAX_ITEMS = 500
_MAX_RECEIPT_BYTES = 16_384
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "web_search_calls",
    "web_extractor_calls",
)


@dataclass(frozen=True, slots=True)
class AgentConversationMetrics:
    conversation_id: str
    model_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    web_search_calls: int
    web_extractor_calls: int
    web_search_used_turns: int
    web_extractor_used_turns: int
    latency_ms: int
    turn_statuses: dict[str, int]
    api_styles: tuple[str, ...]
    malformed_receipt_count: int
    sampled_messages: int
    sampled_turns: int
    truncated: bool
    research_profiles: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "web_search_calls": self.web_search_calls,
            "web_extractor_calls": self.web_extractor_calls,
            "web_search_used_turns": self.web_search_used_turns,
            "web_extractor_used_turns": self.web_extractor_used_turns,
            "latency_ms": self.latency_ms,
            "turn_statuses": dict(self.turn_statuses),
            "api_styles": list(self.api_styles),
            "malformed_receipt_count": self.malformed_receipt_count,
            "sampled_messages": self.sampled_messages,
            "sampled_turns": self.sampled_turns,
            "truncated": self.truncated,
            "research_profiles": list(self.research_profiles),
        }


class AgentConversationMetricsService:
    """Read durable messages/turns and aggregate only bounded receipt fields."""

    def __init__(self, repository: AgentConversationRepository) -> None:
        self._repository = repository

    def aggregate(self, conversation_id: str) -> AgentConversationMetrics:
        messages = self._repository.list_messages(
            conversation_id,
            limit=_MAX_ITEMS,
            newest_first=False,
        )
        turns = self._repository.list_turns(
            conversation_id,
            limit=_MAX_ITEMS,
            newest_first=False,
        )
        totals = {field: 0 for field in _USAGE_FIELDS}
        model_calls = 0
        latency_ms = 0
        web_search_used_turns = 0
        web_extractor_used_turns = 0
        malformed = 0
        api_styles: set[str] = set()
        for message in messages:
            raw = message.model_receipt_json
            if raw is None:
                continue
            parsed = _parse_receipt(raw)
            if parsed is None:
                malformed += 1
                continue
            calls = parsed.get("model_calls")
            if type(calls) is int and calls >= 0:
                model_calls += calls
            usage = parsed.get("usage")
            if isinstance(usage, dict):
                for field_name in _USAGE_FIELDS:
                    value = usage.get(field_name)
                    if type(value) is int and value >= 0:
                        totals[field_name] += value
            latency = parsed.get("latency_ms")
            if type(latency) is int and latency >= 0:
                latency_ms += latency
            if parsed.get("web_search_used") is True:
                web_search_used_turns += 1
            if parsed.get("web_extractor_used") is True:
                web_extractor_used_turns += 1
            style = parsed.get("api_style")
            if isinstance(style, str) and 0 < len(style) <= 64:
                api_styles.add(style)

        statuses = {status.value: 0 for status in AgentTurnStatus}
        for turn in turns:
            statuses[turn.status.value] = statuses.get(turn.status.value, 0) + 1
        return AgentConversationMetrics(
            conversation_id=conversation_id,
            model_calls=model_calls,
            input_tokens=totals["input_tokens"],
            output_tokens=totals["output_tokens"],
            total_tokens=totals["total_tokens"],
            web_search_calls=totals["web_search_calls"],
            web_extractor_calls=totals["web_extractor_calls"],
            web_search_used_turns=web_search_used_turns,
            web_extractor_used_turns=web_extractor_used_turns,
            latency_ms=latency_ms,
            turn_statuses=statuses,
            api_styles=tuple(sorted(api_styles)),
            malformed_receipt_count=malformed,
            sampled_messages=len(messages),
            sampled_turns=len(turns),
            research_profiles=_research_profiles(messages, turns),
            # Repositories cap reads at 500. Treat a full sample as a
            # conservative truncation signal so callers do not overstate totals.
            truncated=len(messages) >= _MAX_ITEMS or len(turns) >= _MAX_ITEMS,
        )


def _research_tokens(
    values: list[tuple[CopilotResearchReceipt, dict[str, Any]]],
    field_name: str,
) -> int | None:
    valid = []
    for _, raw in values:
        usage = raw.get("usage")
        if isinstance(usage, dict):
            value = usage.get(field_name)
            if type(value) is int and value >= 0:
                valid.append(value)
    return sum(valid) if valid else None


def _research_profiles(messages: Any, turns: Any) -> tuple[dict[str, Any], ...]:
    """Descriptive telemetry by actual model, effort and budget; never a model ranking."""
    by_message = {turn.assistant_message_id: turn for turn in turns if turn.assistant_message_id}
    groups: dict[tuple[Any, ...], list[tuple[CopilotResearchReceipt, dict[str, Any]]]] = {}
    for message in messages:
        if str(message.role) != "ASSISTANT" or not message.model_receipt_json:
            continue
        raw = _parse_receipt(message.model_receipt_json)
        if raw is None or not isinstance(raw.get("research"), dict):
            continue
        try:
            research = CopilotResearchReceipt.model_validate(raw["research"])
        except ValidationError:
            continue
        if research.phase != "FINISHED":
            continue
        turn = by_message.get(message.message_id)
        key = (
            turn.model_id
            if turn
            else (
                raw.get("selected_provider_id")
                if isinstance(raw.get("selected_provider_id"), str)
                else None
            ),
            turn.model if turn else message.model,
            turn.reasoning_effort if turn else None,
            research.mode,
            research.max_seconds,
            research.max_model_calls,
            research.max_tool_calls,
        )
        groups.setdefault(key, []).append((research, raw))
    result = []
    for key, values in groups.items():
        durations = sorted(item.elapsed_ms for item, _ in values)
        usage_complete = all(item.usage_complete for item, _ in values)
        result.append(
            {
                "provider": key[0],
                "model": key[1],
                "reasoning_effort": key[2],
                "mode": key[3],
                "max_seconds": key[4],
                "max_model_calls": key[5],
                "max_tool_calls": key[6],
                "sample_count": len(values),
                "completed_count": sum(item.stop_reason == "COMPLETED" for item, _ in values),
                "p50_elapsed_ms": durations[ceil(len(durations) * 0.50) - 1],
                "p95_elapsed_ms": durations[ceil(len(durations) * 0.95) - 1],
                "input_tokens": _research_tokens(values, "input_tokens"),
                "output_tokens": _research_tokens(values, "output_tokens"),
                "usage_complete": usage_complete,
                "cost_usd": None,
            }
        )
    return tuple(result)


def _parse_receipt(raw: str) -> dict[str, Any] | None:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = ["AgentConversationMetrics", "AgentConversationMetricsService"]
