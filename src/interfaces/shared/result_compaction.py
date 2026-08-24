"""Secret-safe result compaction shared by Agent and MCP transports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

CANONICAL_RESULT_MAX_BYTES = 15 * 1024
MCP_TEXT_CONTENT_MAX_BYTES = 16 * 1024
DEFAULT_RESULT_MAX_BYTES = 16 * 1024
MIN_RESULT_MAX_BYTES = 32

_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "error",
    "exception",
    "header",
    "password",
    "proxy",
    "response",
    "secret",
    "token",
    "traceback",
    "url",
)
_COMPACTION_ENVELOPE_KEYS = (
    "ok",
    "request_id",
    "as_of",
    "fetched_at",
    "freshness",
    "degraded",
)
_SPECIALTY_OPERATIONS = frozenset(
    {
        ("market_data_get", "quotes"),
        ("monitor_read", "dashboard"),
        ("monitor_read", "runs"),
        ("portfolio_analyze", "exposure"),
        ("research_memory_get", "timeline"),
        ("research_memory_get", "search"),
        ("research_memory_get", "agenda"),
        ("us_company_get", "filings"),
        ("us_company_get", "live_news"),
        ("us_company_get", "company_updates"),
        ("investment_case_read", "context"),
        ("investment_case_read", "attention"),
        ("research_workflow_run", "deep_dive"),
        ("research_workflow_run", "catalyst_review"),
        ("research_workflow_run", "portfolio_review"),
        ("a_share_get_facts", "financials"),
        ("a_share_get_facts", "industry_cycle"),
        ("a_share_get_facts", "company_operating_metrics"),
        ("decision_workbench_review_queue", "open_items"),
        ("decision_workbench_review_queue", "summary"),
    }
)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"__bytes__": f"{len(value)} bytes"}
    return str(value)


def safe_code(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
        return value
    return None


def safe_request_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return None


def _safe_string(value: str, *, max_chars: int = 1024) -> str:
    sanitized = _URL.sub("[REDACTED_URL]", value)
    if len(sanitized) <= max_chars:
        return sanitized
    return f"{sanitized[:max_chars]}…[TRUNCATED {len(sanitized) - max_chars} chars]"


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


_SAFE_ERROR_DETAIL_KEYS = frozenset(
    {
        "tool",
        "operation",
        "missing_fields",
        "unexpected_fields",
        "invalid_fields",
        "code",
        "retryable",
    }
)


def _safe_error_details(value: object) -> object:
    if isinstance(value, Mapping):
        details: dict[str, object] = {}
        for key in sorted(value, key=lambda item: str(item)):
            name = str(key)
            if name not in _SAFE_ERROR_DETAIL_KEYS:
                continue
            item = value[key]
            if isinstance(item, str) and len(item) <= 128:
                details[name] = item
            elif isinstance(item, (list, tuple)):
                cleaned: list[object] = []
                for entry in item[:64]:
                    if isinstance(entry, str) and len(entry) <= 128:
                        cleaned.append(entry)
                    elif isinstance(entry, Mapping):
                        field = entry.get("name")
                        reason = entry.get("reason_code")
                        if isinstance(field, str) and isinstance(reason, str):
                            cleaned.append(
                                {"name": field[:128], "reason_code": reason[:32]}
                            )
                details[name] = cleaned
        return details
    return None


def _safe_error_value(value: object, *, depth: int) -> object:
    if isinstance(value, Mapping):
        projected = {
            key: safe_projection(value[key], depth=depth + 1)
            for key in ("code", "retryable", "http_status", "status_code")
            if key in value
        }
        details = _safe_error_details(value.get("details"))
        if details:
            projected["details"] = details
        return projected
    if isinstance(value, (list, tuple)):
        return [_safe_error_value(item, depth=depth + 1) for item in value[:64]]
    code = safe_code(value)
    return code if code is not None else "[REDACTED]"


def safe_projection(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return {"_truncated": True, "reason": "MAX_DEPTH"}
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            if key.lower() in {"error", "errors", "error_code", "error_codes"}:
                projected[key] = _safe_error_value(value[raw_key], depth=depth + 1)
            elif _sensitive_key(key):
                projected[key] = "[REDACTED]"
            else:
                projected[key] = safe_projection(value[raw_key], depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        if isinstance(value, (set, frozenset)):
            values.sort(key=lambda item: json.dumps(item, default=_json_default, sort_keys=True))
        limited = values[:128]
        result = [safe_projection(item, depth=depth + 1) for item in limited]
        if len(values) > len(limited):
            result.append({"_truncated": True, "omitted_items": len(values) - len(limited)})
        return result
    if isinstance(value, str):
        return _safe_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_projection(_json_default(value), depth=depth + 1)


def encode_result(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def serialized_size(value: Any) -> int:
    return len(encode_result(value))


def looks_like_envelope(value: object) -> bool:
    return isinstance(value, Mapping) and "ok" in value and (
        "as_of" in value or "fetched_at" in value or "request_id" in value
    )


def _code_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    codes: list[str] = []
    for item in value:
        raw = item.get("code") if isinstance(item, Mapping) else item
        code = safe_code(raw)
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def _compact_quote_batch(projected: object, *, original: bytes, max_bytes: int) -> object | None:
    if not isinstance(projected, Mapping):
        return None
    data = projected.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        return None
    marker: dict[str, Any] = {
        "_truncated": True,
        "compaction": "quote_batch_v1",
        "size_bytes": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    for key in ("ok", "degraded", "freshness", "as_of", "fetched_at"):
        if key in projected:
            marker[key] = projected[key]
    compact_items: list[dict[str, Any]] = []
    marker["data"] = {
        key: data[key] for key in ("total_requested", "succeeded", "failed") if key in data
    }
    marker_data = marker["data"]
    assert isinstance(marker_data, dict)
    marker_data["previous_close_basis_by_asset"] = {
        "equity_etf_index": "previous_completed_regular_session_close",
        "future": "previous_completed_daily_bar_close",
    }
    marker_data["items"] = compact_items
    for raw_item in data["items"]:
        if not isinstance(raw_item, Mapping):
            continue
        result = raw_item.get("result")
        if not isinstance(result, Mapping):
            continue
        quote = result.get("data")
        quote_facts = (
            {
                key: quote[key]
                for key in (
                    "display_price",
                    "previous_close",
                    "quote_at",
                    "session",
                    "price_basis",
                )
                if key in quote
            }
            if isinstance(quote, Mapping)
            else None
        )
        if (
            isinstance(quote, Mapping)
            and isinstance(quote_facts, dict)
            and "display_price" not in quote_facts
            and "last" in quote
        ):
            quote_facts["last"] = quote["last"]
        sources = result.get("sources")
        source_codes = [
            f"{source.get('role', 'unknown')}:{source.get('name', 'unknown')}"
            for source in sources
            if isinstance(source, Mapping)
        ] if isinstance(sources, list) else []
        compact_item = {
            "instrument_id": raw_item.get("instrument_id"),
            "result": {key: result[key] for key in ("ok", "freshness") if key in result},
        }
        compact_result = compact_item["result"]
        assert isinstance(compact_result, dict)
        compact_result["data"] = quote_facts
        if source_codes:
            compact_result["source_codes"] = source_codes
        warning_codes = _code_list(result.get("warnings"))
        if warning_codes:
            compact_result["warning_codes"] = warning_codes
        error_codes = _code_list(result.get("errors"))
        if error_codes:
            compact_result["error_codes"] = error_codes
        compact_items.append(compact_item)
        if len(encode_result(marker)) > max_bytes:
            compact_items.pop()
            marker_data["omitted_items"] = len(data["items"]) - len(compact_items)
            break
    return marker if compact_items and len(encode_result(marker)) <= max_bytes else None


def _compact_warning_list(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        return []
    compacted: list[object] = []
    for item in value[:64]:
        if isinstance(item, Mapping):
            entry: dict[str, object] = {}
            for key in ("code", "severity", "retryable", "http_status", "status_code"):
                safe = item.get(key)
                if key == "code":
                    safe = safe_code(safe)
                if safe is not None:
                    entry[key] = safe
            details = _safe_error_details(item.get("details"))
            if details:
                entry["details"] = details
            if entry:
                compacted.append(entry)
        else:
            code = safe_code(item)
            if code is not None:
                compacted.append(code)
    if len(value) > len(compacted):
        compacted.append({"_truncated": True, "omitted_items": len(value) - len(compacted)})
    return compacted


def _compact_source_list(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        return []
    compacted: list[object] = []
    for item in value[:32]:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, object] = {}
        for key in ("name", "role", "provider", "basis"):
            safe = item.get(key)
            if isinstance(safe, str) and safe:
                entry[key] = _safe_string(safe, max_chars=128)
        if entry:
            compacted.append(entry)
    if len(value) > len(compacted):
        compacted.append({"_truncated": True, "omitted_items": len(value) - len(compacted)})
    return compacted


def _compact_operation_value(value: object, *, list_limit: int, depth: int = 0) -> object:
    if depth >= 5:
        return {"_truncated": True, "reason": "MAX_DEPTH"}
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            item = value[raw_key]
            if isinstance(item, (str, int, float, bool)) or item is None:
                result[key] = _safe_string(item, max_chars=512) if isinstance(item, str) else item
            elif isinstance(item, Mapping):
                result[key] = _compact_operation_value(item, list_limit=list_limit, depth=depth + 1)
            elif isinstance(item, (list, tuple)):
                values = [
                    _compact_operation_value(entry, list_limit=list_limit, depth=depth + 1)
                    for entry in item[:list_limit]
                ]
                if len(item) > len(values):
                    values.append({"_truncated": True, "omitted_items": len(item) - len(values)})
                result[key] = values
            else:
                result[key] = safe_projection(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        values = [
            _compact_operation_value(entry, list_limit=list_limit, depth=depth + 1)
            for entry in value[:list_limit]
        ]
        if len(value) > len(values):
            values.append({"_truncated": True, "omitted_items": len(value) - len(values)})
        return values
    if isinstance(value, str):
        return _safe_string(value, max_chars=512)
    return value


def _compact_operation_result(
    projected: object,
    *,
    original: bytes,
    max_bytes: int,
    capability: str,
    operation: str,
) -> object | None:
    if not isinstance(projected, Mapping) or not isinstance(
        projected.get("data"), (Mapping, list, tuple)
    ):
        return None
    for list_limit in (64, 32, 16, 8, 4, 2, 1, 0):
        marker: dict[str, object] = {
            "_truncated": True,
            "compaction": f"{capability}_{operation}_v1",
            "size_bytes": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        }
        for key in _COMPACTION_ENVELOPE_KEYS:
            if key in projected:
                marker[key] = projected[key]
        if "sources" in projected:
            marker["sources"] = _compact_source_list(projected["sources"])
        for key in ("warnings", "errors"):
            if key in projected:
                marker[key] = _compact_warning_list(projected[key])
        marker["data"] = _compact_operation_value(projected["data"], list_limit=list_limit)
        if len(encode_result(marker)) <= max_bytes:
            return marker
    marker = {
        "_truncated": True,
        "compaction": f"{capability}_{operation}_v1",
        "size_bytes": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    for key in _COMPACTION_ENVELOPE_KEYS:
        if isinstance(projected, Mapping) and key in projected:
            marker[key] = projected[key]
    if isinstance(projected, Mapping):
        if "sources" in projected:
            marker["sources"] = _compact_source_list(projected["sources"])
        for key in ("warnings", "errors"):
            if key in projected:
                marker[key] = _compact_warning_list(projected[key])
    if len(encode_result(marker)) <= max_bytes:
        return marker
    return None


def compact_tool_result(
    value: Any,
    *,
    max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
    capability: str | None = None,
    operation: str | None = None,
) -> Any:
    """Create a deterministic, secret-safe result projection within a byte cap."""

    if max_bytes < MIN_RESULT_MAX_BYTES:
        raise ValueError(f"max_bytes must be at least {MIN_RESULT_MAX_BYTES}")
    projected = safe_projection(value)
    encoded = encode_result(projected)
    if len(encoded) <= max_bytes:
        return projected
    if capability == "market_data_get" and operation == "quotes":
        compact_quotes = _compact_quote_batch(projected, original=encoded, max_bytes=max_bytes)
        if compact_quotes is not None:
            return compact_quotes
    specialty = capability is not None and (
        (capability, operation or "direct") in _SPECIALTY_OPERATIONS
        or capability == "decision_workbench_review_queue"
    )
    if specialty or looks_like_envelope(projected):
        compact_operation = _compact_operation_result(
            projected,
            original=encoded,
            max_bytes=max_bytes,
            capability=capability or "tool",
            operation=operation or "direct",
        )
        if compact_operation is not None:
            return compact_operation
    if looks_like_envelope(projected):
        floor = {
            "_truncated": True,
            "size_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
        for key in _COMPACTION_ENVELOPE_KEYS:
            if key in projected:
                floor[key] = projected[key]
        if len(encode_result(floor)) <= max_bytes:
            return floor
    marker = {
        "_truncated": True,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    if len(encode_result(marker)) <= max_bytes:
        return marker
    compact_marker = {"_truncated": True, "size_bytes": len(encoded)}
    if len(encode_result(compact_marker)) <= max_bytes:
        return compact_marker
    return {"_truncated": True}


def operation_from_arguments(arguments: Mapping[str, Any] | None) -> str | None:
    if not arguments:
        return None
    request = arguments.get("request")
    if isinstance(request, Mapping):
        operation_value = request.get("operation")
        if isinstance(operation_value, str):
            return operation_value
    request_operation = getattr(request, "operation", None)
    if isinstance(request_operation, str):
        return request_operation
    direct_operation = arguments.get("operation")
    if isinstance(direct_operation, str):
        return direct_operation
    return None


def _content_type(item: object) -> str | None:
    if isinstance(item, Mapping):
        raw = item.get("type")
        return raw if isinstance(raw, str) else None
    raw = getattr(item, "type", None)
    return raw if isinstance(raw, str) else None


def is_content_block_list(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return _content_type(value[0]) in {"text", "image"}


def _replace_text(item: object, text: str) -> object:
    if isinstance(item, Mapping):
        replaced = dict(item)
        replaced["text"] = text
        return replaced
    if hasattr(item, "model_copy"):
        return item.model_copy(update={"text": text})
    return item


def _text_payload(item: object) -> str | None:
    if isinstance(item, Mapping):
        text = item.get("text")
        return text if isinstance(text, str) else None
    text = getattr(item, "text", None)
    return text if isinstance(text, str) else None


def _truncate_utf8_text(value: str, maximum_bytes: int) -> str:
    if maximum_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    marker = "…[TRUNCATED]".encode()
    if maximum_bytes <= len(marker):
        return marker[:maximum_bytes].decode("utf-8", errors="ignore")
    prefix = encoded[: maximum_bytes - len(marker)].decode("utf-8", errors="ignore")
    return f"{prefix}{marker.decode('utf-8')}"


def compact_mcp_result(
    value: Any,
    *,
    capability: str,
    arguments: Mapping[str, Any] | None = None,
    max_bytes: int = CANONICAL_RESULT_MAX_BYTES,
) -> Any:
    """Compact a Registry or FastMCP tool result without touching image bytes."""

    operation = operation_from_arguments(arguments)
    if not is_content_block_list(value):
        encoded_value = encode_result(value)
        if len(encoded_value) <= max_bytes:
            return value
    if is_content_block_list(value):
        compacted_blocks: list[object] = []
        remaining_bytes = max_bytes
        remaining_text_blocks = sum(
            _content_type(item) == "text" for item in value
        )
        for item in value:
            if _content_type(item) != "text":
                compacted_blocks.append(item)
                continue
            block_budget = (
                remaining_bytes
                if remaining_text_blocks <= 1
                else max(0, remaining_bytes // remaining_text_blocks)
            )
            remaining_text_blocks -= 1
            payload = _text_payload(item)
            if payload is None:
                compacted_blocks.append(item)
                continue
            if len(payload.encode("utf-8")) <= block_budget:
                encoded_text = payload
                compacted_blocks.append(item)
                remaining_bytes -= len(encoded_text.encode("utf-8"))
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                encoded_text = _truncate_utf8_text(payload, block_budget)
                compacted_blocks.append(_replace_text(item, encoded_text))
                remaining_bytes -= len(encoded_text.encode("utf-8"))
                continue
            if block_budget < MIN_RESULT_MAX_BYTES:
                encoded_text = ""
            else:
                compacted = compact_tool_result(
                    parsed,
                    max_bytes=block_budget,
                    capability=capability,
                    operation=operation,
                )
                encoded_text = encode_result(compacted).decode("utf-8")
            compacted_blocks.append(_replace_text(item, encoded_text))
            remaining_bytes -= len(encoded_text.encode("utf-8"))
        return compacted_blocks
    return compact_tool_result(
        value,
        max_bytes=max_bytes,
        capability=capability,
        operation=operation,
    )


compact_result = compact_tool_result
compact_value = compact_tool_result
