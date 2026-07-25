"""Shared fact orchestration for the five Phase 1L research recipes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import JsonValue

from application.dto.a_share import (
    AShareGetCapitalSnapshotInput,
    AShareGetCompanyOperatingMetricsInput,
    AShareGetFinancialStatementsInput,
    AShareGetIndustryCycleInput,
    AShareGetLimitUpContextInput,
    AShareGetMarketStructureInput,
    AShareGetSentimentSnapshotInput,
    AShareGetSnapshotInput,
    ResearchSearchReportsInput,
)
from application.dto.account_transactions import AccountGetTransactionsInput
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.portfolio import (
    AccountGetPositionsInput,
    AccountGetSnapshotInput,
    PortfolioAnalyzeInput,
)
from application.dto.research_context import ResearchContextBuildInput
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.dto.us_context import (
    MarketGetLiveNewsInput,
    USGetMacroContextInput,
    USGetPredictionMarketContextInput,
    USGetSentimentSnapshotInput,
)
from application.dto.us_market import MarketGetContextInput, USGetSnapshotInput
from application.dto.us_research import (
    FundamentalGetSnapshotInput,
    FundamentalGetStatementsInput,
    ResearchGetCompanyUpdatesInput,
)
from application.dto.workflow import (
    AShareRunMarketReviewInput,
    PortfolioRunReviewInput,
    ResearchRunCatalystReviewInput,
    ResearchRunDeepDiveInput,
    USRunMarketReviewInput,
    WorkflowRunDTO,
    WorkflowSynthesisContractDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.ports.workflow_run_repository import WorkflowRunRepository
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.investment_case_service import InvestmentCaseService
from application.services.portfolio_review_fact_service import PortfolioReviewFactService
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_context_builder import ResearchContextBuilder
from application.services.us_context_tool_coordinator import USContextToolCoordinator
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from application.services.us_tool_coordinator import USToolCoordinator
from domain.a_share.enums import AShareMarketScope, AShareSnapshotDetail, CapitalMetricType
from domain.common.enums import (
    AssetType,
    Freshness,
    InvestmentCaseType,
    Market,
    ResearchReportType,
    VendorId,
)
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.workflow.enums import WorkflowRunStatus, WorkflowType
from domain.workflow.models import WorkflowRun, WorkflowStepReceipt


@dataclass(frozen=True, slots=True)
class _Step:
    name: str
    tool_name: str
    required: bool
    call: Callable[[], Awaitable[ToolEnvelope[Any]]]


_BASE_SYNTHESIS_SECTIONS = (
    "rating",
    "confidence",
    "investment_thesis",
    "bull_case",
    "bear_case",
    "valuation_context",
    "technical_context",
    "catalysts",
    "risks",
    "invalidation_conditions",
    "portfolio_implications",
    "open_questions",
    "candidate_state_updates",
)


def _synthesis_contract(workflow_type: WorkflowType) -> WorkflowSynthesisContractDTO:
    extras = {
        WorkflowType.DEEP_DIVE: (),
        WorkflowType.CATALYST_REVIEW: (
            "expected_catalysts",
            "realized_catalysts",
            "risk_catalysts",
            "remaining_expectation_gap",
        ),
        WorkflowType.A_SHARE_MARKET_REVIEW: (
            "limit_up_ecology",
            "market_style",
            "next_session_watch_items",
        ),
        WorkflowType.US_MARKET_REVIEW: (
            "sector_factor_rotation",
            "market_breadth_limitations",
            "next_session_watch_items",
        ),
        WorkflowType.PORTFOLIO_REVIEW: (
            "concentration_duplicate_risk",
            "thesis_position_conflicts",
            "opportunity_cost",
        ),
    }[workflow_type]
    return WorkflowSynthesisContractDTO(
        required_sections=_BASE_SYNTHESIS_SECTIONS + extras,
        candidate_update_tools=("thesis_revision_propose", "research_state_update"),
        prohibited_outputs=("orders", "position_sizing", "simulated_fills", "trade_approval"),
    )


class ResearchWorkflowOrchestrator:
    def __init__(
        self,
        repository: WorkflowRunRepository,
        investment_cases: InvestmentCaseService,
        context_builder: ResearchContextBuilder,
        archive: ResearchArchiveService,
        a_share: AShareToolCoordinator,
        us_market: USToolCoordinator,
        us_research: USResearchToolCoordinator,
        us_context: USContextToolCoordinator,
        portfolio: PortfolioToolCoordinator,
        transactions: AccountTransactionCoordinator,
        portfolio_review_facts: PortfolioReviewFactService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._repository = repository
        self._investment_cases = investment_cases
        self._context = context_builder
        self._archive = archive
        self._a_share = a_share
        self._us_market = us_market
        self._us_research = us_research
        self._us_context = us_context
        self._portfolio = portfolio
        self._transactions = transactions
        self._portfolio_review_facts = portfolio_review_facts
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def run_deep_dive(
        self, request: ResearchRunDeepDiveInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        if request.case_id is None and request.instrument_id is not None and request.create_case:
            prepared = self._ensure_deep_dive_case(request)
            if isinstance(prepared, ToolEnvelope):
                return prepared
            request = prepared
        return await self._run_case_recipe(WorkflowType.DEEP_DIVE, request)

    def _ensure_deep_dive_case(
        self, request: ResearchRunDeepDiveInput
    ) -> ResearchRunDeepDiveInput | ToolEnvelope[WorkflowRunDTO]:
        """Reuse one open instrument research file or create a confirmed Draft file."""
        instrument_id = request.instrument_id
        assert instrument_id is not None
        open_cases = self._investment_cases.list_cases(
            primary_instrument_id=instrument_id,
            include_archived=False,
            limit=50,
            offset=0,
        )
        if not open_cases.ok or open_cases.data is None:
            return cast(ToolEnvelope[WorkflowRunDTO], open_cases)
        if open_cases.data.total == 1:
            case_id = open_cases.data.items[0].case_id
        elif open_cases.data.total > 1:
            # Preserve the context builder's typed ambiguity failure; never
            # guess which existing research judgment the user meant.
            return request
        else:
            all_cases = self._investment_cases.list_cases(
                primary_instrument_id=instrument_id,
                include_archived=True,
                limit=50,
                offset=0,
            )
            if not all_cases.ok or all_cases.data is None:
                return cast(ToolEnvelope[WorkflowRunDTO], all_cases)
            _, _, symbol = parse_instrument_id(instrument_id)
            created = self._investment_cases.create_case(
                case_type=InvestmentCaseType.COMPANY,
                title=request.case_title or f"{symbol} 深度研究",
                summary=request.case_summary
                or "由个股深度研究创建的标的研究档案（Draft）；尚未确认投资判断或长期跟踪。",
                primary_instrument_id=instrument_id,
                topic_tags=request.case_topic_tags,
                linked_case_ids=(),
                confirmed_by=request.case_creation_confirmed_by,
                idempotency_key=request.case_creation_idempotency_key
                or f"deep-dive-case:{instrument_id}:v{all_cases.data.total + 1}",
            )
            if not created.ok or created.data is None:
                return cast(ToolEnvelope[WorkflowRunDTO], created)
            case_id = created.data.case_id
        return request.model_copy(
            update={"case_id": case_id, "instrument_id": None, "create_case": False}
        )

    async def run_catalyst_review(
        self, request: ResearchRunCatalystReviewInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        return await self._run_case_recipe(WorkflowType.CATALYST_REVIEW, request)

    async def run_a_share_market_review(
        self, request: AShareRunMarketReviewInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        trade_date = request.trade_date or as_of.date()
        steps = (
            _Step(
                "market_board",
                "a_share_get_market_structure",
                True,
                lambda: self._a_share.get_market_structure(
                    AShareGetMarketStructureInput(
                        scope=AShareMarketScope.MARKET,
                        trade_date=trade_date,
                        include_market_board=True,
                        as_of=as_of,
                    )
                ),
            ),
            _Step(
                "industry_rotation",
                "a_share_get_market_structure",
                False,
                lambda: self._a_share.get_market_structure(
                    AShareGetMarketStructureInput(
                        scope=AShareMarketScope.INDUSTRY,
                        trade_date=trade_date,
                        include_industries=True,
                        as_of=as_of,
                    )
                ),
            ),
            _Step(
                "limit_ecology",
                "a_share_get_limit_up_context",
                True,
                lambda: self._a_share.get_limit_up_context(
                    AShareGetLimitUpContextInput(trade_date=trade_date, as_of=as_of)
                ),
            ),
            _Step(
                "northbound_capital",
                "a_share_get_capital_snapshot",
                False,
                lambda: self._a_share.get_capital_snapshot(
                    AShareGetCapitalSnapshotInput(
                        metrics=(CapitalMetricType.NORTHBOUND,), as_of=as_of
                    )
                ),
            ),
            _Step(
                "market_sentiment",
                "a_share_get_sentiment_snapshot",
                False,
                lambda: self._a_share.get_sentiment_snapshot(
                    AShareGetSentimentSnapshotInput(trade_date=trade_date, as_of=as_of)
                ),
            ),
            self._portfolio_step((), False),
        )
        return await self._execute(WorkflowType.A_SHARE_MARKET_REVIEW, as_of, None, None, steps)

    async def run_us_market_review(
        self, request: USRunMarketReviewInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        steps: list[_Step] = [
            _Step(
                "major_index_context",
                "market_get_context",
                True,
                lambda: self._us_market.get_market_context(MarketGetContextInput(as_of=as_of)),
            ),
            _Step(
                "macro_rates_volatility",
                "us_get_macro_context",
                False,
                lambda: self._us_context.get_macro_context(
                    USGetMacroContextInput(as_of=as_of, lookback_days=365)
                ),
            ),
            _Step(
                "market_news",
                "market_get_live_news",
                False,
                lambda: self._us_context.get_live_news(
                    MarketGetLiveNewsInput(query="US market", as_of=as_of, limit=30)
                ),
            ),
            self._portfolio_step((), False),
        ]
        if request.prediction_topic:
            steps.append(
                _Step(
                    "prediction_context",
                    "us_get_prediction_market_context",
                    False,
                    lambda: self._us_context.get_prediction_market_context(
                        USGetPredictionMarketContextInput(
                            topic=request.prediction_topic or "US market", as_of=as_of
                        )
                    ),
                )
            )
        return await self._execute(WorkflowType.US_MARKET_REVIEW, as_of, None, None, tuple(steps))

    async def run_portfolio_review(
        self, request: PortfolioRunReviewInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        steps: list[_Step] = []
        if request.refresh_accounts:
            steps.append(
                _Step(
                    "account_refresh",
                    "account_get_snapshot",
                    False,
                    lambda: self._portfolio.get_account_snapshot(
                        AccountGetSnapshotInput(providers=request.providers, as_of=as_of)
                    ),
                )
            )
        steps.extend(
            (
                _Step(
                    "current_positions",
                    "account_get_positions",
                    True,
                    lambda: self._async_envelope(
                        self._portfolio.get_account_positions(AccountGetPositionsInput())
                    ),
                ),
                self._portfolio_step(request.account_snapshot_ids, True),
                _Step(
                    "historical_transactions",
                    "account_get_transactions",
                    False,
                    lambda: self._transactions.get_transactions(
                        self._transaction_request(request.providers, as_of)
                    ),
                ),
                _Step(
                    "industry_theme_correlation_beta",
                    "portfolio_run_review.derived_facts",
                    False,
                    lambda: self._portfolio_review_facts.build(
                        account_snapshot_ids=request.account_snapshot_ids,
                        as_of=as_of,
                        lookback_sessions=request.risk_lookback_sessions,
                        max_instruments=request.max_risk_instruments,
                    ),
                ),
            )
        )
        return await self._execute(
            WorkflowType.PORTFOLIO_REVIEW,
            as_of,
            None,
            None,
            tuple(steps),
        )

    async def _run_case_recipe(
        self,
        workflow_type: WorkflowType,
        request: ResearchRunDeepDiveInput | ResearchRunCatalystReviewInput,
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        ad_hoc = request.case_id is None and request.instrument_id is not None
        if ad_hoc:
            instrument_id = request.instrument_id
            assert instrument_id is not None
            _, market, _ = parse_instrument_id(instrument_id)
            since = as_of - timedelta(days=request.lookback_days)
            steps: list[_Step] = []
            if market is Market.US:
                steps.extend(
                    self._us_case_steps(instrument_id, since, as_of, workflow_type, request)
                )
            elif market is Market.A_SHARE:
                steps.extend(
                    self._a_share_case_steps(instrument_id, since, as_of, workflow_type, request)
                )
            else:
                return await self._execute(
                    workflow_type,
                    as_of,
                    None,
                    instrument_id,
                    (self._portfolio_step((), False),),
                    missing_capabilities=(f"workflow market {market.value} is unsupported",),
                )
            steps.append(self._portfolio_step((), False))
            return await self._execute(
                workflow_type,
                as_of,
                None,
                instrument_id,
                tuple(steps),
                missing_capabilities=(
                    "No instrument research file context; research ran in ad-hoc mode",
                ),
                serial=market is Market.A_SHARE,
            )

        context_envelope = self._context.build(
            ResearchContextBuildInput(
                case_id=request.case_id,
                instrument_id=request.instrument_id,
                token_budget=4_000,
            )
        )
        context_step = _Step(
            "durable_research_context",
            "research_context_build",
            True,
            lambda: self._async_envelope(context_envelope),
        )
        if not context_envelope.ok or context_envelope.data is None:
            return await self._execute(
                workflow_type, as_of, request.case_id, request.instrument_id, (context_step,)
            )
        context = context_envelope.data
        case_id = context.case.case_id
        instrument_id = context.case.primary_instrument_id
        if instrument_id is None:
            return await self._execute(
                workflow_type,
                as_of,
                case_id,
                None,
                (context_step,),
                missing_capabilities=("Instrument research file has no primary instrument",),
            )
        _, market, _ = parse_instrument_id(instrument_id)
        since = as_of - timedelta(days=request.lookback_days)
        steps = [context_step]
        if market is Market.US:
            steps.extend(self._us_case_steps(instrument_id, since, as_of, workflow_type, request))
        elif market is Market.A_SHARE:
            steps.extend(
                self._a_share_case_steps(instrument_id, since, as_of, workflow_type, request)
            )
        else:
            return await self._execute(
                workflow_type,
                as_of,
                case_id,
                instrument_id,
                tuple(steps),
                missing_capabilities=(f"workflow market {market.value} is unsupported",),
            )
        steps.append(self._portfolio_step((), False))
        return await self._execute(
            workflow_type,
            as_of,
            case_id,
            instrument_id,
            tuple(steps),
            serial=market is Market.A_SHARE,
        )

    def _us_case_steps(
        self,
        instrument_id: str,
        since: datetime,
        as_of: datetime,
        workflow_type: WorkflowType,
        request: ResearchRunDeepDiveInput | ResearchRunCatalystReviewInput,
    ) -> list[_Step]:
        steps = [
            _Step(
                "market_technical_context",
                "us_get_snapshot",
                True,
                lambda: self._us_market.get_us_snapshot(
                    USGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
                ),
            ),
            _Step(
                "fundamentals",
                "fundamental_get_snapshot",
                workflow_type is WorkflowType.DEEP_DIVE,
                lambda: self._us_research.get_fundamental_snapshot(
                    FundamentalGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
                ),
            ),
            _Step(
                "company_events",
                "research_get_company_updates",
                True,
                lambda: self._us_research.get_company_updates(
                    ResearchGetCompanyUpdatesInput(
                        instrument_id=instrument_id, since=since, as_of=as_of
                    )
                ),
            ),
            _Step(
                "company_news",
                "market_get_live_news",
                False,
                lambda: self._us_context.get_live_news(
                    MarketGetLiveNewsInput(
                        instrument_id=instrument_id,
                        start=since.date(),
                        end=as_of.date(),
                        as_of=as_of,
                    )
                ),
            ),
            _Step(
                "social_sentiment",
                "us_get_sentiment_snapshot",
                False,
                lambda: self._us_context.get_sentiment_snapshot(
                    USGetSentimentSnapshotInput(
                        instrument_id=instrument_id,
                        start=since.date(),
                        end=as_of.date(),
                        as_of=as_of,
                    )
                ),
            ),
            _Step(
                "macro_context",
                "us_get_macro_context",
                False,
                lambda: self._us_context.get_macro_context(
                    USGetMacroContextInput(
                        lookback_days=min(3_650, max(30, (as_of - since).days)), as_of=as_of
                    )
                ),
            ),
        ]
        if workflow_type is WorkflowType.DEEP_DIVE:
            steps.insert(
                2,
                _Step(
                    "financial_statements",
                    "fundamental_get_statements",
                    False,
                    lambda: self._us_research.get_fundamental_statements(
                        FundamentalGetStatementsInput(instrument_id=instrument_id, as_of=as_of)
                    ),
                ),
            )
        topic = getattr(request, "topic", None)
        if topic:
            steps.append(
                _Step(
                    "prediction_context",
                    "us_get_prediction_market_context",
                    False,
                    lambda: self._us_context.get_prediction_market_context(
                        USGetPredictionMarketContextInput(topic=topic, as_of=as_of)
                    ),
                )
            )
        return steps

    def _a_share_case_steps(
        self,
        instrument_id: str,
        since: datetime,
        as_of: datetime,
        workflow_type: WorkflowType,
        request: ResearchRunDeepDiveInput | ResearchRunCatalystReviewInput,
    ) -> list[_Step]:
        asset_type, _, _ = parse_instrument_id(instrument_id)
        capital_metrics = (CapitalMetricType.DAILY_FLOW,) if asset_type is AssetType.ETF else ()
        detail = (
            AShareSnapshotDetail.FULL
            if workflow_type is WorkflowType.DEEP_DIVE
            else AShareSnapshotDetail.SUMMARY
        )
        steps = [
            _Step(
                "company_snapshot",
                "a_share_get_snapshot",
                True,
                lambda: self._a_share.get_snapshot(
                    AShareGetSnapshotInput(instrument_id=instrument_id, detail=detail, as_of=as_of)
                ),
            ),
            _Step(
                "market_technical_context",
                "a_share_get_market_structure",
                True,
                lambda: self._a_share.get_market_structure(
                    AShareGetMarketStructureInput(
                        scope=AShareMarketScope.INSTRUMENT,
                        instrument_id=instrument_id,
                        start=since.date(),
                        end=as_of.date(),
                        include_bars=True,
                        include_order_book=False,
                        as_of=as_of,
                    )
                ),
            ),
            _Step(
                "capital_ownership",
                "a_share_get_capital_snapshot",
                False,
                lambda: self._a_share.get_capital_snapshot(
                    AShareGetCapitalSnapshotInput(
                        instrument_id=instrument_id,
                        metrics=capital_metrics,
                        start=since.date(),
                        end=as_of.date(),
                        as_of=as_of,
                    )
                ),
            ),
            _Step(
                "sentiment_crowding",
                "a_share_get_sentiment_snapshot",
                False,
                lambda: self._a_share.get_sentiment_snapshot(
                    AShareGetSentimentSnapshotInput(
                        instrument_id=instrument_id, trade_date=as_of.date(), as_of=as_of
                    )
                ),
            ),
            _Step(
                "research_reports",
                "research_search_reports",
                False,
                lambda: self._a_share.search_reports(
                    ResearchSearchReportsInput(
                        instrument_id=instrument_id,
                        published_from=since.date(),
                        published_to=as_of.date(),
                        as_of=as_of,
                    )
                ),
            ),
        ]
        if workflow_type is WorkflowType.DEEP_DIVE and asset_type is AssetType.EQUITY:
            steps.insert(
                1,
                _Step(
                    "financial_statements",
                    "a_share_get_facts",
                    False,
                    lambda: self._a_share.get_financial_statements(
                        AShareGetFinancialStatementsInput(
                            instrument_id=instrument_id,
                            periods=8,
                            as_of=as_of,
                        )
                    ),
                ),
            )
        if (
            workflow_type is WorkflowType.DEEP_DIVE
            and isinstance(request, ResearchRunDeepDiveInput)
            and request.industry_cycle == "hog"
        ):
            if asset_type is AssetType.EQUITY:
                steps.insert(
                    1,
                    _Step(
                        "company_operating_metrics",
                        "a_share_get_facts",
                        False,
                        lambda: self._a_share.get_company_operating_metrics(
                            AShareGetCompanyOperatingMetricsInput(
                                instrument_id=instrument_id,
                                lookback_months=request.company_operating_lookback_months,
                                document_limit=request.company_operating_document_limit,
                                as_of=as_of,
                            )
                        ),
                    ),
                )
            steps.insert(
                2 if asset_type is AssetType.EQUITY else 1,
                _Step(
                    "industry_cycle_hog",
                    "a_share_get_facts",
                    False,
                    lambda: self._a_share.get_industry_cycle(
                        AShareGetIndustryCycleInput(
                            cycle="hog",
                            lookback_months=request.industry_cycle_lookback_months,
                            view="compact",
                            as_of=as_of,
                        )
                    ),
                ),
            )
        return steps

    def _portfolio_step(self, account_snapshot_ids: tuple[str, ...], required: bool) -> _Step:
        return _Step(
            "portfolio_context",
            "portfolio_analyze",
            required,
            lambda: self._async_envelope(
                self._portfolio.analyze_portfolio(
                    PortfolioAnalyzeInput(account_snapshot_ids=account_snapshot_ids)
                )
            ),
        )

    @staticmethod
    def _transaction_request(
        providers: tuple[VendorId, ...], as_of: datetime
    ) -> AccountGetTransactionsInput:
        return AccountGetTransactionsInput(
            providers=providers,
            start=as_of - timedelta(days=365),
            end=as_of,
            limit=500,
        )

    @staticmethod
    async def _async_envelope(value: ToolEnvelope[Any]) -> ToolEnvelope[Any]:
        return value

    async def _execute(
        self,
        workflow_type: WorkflowType,
        as_of: datetime,
        case_id: str | None,
        instrument_id: str | None,
        steps: tuple[_Step, ...],
        *,
        missing_capabilities: tuple[str, ...] = (),
        serial: bool = False,
    ) -> ToolEnvelope[WorkflowRunDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        started_at = self._clock.now()
        try:
            if serial:
                outcomes: list[ToolEnvelope[Any] | BaseException] = []
                for step in steps:
                    try:
                        outcomes.append(await step.call())
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — step receipt boundary
                        outcomes.append(exc)
            else:
                outcomes = list(
                    await asyncio.gather(*(step.call() for step in steps), return_exceptions=True)
                )
            receipts: list[WorkflowStepReceipt] = []
            fact_data: list[JsonValue | None] = []
            for ordinal, (step, outcome) in enumerate(zip(steps, outcomes, strict=True), 1):
                envelope = self._coerce_outcome(outcome, as_of)
                receipts.append(self._receipt(ordinal, step, envelope))
                dumped = envelope.model_dump(mode="json")
                fact_data.append(dumped.get("data"))
            required_failed = any(item.required and not item.ok for item in receipts)
            imperfect = any(not item.ok or item.degraded for item in receipts)
            status = (
                WorkflowRunStatus.FAILED
                if required_failed
                else WorkflowRunStatus.PARTIAL
                if imperfect
                else WorkflowRunStatus.COMPLETE
            )
            run = WorkflowRun(
                run_id=self._ids.new(EntityIdPrefix.RUN),
                workflow_type=workflow_type,
                case_id=case_id,
                instrument_id=instrument_id,
                requested_as_of=as_of,
                started_at=started_at,
                completed_at=self._clock.now(),
                status=status,
                steps=tuple(receipts),
            )
            archive_missing = list(missing_capabilities)
            if case_id is not None:
                report = self._archive_fact_report(run)
                if report.ok and report.data is not None:
                    run = replace(run, report_id=report.data.report_id)
                else:
                    archive_missing.append("case-bound fact report could not be archived")
            self._repository.append(run)
            data = WorkflowRunDTO.from_domain(
                run,
                fact_data=tuple(fact_data),
                synthesis_contract=_synthesis_contract(workflow_type),
                missing_capabilities=tuple(archive_missing),
            )
            warning = WarningInfo(
                code="WORKFLOW_INCOMPLETE",
                message="One or more workflow facts are degraded, failed, or unavailable.",
                details={"status": status.value},
            )
            warnings = (warning,) if status is not WorkflowRunStatus.COMPLETE else ()
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=data,
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                errors=(error,),
            )

    def _coerce_outcome(
        self, outcome: ToolEnvelope[Any] | BaseException, as_of: datetime
    ) -> ToolEnvelope[Any]:
        if isinstance(outcome, ToolEnvelope):
            return outcome
        error = (
            to_error_info(outcome, self._redactor)
            if isinstance(outcome, TradingPartnerError)
            else to_error_info_from_exception(outcome, self._redactor)
        )
        return ToolEnvelope.failure(
            request_id=self._ids.new(EntityIdPrefix.REQ),
            market=None,
            as_of=as_of,
            fetched_at=self._clock.now(),
            errors=(error,),
        )

    @staticmethod
    def _receipt(ordinal: int, step: _Step, envelope: ToolEnvelope[Any]) -> WorkflowStepReceipt:
        return WorkflowStepReceipt(
            ordinal=ordinal,
            step_name=step.name,
            tool_name=step.tool_name,
            required=step.required,
            ok=envelope.ok,
            degraded=envelope.degraded,
            request_id=envelope.request_id,
            as_of=envelope.as_of,
            source_names=tuple(dict.fromkeys(item.name for item in envelope.sources)),
            warning_codes=tuple(dict.fromkeys(item.code for item in envelope.warnings)),
            error_codes=tuple(dict.fromkeys(item.code for item in envelope.errors)),
        )

    def _archive_fact_report(self, run: WorkflowRun) -> ToolEnvelope[Any]:
        lines = [
            f"# {run.workflow_type.value} fact package",
            "",
            "This is a deterministic fact receipt, not an investment recommendation.",
            "",
        ]
        lines.extend(
            f"- {item.ordinal}. `{item.tool_name}`: ok={item.ok}, "
            f"degraded={item.degraded}, sources={','.join(item.source_names) or 'none'}, "
            f"warnings={','.join(item.warning_codes) or 'none'}, "
            f"errors={','.join(item.error_codes) or 'none'}"
            for item in run.steps
        )
        report_type = ResearchReportType(run.workflow_type.value)
        return self._archive.archive_report(
            case_id=run.case_id or "",
            report_type=report_type,
            title=f"{run.workflow_type.value.replace('_', ' ').title()} fact package",
            summary=f"{len(run.steps)} fact steps; terminal status {run.status.value}.",
            content_markdown="\n".join(lines),
            as_of=run.requested_as_of,
            created_by="workflow",
            research_run_id=run.run_id,
            evidence_ids=(),
            thesis_revision_ids=(),
            supersedes_report_id=None,
            model_name=None,
            prompt_version="workflow_fact_package_v1",
        )
