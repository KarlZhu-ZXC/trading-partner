from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from application.dto.a_share import (
    AShareGetCapitalSnapshotInput,
    AShareGetFinancialStatementsInput,
)
from application.dto.peer_comparison import PeerComparisonRunInput
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.dto.workflow import (
    AShareRunMarketReviewInput,
    PortfolioRunReviewInput,
    ResearchRunCatalystReviewInput,
    ResearchRunDeepDiveInput,
    USRunMarketReviewInput,
)
from application.ports.workflow_run_repository import WorkflowRunClaim, WorkflowRunRecord
from application.services.portfolio_enrichment_calculator import PortfolioEnrichmentCalculator
from application.services.portfolio_review_fact_service import PortfolioReviewFactService
from application.services.portfolio_risk_calculator import PortfolioRiskCalculator
from application.services.research_workflow_orchestrator import ResearchWorkflowOrchestrator
from domain.a_share.enums import CapitalMetricType
from domain.common.enums import Freshness, Market, VendorId
from domain.common.errors import IdempotencyConflict
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
        self.records: dict[str, WorkflowRunRecord] = {}
        self.keys: dict[str, str] = {}

    def claim(
        self,
        run: object,
        *,
        idempotency_key: str,
        request_payload_sha256: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRunClaim:
        existing_id = self.keys.get(idempotency_key)
        if existing_id is not None:
            existing = self.records[existing_id]
            if existing.request_payload_sha256 != request_payload_sha256:
                raise IdempotencyConflict("Workflow idempotency key was reused")
            return WorkflowRunClaim(existing, False)
        record = WorkflowRunRecord(
            run=run,
            request_payload_sha256=request_payload_sha256,
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
        )
        self.records[run.run_id] = record
        self.keys[idempotency_key] = run.run_id
        return WorkflowRunClaim(record, True)

    def mark_running(self, run_id: str, **_: object) -> None:
        assert run_id in self.records

    def complete(
        self,
        run: object,
        *,
        fact_data: tuple[object, ...],
        missing_capabilities: tuple[str, ...],
    ) -> WorkflowRunRecord:
        self.runs.append(run)
        previous = self.records[run.run_id]
        record = WorkflowRunRecord(
            run=run,
            request_payload_sha256=previous.request_payload_sha256,
            heartbeat_at=NOW,
            lease_expires_at=NOW,
            fact_data=fact_data,
            missing_capabilities=missing_capabilities,
        )
        self.records[run.run_id] = record
        return record

    def get(self, run_id: str) -> object:
        return self.records[run_id].run

    def get_record(self, run_id: str) -> WorkflowRunRecord:
        return self.records[run_id]

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRunRecord | None:
        run_id = self.keys.get(idempotency_key)
        return self.records.get(run_id) if run_id is not None else None


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
        peer_comparison=MagicMock(),
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
                "get_financial_statements",
                "get_company_operating_metrics",
                "get_industry_cycle",
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
    dependencies.peer_comparison.compare = AsyncMock(
        return_value=_success("req_peer_comparison")
    )
    dependencies.transactions.list_durable_transactions.return_value = _success(
        "req_durable_transactions"
    )
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
        dependencies.peer_comparison,
        _Clock(),
        _Ids(),
        DefaultSecretRedactor(),
    )
    return service, dependencies


@pytest.mark.asyncio
async def test_peer_comparison_is_one_replay_safe_workflow_step() -> None:
    service, dependencies = _orchestrator()
    request = PeerComparisonRunInput(
        idempotency_key="peer-comparison-1",
        primary_instrument_id="equity:US:NVDA",
        peer_instrument_ids=("equity:US:AMD",),
        as_of=NOW,
    )

    first = await service.run_peer_comparison(request)
    replay = await service.run_peer_comparison(request)

    assert first.ok and replay.ok
    assert first.data is not None
    assert first.data.workflow_type is WorkflowType.PEER_COMPARISON
    assert tuple(fact.receipt.step_name for fact in first.data.facts) == (
        "peer_comparison_facts",
    )
    assert replay.data == first.data
    dependencies.peer_comparison.compare.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_type", "instrument_id", "method", "payload"),
    (
        (
            WorkflowType.DEEP_DIVE,
            "equity:US:NVDA",
            "run_deep_dive",
            ResearchRunDeepDiveInput(
                idempotency_key="workflow-1", case_id="case_1", as_of=NOW
            ),
        ),
        (
            WorkflowType.CATALYST_REVIEW,
            "equity:A_SHARE:600519.SH",
            "run_catalyst_review",
            ResearchRunCatalystReviewInput(
                idempotency_key="workflow-1", case_id="case_1", as_of=NOW
            ),
        ),
        (
            WorkflowType.A_SHARE_MARKET_REVIEW,
            "equity:US:NVDA",
            "run_a_share_market_review",
            AShareRunMarketReviewInput(idempotency_key="workflow-1", as_of=NOW),
        ),
        (
            WorkflowType.US_MARKET_REVIEW,
            "equity:US:NVDA",
            "run_us_market_review",
            USRunMarketReviewInput(
                idempotency_key="workflow-1", as_of=NOW, prediction_topic="Fed"
            ),
        ),
        (
            WorkflowType.PORTFOLIO_REVIEW,
            "equity:US:NVDA",
            "run_portfolio_review",
            PortfolioRunReviewInput(
                idempotency_key="workflow-1", refresh_accounts=True, as_of=NOW
            ),
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
    assert result.data.status is WorkflowRunStatus.SUCCEEDED
    assert result.data.execution_effect is False
    assert dependencies.repository.runs
    if workflow_type in {WorkflowType.DEEP_DIVE, WorkflowType.CATALYST_REVIEW}:
        assert result.data.report_id == "report_1"


@pytest.mark.asyncio
async def test_optional_fact_failure_returns_persisted_partial_run() -> None:
    service, dependencies = _orchestrator()
    dependencies.us_context.get_macro_context.return_value = _failure()

    result = await service.run_us_market_review(
        USRunMarketReviewInput(idempotency_key="workflow-1", as_of=NOW)
    )

    assert result.ok is True and result.data is not None
    assert result.data.status is WorkflowRunStatus.PARTIAL
    assert result.degraded is True
    assert any(not fact.receipt.ok for fact in result.data.facts)
    assert dependencies.repository.runs[0].status is WorkflowRunStatus.PARTIAL


@pytest.mark.asyncio
async def test_terminal_workflow_replay_skips_all_provider_calls() -> None:
    service, dependencies = _orchestrator()
    request = USRunMarketReviewInput(idempotency_key="workflow-replay", as_of=NOW)

    first = await service.run_us_market_review(request)
    replay = await service.run_us_market_review(request)
    conflict = await service.run_us_market_review(
        request.model_copy(update={"prediction_topic": "Fed"})
    )

    assert first.ok and replay.ok
    assert first.data == replay.data
    assert not conflict.ok
    assert conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"
    dependencies.us_market.get_market_context.assert_awaited_once()
    dependencies.us_context.get_macro_context.assert_awaited_once()
    dependencies.us_context.get_live_news.assert_awaited_once()


@pytest.mark.asyncio
async def test_portfolio_review_defaults_to_durable_accounts() -> None:
    service, dependencies = _orchestrator()

    result = await service.run_portfolio_review(
        PortfolioRunReviewInput(idempotency_key="workflow-1", as_of=NOW)
    )

    assert result.ok is True
    dependencies.portfolio.get_account_snapshot.assert_not_awaited()
    dependencies.portfolio.get_account_positions.assert_called_once()
    dependencies.transactions.get_transactions.assert_not_awaited()
    dependencies.transactions.list_durable_transactions.assert_called_once()


@pytest.mark.asyncio
async def test_deep_dive_can_run_ad_hoc_when_instrument_has_no_case() -> None:
    service, dependencies = _orchestrator()

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1",
            instrument_id="equity:US:NVDA",
            as_of=NOW,
            create_case=False,
        )
    )

    assert result.ok is True and result.data is not None
    assert result.data.status is WorkflowRunStatus.SUCCEEDED
    assert result.data.case_id is None
    assert result.data.instrument_id == "equity:US:NVDA"
    assert result.data.report_id is None
    assert "No instrument research file context; research ran in ad-hoc mode" in (
        result.data.missing_capabilities
    )
    assert len(result.data.facts) > 1
    assert all(fact.receipt.step_name != "durable_research_context" for fact in result.data.facts)
    dependencies.context.build.assert_not_called()


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
            idempotency_key="workflow-1",
            instrument_id="equity:US:NVDA",
            as_of=NOW,
            case_title="NVDA 深度研究",
            case_creation_confirmed_by="user",
            case_creation_idempotency_key="deep-dive-nvda-case",
        )
    )

    assert result.ok is True and result.data is not None
    assert result.data.case_id == "case_created"
    assert result.data.missing_capabilities == ()
    dependencies.cases.create_case.assert_called_once()


@pytest.mark.asyncio
async def test_deep_dive_never_infers_user_confirmation_for_case_creation() -> None:
    service, dependencies = _orchestrator()

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1",
            instrument_id="equity:US:NVDA",
            as_of=NOW,
        )
    )

    assert result.ok is False
    assert result.errors[0].code == "INPUT_VALIDATION_ERROR"
    dependencies.cases.create_case.assert_not_called()


def test_deep_dive_case_confirmation_fields_are_atomic() -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1",
            instrument_id="equity:US:NVDA",
            case_creation_confirmed_by="user",
        )


@pytest.mark.asyncio
async def test_workflow_receipts_only_name_public_tools() -> None:
    service, _dependencies = _orchestrator()

    result = await service.run_portfolio_review(
        PortfolioRunReviewInput(idempotency_key="workflow-1", as_of=NOW)
    )

    assert result.ok is True and result.data is not None
    assert {fact.receipt.tool_name for fact in result.data.facts} == {
        "account_get",
        "portfolio_analyze",
        "portfolio_run_review",
    }


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

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1", case_id="case_1", as_of=NOW
        )
    )

    assert result.ok is True and result.data is not None
    assert result.data.status is WorkflowRunStatus.SUCCEEDED
    request = dependencies.a_share.get_capital_snapshot.call_args.args[0]
    assert isinstance(request, AShareGetCapitalSnapshotInput)
    assert request.metrics == expected_metrics


@pytest.mark.asyncio
async def test_a_share_equity_deep_dive_includes_structured_financial_statements() -> None:
    service, dependencies = _orchestrator("equity:A_SHARE:600519.SH")

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1", case_id="case_1", as_of=NOW
        )
    )

    assert result.ok is True and result.data is not None
    step_names = {fact.receipt.step_name for fact in result.data.facts}
    assert "financial_statements" in step_names
    request = dependencies.a_share.get_financial_statements.call_args.args[0]
    assert isinstance(request, AShareGetFinancialStatementsInput)
    assert request.instrument_id == "equity:A_SHARE:600519.SH"
    assert request.periods == 8


@pytest.mark.asyncio
async def test_a_share_deep_dive_explicit_hog_cycle_adds_company_and_cycle_facts() -> None:
    service, dependencies = _orchestrator("equity:A_SHARE:002714.SZ")

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1",
            case_id="case_1",
            as_of=NOW,
            industry_cycle="hog",
            industry_cycle_lookback_months=180,
            company_operating_lookback_months=48,
            company_operating_document_limit=25,
        )
    )

    assert result.ok is True and result.data is not None
    step_names = {fact.receipt.step_name for fact in result.data.facts}
    assert "company_operating_metrics" in step_names
    assert "industry_cycle_hog" in step_names
    company_request = dependencies.a_share.get_company_operating_metrics.call_args.args[0]
    assert company_request.instrument_id == "equity:A_SHARE:002714.SZ"
    assert company_request.lookback_months == 48
    assert company_request.document_limit == 25
    cycle_request = dependencies.a_share.get_industry_cycle.call_args.args[0]
    assert cycle_request.cycle == "hog"
    assert cycle_request.lookback_months == 180
    assert cycle_request.view == "compact"


@pytest.mark.asyncio
async def test_a_share_deep_dive_does_not_infer_hog_cycle_from_instrument() -> None:
    service, dependencies = _orchestrator("equity:A_SHARE:002714.SZ")

    result = await service.run_deep_dive(
        ResearchRunDeepDiveInput(
            idempotency_key="workflow-1", case_id="case_1", as_of=NOW
        )
    )

    assert result.ok is True
    dependencies.a_share.get_company_operating_metrics.assert_not_awaited()
    dependencies.a_share.get_industry_cycle.assert_not_awaited()
