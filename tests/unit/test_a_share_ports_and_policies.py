"""Phase 1E E1: protocol surfaces, tool policies, asset support constants."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import date
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from application.dto.a_share import (
    AShareGetCapitalSnapshotInput,
    AShareGetEtfOptionSnapshotInput,
    AShareGetMarketStructureInput,
    AShareGetSentimentSnapshotInput,
    AShareGetSnapshotInput,
    ResearchSearchReportsInput,
)
from application.ports import a_share_providers as ports
from application.ports.a_share_providers import (
    A_SHARE_CAPITAL_METRIC_PROTOCOLS,
    A_SHARE_RUNTIME_PROTOCOLS,
)
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from application.services.a_share_tool_policies import (
    A_SHARE_TOOL_ASSET_SUPPORT,
    A_SHARE_WARNING_CODES,
    CAPITAL_DEFAULT_OPTIONAL_METRICS,
    CAPITAL_DEFAULT_REQUIRED_METRICS,
    CAPITAL_DEFAULT_SUMMARY_METRICS,
    CAPITAL_METRIC_CATEGORY,
    CAPITAL_METRIC_CHAIN_OVERRIDES,
    CAPITAL_METRIC_METHOD_NAME,
    OPTIONS_POLICY,
    SNAPSHOT_FULL_POLICY,
    SNAPSHOT_SUMMARY_POLICY,
    capital_metric_router_policy,
    capital_tool_policy_for_metrics,
)
from application.services.criticality_policy import CriticalityPolicy
from domain.a_share.enums import AShareMarketScope, CapitalMetricType
from domain.common.enums import AssetType, DataCategory, DataCriticality, VendorId

EQUITY_A = "equity:A_SHARE:600519.SH"
ETF_A = "etf:A_SHARE:510050.SH"
INDEX_A = "index:A_SHARE:000001.SH"
OPTION_A = "option:A_SHARE:510050C2403M00300"
US_EQUITY = "equity:US:NVDA"
US_ETF = "etf:US:SPY"


def test_runtime_protocol_inventory_and_no_fat_capital() -> None:
    assert len(A_SHARE_RUNTIME_PROTOCOLS) == 20
    assert len(A_SHARE_CAPITAL_METRIC_PROTOCOLS) == 8
    names = {p.__name__ for p in A_SHARE_RUNTIME_PROTOCOLS}
    assert "AShareCapitalProvider" not in names
    assert "AShareCapitalProvider" not in dir(ports)
    for proto in A_SHARE_RUNTIME_PROTOCOLS:
        assert getattr(proto, "_is_runtime_protocol", False) is True


def test_capital_protocols_are_single_method() -> None:
    expected = {
        "AShareIntradayFlowProvider": "get_intraday_flow",
        "AShareDailyFlowProvider": "get_daily_flow",
        "AShareNorthboundProvider": "get_northbound",
        "AShareDragonTigerProvider": "get_dragon_tiger",
        "AShareMarginProvider": "get_margin",
        "AShareBlockTradeProvider": "get_block_trades",
        "AShareShareholderProvider": "get_shareholder_counts",
        "AShareChipProvider": "get_chip_distribution",
    }
    for proto in A_SHARE_CAPITAL_METRIC_PROTOCOLS:
        methods = [
            name
            for name, attr in vars(proto).items()
            if callable(attr) and not name.startswith("_")
        ]
        # Protocol may only expose the data method in its own body.
        assert len(methods) == 1, proto.__name__
        assert methods[0] == expected[proto.__name__]
        assert inspect.iscoroutinefunction(getattr(proto, methods[0]))


def test_http_request_params_are_str_mapping() -> None:
    hints = get_type_hints(HttpRequest)
    params_type = hints["params"]
    assert params_type == Mapping[str, str]
    req = HttpRequest(
        method="GET",
        url="https://example.invalid/q",
        params={"symbol": "600519"},
        headers={},
        body=None,
        timeout_seconds=1.0,
    )
    assert req.params["symbol"] == "600519"
    assert HttpTransport is not None
    assert HttpResponse is not None


def test_tool_policies_required_optional() -> None:
    assert SNAPSHOT_SUMMARY_POLICY.required_categories == (DataCategory.MARKET_QUOTE,)
    assert DataCategory.FUNDAMENTALS in SNAPSHOT_SUMMARY_POLICY.optional_categories
    assert SNAPSHOT_FULL_POLICY.required_categories == (DataCategory.MARKET_QUOTE,)
    assert SNAPSHOT_FULL_POLICY.optional_categories == (
        DataCategory.FUNDAMENTALS,
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.ANNOUNCEMENTS,
        DataCategory.NEWS,
        DataCategory.CORPORATE_ACTIONS,
    )
    from application.services.a_share_tool_policies import (  # noqa: PLC0415
        SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY,
    )

    assert DataCategory.FUNDAMENTALS in (SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY.optional_categories)
    assert SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY.required_categories == ()
    assert OPTIONS_POLICY.required_categories == (DataCategory.OPTIONS,)
    policy = CriticalityPolicy()
    assert policy.for_category(DataCategory.OPTIONS, OPTIONS_POLICY) is DataCriticality.CORE


def test_capital_metric_ownership_and_chains() -> None:
    assert set(CAPITAL_METRIC_CATEGORY) == set(CapitalMetricType)
    assert set(CAPITAL_METRIC_CHAIN_OVERRIDES) == set(CapitalMetricType)
    assert set(CAPITAL_METRIC_METHOD_NAME) == set(CapitalMetricType)
    assert CAPITAL_METRIC_CATEGORY[CapitalMetricType.UNLOCK] is (DataCategory.CORPORATE_ACTIONS)
    assert CAPITAL_METRIC_CATEGORY[CapitalMetricType.DIVIDEND] is (DataCategory.CORPORATE_ACTIONS)
    assert CAPITAL_METRIC_CATEGORY[CapitalMetricType.DAILY_FLOW] is DataCategory.CAPITAL
    assert CAPITAL_METRIC_CHAIN_OVERRIDES[CapitalMetricType.NORTHBOUND] == (
        VendorId.HKEX,
        VendorId.EASTMONEY,
    )
    assert CAPITAL_METRIC_CHAIN_OVERRIDES[CapitalMetricType.DRAGON_TIGER] == (
        VendorId.EASTMONEY,
        VendorId.SSE,
        VendorId.SZSE,
    )
    assert CAPITAL_METRIC_CHAIN_OVERRIDES[CapitalMetricType.DAILY_FLOW] == (
        VendorId.EASTMONEY,
        VendorId.SINA,
    )
    assert CapitalMetricType.DAILY_FLOW in CAPITAL_DEFAULT_REQUIRED_METRICS
    assert CapitalMetricType.DAILY_FLOW in CAPITAL_DEFAULT_SUMMARY_METRICS


def test_capital_tool_policy_explicit_metrics_classification_only() -> None:
    empty = capital_tool_policy_for_metrics(())
    assert empty.required_categories == (DataCategory.CAPITAL,)
    assert empty.category_chain_overrides == {}
    explicit = capital_tool_policy_for_metrics(
        (CapitalMetricType.NORTHBOUND, CapitalMetricType.UNLOCK)
    )
    assert DataCategory.CAPITAL in explicit.required_categories
    assert DataCategory.CORPORATE_ACTIONS in explicit.required_categories
    # Classification only — never emit chain overrides (including empty).
    assert explicit.category_chain_overrides == {}


def test_capital_metric_router_policy_all_metrics_and_semantics() -> None:
    """Every metric: one category, exact non-empty chain, required/optional."""
    assert len(CapitalMetricType) == 10
    for metric in CapitalMetricType:
        required_policy = capital_metric_router_policy(metric, required=True)
        optional_policy = capital_metric_router_policy(metric, required=False)
        category = CAPITAL_METRIC_CATEGORY[metric]
        chain = CAPITAL_METRIC_CHAIN_OVERRIDES[metric]
        assert chain, f"{metric} must have non-empty chain"
        assert required_policy.required_categories == (category,)
        assert required_policy.optional_categories == ()
        assert dict(required_policy.category_chain_overrides) == {category: chain}
        assert optional_policy.required_categories == ()
        assert optional_policy.optional_categories == (category,)
        assert dict(optional_policy.category_chain_overrides) == {category: chain}
        # unlock/dividend elevate CORPORATE_ACTIONS, not CAPITAL.
        if metric in (CapitalMetricType.UNLOCK, CapitalMetricType.DIVIDEND):
            assert category is DataCategory.CORPORATE_ACTIONS
        else:
            assert category is DataCategory.CAPITAL

    # Default summary required vs optional semantics.
    assert frozenset({CapitalMetricType.DAILY_FLOW}) == CAPITAL_DEFAULT_REQUIRED_METRICS
    for metric in CAPITAL_DEFAULT_REQUIRED_METRICS:
        p = capital_metric_router_policy(metric, required=True)
        assert p.required_categories
        assert not p.optional_categories
    for metric in CAPITAL_DEFAULT_OPTIONAL_METRICS:
        p = capital_metric_router_policy(metric, required=False)
        assert p.optional_categories
        assert not p.required_categories
    assert set(CAPITAL_DEFAULT_SUMMARY_METRICS) == (
        CAPITAL_DEFAULT_REQUIRED_METRICS | CAPITAL_DEFAULT_OPTIONAL_METRICS
    )

    # Multi-metric classification still never yields empty chains for Router use.
    multi = capital_tool_policy_for_metrics(
        (
            CapitalMetricType.DAILY_FLOW,
            CapitalMetricType.NORTHBOUND,
            CapitalMetricType.DRAGON_TIGER,
            CapitalMetricType.UNLOCK,
        )
    )
    assert multi.category_chain_overrides == {}
    for override in multi.category_chain_overrides.values():
        assert override  # would fail if empty chain slipped in


def test_asset_support_matrix_covers_tools_and_asset_types() -> None:
    tools = {
        "snapshot",
        "structure",
        "capital",
        "limit_sentiment",
        "etf_options",
        "reports",
    }
    assert set(A_SHARE_TOOL_ASSET_SUPPORT) == tools
    for matrix in A_SHARE_TOOL_ASSET_SUPPORT.values():
        assert set(matrix) == set(AssetType)
    assert A_SHARE_TOOL_ASSET_SUPPORT["snapshot"][AssetType.OPTION] == "reject"
    assert A_SHARE_TOOL_ASSET_SUPPORT["etf_options"][AssetType.ETF] == "required"
    assert "PARTIAL_A_SHARE_SNAPSHOT" in A_SHARE_WARNING_CODES


def test_snapshot_input_dto_closed() -> None:
    ok = AShareGetSnapshotInput(instrument_id=EQUITY_A)
    assert ok.detail.value == "summary"
    with pytest.raises(ValidationError):
        AShareGetSnapshotInput(
            instrument_id=EQUITY_A,
            extra_field=1,  # type: ignore[call-arg]
        )


def test_market_structure_scope_matrix() -> None:
    inst = AShareGetMarketStructureInput(
        scope=AShareMarketScope.INSTRUMENT,
        instrument_id=EQUITY_A,
        start=date(2024, 1, 2),
        end=date(2024, 1, 10),
    )
    assert inst.include_bars is True
    assert inst.include_order_book is True
    with pytest.raises(ValidationError):
        AShareGetMarketStructureInput(
            scope=AShareMarketScope.MARKET,
            instrument_id=EQUITY_A,
        )
    market = AShareGetMarketStructureInput(scope=AShareMarketScope.MARKET)
    assert market.include_market_board is True
    assert market.include_bars is False


def test_capital_input_northbound_only_allows_missing_instrument() -> None:
    AShareGetCapitalSnapshotInput(metrics=(CapitalMetricType.NORTHBOUND,))
    with pytest.raises(ValidationError):
        AShareGetCapitalSnapshotInput(metrics=())
    with pytest.raises(ValidationError):
        AShareGetCapitalSnapshotInput(
            metrics=(CapitalMetricType.DAILY_FLOW, CapitalMetricType.NORTHBOUND)
        )


def test_research_reports_requires_filter() -> None:
    with pytest.raises(ValidationError):
        ResearchSearchReportsInput()
    ResearchSearchReportsInput(text="茅台")
    ResearchSearchReportsInput(instrument_id=EQUITY_A)


def test_mcp_inputs_reject_us_and_wrong_asset_types() -> None:
    """Exhaustive US / wrong-asset rejection for frozen A-share MCP inputs."""
    # Snapshot: A_SHARE equity/etf/index ok; US and OPTION reject.
    for good in (EQUITY_A, ETF_A, INDEX_A):
        AShareGetSnapshotInput(instrument_id=good)
    for bad in (US_EQUITY, US_ETF, OPTION_A):
        with pytest.raises(ValidationError):
            AShareGetSnapshotInput(instrument_id=bad)

    # Structure instrument scope: same matrix.
    for good in (EQUITY_A, ETF_A, INDEX_A):
        AShareGetMarketStructureInput(
            instrument_id=good,
            start=date(2024, 1, 2),
            end=date(2024, 1, 10),
        )
    for bad in (US_EQUITY, OPTION_A):
        with pytest.raises(ValidationError):
            AShareGetMarketStructureInput(
                instrument_id=bad,
                start=date(2024, 1, 2),
                end=date(2024, 1, 10),
            )

    # Capital: equity/etf ok; INDEX market-scope-only so instrument INDEX rejects;
    # US and OPTION reject.
    AShareGetCapitalSnapshotInput(instrument_id=EQUITY_A, metrics=(CapitalMetricType.DAILY_FLOW,))
    AShareGetCapitalSnapshotInput(instrument_id=ETF_A, metrics=(CapitalMetricType.DAILY_FLOW,))
    for bad in (US_EQUITY, INDEX_A, OPTION_A):
        with pytest.raises(ValidationError):
            AShareGetCapitalSnapshotInput(
                instrument_id=bad, metrics=(CapitalMetricType.DAILY_FLOW,)
            )

    # Sentiment: A_SHARE quote-like; US/OPTION reject.
    AShareGetSentimentSnapshotInput(instrument_id=EQUITY_A)
    for bad in (US_EQUITY, OPTION_A):
        with pytest.raises(ValidationError):
            AShareGetSentimentSnapshotInput(instrument_id=bad)

    # Options underlying must be ETF:A_SHARE.
    AShareGetEtfOptionSnapshotInput(underlying_instrument_id=ETF_A)
    for bad in (EQUITY_A, INDEX_A, OPTION_A, US_EQUITY, US_ETF):
        with pytest.raises(ValidationError):
            AShareGetEtfOptionSnapshotInput(underlying_instrument_id=bad)

    # Reports: equity/etf/index A_SHARE; reject US/OPTION.
    for good in (EQUITY_A, ETF_A, INDEX_A):
        ResearchSearchReportsInput(instrument_id=good)
    for bad in (US_EQUITY, OPTION_A):
        with pytest.raises(ValidationError):
            ResearchSearchReportsInput(instrument_id=bad)
