from __future__ import annotations

from application.ports.agent_model_provider import ModelMessage, ModelRequest, ModelTool
from infrastructure.providers.llm.chat_completions_codec import ChatCompletionsCodec
from infrastructure.providers.llm.responses_codec import ResponsesCodec


def test_chat_codec_round_trips_text_tool_calls_and_usage() -> None:
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="查持仓"),),
        tools=(ModelTool(name="tp_read", parameters={"type": "object"}),),
    )
    encoded = ChatCompletionsCodec.encode(request, model="fake", max_output_tokens=100)
    assert encoded["model"] == "fake"
    assert encoded["max_tokens"] == 100
    assert encoded["tools"]

    result = ChatCompletionsCodec.decode(
        {
            "model": "fake",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "tp_read",
                                    "arguments": '{"operation":"positions"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
    )
    assert result.tool_calls[0].name == "tp_read"
    assert result.tool_calls[0].arguments == '{"operation":"positions"}'
    assert result.usage is not None
    assert result.usage.total_tokens == 8


def test_codecs_encode_strict_json_schema_for_each_openai_protocol() -> None:
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["OK"]}},
        "required": ["status"],
        "additionalProperties": False,
    }
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="return status"),),
        response_schema_name="status_result",
        response_schema=schema,
    )

    chat = ChatCompletionsCodec.encode(request, model="fake")
    responses = ResponsesCodec.encode(request, model="fake")

    assert chat["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "status_result",
            "strict": True,
            "schema": schema,
        },
    }
    assert responses["text"] == {
        "format": {
            "type": "json_schema",
            "name": "status_result",
            "strict": True,
            "schema": schema,
        }
    }


def test_responses_codec_collects_text_tool_calls_and_bounded_web_sources() -> None:
    result = ResponsesCodec.decode(
        {
            "model": "fake",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "回答"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_2",
                    "name": "tp_read",
                    "arguments": {"operation": "quote"},
                },
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {"url": "https://example.com/source"},
                            {"url": "javascript:alert(1)"},
                        ]
                    },
                },
                {"type": "web_extractor_call"},
            ],
            "id": "resp_safe_123",
            "usage": {
                "input_tokens": 7,
                "output_tokens": 4,
                "total_tokens": 11,
                "x_tools": {
                    "web_search": {"count": 1},
                    "web_extractor": {"count": 2},
                },
            },
        }
    )
    assert result.text == "回答"
    assert result.tool_calls[0].arguments == '{"operation":"quote"}'
    assert result.web_search_used is True
    assert result.web_extractor_used is True
    assert result.web_source_urls == ("https://example.com/source",)
    assert result.usage is not None
    assert result.usage.input_tokens == 7
    assert result.usage.web_search_calls == 1
    assert result.usage.web_extractor_calls == 2
    assert result.request_id == "resp_safe_123"


def test_responses_codec_flattens_function_tool_definition() -> None:
    encoded = ResponsesCodec.encode(
        ModelRequest(
            messages=(ModelMessage(role="user", content="查持仓"),),
            tools=(
                ModelTool(
                    name="tp_read",
                    description="Read one capability",
                    parameters={"type": "object", "properties": {}},
                ),
            ),
        ),
        model="fake",
        native_web_search=True,
        native_web_extractor=True,
    )

    assert encoded["store"] is False
    assert encoded["tools"] == [
        {
            "type": "function",
            "name": "tp_read",
            "description": "Read one capability",
            "parameters": {"type": "object", "properties": {}},
        },
        {"type": "web_search"},
        {"type": "web_extractor"},
    ]
