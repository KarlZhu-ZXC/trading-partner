from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.account_transactions import AccountGetTransactionsInput
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.dto.workflow import (
    AShareRunMarketReviewInput,
    PortfolioRunReviewInput,
    ResearchRunCatalystReviewInput,
    ResearchRunDeepDiveInput,
    USRunMarketReviewInput,
)
from domain.common.enums import Freshness
from interfaces.mcp.server import (
    PHASE1L_WORKFLOW_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def _envelope(request_id: str) -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id=request_id,
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        errors=(ErrorInfo(code="STUB", message="stub", retryable=False),),
    )


@pytest.mark.asyncio
async def test_workflows_and_account_transactions_are_thin_delegates() -> None:
    container = MagicMock()
    container.settings.mcp_server_name = "phase1l-test"
    container.account_transaction_coordinator.get_transactions = AsyncMock(
        return_value=_envelope("req_transactions")
    )
    methods = (
        "run_deep_dive",
        "run_catalyst_review",
        "run_a_share_market_review",
        "run_us_market_review",
        "run_portfolio_review",
    )
    for method in methods:
        setattr(
            container.research_workflow_orchestrator,
            method,
            AsyncMock(return_value=_envelope(f"req_{method}")),
        )
    manager = create_mcp_server(container)._tool_manager

    assert {tool.name for tool in manager.list_tools()} == set(PUBLIC_TOOL_NAMES)
    assert len(PUBLIC_TOOL_NAMES) == 52
    assert len(PHASE1L_WORKFLOW_TOOL_NAMES) == 5
    calls: tuple[tuple[str, dict[str, object], type[object]], ...] = (
        (
            "account_get",
            {"operation": "transactions"},
            AccountGetTransactionsInput,
        ),
        ("research_run_deep_dive", {"case_id": "case_1"}, ResearchRunDeepDiveInput),
        (
            "research_run_catalyst_review",
            {"case_id": "case_1"},
            ResearchRunCatalystReviewInput,
        ),
        ("a_share_run_market_review", {}, AShareRunMarketReviewInput),
        ("us_run_market_review", {}, USRunMarketReviewInput),
        ("portfolio_run_review", {}, PortfolioRunReviewInput),
    )
    for tool_name, arguments, input_type in calls:
        result = await manager.call_tool(tool_name, arguments)
        assert result["request_id"].startswith("req_")
        mock = (
            container.account_transaction_coordinator.get_transactions
            if tool_name == "account_get"
            else getattr(
                container.research_workflow_orchestrator,
                {
                    "research_run_deep_dive": "run_deep_dive",
                    "research_run_catalyst_review": "run_catalyst_review",
                    "a_share_run_market_review": "run_a_share_market_review",
                    "us_run_market_review": "run_us_market_review",
                    "portfolio_run_review": "run_portfolio_review",
                }[tool_name],
            )
        )
        assert isinstance(mock.call_args.args[0], input_type)
