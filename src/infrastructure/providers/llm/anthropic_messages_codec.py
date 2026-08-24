"""Codec for the Anthropic-compatible Messages protocol."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from application.ports.agent_model_provider import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    ModelUsage,
)
from domain.common.errors import DataContractError


def _mapping(value: ModelMessage | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(value, ModelMessage):
        return value.as_dict()
    if not isinstance(value, Mapping):
        raise DataContractError("LLM Messages request contains an invalid message")
    return {str(key): item for key, item in value.items()}


def _tool(value: ModelTool | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(value, ModelTool):
        result: dict[str, object] = {
            "name": value.name,
            "input_schema": dict(value.parameters),
        }
        if value.description is not None:
            result["description"] = value.description
        return result
    if not isinstance(value, Mapping):
        raise DataContractError("LLM Messages request contains an invalid tool")
    raw = {str(key): item for key, item in value.items()}
    function = raw.get("function")
    if raw.get("type") == "function" and isinstance(function, Mapping):
        name = function.get("name")
        parameters = function.get("parameters", {})
        if not isinstance(name, str) or not name or not isinstance(parameters, Mapping):
            raise DataContractError("LLM Messages tool definition is invalid")
        result = {"name": name, "input_schema": dict(parameters)}
        description = function.get("description")
        if isinstance(description, str):
            result["description"] = description
        return result
    name = raw.get("name")
    schema = raw.get("input_schema", raw.get("parameters", {}))
    if not isinstance(name, str) or not name or not isinstance(schema, Mapping):
        raise DataContractError("LLM Messages tool definition is invalid")
    return {"name": name, "input_schema": dict(schema)}


def _assistant_content(message: Mapping[str, object]) -> list[dict[str, object]]:
    content: list[dict[str, object]] = []
    content.extend(_content_blocks(message.get("content")))
    calls = message.get("tool_calls", ())
    if calls is None:
        calls = ()
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        raise DataContractError("LLM Messages assistant tool calls are invalid")
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            raise DataContractError("LLM Messages assistant tool call is invalid")
        function = call.get("function")
        if not isinstance(function, Mapping):
            raise DataContractError("LLM Messages assistant function call is invalid")
        name = function.get("name")
        arguments = function.get("arguments", "{}")
        if not isinstance(name, str) or not name:
            raise DataContractError("LLM Messages assistant function name is invalid")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (TypeError, ValueError) as error:
            raise DataContractError(
                "LLM Messages assistant function arguments are invalid"
            ) from error
        if not isinstance(parsed, Mapping):
            raise DataContractError("LLM Messages function arguments must be an object")
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{index}"
        content.append(
            {"type": "tool_use", "id": call_id, "name": name, "input": dict(parsed)}
        )
    return content or [{"type": "text", "text": ""}]


def _content_blocks(value: object) -> list[dict[str, object]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value else []
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DataContractError("LLM Messages content is invalid")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DataContractError("LLM Messages content part is invalid")
        part_type = item.get("type")
        if part_type == "text":
            text = item.get("text")
            if not isinstance(text, str):
                raise DataContractError("LLM Messages text content is invalid")
            result.append({"type": "text", "text": text})
            continue
        if part_type != "image_url":
            raise DataContractError("LLM Messages content part type is unsupported")
        image_url = item.get("image_url")
        if not isinstance(image_url, Mapping) or not isinstance(image_url.get("url"), str):
            raise DataContractError("LLM Messages image content is invalid")
        match = re.fullmatch(
            r"data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)",
            image_url["url"],
        )
        if match is None:
            raise DataContractError("LLM Messages image must be an inline data URL")
        try:
            base64.b64decode(match.group(2), validate=True)
        except ValueError as error:
            raise DataContractError("LLM Messages image data is invalid") from error
        result.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": match.group(1),
                    "data": match.group(2),
                },
            }
        )
    return result


class AnthropicMessagesCodec:
    @staticmethod
    def encode(
        request: ModelRequest,
        *,
        model: str,
        max_output_tokens: int,
    ) -> dict[str, object]:
        system_parts: list[str] = []
        messages: list[dict[str, object]] = []
        for value in request.messages:
            message = _mapping(value)
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                if isinstance(content, str) and content:
                    system_parts.append(content)
                continue
            if role == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise DataContractError("LLM Messages tool result lacks call id")
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": content if isinstance(content, str) else "",
                            }
                        ],
                    }
                )
                continue
            if role == "assistant":
                messages.append(
                    {"role": "assistant", "content": _assistant_content(message)}
                )
                continue
            if role != "user":
                raise DataContractError("LLM Messages request role/content is invalid")
            messages.append({"role": "user", "content": _content_blocks(content) or ""})

        effective_max_tokens = request.max_output_tokens or max_output_tokens
        payload: dict[str, object] = {
            "model": request.model or model,
            "messages": messages,
            "max_tokens": effective_max_tokens,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.tools:
            payload["tools"] = [_tool(item) for item in request.tools]
        if request.reasoning_mode == "thinking" and request.reasoning_effort:
            requested_budget = {
                "low": 512,
                "medium": 1_024,
                "high": 2_048,
                "max": 4_096,
            }.get(request.reasoning_effort, 2_048)
            # Reserve at least half of the caller's output budget for the
            # visible answer; an all-thinking response is unusable to Monitor.
            budget = min(requested_budget, effective_max_tokens // 2)
            if budget >= 512:
                payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                }
        return payload

    @staticmethod
    def decode(payload: Mapping[str, Any]) -> ModelResponse:
        content = payload.get("content")
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
            raise DataContractError("LLM Messages response content is invalid")
        text_parts: list[str] = []
        calls: list[ModelToolCall] = []
        for index, item in enumerate(content):
            if not isinstance(item, Mapping):
                raise DataContractError("LLM Messages response block is invalid")
            if item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif item.get("type") == "tool_use":
                call_id = item.get("id")
                name = item.get("name")
                arguments = item.get("input", {})
                if not isinstance(call_id, str) or not call_id:
                    call_id = f"call_{index}"
                if not isinstance(name, str) or not name or not isinstance(arguments, Mapping):
                    raise DataContractError("LLM Messages tool-use block is invalid")
                calls.append(
                    ModelToolCall(
                        id=call_id,
                        name=name,
                        arguments=json.dumps(
                            dict(arguments), ensure_ascii=False, separators=(",", ":")
                        ),
                    )
                )
        usage_raw = payload.get("usage")
        usage = None
        if usage_raw is not None:
            if not isinstance(usage_raw, Mapping):
                raise DataContractError("LLM Messages usage is invalid")
            input_tokens = _token_count(usage_raw.get("input_tokens"))
            output_tokens = _token_count(usage_raw.get("output_tokens"))
            usage = ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=(
                    input_tokens + output_tokens
                    if input_tokens is not None and output_tokens is not None
                    else None
                ),
            )
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(calls),
            usage=usage,
            model=payload.get("model") if isinstance(payload.get("model"), str) else None,
            finish_reason=(
                payload.get("stop_reason")
                if isinstance(payload.get("stop_reason"), str)
                else None
            ),
            request_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
        )


def _token_count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataContractError("LLM Messages token usage is invalid")
    return value


__all__ = ["AnthropicMessagesCodec"]
