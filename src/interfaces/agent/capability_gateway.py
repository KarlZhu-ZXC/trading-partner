"""Bounded Agent read/proposal gateway over the transport-neutral compact registry.

This module intentionally does not create an MCP server or expose the private
``tp_*`` names as MCP tools. The gateway searches operation-level descriptors,
checks the read/proposal allow-lists a second time, and delegates to the
registry's closed Pydantic validation/dispatch path.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any

from application.ports.agent_tool_gateway import (
    AgentCapabilityDescriptor,
    AgentToolDescriptor,
    AgentToolGateway,
    AgentToolReceipt,
    AgentToolResult,
)
from application.services.review_item_service import ReviewItemService
from interfaces.mcp.tools.compact import (
    READ_DURABLE,
    CapabilityNotFoundError,
    CompactCapabilityRegistry,
    CompactOperationDescriptor,
)
from interfaces.shared.result_compaction import (
    DEFAULT_RESULT_MAX_BYTES,
    MIN_RESULT_MAX_BYTES,
    compact_result,
    compact_tool_result,
    compact_value,
    safe_code,
    safe_request_id,
    serialized_size,
)

DEFAULT_SEARCH_LIMIT = 3
MAX_SEARCH_LIMIT = 8
SEARCH_MODES = frozenset({"read", "propose", "prepare_action"})
_PROPOSAL_OPERATIONS = frozenset(
    {
        ("research_judgment_propose", "research_state"),
        ("research_judgment_propose", "thesis_revision"),
    }
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
    (
        "研究",
        ("investment_case_read", "research_memory_get", "search", "timeline", "attention"),
    ),
    ("待处理", ("investment_case_read", "attention")),
    ("待办", ("investment_case_read", "attention")),
    ("注意事项", ("investment_case_read", "attention")),
    ("今天决策", ("investment_case_read", "attention")),
    ("需要处理", ("investment_case_read", "attention")),
    ("decision inbox", ("investment_case_read", "attention")),
    ("催化", ("research_memory_get", "agenda")),
    ("健康", ("system_health",)),
    ("数据质量", ("system_health",)),
    ("财报", ("a_share_get_facts", "financials", "us_company_get")),
    ("公告", ("us_company_get", "filings", "company_updates")),
    ("新闻", ("us_company_get", "live_news")),
    (
        "审阅",
        (
            "view_inbox",
            "view_review_get",
            "current_view_get",
            "decision_workbench_review_queue",
            "open_items",
            "summary",
            "subject",
        ),
    ),
    (
        "复核",
        (
            "view_inbox",
            "view_review_get",
            "current_view_get",
            "decision_workbench_review_queue",
            "open_items",
            "summary",
            "subject",
        ),
    ),
    ("笔记", ("view_inbox", "view_review_get", "current_view_get")),
    ("观点", ("view_inbox", "view_review_get", "current_view_get")),
    ("当前看法", ("current_view_get",)),
    ("当前观点", ("current_view_get",)),
    (
        "工作台",
        ("decision_workbench_review_queue", "open_items", "summary", "subject"),
    ),
    (
        "队列",
        ("decision_workbench_review_queue", "open_items", "summary", "subject"),
    ),
    ("review queue", ("decision_workbench_review_queue", "open_items", "summary")),
    ("review", ("decision_workbench_review_queue", "open_items", "summary")),
)

_REVIEW_QUEUE_CAPABILITY = "decision_workbench_review_queue"
_REVIEW_QUEUE_OPERATIONS = ("open_items", "summary", "subject")
_REVIEW_ACTION_OPERATIONS = ("acknowledge", "resolve")
_REVIEW_ADJACENT: tuple[tuple[str, str | None], ...] = (
    (_REVIEW_QUEUE_CAPABILITY, "open_items"),
    (_REVIEW_QUEUE_CAPABILITY, "summary"),
    ("research_memory_get", "agenda"),
    ("monitor_read", "dashboard"),
)
_ROUTING_SENSITIVE_TERMS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "cookie",
        "proxy",
    }
)


class AgentCapabilityAccessDeniedError(PermissionError):
    """The operation exists but is not auto-readable by Agent-A."""


class AgentToolResultError(RuntimeError):
    """Safe typed error raised only when result compaction cannot proceed."""



_safe_code = safe_code
_safe_request_id = safe_request_id
_serialized_size = serialized_size

def _review_queue_schema(operation: str) -> dict[str, Any]:
    """Return an exact private schema for one durable Review Queue read."""

    properties: dict[str, Any] = {
        "operation": {"type": "string", "const": operation},
    }
    required = ["operation"]
    if operation in {"open_items", "subject"}:
        properties["subject_id"] = {"type": ["string", "null"], "maxLength": 128}
    if operation in {"open_items", "subject"}:
        properties["limit"] = {"type": "integer", "minimum": 1, "maximum": 100}
    if operation == "subject":
        properties["subject_id"] = {"type": "string", "minLength": 1, "maxLength": 128}
        required.append("subject_id")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _review_action_schema(operation: str) -> dict[str, Any]:
    """Exact schema exposed only during ``prepare_action`` discovery."""

    properties: dict[str, Any] = {
        "operation": {"type": "string", "const": operation},
        "review_item_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "expected_version": {"type": "integer", "minimum": 1},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
        "authorization_note": {"type": "string", "minLength": 1, "maxLength": 4_000},
        "actor": {"type": "string", "const": "user"},
    }
    required = [
        "operation",
        "review_item_id",
        "expected_version",
        "idempotency_key",
        "authorization_note",
        "actor",
    ]
    if operation == "resolve":
        properties["resolution_note"] = {"type": "string", "minLength": 1, "maxLength": 2_000}
        properties["resolution_ref"] = {"type": ["string", "null"], "maxLength": 256}
        required.append("resolution_note")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _routing_token(value: str) -> str | None:
    """Keep only bounded vocabulary tokens; never persist arbitrary query text."""

    lowered = value.casefold()
    if lowered in _ROUTING_SENSITIVE_TERMS or any(
        marker in lowered for marker in ("password", "secret", "token", "apikey", "api_key")
    ):
        return None
    if value in {alias for alias, _expanded in _SEARCH_ALIASES}:
        return value[:32]
    if re.fullmatch(r"[a-z0-9_:-]{1,64}", value):
        return value
    return None


def _routing_metadata(
    *,
    query: str,
    reason: str,
    matched_terms: Sequence[str],
    adjacent: Sequence[tuple[str, str | None]] = (),
    hints: Sequence[str] = (),
) -> dict[str, Any]:
    normalized = " ".join(str(query).casefold().split())
    vocabulary_values: list[str] = []
    for raw_token in matched_terms:
        token = _routing_token(str(raw_token))
        if token is not None and token not in vocabulary_values:
            vocabulary_values.append(token)
    vocabulary = tuple(vocabulary_values[:16])
    adjacent_values = [
        {
            "capability": capability,
            "operation": operation,
        }
        for capability, operation in adjacent[:8]
    ]
    value: dict[str, Any] = {
        "reason": reason,
        "query_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "matched_terms": list(vocabulary),
        "adjacent": adjacent_values,
    }
    if hints:
        value["hints"] = [str(item)[:160] for item in hints[:8]]
    return value


def _schema_field_terms(schema: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect bounded property names for deterministic schema-field routing."""

    fields: list[str] = []
    stack: list[object] = [schema]
    while stack and len(fields) < 64:
        current = stack.pop()
        if not isinstance(current, Mapping):
            continue
        properties = current.get("properties")
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                name = str(key)
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", name) and name not in fields:
                    fields.append(name)
                stack.append(child)
        for key in ("items", "additionalProperties", "$defs"):
            child = current.get(key)
            if isinstance(child, Mapping):
                stack.append(child)
    return tuple(fields)


def replace_descriptor(
    descriptor: AgentToolDescriptor,
    *,
    routing: Mapping[str, Any],
) -> AgentToolDescriptor:
    """Copy a descriptor while isolating bounded routing metadata."""

    return replace(descriptor, routing=deepcopy(dict(routing)))


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
        action_allowlist: Sequence[tuple[str, str]] | None = None,
        review_item_service: ReviewItemService | None = None,
        clock: Any | None = None,
    ) -> None:
        if result_max_bytes < MIN_RESULT_MAX_BYTES:
            raise ValueError(f"result_max_bytes must be at least {MIN_RESULT_MAX_BYTES}")
        if search_limit < 1 or search_limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"search_limit must be between 1 and {MAX_SEARCH_LIMIT}")
        self._registry = registry
        self._result_max_bytes = result_max_bytes
        self._search_limit = search_limit
        self._action_allowlist: frozenset[tuple[str, str]] = frozenset(action_allowlist or ())
        self._review_item_service = review_item_service
        self._clock = clock

    @property
    def registry(self) -> CompactCapabilityRegistry:
        return self._registry

    def descriptors(self) -> tuple[AgentToolDescriptor, ...]:
        values = [self._to_descriptor(item) for item in self._registry.operation_descriptors()]
        if self._review_item_service is not None:
            values.extend(
                self._review_descriptor(operation, mode="read")
                for operation in _REVIEW_QUEUE_OPERATIONS
            )
        return tuple(values)

    def set_action_allowlist(self, values: Sequence[tuple[str, str]] | None) -> None:
        """Inject the pending-action operation allowlist without importing its service.

        The runtime wires this from the channel-neutral pending gateway when one
        is configured.  Keeping the list outside the registry prevents a write
        descriptor from becoming an Agent-A read capability by accident.
        """

        self._action_allowlist = frozenset(values or ())

    def descriptor(
        self,
        capability: str,
        operation: str | None = None,
    ) -> AgentToolDescriptor | None:
        """Return one exact descriptor for safe argument-hint generation."""

        if capability == _REVIEW_QUEUE_CAPABILITY:
            if operation in _REVIEW_QUEUE_OPERATIONS and self._review_item_service is not None:
                return self._review_descriptor(operation, mode="read")
            return None
        try:
            return self._to_descriptor(self._registry.find_operation(capability, operation))
        except CapabilityNotFoundError:
            return None

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

    def _review_descriptor(self, operation: str, *, mode: str) -> AgentToolDescriptor:
        if mode == "prepare_action":
            return AgentToolDescriptor(
                capability=_REVIEW_QUEUE_CAPABILITY,
                operation=operation,
                description=(
                    "Prepare an explicit user-confirmed Review Queue transition; "
                    "this schema never executes automatically."
                ),
                schema=_review_action_schema(operation),
                effect="MANAGE",
                confirmation_required=True,
                auto_allowed=False,
                direct=False,
            )
        description = {
            "open_items": "Read open durable Decision Workbench Review Queue items.",
            "summary": (
                "Read durable Review Queue metrics without reconciliation or Provider calls."
            ),
            "subject": "Read Review Queue items and metrics scoped to one Research Subject.",
        }[operation]
        return AgentToolDescriptor(
            capability=_REVIEW_QUEUE_CAPABILITY,
            operation=operation,
            description=description,
            schema=_review_queue_schema(operation),
            effect="READ_DURABLE",
            confirmation_required=False,
            auto_allowed=True,
            direct=False,
        )

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        mode: str = "read",
    ) -> tuple[AgentToolDescriptor, ...]:
        """Return exact read or pending-action operation descriptors.

        ``prepare_action`` never invokes an operation; it only exposes schemas
        already present in the injected pending-action allowlist.
        """

        if limit < 1:
            return ()
        if mode not in SEARCH_MODES:
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
        candidates: list[CompactOperationDescriptor] = []
        review_candidates: list[AgentToolDescriptor] = []
        if mode == "read":
            candidates = [
                item
                for item in self._registry.operation_descriptors()
                if item.auto_allowed
            ]
            if self._review_item_service is not None:
                # The Review Queue is a Console-only durable capability.  It
                # intentionally never enters the public MCP registry.
                review_candidates = [
                    self._review_descriptor(operation, mode="read")
                    for operation in _REVIEW_QUEUE_OPERATIONS
                ]
            else:
                review_candidates = []
        elif mode == "propose":
            candidates = [
                item
                for item in self._registry.operation_descriptors()
                if item.operation is not None
                and (item.capability, item.operation) in _PROPOSAL_OPERATIONS
            ]
            review_candidates = []
        else:
            candidates = [
                item
                for item in self._registry.operation_descriptors()
                if item.operation is not None
                and (item.capability, item.operation) in self._action_allowlist
                and not item.auto_allowed
            ]
            review_candidates = [
                self._review_descriptor(operation, mode="prepare_action")
                for operation in _REVIEW_ACTION_OPERATIONS
                if (_REVIEW_QUEUE_CAPABILITY, operation) in self._action_allowlist
            ]
        if terms:
            candidates = [
                item
                for item in candidates
                if any(
                    term in " ".join(
                        part
                        for part in (
                            item.capability,
                            item.operation or "",
                            item.description,
                            *_schema_field_terms(item.schema),
                        )
                        if part
                    ).lower()
                    for term in terms
                )
            ]
            review_candidates = [
                item
                for item in review_candidates
                if any(
                    term in " ".join(
                        part
                        for part in (
                            item.capability,
                            item.operation or "",
                            item.description,
                            *_schema_field_terms(item.schema),
                        )
                        if part
                    ).lower()
                    for term in terms
                )
            ]
            review_operation_terms = set(terms).intersection(
                _REVIEW_QUEUE_OPERATIONS
                if mode == "read"
                else _REVIEW_ACTION_OPERATIONS
            )
            if review_operation_terms:
                review_candidates = [
                    item for item in review_candidates if item.operation in review_operation_terms
                ]
        elif normalized:
            # A nonblank query with no understood term must not silently return
            # unrelated shortest-schema capabilities.
            candidates = []
            review_candidates = []

        # ``CompactOperationDescriptor`` and ``AgentToolDescriptor`` are kept
        # separate so the private Review Queue can remain outside the MCP
        # inventory.  Normalize both to one deterministic candidate shape.
        candidate_descriptors: list[AgentToolDescriptor] = []
        candidate_descriptors.extend(self._to_descriptor(item) for item in candidates)
        candidate_descriptors.extend(review_candidates)
        if mode == "propose":
            candidate_descriptors = [
                replace(item, confirmation_required=False, auto_allowed=True)
                for item in candidate_descriptors
            ]

        if not candidate_descriptors and normalized:
            adjacent_targets: list[tuple[str, str | None]] = []
            # A known alias with no exact schema still returns the closest
            # safe operation descriptors plus bounded missing-field hints.
            for alias, expanded in _SEARCH_ALIASES:
                if alias in normalized:
                    adjacent_targets = [
                        (item.capability, item.operation)
                        for item in self._registry.operation_descriptors()
                        if item.operation in expanded or item.capability in expanded
                    ]
                    if not adjacent_targets and _REVIEW_QUEUE_CAPABILITY in expanded:
                        adjacent_targets = list(_REVIEW_ADJACENT)
                    break
            adjacent_descriptors: list[AgentToolDescriptor] = []
            for capability, operation in adjacent_targets:
                if (
                    mode == "prepare_action"
                    and (capability, operation) not in self._action_allowlist
                ):
                    continue
                if mode == "propose" and (capability, operation) not in _PROPOSAL_OPERATIONS:
                    continue
                descriptor = (
                    self._review_descriptor(operation, mode="prepare_action")
                    if mode == "prepare_action"
                    and capability == _REVIEW_QUEUE_CAPABILITY
                    and operation in _REVIEW_ACTION_OPERATIONS
                    and (capability, operation) in self._action_allowlist
                    else self.descriptor(capability, operation)
                )
                if descriptor is None:
                    continue
                adjacent_descriptors.append(descriptor)
                if len(adjacent_descriptors) >= bounded_limit:
                    break
            routing = _routing_metadata(
                query=query,
                reason="adjacent_match" if adjacent_descriptors else "no_match",
                matched_terms=terms,
                adjacent=adjacent_targets,
                hints=(
                    "Specify the exact operation and required identifier fields.",
                    "Use a bounded subject_id or instrument_id when the capability is scoped.",
                ),
            )
            return tuple(replace_descriptor(item, routing=routing) for item in adjacent_descriptors)

        def score(item: AgentToolDescriptor) -> tuple[int, int, str, str]:
            haystack = " ".join(
                part
                for part in (
                    item.capability,
                    item.operation or "",
                    item.description,
                    *_schema_field_terms(item.schema),
                )
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

        candidate_descriptors.sort(key=score)
        reason = "exact_match" if terms else "default"
        routing = _routing_metadata(
            query=query,
            reason=reason,
            matched_terms=terms,
            adjacent=_REVIEW_ADJACENT,
        )
        return tuple(
            replace_descriptor(item, routing=routing)
            for item in candidate_descriptors[:bounded_limit]
        )

    def search_audit(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        mode: str = "read",
    ) -> dict[str, Any]:
        """Return bounded routing metadata for one capability search.

        The raw query is deliberately omitted.  Callers that persist a
        ``tp_capability_search`` receipt should store this object alongside
        the returned descriptor list.
        """

        descriptors = self.search(query, limit, mode=mode)
        routing_values = [dict(item.routing) for item in descriptors if item.routing]
        if routing_values:
            first = routing_values[0]
            return {
                "reason": first.get("reason", "no_match"),
                "query_sha256": first.get("query_sha256"),
                "matched_terms": list(first.get("matched_terms", ()))[:16],
                "adjacent": list(first.get("adjacent", ()))[:8],
                "result_count": len(descriptors),
            }
        normalized = " ".join(str(query).casefold().split())
        return {
            "reason": "no_match" if normalized else "default",
            "query_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "matched_terms": [],
            "adjacent": [],
            "result_count": 0,
        }

    async def _read_review_queue(
        self,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        service = self._review_item_service
        if service is None:
            raise AgentCapabilityAccessDeniedError("Review Queue capability is unavailable")
        if operation not in _REVIEW_QUEUE_OPERATIONS:
            raise CapabilityNotFoundError(f"{_REVIEW_QUEUE_CAPABILITY}:{operation}")
        allowed_keys = {
            "summary": {"operation"},
            "open_items": {"operation", "subject_id", "limit"},
            "subject": {"operation", "subject_id", "limit"},
        }[operation]
        if any(key not in allowed_keys for key in arguments):
            raise ValueError("Review Queue arguments do not match the exact operation schema")
        if "operation" in arguments and arguments["operation"] != operation:
            raise ValueError("Review Queue operation discriminator is invalid")
        subject_id = arguments.get("subject_id")
        if subject_id is not None and not isinstance(subject_id, str):
            raise ValueError("subject_id must be text")
        limit = arguments.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if operation == "summary":
            data = service.metrics(subject_id=subject_id).model_dump(mode="json")
        elif operation == "open_items":
            items = service.list_open(subject_id=subject_id, limit=limit)
            data = {"items": [item.model_dump(mode="json") for item in items]}
        else:
            if not isinstance(subject_id, str) or not subject_id.strip():
                raise ValueError("subject_id is required")
            items = service.list_open(subject_id=subject_id, limit=limit)
            data = {
                "subject_id": subject_id,
                "items": [item.model_dump(mode="json") for item in items],
                "metrics": service.metrics(subject_id=subject_id).model_dump(mode="json"),
            }
        now = self._clock.now() if self._clock is not None else datetime.now().astimezone()
        raw = {
            "ok": True,
            "request_id": f"review_queue_{operation}",
            "as_of": now.isoformat(),
            "fetched_at": now.isoformat(),
            "freshness": "durable",
            "degraded": False,
            "sources": [{"name": "review_queue", "role": "PRIMARY", "basis": "durable_only"}],
            "warnings": [],
            "errors": [],
            "data": data,
        }
        compacted = compact_tool_result(
            raw,
            max_bytes=self._result_max_bytes,
            capability=_REVIEW_QUEUE_CAPABILITY,
            operation=operation,
        )
        descriptor = self._review_descriptor(operation, mode="read")
        return AgentToolResult(
            result=compacted,
            receipt=_receipt_from_result(
                descriptor=CompactOperationDescriptor(
                    capability=descriptor.capability,
                    operation=descriptor.operation,
                    description=descriptor.description,
                    schema=descriptor.schema,
                    policy=READ_DURABLE,
                    direct=False,
                ),
                original=raw,
                compacted=compacted,
            ),
        )

    async def read(
        self,
        capability: str,
        operation: str | None,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        """Policy-check, exact-validate, and execute one read operation."""

        if capability == _REVIEW_QUEUE_CAPABILITY:
            return await self._read_review_queue(operation or "", arguments)
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

    async def propose(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        """Create one bounded proposal; final domain state remains unchanged."""

        if (capability, operation) not in _PROPOSAL_OPERATIONS:
            raise AgentCapabilityAccessDeniedError(
                f"Agent proposal is not allowed for {capability}:{operation}"
            )
        descriptor = self._registry.find_operation(capability, operation)
        payload = arguments.get("payload")
        if not isinstance(payload, Mapping):
            raise AgentCapabilityAccessDeniedError("Agent proposal payload is invalid")
        kind = payload.get("kind")
        if operation == "thesis_revision" and kind != "thesis_revision":
            raise AgentCapabilityAccessDeniedError("Agent Thesis proposal kind is invalid")
        if operation == "research_state" and kind not in {"watchlist_item", "trade_plan"}:
            raise AgentCapabilityAccessDeniedError("Agent Research proposal kind is invalid")
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
    action_allowlist: Sequence[tuple[str, str]] | None = None,
) -> AgentCapabilityGateway:
    return AgentCapabilityGateway(
        registry,
        result_max_bytes=result_max_bytes,
        search_limit=search_limit,
        action_allowlist=action_allowlist,
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
