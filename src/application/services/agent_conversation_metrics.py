"""Shared, secret-safe Agent conversation cost and lifecycle aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

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
            # Repositories cap reads at 500. Treat a full sample as a
            # conservative truncation signal so callers do not overstate totals.
            truncated=len(messages) >= _MAX_ITEMS or len(turns) >= _MAX_ITEMS,
        )


def _parse_receipt(raw: str) -> dict[str, Any] | None:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = ["AgentConversationMetrics", "AgentConversationMetricsService"]
