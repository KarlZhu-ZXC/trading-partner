"""Optional bounded LLM narrative port for Trade Retro."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TradeRetroNarrativeRequest:
    deterministic_facts_json: str


@dataclass(frozen=True, slots=True)
class TradeRetroNarrativeResponse:
    summary_markdown: str
    provider_name: str
    model: str


class TradeRetroNarrativeProvider(Protocol):
    provider_name: str
    model: str

    async def narrate(
        self, request: TradeRetroNarrativeRequest
    ) -> TradeRetroNarrativeResponse: ...
