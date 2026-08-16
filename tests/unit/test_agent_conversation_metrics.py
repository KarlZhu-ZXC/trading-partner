from __future__ import annotations

import json
from datetime import UTC, datetime

from application.services.agent_conversation_metrics import AgentConversationMetricsService
from domain.agent.enums import AgentChannel, AgentMessageRole, AgentTurnStatus
from domain.agent.models import AgentMessage, AgentTurn


class _Repo:
    def __init__(self) -> None:
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        self.messages = [
            AgentMessage(
                message_id="m-1",
                conversation_id="c-1",
                role=AgentMessageRole.ASSISTANT,
                content="ok",
                model_receipt_json=json.dumps(
                    {
                        "model_calls": 2,
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 4,
                            "total_tokens": 7,
                            "web_search_calls": 1,
                            "web_extractor_calls": 1,
                        },
                        "latency_ms": 120,
                        "web_search_used": True,
                        "api_style": "responses",
                    }
                ),
                created_at=now,
            ),
            AgentMessage(
                message_id="m-2",
                conversation_id="c-1",
                role=AgentMessageRole.ASSISTANT,
                content="bad",
                model_receipt_json="not-json",
                created_at=now,
            ),
        ]
        self.turn = AgentTurn(
            turn_id="t-1",
            conversation_id="c-1",
            user_message_id="m-user",
            channel=AgentChannel.CONSOLE,
            status=AgentTurnStatus.COMPLETED,
            started_at=now,
            updated_at=now,
            completed_at=now,
        )

    def list_messages(self, conversation_id: str, **kwargs):
        return tuple(self.messages)

    def list_turns(self, conversation_id: str, **kwargs):
        return (self.turn,)


def test_metrics_aggregate_usage_and_ignore_malformed_receipts() -> None:
    metrics = AgentConversationMetricsService(_Repo()).aggregate("c-1")
    assert metrics.model_calls == 2
    assert metrics.total_tokens == 7
    assert metrics.web_search_calls == 1
    assert metrics.web_extractor_calls == 1
    assert metrics.web_search_used_turns == 1
    assert metrics.latency_ms == 120
    assert metrics.api_styles == ("responses",)
    assert metrics.malformed_receipt_count == 1
    assert metrics.turn_statuses["COMPLETED"] == 1
    assert metrics.truncated is False
