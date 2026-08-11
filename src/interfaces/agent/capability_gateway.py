"""Read-only Agent gateway over the transport-neutral compact registry.

This module intentionally does not create an MCP server or expose the private
``tp_*`` names as MCP tools.  The gateway searches operation-level descriptors,
checks Agent-A's allow-list a second time, and delegates to the registry's
closed Pydantic validation/dispatch path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from application.ports.agent_tool_gateway import (
    AgentCapabilityDescriptor,
    AgentToolDescriptor,
    AgentToolGateway,
    AgentToolReceipt,
    AgentToolResult,
)
from interfaces.mcp.tools.compact import (
    CapabilityNotFoundError,
    CompactCapabilityRegistry,
    CompactOperationDescriptor,
)

DEFAULT_RESULT_MAX_BYTES = 16 * 1024
DEFAULT_SEARCH_LIMIT = 3
MAX_SEARCH_LIMIT = 8
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

# Compact operation descriptions are intentionally English-only registry
# metadata.  Keep conversational aliases here so Chinese requests route
# deterministically without adding translated prose to every public schema.
_SEARCH_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("持仓", ("account_get", "positions", "portfolio_analyze", "exposure")),
    ("仓位", ("account_get", "positions", "portfolio_analyze", "exposure")),
    ("组合", ("portfolio_analyze", "account_get", "positions", "exposure")),
    ("账户", ("account_get", "positions", "transactions")),
    ("交易记录", ("account_get", "transactions")),
    ("自选", ("watchlist_get", "groups", "items")),
    ("关注", ("watchlist_get", "items")),
    ("监控", ("monitor_read", "dashboard", "definitions", "runs", "events")),
    ("告警", ("monitor_read", "dashboard", "events", "runs")),
    ("提醒", ("monitor_read", "dashboard", "events")),
    ("黄金", ("monitor_read", "market_data_get", "quote")),
    ("价格", ("market_data_get", "quote", "quotes")),
    ("行情", ("market_data_get", "quote", "bars")),
    ("技术", ("technical_get_snapshot", "technical_render_chart")),
    ("图表", ("technical_render_chart",)),
    ("风险", ("portfolio_risk_get", "check", "policy")),
    ("研究标的", ("investment_case_read", "query", "context")),
    ("研究档案", ("investment_case_read", "query", "context")),
    ("研究", ("investment_case_read", "research_memory_get", "search", "timeline")),
    ("催化", ("research_memory_get", "agenda")),
    ("健康", ("system_health",)),
    ("数据质量", ("system_health",)),
    ("财报", ("a_share_get_facts", "financials", "us_company_get")),
    ("公告", ("us_company_get", "filings", "company_updates")),
    ("新闻", ("us_company_get", "live_news")),
)


class AgentCapabilityAccessDeniedError(PermissionError):
    """The operation exists but is not auto-readable by Agent-A."""


class AgentToolResultError(RuntimeError):
    """Safe typed error raised only when result compaction cannot proceed."""


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


def _safe_string(value: str, *, max_chars: int = 1024) -> str:
    # Source URLs and exception bodies are never copied into Agent receipts or
    # result projections.  Keep a deterministic marker instead.
    sanitized = _URL.sub("[REDACTED_URL]", value)
    if len(sanitized) <= max_chars:
        return sanitized
    return f"{sanitized[:max_chars]}…[TRUNCATED {len(sanitized) - max_chars} chars]"


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _safe_error_value(value: object, *, depth: int) -> object:
    if isinstance(value, Mapping):
        return {
            key: _safe_projection(value[key], depth=depth + 1)
            for key in ("code", "retryable", "http_status", "status_code")
            if key in value
        }
    if isinstance(value, (list, tuple)):
        return [_safe_error_value(item, depth=depth + 1) for item in value[:64]]
    code = _safe_code(value)
    return code if code is not None else "[REDACTED]"


def _safe_projection(value: Any, *, depth: int = 0) -> Any:
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
                projected[key] = _safe_projection(value[raw_key], depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple, set, frozenset)):
        # Sets are sorted by their safe JSON representation to keep the output
        # stable across processes.
        values = list(value)
        if isinstance(value, (set, frozenset)):
            values.sort(key=lambda item: json.dumps(item, default=_json_default, sort_keys=True))
        limited = values[:128]
        result = [_safe_projection(item, depth=depth + 1) for item in limited]
        if len(values) > len(limited):
            result.append({"_truncated": True, "omitted_items": len(values) - len(limited)})
        return result
    if isinstance(value, str):
        return _safe_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_projection(_json_default(value), depth=depth + 1)


def _encode(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _code_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    codes: list[str] = []
    for item in value:
        raw = item.get("code") if isinstance(item, Mapping) else item
        code = _safe_code(raw)
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def _compact_quote_batch(projected: object, *, original: bytes, max_bytes: int) -> object | None:
    """Keep decision-useful quote facts when a large Agent batch exceeds its cap."""
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
        key: data[key]
        for key in ("total_requested", "succeeded", "failed")
        if key in data
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
            "result": {
                key: result[key]
                for key in ("ok", "freshness")
                if key in result
            },
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
        if len(_encode(marker)) > max_bytes:
            compact_items.pop()
            marker_data["omitted_items"] = len(data["items"]) - len(compact_items)
            break
    return marker if compact_items and len(_encode(marker)) <= max_bytes else None


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
    projected = _safe_projection(value)
    encoded = _encode(projected)
    if len(encoded) <= max_bytes:
        return projected
    if capability == "market_data_get" and operation == "quotes":
        compact_quotes = _compact_quote_batch(projected, original=encoded, max_bytes=max_bytes)
        if compact_quotes is not None:
            return compact_quotes
    # A digest lets the caller correlate a bounded result without persisting an
    # unbounded provider payload, URL, header, or exception body.
    marker = {
        "_truncated": True,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    if len(_encode(marker)) <= max_bytes:
        return marker
    compact_marker = {"_truncated": True, "size_bytes": len(encoded)}
    if len(_encode(compact_marker)) <= max_bytes:
        return compact_marker
    return {"_truncated": True}


# Short aliases make the compaction contract convenient for runtime callers.
compact_result = compact_tool_result
compact_value = compact_tool_result


def _serialized_size(value: Any) -> int:
    return len(_encode(value))


def _safe_code(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
        return value
    return None


def _safe_request_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return None


def _receipt_from_result(
    *,
    descriptor: CompactOperationDescriptor,
    original: Any,
    compacted: Any,
) -> AgentToolReceipt:
    request_id: str | None = None
    degraded = False
    source_codes: list[str] = []
    warning_codes: list[str] = []
    error_code: str | None = None
    if isinstance(original, Mapping):
        request_id = _safe_request_id(original.get("request_id"))
        degraded = bool(original.get("degraded", False))
        raw_sources = original.get("sources", ())
        if isinstance(raw_sources, Sequence) and not isinstance(raw_sources, (str, bytes)):
            for source in raw_sources:
                if not isinstance(source, Mapping):
                    continue
                name = source.get("name")
                role = source.get("role")
                if not isinstance(name, str) or not name.strip():
                    continue
                label = f"{role}:{name}" if isinstance(role, str) and role else name
                label = label.strip()[:128]
                if label not in source_codes:
                    source_codes.append(label)
        raw_warnings = original.get("warnings", ())
        if isinstance(raw_warnings, Sequence) and not isinstance(raw_warnings, (str, bytes)):
            for warning in raw_warnings:
                raw_code = warning.get("code") if isinstance(warning, Mapping) else warning
                code = _safe_code(raw_code)
                if code is not None and code not in warning_codes:
                    warning_codes.append(code)
        error_code = _safe_code(original.get("error_code"))
        if error_code is None and isinstance(original.get("error"), Mapping):
            error_code = _safe_code(original["error"].get("code"))
        if error_code is None:
            raw_errors = original.get("errors", ())
            if isinstance(raw_errors, Sequence) and not isinstance(raw_errors, (str, bytes)):
                for error in raw_errors:
                    raw_code = error.get("code") if isinstance(error, Mapping) else error
                    error_code = _safe_code(raw_code)
                    if error_code is not None:
                        break
    return AgentToolReceipt(
        capability=descriptor.capability,
        operation=descriptor.operation,
        request_id=request_id,
        effect=descriptor.policy.effect.value,
        degraded=degraded,
        source_codes=tuple(sorted(source_codes)),
        warning_codes=tuple(sorted(warning_codes)),
        error_code=error_code,
        result_size_bytes=_serialized_size(compacted),
        result_truncated=isinstance(compacted, Mapping) and bool(compacted.get("_truncated")),
    )


class AgentCapabilityGateway(AgentToolGateway):
    """Agent-A read/search implementation over :class:`CompactCapabilityRegistry`."""

    def __init__(
        self,
        registry: CompactCapabilityRegistry,
        *,
        result_max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
        search_limit: int = MAX_SEARCH_LIMIT,
    ) -> None:
        if result_max_bytes < MIN_RESULT_MAX_BYTES:
            raise ValueError(f"result_max_bytes must be at least {MIN_RESULT_MAX_BYTES}")
        if search_limit < 1 or search_limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"search_limit must be between 1 and {MAX_SEARCH_LIMIT}")
        self._registry = registry
        self._result_max_bytes = result_max_bytes
        self._search_limit = search_limit

    @property
    def registry(self) -> CompactCapabilityRegistry:
        return self._registry

    def descriptors(self) -> tuple[AgentToolDescriptor, ...]:
        return tuple(self._to_descriptor(item) for item in self._registry.operation_descriptors())

    def _to_descriptor(self, item: CompactOperationDescriptor) -> AgentToolDescriptor:
        return AgentToolDescriptor(
            capability=item.capability,
            operation=item.operation,
            description=item.description,
            schema=deepcopy(item.schema),
            effect=item.policy.effect.value,
            confirmation_required=item.confirmation_required,
            auto_allowed=item.auto_allowed,
            direct=item.direct,
        )

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[AgentToolDescriptor, ...]:
        """Return a deterministic bounded set of Agent-A-readable operations."""

        if limit < 1:
            return ()
        bounded_limit = min(limit, self._search_limit, MAX_SEARCH_LIMIT)
        normalized = " ".join(str(query).lower().split())
        ascii_terms = tuple(
            item for item in re.split(r"[^a-z0-9_]+", normalized) if item
        )
        alias_terms = tuple(
            term
            for alias, expanded in _SEARCH_ALIASES
            if alias in normalized
            for term in expanded
        )
        terms = tuple(dict.fromkeys((*ascii_terms, *alias_terms)))
        candidates = [
            item
            for item in self._registry.operation_descriptors()
            if item.auto_allowed
        ]
        if terms:
            candidates = [
                item
                for item in candidates
                if any(
                    term in " ".join(
                        part
                        for part in (item.capability, item.operation or "", item.description)
                        if part
                    ).lower()
                    for term in terms
                )
            ]
        elif normalized:
            # A nonblank query with no understood term must not silently return
            # unrelated shortest-schema capabilities.
            return ()

        def score(item: CompactOperationDescriptor) -> tuple[int, int, str, str]:
            haystack = " ".join(
                part
                for part in (item.capability, item.operation or "", item.description)
                if part
            ).lower()
            capability = item.capability.lower()
            operation = (item.operation or "").lower()
            if not terms:
                relevance = 0
            else:
                relevance = sum(
                    100 if term == capability else 60 if term == operation else 20
                    for term in terms
                    if term in haystack
                )
            return (-relevance, len(haystack), item.capability, item.operation or "")

        candidates.sort(key=score)
        return tuple(self._to_descriptor(item) for item in candidates[:bounded_limit])

    async def read(
        self,
        capability: str,
        operation: str | None,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        """Policy-check, exact-validate, and execute one read operation."""

        try:
            descriptor = self._registry.find_operation(capability, operation)
        except CapabilityNotFoundError:
            raise
        if not descriptor.auto_allowed:
            raise AgentCapabilityAccessDeniedError(
                f"Agent-A read is not allowed for {capability}:{operation or ''}".rstrip(":")
            )
        # ``invoke_validated`` does not inject or check a confirmation.  This
        # is important for technical_render_chart: its public MCP policy is
        # unchanged, while Agent-A reaches the explicit internal read path.
        raw_result = await self._registry.invoke_validated(
            capability,
            descriptor.operation,
            dict(arguments),
            enforce_confirmation=False,
        )
        compacted = compact_tool_result(
            raw_result,
            max_bytes=self._result_max_bytes,
            capability=descriptor.capability,
            operation=descriptor.operation,
        )
        return AgentToolResult(
            result=compacted,
            receipt=_receipt_from_result(
                descriptor=descriptor,
                original=raw_result,
                compacted=compacted,
            ),
        )


# Friendly aliases for adapters that name this boundary simply "CapabilityGateway".
CapabilityGateway = AgentCapabilityGateway
AgentGateway = AgentCapabilityGateway
CapabilityDescriptor = AgentCapabilityDescriptor


def create_agent_capability_gateway(
    registry: CompactCapabilityRegistry,
    *,
    result_max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
    search_limit: int = MAX_SEARCH_LIMIT,
) -> AgentCapabilityGateway:
    return AgentCapabilityGateway(
        registry,
        result_max_bytes=result_max_bytes,
        search_limit=search_limit,
    )


build_agent_capability_gateway = create_agent_capability_gateway


__all__ = [
    "AgentCapabilityAccessDeniedError",
    "AgentCapabilityGateway",
    "AgentCapabilityDescriptor",
    "AgentGateway",
    "CapabilityGateway",
    "CapabilityDescriptor",
    "DEFAULT_RESULT_MAX_BYTES",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "MIN_RESULT_MAX_BYTES",
    "build_agent_capability_gateway",
    "compact_result",
    "compact_tool_result",
    "compact_value",
    "create_agent_capability_gateway",
]
