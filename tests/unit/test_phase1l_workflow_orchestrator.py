from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from application.dto.a_share import AShareGetCapitalSnapshotInput
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.dto.workflow import (
    AShareRunMarketReviewInput,
    PortfolioRunReviewInput,
    ResearchRunCatalystReviewInput,
    ResearchRunDeepDiveInput,
    USRunMarketReviewInput,
)
from application.services.portfolio_enrichment_calculator import PortfolioEnrichmentCalculator
from application.services.portfolio_review_fact_service import PortfolioReviewFactService
from application.services.portfolio_risk_calculator import PortfolioRiskCalculator
from application.services.research_workflow_orchestrator import ResearchWorkflowOrchestrator
from domain.a_share.enums import CapitalMetricType
from domain.common.enums import Freshness, Market, VendorId
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot
from domain.workflow.enums import WorkflowRunStatus, WorkflowType
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


class _CaseData(BaseModel):
    case_id: str
    primary_instrument_id: str


class _ContextData(BaseModel):
    case: _CaseData


class _ReportData(BaseModel):
    report_id: str


class _CaseListData(BaseModel):
    items: tuple[_CaseData, ...] = ()
    total: int = 0


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def __init__(self) -> None:
        self.index = 0

    def new(self, prefix: object) -> str:
        self.index += 1
        return f"{getattr(prefix, 'value', 'id')}_{self.index}"


class _Repository:
    def __init__(self) -> None:
        self.runs: list[object] = []

    def append(self, run: object) -> object:
        self.runs.append(run)
        return run

    def get(self, run_id: str) -> object:
        raise NotImplementedError(run_id)


def _success(request_id: str = "req_fact") -> ToolEnvelope[dict[str, int]]:
    return ToolEnvelope.success(
        request_id=request_id,
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data={"count": 1},
    )


def _failure() -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id="req_failed",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        errors=(ErrorInfo(code="STUB_FAILURE", message="stub", retryable=False),),
    )


def _orchestrator(
    instrument_id: str = "equity:US:NVDA",
) -> tuple[ResearchWorkflowOrchestrator, SimpleNamespace]:
    dependencies = SimpleNamespace(
        repository=_Repository(),
        cases=MagicMock(),
        context=MagicMock(),
        archive=MagicMock(),
        a_share=MagicMock(),
        us_market=MagicMock(),
        us_research=MagicMock(),
        us_context=MagicMock(),
        portfolio=MagicMock(),
        transactions=MagicMock(),
        derived=MagicMock(),
    )
    dependencies.context.build.return_value = ToolEnvelope.success(
        request_id="req_context",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=_ContextData(case=_CaseData(case_id="case_1", primary_instrument_id=instrument_id)),
    )
    dependencies.archive.archive_report.return_value = ToolEnvelope.success(
        request_id="req_report",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=_ReportData(report_id="report_1"),
    )
    dependencies.cases.list_cases.return_value = ToolEnvelope.success(
        request_id="req_cases",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=_CaseListData(),
    )
    for target, methods in (
        (
            dependencies.a_share,
            (
                "get_snapshot",
                "get_market_structure",
                "get_capital_snapshot",
                "get_limit_up_context",
                "get_sentiment_snapshot",
                "search_reports",
            ),
        ),
        (dependencies.us_market, ("get_market_context", "get_us_snapshot")),
        (
            dependencies.us_research,
            (
                "get_fundamental_snapshot",
                "get_fundamental_statements",
                "get_company_updates",
            ),
        ),
        (
            dependencies.us_context,
            (
                "get_live_news",
                "get_macro_context",
                "get_sentiment_snapshot",
                "get_prediction_market_context",
            ),
        ),
        (dependencies.transactions, ("get_transactions",)),
    ):
        for method in methods:
            setattr(target, method, AsyncMock(return_value=_success(f"req_{method}")))
    dependencies.portfolio.get_account_snapshot = AsyncMock(return_value=_success("req_accounts"))
    dependencies.portfolio.get_account_positions.return_value = _success("req_positions")
    dependencies.portfolio.analyze_portfolio.return_value = _success("req_portfolio")
    dependencies.derived.build = AsyncMock(return_value=_success("req_derived"))
    service = ResearchWorkflowOrchestrator(
        dependencies.repository,
        dependencies.cases,
        dependencies.context,
        dependencies.archive,
        dependencies.a_share,
        dependencies.us_market,
        dependencies.us_research,
        dependencies.us_context,
        dependencies.portfolio,
        dependencies.transactions,
        dependencies.derived,
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )
    return service, dependencies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_type", "instrument_id", "method", "payload"),
    (
        (
            WorkflowType.DEEP_DIVE,
            "equity:US:NVDA",
            "run_deep_dive",
            ResearchRunDeepDiveInput(case_id="case_1", as_of=NOW),
        ),
        (
            WorkflowType.CATALYST_REVIEW,
            "equity:A_SHARE:600519.SH",
            "run_catalyst_review",
            ResearchRunCatalystReviewInput(case_id="case_1", as_of=NOW),
        ),
        (
            WorkflowType.A_SHARE_MARKET_REVIEW,
            "equity:US:NVDA",
            "run_a_share_market_review",
            AShareRunMarketReviewInput(as_of=NOW),
        ),
        (
            WorkflowType.US_MARKET_REVIEW,
            "equity:US:NVDA",
            "run_us_market_review",
            USRunMarketReviewInput(as_of=NOW, prediction_topic="Fed"),
        ),
        (
            WorkflowType.PORTFOLIO_REVIEW,
            "equity:US:NVDA",
            "run_portfolio_review",
            PortfolioRunReviewInput(refresh_accounts=True, as_of=NOW),
        ),
    ),
)
async def test_five_recipes_share_one_terminal_fact_package(
    workflow_type: WorkflowType,
    instrument_id: str,
    method: str,
    payload: object,
) -> None:
    service, dependencies = _orchestrator(instrument_id)

    result = await getattr(service, method)(payload)

    assert result.ok is True and result.data is not None
    assert result.data.workflow_type is workflow_type
    assert result.data.status is WorkflowRunStatus.COMPLETE
    assert result.data.execution_effect is False
    assert dependencies.repository.runs
    if workflow_type in {WorkflowType.DEEP_DIVE, WorkflowType.CATALYST_REVIEW}:
        assert result.data.report_id == "report_1"


@pytest.mark.asyncio
async def test_optional_fact_failure_returns_persisted_partial_run() -> None:
    service, dependencies = _orchestrator()
    dependencies.us_context.get_macro_context.return_value = _failure()

    result = await service.run_us_market_review(USRunMarketReviewInput(as_of=NOW))

    assert result.ok is True and result.data is not None
    assert result.data.status is WorkflowRunStatus.PARTIAL
    assert result.degraded is True
    assert any(not fact.receipt.ok for fact in result.data.facts)
    assert dependencies.repository.runs[0].status is WorkflowRunStatus.PARTIAL


@pytest.mark.asyncio
async def test_portfolio_review_defaults_to_durable_accounts() -> None:
    service, dependencies = _orchestrator()

    result = await service.run_portfolio_review(PortfolioRunReviewInput(as_of=NOW))

    assert result.ok is True
    dependencies.portfolio.get_account_snapshot.assert_not_awaited()
    dependencies.portfolio.get_account_positions.assert_called_once()


@pytest.mark.asyncio
async def test_deep_dive_can_run_ad_hoc_when_instrument_has_no_case() -> None:
    service, dependencies = _orchestrator()
    dependencies.context.build.return_value = _failure()

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(instrument_id="equity:US:NVDA", as_of=NOW, create_case=False)
    )

    assert result.ok is True and result.data is not None
    assert result.data.status is WorkflowRunStatus.PARTIAL
    assert result.data.case_id is None
    assert result.data.instrument_id == "equity:US:NVDA"
    assert result.data.report_id is None
    assert "No Investment Case context; research ran in ad-hoc mode" in (
        result.data.missing_capabilities
    )
    assert len(result.data.facts) > 1


@pytest.mark.asyncio
async def test_deep_dive_creates_draft_case_by_default_then_runs_case_bound() -> None:
    service, dependencies = _orchestrator()
    created_case = _CaseData(case_id="case_created", primary_instrument_id="equity:US:NVDA")
    dependencies.cases.create_case.return_value = ToolEnvelope.success(
        request_id="req_create_case",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=created_case,
    )
    dependencies.context.build.return_value = ToolEnvelope.success(
        request_id="req_context",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=_ContextData(case=created_case),
    )

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            instrument_id="equity:US:NVDA",
            as_of=NOW,
            case_title="NVDA 深度研究",
        )
    )

    assert result.ok is True and result.data is not None
    assert result.data.case_id == "case_created"
    assert result.data.missing_capabilities == ()
    dependencies.cases.create_case.assert_called_once()


@pytest.mark.asyncio
async def test_portfolio_derived_fact_uses_provider_industry_case_theme_and_price_history() -> None:
    position = AccountPosition(
        instrument_id="equity:US:NVDA",
        side=AccountPositionSide.LONG,
        quantity=Decimal(2),
        sellable_quantity=None,
        average_cost=None,
        diluted_cost=None,
        market_price=None,
        market_price_at=None,
        market_value=Decimal(200),
        unrealized_pnl=None,
        realized_pnl=None,
        currency="USD",
    )
    snapshot = AccountSnapshot(
        snapshot_id="snapshot_1",
        account_ref="account_1",
        provider=VendorId.MOOMOO,
        environment=AccountEnvironment.REAL,
        base_currency="USD",
        account_as_of=NOW,
        fetched_at=NOW,
        cash=None,
        buying_power=None,
        net_assets=None,
        margin_used=None,
        positions=(position,),
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )
    accounts = MagicMock()
    accounts.get_snapshots.return_value = (snapshot,)
    instrument_resolver = MagicMock()
    instrument_resolver.resolve_dynamic = AsyncMock()
    context = MagicMock()
    context.build.return_value = SimpleNamespace(
        ok=True,
        data=SimpleNamespace(case=SimpleNamespace(topic_tags=("AI",))),
    )
    bars = tuple(
        SimpleNamespace(timestamp=NOW + timedelta(days=index), close=Decimal(100 + index))
        for index in range(25)
    )
    us_market = MagicMock()
    us_market.get_market_bars = AsyncMock(
        return_value=SimpleNamespace(ok=True, data=SimpleNamespace(bars=bars), sources=())
    )
    us_research = MagicMock()
    us_research.get_fundamental_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                profile=SimpleNamespace(industry="Semiconductors", sector="Technology")
            ),
            sources=(),
        )
    )
    service = PortfolioReviewFactService(
        accounts,
        instrument_resolver,
        context,
        MagicMock(),
        us_market,
        us_research,
        PortfolioRiskCalculator(),
        PortfolioEnrichmentCalculator(),
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )

    result = await service.build(
        account_snapshot_ids=(), as_of=NOW, lookback_sessions=20, max_instruments=5
    )

    assert result.ok is True and result.data is not None
    assert result.data.risk_metrics[0].correlation is not None
    assert result.data.risk_metrics[0].correlation > Decimal("0.9999")
    assert {(item.dimension, item.key) for item in result.data.enrichment.exposures} == {
        ("industry", "Semiconductors"),
        ("theme", "AI"),
    }


@pytest.mark.asyncio
async def test_portfolio_derived_fact_does_not_send_etf_to_equity_fundamentals() -> None:
    context = MagicMock()
    context.build.return_value = SimpleNamespace(ok=False, data=None)
    us_research = MagicMock()
    us_research.get_fundamental_snapshot = AsyncMock()
    service = PortfolioReviewFactService(
        MagicMock(),
        MagicMock(),
        context,
        MagicMock(),
        MagicMock(),
        us_research,
        PortfolioRiskCalculator(),
        PortfolioEnrichmentCalculator(),
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )

    classification, sources = await service._classification("etf:US:GLDM", Market.US, NOW)

    assert classification is None
    assert sources == ()
    us_research.get_fundamental_snapshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instrument_id", "expected_metrics"),
    (
        ("equity:A_SHARE:600519.SH", ()),
        ("etf:A_SHARE:510300.SH", (CapitalMetricType.DAILY_FLOW,)),
    ),
)
async def test_a_share_case_capital_metrics_follow_asset_type(
    instrument_id: str, expected_metrics: tuple[CapitalMetricType, ...]
) -> None:
    service, dependencies = _orchestrator(instrument_id)

    result = await service.run_deep_dive(ResearchRunDeepDiveInput(case_id="case_1", as_of=NOW))

    assert result.ok is True and result.data is not None
    assert result.data.status is WorkflowRunStatus.COMPLETE
    request = dependencies.a_share.get_capital_snapshot.call_args.args[0]
    assert isinstance(request, AShareGetCapitalSnapshotInput)
    assert request.metrics == expected_metrics
