"""Port for bounded structured interpretation of deterministic Monitor facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MonitorJudgmentRequest:
    playbook: str
    confirmed_state_json: str
    feature_snapshot_json: str
    allowed_feature_ids: tuple[str, ...]
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class MonitorJudgmentResponse:
    urgency: str
    phase: str
    market_state: str
    divergence: str
    conclusion: str
    quantity_min: int
    quantity_max: int
    summary: str
    evidence_feature_ids: tuple[str, ...]
    next_trigger: str
    invalidation: str
    reasoning_effort_used: str = "unknown"
    web_search_used: bool = False
    web_source_urls: tuple[str, ...] = ()


class MonitorJudgmentProvider(Protocol):
    provider_name: str
    model: str
    reasoning_effort: str

    async def judge(self, request: MonitorJudgmentRequest) -> MonitorJudgmentResponse: ...
