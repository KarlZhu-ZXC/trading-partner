"""Codec for OpenAI-compatible ``/responses`` payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

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

MAX_WEB_SOURCE_URLS = 10
MAX_WEB_SOURCE_URL_LENGTH = 2048


def _message(value: ModelMessage | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(value, ModelMessage):
        return value.as_dict()
    if not isinstance(value, Mapping):
        raise DataContractError("LLM Responses request contains an invalid message")
    return {str(key): item for key, item in value.items()}


def _content(value: object) -> object:
    if isinstance(value, str) or value is None:
        return value
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DataContractError("LLM Responses message content is invalid")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DataContractError("LLM Responses content part is invalid")
        part_type = item.get("type")
        if part_type == "text":
            text = item.get("text")
            if not isinstance(text, str):
                raise DataContractError("LLM Responses text content is invalid")
            result.append({"type": "input_text", "text": text})
        elif part_type == "image_url":
            image_url = item.get("image_url")
            if not isinstance(image_url, Mapping) or not isinstance(image_url.get("url"), str):
                raise DataContractError("LLM Responses image content is invalid")
            result.append({"type": "input_image", "image_url": image_url["url"]})
        else:
            raise DataContractError("LLM Responses content part type is unsupported")
    return result


def _messages(values: Sequence[ModelMessage | Mapping[str, Any]]) -> list[dict[str, object]]:
    """Translate chat-style tool history to Responses input items."""

    result: list[dict[str, object]] = []
    for value in values:
        message = _message(value)
        role = message.get("role")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise DataContractError("LLM Responses tool message lacks call id")
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": message.get("content") or "",
                }
            )
            continue

        calls = message.get("tool_calls")
        if (
            role == "assistant"
            and isinstance(calls, Sequence)
            and not isinstance(calls, (str, bytes))
        ):
            assistant = {key: item for key, item in message.items() if key != "tool_calls"}
            if "content" in assistant:
                assistant["content"] = _content(assistant["content"])
            if assistant.get("content") not in (None, ""):
                result.append(assistant)
            for index, raw_call in enumerate(calls):
                if not isinstance(raw_call, Mapping):
                    raise DataContractError("LLM Responses assistant tool call is invalid")
                function = raw_call.get("function")
                if not isinstance(function, Mapping):
                    raise DataContractError("LLM Responses assistant function call is invalid")
                name = function.get("name")
                arguments = function.get("arguments", "")
                call_id = raw_call.get("id")
                if not isinstance(name, str) or not name:
                    raise DataContractError("LLM Responses assistant function name is invalid")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                if not isinstance(call_id, str) or not call_id:
                    call_id = f"call_{index}"
                result.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                )
            continue
        if "content" in message:
            message["content"] = _content(message["content"])
        result.append(message)
    return result


def _tool(value: ModelTool | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(value, ModelTool):
        tool: dict[str, object] = {
            "type": "function",
            "name": value.name,
            "parameters": dict(value.parameters),
        }
        if value.description is not None:
            tool["description"] = value.description
        return tool
    if not isinstance(value, Mapping):
        raise DataContractError("LLM Responses request contains an invalid tool")
    raw = {str(key): item for key, item in value.items()}
    # Provider-neutral callers may supply the Chat Completions-shaped function
    # wrapper. Responses requires name/description/parameters at the tool's
    # top level, so normalize it here instead of leaking protocol details into
    # the application port.
    function = raw.get("function")
    if raw.get("type") == "function" and isinstance(function, Mapping):
        flattened: dict[str, object] = {"type": "function"}
        for key in ("name", "description", "parameters", "strict"):
            if key in function:
                flattened[key] = function[key]
        return flattened
    return raw


class ResponsesCodec:
    """Encode/decode Responses payloads, including bounded web receipts."""

    @staticmethod
    def encode(
        request: ModelRequest,
        *,
        model: str | None = None,
        max_output_tokens: int = 8000,
        native_web_search: bool = False,
        native_web_extractor: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model or model or "",
            "input": _messages(request.messages),
            "max_output_tokens": request.max_output_tokens or max_output_tokens,
            # Responses providers may otherwise retain application state by
            # default. Trading Partner owns its durable conversation history.
            "store": False,
        }
        tools = [_tool(item) for item in request.tools]
        if native_web_search or request.native_web_search:
            tools.append({"type": "web_search"})
        if native_web_extractor:
            tools.append({"type": "web_extractor"})
        if tools:
            payload["tools"] = tools
        if request.reasoning_effort or request.reasoning_mode in {"effort", "thinking"}:
            reasoning: dict[str, object] = {}
            if request.reasoning_effort:
                reasoning["effort"] = request.reasoning_effort
            payload["reasoning"] = reasoning
        if request.response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.response_schema_name or "structured_response",
                    "strict": True,
                    "schema": dict(request.response_schema),
                }
            }
        elif request.json_output:
            payload["text"] = {"format": {"type": "json_object"}}
        return payload

    @staticmethod
    def decode(payload: Mapping[str, Any]) -> ModelResponse:
        if not isinstance(payload, Mapping):
            raise DataContractError("LLM Responses response is not an object")
        text_parts: list[str] = []
        tool_calls: list[ModelToolCall] = []
        source_urls: list[str] = []
        web_search_used = False
        web_extractor_used = False

        direct = payload.get("output_text")
        if isinstance(direct, str):
            text_parts.append(direct)
        elif direct is not None:
            raise DataContractError("LLM Responses output_text is invalid")

        output = payload.get("output", ())
        if output is None:
            output = ()
        if not isinstance(output, Sequence) or isinstance(output, (str, bytes)):
            raise DataContractError("LLM Responses output is invalid")
        for index, item in enumerate(output):
            if not isinstance(item, Mapping):
                raise DataContractError("LLM Responses output item is invalid")
            item_type = item.get("type")
            if item_type == "message":
                _append_message_text(item, text_parts, source_urls)
            elif item_type in {"function_call", "tool_call"}:
                tool_calls.append(_function_call(item, index))
            elif item_type == "web_search_call":
                web_search_used = True
                _append_sources(item, source_urls)
            elif item_type == "web_extractor_call":
                web_extractor_used = True
                _append_sources(item, source_urls)
            else:
                # Unknown output items are ignored by the protocol as long as
                # a usable text/tool result exists; this preserves forwards
                # compatibility with provider annotations.
                _append_sources(item, source_urls)

        # A few compatible Responses endpoints place citations at the top
        # level rather than on the web-search output item.
        _append_sources(payload, source_urls)

        usage = _usage(payload.get("usage"))
        finish_reason = payload.get("status")
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            usage=usage,
            model=str(payload["model"]) if isinstance(payload.get("model"), str) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            web_search_used=web_search_used,
            web_extractor_used=web_extractor_used,
            web_source_urls=tuple(source_urls[:MAX_WEB_SOURCE_URLS]),
            request_id=(
                str(payload["id"]) if isinstance(payload.get("id"), str) and payload["id"] else None
            ),
        )

    @staticmethod
    def decode_stream_event(
        payload: Mapping[str, Any],
        *,
        event_name: str | None = None,
    ) -> ModelStreamChunk:
        """Decode one Responses API SSE event without replaying full text."""

        kind = event_name or payload.get("type")
        if not isinstance(kind, str):
            kind = ""
        request_id = next(
            (
                payload.get(key)
                for key in ("id", "response_id", "request_id")
                if isinstance(payload.get(key), str) and payload.get(key)
            ),
            None,
        )
        model = payload.get("model") if isinstance(payload.get("model"), str) else None
        if kind.endswith("output_text.delta"):
            delta = payload.get("delta", "")
            if not isinstance(delta, str):
                raise DataContractError("LLM Responses stream text delta is invalid")
            return ModelStreamChunk(text_delta=delta, model=model, request_id=request_id)
        if kind.endswith("function_call_arguments.delta"):
            delta = payload.get("delta", "")
            if not isinstance(delta, str):
                raise DataContractError("LLM Responses stream function delta is invalid")
            call_id = payload.get("call_id", payload.get("item_id", "call_0"))
            name = payload.get("name", "")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise DataContractError("LLM Responses stream function identity is invalid")
            return ModelStreamChunk(
                tool_calls=(ModelToolCall(id=call_id, name=name, arguments=delta),),
                model=model,
                request_id=request_id,
            )
        if kind.endswith("output_item.added"):
            item = payload.get("item")
            if isinstance(item, Mapping) and item.get("type") in {"function_call", "tool_call"}:
                call_id = item.get("call_id", item.get("id", "call_0"))
                name = item.get("name", "")
                arguments = item.get("arguments", "")
                if not all(isinstance(value, str) for value in (call_id, name, arguments)):
                    raise DataContractError("LLM Responses stream function item is invalid")
                return ModelStreamChunk(
                    tool_calls=(ModelToolCall(id=call_id, name=name, arguments=arguments),),
                    model=model,
                    request_id=request_id,
                )
            return ModelStreamChunk(model=model, request_id=request_id)
        if kind.endswith("response.completed") or kind in {"completed", "response.done"}:
            response = payload.get("response")
            if isinstance(response, Mapping):
                return ModelStreamChunk(
                    model=model,
                    request_id=request_id,
                    done=True,
                    final_response=ResponsesCodec.decode(response),
                )
            return ModelStreamChunk(
                model=model,
                request_id=request_id,
                usage=_usage(payload.get("usage")),
                done=True,
            )
        # Responses emits lifecycle variants such as
        # ``response.web_search_call.in_progress`` and
        # ``response.web_search_call.completed``; retain the web usage/source
        # marker for all of them rather than only the bare item event.
        if "web_search_call" in kind:
            return ModelStreamChunk(
                web_search_used=True,
                web_source_urls=_stream_sources(payload),
                model=model,
                request_id=request_id,
            )
        if "web_extractor_call" in kind:
            return ModelStreamChunk(
                web_extractor_used=True,
                web_source_urls=_stream_sources(payload),
                model=model,
                request_id=request_id,
            )
        return ModelStreamChunk(
            usage=_usage(payload.get("usage")),
            model=model,
            request_id=request_id,
        )


def _append_message_text(
    item: Mapping[str, Any], text_parts: list[str], source_urls: list[str]
) -> None:
    content = item.get("content", ())
    if isinstance(content, str):
        text_parts.append(content)
        return
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        raise DataContractError("LLM Responses message content is invalid")
    for part in content:
        if not isinstance(part, Mapping):
            raise DataContractError("LLM Responses message content item is invalid")
        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text)
        elif text is not None:
            raise DataContractError("LLM Responses output text is invalid")
        _append_sources(part, source_urls)


def _function_call(item: Mapping[str, Any], index: int) -> ModelToolCall:
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DataContractError("LLM Responses function name is invalid")
    arguments = item.get("arguments", "")
    if isinstance(arguments, Mapping):
        arguments = json.dumps(dict(arguments), ensure_ascii=False, separators=(",", ":"))
    if not isinstance(arguments, str):
        raise DataContractError("LLM Responses function arguments are invalid")
    call_id = item.get("call_id", item.get("id"))
    if not isinstance(call_id, str) or not call_id.strip():
        call_id = f"call_{index}"
    return ModelToolCall(id=call_id, name=name, arguments=arguments)


def _append_sources(item: Mapping[str, Any], urls: list[str]) -> None:
    candidates: list[object] = []
    for key in ("sources", "annotations"):
        value = item.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            candidates.extend(value)
    action = item.get("action")
    if isinstance(action, Mapping):
        value = action.get("sources")
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            candidates.extend(value)
    for source in candidates:
        url: object
        if isinstance(source, str):
            url = source
        elif isinstance(source, Mapping):
            url = source.get("url", source.get("source_url"))
        else:
            continue
        if not isinstance(url, str):
            continue
        safe = _safe_url(url)
        if safe is not None and safe not in urls and len(urls) < MAX_WEB_SOURCE_URLS:
            urls.append(safe)


def _stream_sources(item: Mapping[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    _append_sources(item, urls)
    return tuple(urls[:MAX_WEB_SOURCE_URLS])


def _safe_url(url: str) -> str | None:
    if len(url) > MAX_WEB_SOURCE_URL_LENGTH:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return url


def _usage(raw: object) -> ModelUsage | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise DataContractError("LLM Responses usage is invalid")
    return ModelUsage(
        input_tokens=_nonnegative_int(raw.get("input_tokens", raw.get("prompt_tokens"))),
        output_tokens=_nonnegative_int(raw.get("output_tokens", raw.get("completion_tokens"))),
        total_tokens=_nonnegative_int(raw.get("total_tokens")),
        web_search_calls=_x_tool_count(raw, "web_search"),
        web_extractor_calls=_x_tool_count(raw, "web_extractor"),
    )


def _x_tool_count(raw: Mapping[str, Any], name: str) -> int | None:
    tools = raw.get("x_tools")
    if not isinstance(tools, Mapping):
        return None
    value = tools.get(name)
    if isinstance(value, Mapping):
        value = value.get("count")
    return _nonnegative_int(value)


def _nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataContractError("LLM response usage must contain nonnegative integers")
    return value


def encode_request(
    request: ModelRequest,
    *,
    model: str,
    max_output_tokens: int,
    native_web_search: bool = False,
    native_web_extractor: bool = False,
) -> dict[str, object]:
    return ResponsesCodec.encode(
        request,
        model=model,
        max_output_tokens=max_output_tokens,
        native_web_search=native_web_search,
        native_web_extractor=native_web_extractor,
    )


def decode_response(payload: Mapping[str, Any]) -> ModelResponse:
    return ResponsesCodec.decode(payload)


__all__ = [
    "MAX_WEB_SOURCE_URL_LENGTH",
    "MAX_WEB_SOURCE_URLS",
    "ResponsesCodec",
    "decode_response",
    "encode_request",
]
