"""Focused contracts for the Shared Agent Runtime read-only gateway."""

from __future__ import annotations

import json
from typing import Any

import pytest

from interfaces.agent.capability_gateway import (
    AgentCapabilityAccessDeniedError,
    AgentCapabilityGateway,
    compact_tool_result,
)
from interfaces.agent.prompts import AGENT_SYSTEM_PROMPT
from interfaces.mcp.tools.compact import (
    APPEND,
    LOCAL_ARTIFACT,
    READ_DURABLE,
    CompactCapabilityRegistry,
    _register_dispatch_tool,
    _spec,
)


async def _health(*, value: int = 1) -> dict[str, Any]:
    return {
        "request_id": "req_agent",
        "sources": [
            {"name": "durable_store", "role": "PRIMARY", "url": "https://secret.example"}
        ],
        "data": {"value": value},
    }


async def _grouped(*, value: int = 1, operation: str = "alpha") -> dict[str, Any]:
    return {
        "request_id": "req_group",
        "data": {"value": value, "operation": operation},
    }


async def _write(*, value: int = 1) -> dict[str, Any]:
    return {"data": {"value": value}}


async def _proposal(
    *,
    case_id: str,
    payload: dict[str, Any],
    proposed_by: str,
    idempotency_key: str,
    operation: str = "thesis_revision",
) -> dict[str, Any]:
    return {
        "request_id": "req_proposal",
        "data": {
            "case_id": case_id,
            "kind": payload.get("kind"),
            "proposed_by": proposed_by,
            "idempotency_key": idempotency_key,
            "operation": operation,
            "status": "PENDING",
        },
    }


def _registry() -> CompactCapabilityRegistry:
    registry = CompactCapabilityRegistry()
    registry.add_capability(_health, name="system_health", policy=READ_DURABLE)
    registry.add_capability(
        _write,
        name="watchlist_manage",
        policy=APPEND,
    )
    _register_dispatch_tool(
        registry,
        name="grouped_read",
        description="Read one exact grouped operation.",
        variants=(
            _spec("alpha", _grouped, ("value",), adapter_operation="alpha"),
            _spec("beta", _grouped, ("value",), adapter_operation="beta"),
        ),
        policy=READ_DURABLE,
    )
    _register_dispatch_tool(
        registry,
        name="action_manage",
        description="Write one exact action after explicit confirmation.",
        variants=(
            _spec("add", _write, ("value",)),
            _spec("remove", _write, ("value",)),
        ),
        policy=APPEND,
    )
    registry.add_capability(
        _health,
        name="technical_render_chart",
        policy=LOCAL_ARTIFACT,
    )
    return registry


def _proposal_registry() -> CompactCapabilityRegistry:
    registry = _registry()
    _register_dispatch_tool(
        registry,
        name="research_judgment_propose",
        description="Create one non-effective research proposal.",
        variants=(
            _spec(
                "thesis_revision",
                _proposal,
                ("case_id", "payload", "proposed_by", "idempotency_key"),
                adapter_operation="thesis_revision",
            ),
        ),
        policy=APPEND,
    )
    return registry


def test_private_agent_capabilities_are_registry_metadata_only() -> None:
    registry = _registry()
    assert {tool.name for tool in registry.list_tools()} == {
        "system_health",
        "watchlist_manage",
        "action_manage",
        "grouped_read",
        "technical_render_chart",
    }
    assert "tp_read" not in {tool.name for tool in registry.list_tools()}
    descriptors = registry.operation_descriptors()
    grouped = [item for item in descriptors if item.capability == "grouped_read"]
    assert {item.operation for item in grouped} == {"alpha", "beta"}
    assert all(
        item.schema["properties"]["operation"]["const"] in {"alpha", "beta"}
        for item in grouped
    )


def test_search_returns_only_agent_a_read_operations() -> None:
    gateway = AgentCapabilityGateway(_registry())
    descriptors = gateway.search("", limit=20)
    assert {item.capability for item in descriptors} == {
        "system_health",
        "grouped_read",
        "technical_render_chart",
    }
    assert all(item.auto_allowed for item in descriptors)
    assert gateway.search("watchlist", limit=3) == ()


def test_search_does_not_return_unrelated_defaults_for_unknown_language() -> None:
    gateway = AgentCapabilityGateway(_registry())
    assert gateway.search("完全未知的能力", limit=3) == ()


def test_prepare_action_search_returns_only_injected_allowlist_schema() -> None:
    gateway = AgentCapabilityGateway(
        _registry(),
        action_allowlist=(("action_manage", "add"),),
    )
    descriptors = gateway.search("add", mode="prepare_action", limit=8)
    assert [(item.capability, item.operation) for item in descriptors] == [
        ("action_manage", "add")
    ]
    assert descriptors[0].auto_allowed is False
    assert gateway.search("add", mode="read", limit=8) == ()


@pytest.mark.asyncio
async def test_proposal_search_and_invoke_skip_only_the_redundant_outer_gate() -> None:
    gateway = AgentCapabilityGateway(_proposal_registry())
    descriptors = gateway.search("thesis revision", mode="propose", limit=8)
    assert [(item.capability, item.operation) for item in descriptors] == [
        ("research_judgment_propose", "thesis_revision")
    ]
    assert descriptors[0].auto_allowed is True
    assert descriptors[0].confirmation_required is False

    result = await gateway.propose(
        "research_judgment_propose",
        "thesis_revision",
        {
            "case_id": "case_test",
            "payload": {"kind": "thesis_revision"},
            "proposed_by": "user",
            "idempotency_key": "proposal-test",
        },
    )
    assert result.result["data"]["status"] == "PENDING"
    assert result.receipt.request_id == "req_proposal"
    with pytest.raises(AgentCapabilityAccessDeniedError):
        await gateway.propose(
            "research_judgment_propose",
            "thesis_revision",
            {
                "case_id": "case_test",
                "payload": {"kind": "trade_plan"},
                "proposed_by": "user",
                "idempotency_key": "wrong-kind",
            },
        )


def test_chinese_alias_routes_to_matching_capability() -> None:
    gateway = AgentCapabilityGateway(_registry())
    assert [item.capability for item in gateway.search("系统健康", limit=3)] == [
        "system_health"
    ]


@pytest.mark.asyncio
async def test_read_uses_exact_grouped_validation_without_confirmation() -> None:
    gateway = AgentCapabilityGateway(_registry())
    result = await gateway.read("grouped_read", "alpha", {"value": 7})
    assert result.result["data"] == {"operation": "alpha", "value": 7}
    assert result.receipt.request_id == "req_group"
    invalid = await gateway.read("grouped_read", "alpha", {"value": "wrong"})
    assert invalid.result["ok"] is False
    assert invalid.result["errors"][0]["code"] == "TOOL_INPUT_INVALID"


@pytest.mark.asyncio
async def test_technical_chart_is_explicitly_allowed_without_fake_confirmation() -> None:
    gateway = AgentCapabilityGateway(_registry())
    result = await gateway.read("technical_render_chart", None, {"value": 2})
    assert result.result["data"]["value"] == 2
    assert result.receipt.effect == "LOCAL_ARTIFACT"
    assert result.receipt.source_codes == ("PRIMARY:durable_store",)
    assert "secret.example" not in str(result.receipt.as_dict())


@pytest.mark.asyncio
async def test_read_second_checks_operation_policy() -> None:
    gateway = AgentCapabilityGateway(_registry())
    with pytest.raises(AgentCapabilityAccessDeniedError):
        await gateway.read("watchlist_manage", None, {"value": 1})


def test_result_compaction_is_deterministic_bounded_and_secret_safe() -> None:
    value = {
        "headers": {"Authorization": "Bearer secret"},
        "url": "https://user:pass@example.test/path?token=secret",
        "exception": "traceback with a secret",
        "data": ["x" * 2_000 for _ in range(50)],
    }
    first = compact_tool_result(value, max_bytes=256)
    second = compact_tool_result(value, max_bytes=256)
    assert first == second
    serialized = str(first)
    assert len(serialized.encode()) < 512
    assert "Bearer secret" not in serialized
    assert "https://user:pass" not in serialized
    assert "traceback with a secret" not in serialized


def test_result_compaction_preserves_typed_errors_without_exception_text() -> None:
    compacted = compact_tool_result(
        {
            "errors": [
                {
                    "code": "PROVIDER_TIMEOUT_ERROR",
                    "message": "secret provider exception",
                    "retryable": True,
                }
            ],
            "error_codes": ["PROVIDER_TIMEOUT_ERROR"],
        }
    )
    assert compacted["errors"] == [
        {"code": "PROVIDER_TIMEOUT_ERROR", "retryable": True}
    ]
    assert compacted["error_codes"] == ["PROVIDER_TIMEOUT_ERROR"]
    assert "secret provider exception" not in str(compacted)


def test_large_quote_batch_compaction_preserves_latest_price_and_baseline() -> None:
    items = []
    for index in range(50):
        instrument_id = f"equity:US:T{index:02d}"
        items.append(
            {
                "instrument_id": instrument_id,
                "result": {
                    "ok": True,
                    "degraded": False,
                    "freshness": "delayed",
                    "data": {
                        "instrument_id": instrument_id,
                        "display_price": "101.25",
                        "last": "101.25",
                        "previous_close": "100.00",
                        "previous_close_basis": "previous_completed_regular_session_close",
                        "quote_at": "2026-08-11T19:58:00-04:00",
                        "session": "post_market",
                        "price_basis": "last",
                        "unused_payload": "x" * 2_000,
                    },
                    "sources": [{"name": "yfinance", "role": "primary"}],
                    "warnings": [{"code": "EXTENDED_HOURS_PRICE", "message": "x" * 500}],
                    "errors": [],
                },
            }
        )
    value = {
        "ok": True,
        "freshness": "delayed",
        "data": {"items": items, "total_requested": 50, "succeeded": 50, "failed": 0},
    }

    compacted = compact_tool_result(
        value,
        max_bytes=16 * 1024,
        capability="market_data_get",
        operation="quotes",
    )

    assert compacted["_truncated"] is True
    assert compacted["compaction"] == "quote_batch_v1"
    assert len(
        json.dumps(compacted, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ) <= 16 * 1024
    assert len(compacted["data"]["items"]) == 50
    assert compacted["data"]["previous_close_basis_by_asset"] == {
        "equity_etf_index": "previous_completed_regular_session_close",
        "future": "previous_completed_daily_bar_close",
    }
    first = compacted["data"]["items"][0]["result"]
    assert first["data"] == {
        "display_price": "101.25",
        "previous_close": "100.00",
        "quote_at": "2026-08-11T19:58:00-04:00",
        "session": "post_market",
        "price_basis": "last",
    }
    assert first["source_codes"] == ["primary:yfinance"]
    assert first["warning_codes"] == ["EXTENDED_HOURS_PRICE"]


@pytest.mark.parametrize(
    ("capability", "operation", "data_key"),
    (
        ("monitor_read", "dashboard", "monitors"),
        ("monitor_read", "runs", "runs"),
        ("portfolio_analyze", "exposure", "positions"),
        ("research_memory_get", "timeline", "entries"),
        ("research_memory_get", "search", "results"),
        ("research_memory_get", "agenda", "items"),
        ("us_company_get", "filings", "filings"),
        ("us_company_get", "live_news", "items"),
        ("us_company_get", "company_updates", "updates"),
        ("decision_workbench_review_queue", "open_items", "items"),
        ("investment_case_read", "attention", "items"),
        ("investment_case_read", "context", "entries"),
        ("research_workflow_run", "deep_dive", "sections"),
        ("a_share_get_facts", "financials", "statements"),
    ),
)
def test_operation_compaction_keeps_provenance_and_decision_data(
    capability: str,
    operation: str,
    data_key: str,
) -> None:
    value = {
        "ok": True,
        "as_of": "2026-08-13T10:00:00+08:00",
        "fetched_at": "2026-08-13T10:00:01+08:00",
        "freshness": "fresh",
        "degraded": True,
        "sources": [{"name": "durable_store", "role": "PRIMARY"}],
        "warnings": [{"code": "STALE_DATA", "message": "do not include this text"}],
        "errors": [{"code": "UPSTREAM_TIMEOUT", "message": "do not include this text"}],
        "data": {
            data_key: [
                {
                    "monitor_id": "monitor_1",
                    "status": "ACTIVE",
                    "severity": "HIGH",
                    "current_value": "101.25",
                    "unbounded": "x" * 2_000,
                }
                for _ in range(32)
            ]
        },
    }

    compacted = compact_tool_result(
        value,
        max_bytes=2_048,
        capability=capability,
        operation=operation,
    )
    encoded = json.dumps(compacted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(encoded.encode()) <= 2_048
    assert compacted["compaction"] == f"{capability}_{operation}_v1"
    assert compacted["as_of"] == value["as_of"]
    assert compacted["degraded"] is True
    assert compacted["data"][data_key][0]["status"] == "ACTIVE"
    assert "do not include this text" not in encoded


def test_agent_prompt_binds_previous_close_semantics() -> None:
    assert "previous_close_basis" in AGENT_SYSTEM_PROMPT
    assert "前收（前一已完成常规交易时段收盘）" in AGENT_SYSTEM_PROMPT
    assert "不得称“昨收”" in AGENT_SYSTEM_PROMPT
    assert "previous_completed_daily_bar_close" in AGENT_SYSTEM_PROMPT
    assert "前一完整日线收盘" in AGENT_SYSTEM_PROMPT
    assert "不得称常规盘前收或“结算价”" in AGENT_SYSTEM_PROMPT
