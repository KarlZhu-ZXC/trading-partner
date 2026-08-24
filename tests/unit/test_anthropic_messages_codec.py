from __future__ import annotations

from application.ports.agent_model_provider import (
    ModelMessage,
    ModelRequest,
    ModelTool,
    ModelToolCall,
)
from infrastructure.providers.llm.anthropic_messages_codec import AnthropicMessagesCodec


def test_messages_codec_translates_tool_history_and_result() -> None:
    request = ModelRequest(
        messages=(
            ModelMessage(role="system", content="system"),
            ModelMessage(role="user", content="question"),
            ModelMessage(
                role="assistant",
                tool_calls=(ModelToolCall(id="call_1", name="lookup", arguments='{"x":1}'),),
            ),
            ModelMessage(role="tool", tool_call_id="call_1", content='{"ok":true}'),
        ),
        tools=(ModelTool(name="lookup", parameters={"type": "object"}),),
        reasoning_mode="thinking",
        reasoning_effort="max",
    )

    payload = AnthropicMessagesCodec.encode(
        request,
        model="qwen3.8-max",
        max_output_tokens=2000,
    )

    assert payload["system"] == "system"
    assert payload["messages"][1]["content"][0] == {  # type: ignore[index]
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup",
        "input": {"x": 1},
    }
    assert payload["messages"][2]["content"][0]["tool_use_id"] == "call_1"  # type: ignore[index]
    assert payload["tools"] == [{"name": "lookup", "input_schema": {"type": "object"}}]
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1000}


def test_messages_codec_decodes_text_tools_and_usage() -> None:
    response = AnthropicMessagesCodec.decode(
        {
            "id": "msg_1",
            "model": "qwen3.8-max",
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"x": 1}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )

    assert response.text == "checking"
    assert response.tool_calls[0].arguments == '{"x":1}'
    assert response.usage is not None and response.usage.total_tokens == 15
    assert response.request_id == "msg_1"
