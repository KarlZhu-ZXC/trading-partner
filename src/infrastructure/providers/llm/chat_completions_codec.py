"""Codec for the OpenAI-compatible ``/chat/completions`` protocol."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from application.ports.agent_model_provider import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelTool,
    ModelToolCall,
    ModelUsage,
)
from domain.common.errors import DataContractError


def _message(value: ModelMessage | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(value, ModelMessage):
        return value.as_dict()
    if not isinstance(value, Mapping):
        raise DataContractError("LLM chat request contains an invalid message")
    return {str(key): item for key, item in value.items()}


def _tool(value: ModelTool | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(value, ModelTool):
        return value.as_dict()
    if not isinstance(value, Mapping):
        raise DataContractError("LLM chat request contains an invalid tool")
    return {str(key): item for key, item in value.items()}


class ChatCompletionsCodec:
    """Encode/decode Chat Completions payloads without vendor assumptions."""

    @staticmethod
    def encode(
        request: ModelRequest,
        *,
        model: str | None = None,
        max_output_tokens: int = 8000,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model or model or "",
            "messages": [_message(item) for item in request.messages],
            "max_tokens": request.max_output_tokens or max_output_tokens,
        }
        if request.tools:
            payload["tools"] = [_tool(item) for item in request.tools]
        if request.reasoning_effort:
            payload["reasoning_effort"] = request.reasoning_effort
        if request.reasoning_mode == "thinking":
            payload["thinking"] = {"type": "enabled"}
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema_name or "structured_response",
                    "strict": True,
                    "schema": dict(request.response_schema),
                },
            }
        elif request.json_output:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def decode(payload: Mapping[str, Any]) -> ModelResponse:
        choices = payload.get("choices")
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            raise DataContractError("LLM chat response has no choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise DataContractError("LLM chat response choice is invalid")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise DataContractError("LLM chat response message is invalid")

        text = _content_text(message.get("content"))
        calls = _tool_calls(message.get("tool_calls"))
        usage = _usage(payload.get("usage"))
        finish_reason = first.get("finish_reason")
        return ModelResponse(
            text=text,
            tool_calls=calls,
            usage=usage,
            model=str(payload["model"]) if isinstance(payload.get("model"), str) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )

    @staticmethod
    def decode_stream_event(payload: Mapping[str, Any]) -> ModelStreamChunk:
        """Decode one Chat Completions SSE JSON object.

        Providers are inconsistent about whether ``usage`` appears on the
        final ``[DONE]`` event or the preceding choice.  This method accepts
        either shape and leaves aggregation to the runtime.
        """

        choices = payload.get("choices", ())
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
            raise DataContractError("LLM chat stream choices are invalid")
        if not choices:
            return ModelStreamChunk(
                usage=_usage(payload.get("usage")),
                model=payload.get("model") if isinstance(payload.get("model"), str) else None,
                request_id=next(
                    (
                        payload.get(key)
                        for key in ("id", "request_id")
                        if isinstance(payload.get(key), str) and payload.get(key)
                    ),
                    None,
                ),
                done=True,
            )
        first = choices[0]
        if not isinstance(first, Mapping):
            raise DataContractError("LLM chat stream choice is invalid")
        delta = first.get("delta", {})
        if not isinstance(delta, Mapping):
            raise DataContractError("LLM chat stream delta is invalid")
        text = _content_text(delta.get("content"))
        calls = _stream_tool_calls(delta.get("tool_calls"))
        finish_reason = first.get("finish_reason")
        return ModelStreamChunk(
            text_delta=text,
            tool_calls=calls,
            usage=_usage(payload.get("usage")),
            model=payload.get("model") if isinstance(payload.get("model"), str) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            request_id=next(
                (
                    payload.get(key)
                    for key in ("id", "request_id")
                    if isinstance(payload.get(key), str) and payload.get(key)
                ),
                None,
            ),
            done=finish_reason is not None,
        )


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    raise DataContractError("LLM chat response content is invalid")


def _tool_calls(raw: object) -> tuple[ModelToolCall, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise DataContractError("LLM chat response tool calls are invalid")
    result: list[ModelToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise DataContractError("LLM chat response tool call is invalid")
        function = item.get("function")
        if not isinstance(function, Mapping):
            raise DataContractError("LLM chat response function call is invalid")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DataContractError("LLM chat response function name is invalid")
        arguments = function.get("arguments", "")
        if isinstance(arguments, Mapping):
            arguments = json.dumps(dict(arguments), ensure_ascii=False, separators=(",", ":"))
        if not isinstance(arguments, str):
            raise DataContractError("LLM chat response function arguments are invalid")
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"call_{index}"
        result.append(ModelToolCall(id=call_id, name=name, arguments=arguments))
    return tuple(result)


def _stream_tool_calls(raw: object) -> tuple[ModelToolCall, ...]:
    """Decode permissive tool-call deltas (name may arrive only once)."""

    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise DataContractError("LLM chat stream tool calls are invalid")
    result: list[ModelToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise DataContractError("LLM chat stream tool call is invalid")
        function = item.get("function")
        if not isinstance(function, Mapping):
            raise DataContractError("LLM chat stream function call is invalid")
        name = function.get("name", "")
        arguments = function.get("arguments", "")
        if not isinstance(name, str) or not isinstance(arguments, str):
            raise DataContractError("LLM chat stream function fields are invalid")
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{index}"
        result.append(ModelToolCall(id=call_id, name=name, arguments=arguments))
    return tuple(result)


def _usage(raw: object) -> ModelUsage | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise DataContractError("LLM chat response usage is invalid")
    return ModelUsage(
        input_tokens=_nonnegative_int(raw.get("prompt_tokens")),
        output_tokens=_nonnegative_int(raw.get("completion_tokens")),
        total_tokens=_nonnegative_int(raw.get("total_tokens")),
    )


def _nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataContractError("LLM response usage must contain nonnegative integers")
    return value


def encode_request(
    request: ModelRequest, *, model: str, max_output_tokens: int
) -> dict[str, object]:
    """Functional alias used by small adapters and tests."""

    return ChatCompletionsCodec.encode(request, model=model, max_output_tokens=max_output_tokens)


def decode_response(payload: Mapping[str, Any]) -> ModelResponse:
    return ChatCompletionsCodec.decode(payload)


__all__ = ["ChatCompletionsCodec", "decode_response", "encode_request"]
