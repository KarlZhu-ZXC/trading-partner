"""Shared fact orchestration for the closed research workflow recipes."""

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
from application.dto.peer_comparison import PeerComparisonRunInput
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
from application.ports.workflow_run_repository import WorkflowRunRecord, WorkflowRunRepository
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.idempotency import canonical_payload_sha256
from application.services.investment_case_service import InvestmentCaseService
from application.services.peer_comparison_service import PeerComparisonService
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
from domain.common.errors import InputValidationError, TradingPartnerError, WorkflowRunInProgress
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

_WORKFLOW_LEASE = timedelta(minutes=5)


def _synthesis_contract(workflow_type: WorkflowType) -> WorkflowSynthesisContractDTO:
    if workflow_type is WorkflowType.PEER_COMPARISON:
        return WorkflowSynthesisContractDTO(
            required_sections=(
                "comparison_basis",
                "material_differences",
                "cash_flow_quality",
                "balance_sheet_resilience",
                "valuation_basis_and_gaps",
                "peer_data_limitations",
            ),
            candidate_update_tools=("research_judgment_propose",),
            prohibited_outputs=("orders", "position_sizing", "trade_approval"),
        )
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
        candidate_update_tools=("research_judgment_propose",),
        prohibited_outputs=("orders", "position_sizing", "trade_approval"),
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
        peer_comparison: PeerComparisonService,
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
        self._peer_comparison = peer_comparison
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
            if (
                request.case_creation_confirmed_by is None
                or request.case_creation_idempotency_key is None
            ):
                exc = InputValidationError(
                    "Creating a Draft research file requires explicit "
                    "case_creation_confirmed_by and case_creation_idempotency_key"
                )
                now = self._clock.now()
                return ToolEnvelope.failure(
                    request_id=self._ids.new(EntityIdPrefix.REQ),
                    market=None,
                    as_of=request.as_of or now,
                    fetched_at=now,
                    errors=(to_error_info(exc, self._redactor),),
                )
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
                idempotency_key=request.case_creation_idempotency_key,
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
                "a_share_get_facts/market_structure",
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
                "a_share_get_facts/market_structure",
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
                "a_share_get_facts/limit_up",
                True,
                lambda: self._a_share.get_limit_up_context(
                    AShareGetLimitUpContextInput(trade_date=trade_date, as_of=as_of)
                ),
            ),
            _Step(
                "northbound_capital",
                "a_share_get_facts/capital",
                False,
                lambda: self._a_share.get_capital_snapshot(
                    AShareGetCapitalSnapshotInput(
                        metrics=(CapitalMetricType.NORTHBOUND,), as_of=as_of
                    )
                ),
            ),
            _Step(
                "market_sentiment",
                "a_share_get_facts/sentiment",
                False,
                lambda: self._a_share.get_sentiment_snapshot(
                    AShareGetSentimentSnapshotInput(trade_date=trade_date, as_of=as_of)
                ),
            ),
            self._portfolio_step((), False),
        )
        return await self._execute(
            WorkflowType.A_SHARE_MARKET_REVIEW,
            as_of,
            None,
            None,
            steps,
            idempotency_key=request.idempotency_key,
            request_payload_sha256=self._request_payload_sha256(request),
        )

    async def run_us_market_review(
        self, request: USRunMarketReviewInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        steps: list[_Step] = [
            _Step(
                "major_index_context",
                "market_data_get/us_market",
                True,
                lambda: self._us_market.get_market_context(MarketGetContextInput(as_of=as_of)),
            ),
            _Step(
                "macro_rates_volatility",
                "us_context_get/macro",
                False,
                lambda: self._us_context.get_macro_context(
                    USGetMacroContextInput(as_of=as_of, lookback_days=365)
                ),
            ),
            _Step(
                "market_news",
                "us_company_get/live_news",
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
                    "us_context_get/prediction_market",
                    False,
                    lambda: self._us_context.get_prediction_market_context(
                        USGetPredictionMarketContextInput(
                            topic=request.prediction_topic or "US market", as_of=as_of
                        )
                    ),
                )
            )
        return await self._execute(
            WorkflowType.US_MARKET_REVIEW,
            as_of,
            None,
            None,
            tuple(steps),
            idempotency_key=request.idempotency_key,
            request_payload_sha256=self._request_payload_sha256(request),
        )

    async def run_portfolio_review(
        self, request: PortfolioRunReviewInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        steps: list[_Step] = []
        if request.refresh_accounts:
            steps.append(
                _Step(
                    "account_refresh",
                    "external_state_sync/accounts",
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
                    "account_get/positions",
                    True,
                    lambda: self._async_envelope(
                        self._portfolio.get_account_positions(AccountGetPositionsInput())
                    ),
                ),
                self._portfolio_step(request.account_snapshot_ids, True),
                _Step(
                    "historical_transactions",
                    "account_get/transactions",
                    False,
                    lambda: self._async_envelope(
                        self._transactions.list_durable_transactions(
                            self._transaction_request(request.providers, as_of)
                        )
                    ),
                ),
                _Step(
                    "industry_theme_correlation_beta",
                    "research_workflow_run/portfolio_review",
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
            idempotency_key=request.idempotency_key,
            request_payload_sha256=self._request_payload_sha256(request),
        )

    async def run_peer_comparison(
        self, request: PeerComparisonRunInput
    ) -> ToolEnvelope[WorkflowRunDTO]:
        """Run one replay-safe, caller-specified cross-company fact comparison."""
        as_of = request.as_of or self._clock.now()
        step = _Step(
            "peer_comparison_facts",
            "research_workflow_run/peer_comparison",
            True,
            lambda: self._peer_comparison.compare(request),
        )
        return await self._execute(
            WorkflowType.PEER_COMPARISON,
            as_of,
            None,
            request.primary_instrument_id,
            (step,),
            serial=True,
            idempotency_key=request.idempotency_key,
            request_payload_sha256=self._request_payload_sha256(request),
        )

    async def _run_case_recipe(
        self,
        workflow_type: WorkflowType,
        request: ResearchRunDeepDiveInput | ResearchRunCatalystReviewInput,
    ) -> ToolEnvelope[WorkflowRunDTO]:
        as_of = request.as_of or self._clock.now()
        request_payload_sha256 = self._request_payload_sha256(request)
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
                    idempotency_key=request.idempotency_key,
                    request_payload_sha256=request_payload_sha256,
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
                idempotency_key=request.idempotency_key,
                request_payload_sha256=request_payload_sha256,
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
            "investment_case_read/context",
            True,
            lambda: self._async_envelope(context_envelope),
        )
        if not context_envelope.ok or context_envelope.data is None:
            return await self._execute(
                workflow_type,
                as_of,
                request.case_id,
                request.instrument_id,
                (context_step,),
                idempotency_key=request.idempotency_key,
                request_payload_sha256=request_payload_sha256,
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
                idempotency_key=request.idempotency_key,
                request_payload_sha256=request_payload_sha256,
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
                idempotency_key=request.idempotency_key,
                request_payload_sha256=request_payload_sha256,
            )
        steps.append(self._portfolio_step((), False))
        return await self._execute(
            workflow_type,
            as_of,
            case_id,
            instrument_id,
            tuple(steps),
            serial=market is Market.A_SHARE,
            idempotency_key=request.idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )

    def _us_case_steps(
        self,
        instrument_id: str,
        since: datetime,
        as_of: datetime,
        workflow_type: WorkflowType,
        request: ResearchRunDeepDiveInput | ResearchRunCatalystReviewInput,
    ) -> list[_Step]:
        asset_type, _, symbol = parse_instrument_id(instrument_id)
        steps: list[_Step] = [
            _Step(
                "market_technical_context",
                "market_data_get/composite",
                True,
                lambda: self._us_market.get_us_snapshot(
                    USGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
                ),
            )
        ]
        if asset_type is not AssetType.EQUITY:
            asset_label = asset_type.value.replace("_", " ")
            steps.extend(
                (
                    _Step(
                        "instrument_news",
                        "us_company_get/live_news",
                        False,
                        lambda: self._us_context.get_live_news(
                            MarketGetLiveNewsInput(
                                instrument_id=(
                                    instrument_id
                                    if asset_type is AssetType.ETF
                                    else None
                                ),
                                query=(
                                    None
                                    if asset_type is AssetType.ETF
                                    else f"{symbol} {asset_label}"
                                ),
                                start=since.date(),
                                end=as_of.date(),
                                as_of=as_of,
                            )
                        ),
                    ),
                    _Step(
                        "macro_context",
                        "us_context_get/macro",
                        False,
                        lambda: self._us_context.get_macro_context(
                            USGetMacroContextInput(
                                lookback_days=min(3_650, max(30, (as_of - since).days)),
                                as_of=as_of,
                            )
                        ),
                    ),
                )
            )
            if asset_type is AssetType.ETF:
                steps.insert(
                    2,
                    _Step(
                        "social_sentiment",
                        "us_context_get/sentiment",
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
                )
            topic = getattr(request, "topic", None)
            if isinstance(topic, str) and topic.strip():
                prediction_topic = topic.strip()
                steps.append(
                    _Step(
                        "prediction_context",
                        "us_context_get/prediction_market",
                        False,
                        lambda: self._us_context.get_prediction_market_context(
                            USGetPredictionMarketContextInput(
                                topic=prediction_topic,
                                as_of=as_of,
                            )
                        ),
                    )
                )
            return steps

        steps.extend(
            (
                _Step(
                    "fundamentals",
                    "us_company_get/fundamentals_snapshot",
                    workflow_type is WorkflowType.DEEP_DIVE,
                    lambda: self._us_research.get_fundamental_snapshot(
                        FundamentalGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
                    ),
                ),
                _Step(
                    "company_events",
                    "us_company_get/company_updates",
                    True,
                    lambda: self._us_research.get_company_updates(
                        ResearchGetCompanyUpdatesInput(
                            instrument_id=instrument_id, since=since, as_of=as_of
                        )
                    ),
                ),
                _Step(
                    "company_news",
                    "us_company_get/live_news",
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
                    "us_context_get/sentiment",
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
                    "us_context_get/macro",
                    False,
                    lambda: self._us_context.get_macro_context(
                        USGetMacroContextInput(
                            lookback_days=min(3_650, max(30, (as_of - since).days)), as_of=as_of
                        )
                    ),
                ),
            )
        )
        if workflow_type is WorkflowType.DEEP_DIVE:
            steps.insert(
                2,
                _Step(
                    "financial_statements",
                    "us_company_get/fundamental_statements",
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
                    "us_context_get/prediction_market",
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
                "a_share_get_facts/snapshot",
                True,
                lambda: self._a_share.get_snapshot(
                    AShareGetSnapshotInput(instrument_id=instrument_id, detail=detail, as_of=as_of)
                ),
            ),
            _Step(
                "market_technical_context",
                "a_share_get_facts/market_structure",
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
                "a_share_get_facts/capital",
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
                "a_share_get_facts/sentiment",
                False,
                lambda: self._a_share.get_sentiment_snapshot(
                    AShareGetSentimentSnapshotInput(
                        instrument_id=instrument_id, trade_date=as_of.date(), as_of=as_of
                    )
                ),
            ),
            _Step(
                "research_reports",
                "a_share_get_facts/research_reports",
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
                    "a_share_get_facts/financials",
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
                        "a_share_get_facts/company_operating_metrics",
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
                    "a_share_get_facts/industry_cycle",
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
            "portfolio_analyze/exposure",
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
        idempotency_key: str,
        request_payload_sha256: str,
    ) -> ToolEnvelope[WorkflowRunDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        started_at = self._clock.now()
        try:
            claimed_run = WorkflowRun(
                run_id=self._ids.new(EntityIdPrefix.RUN),
                workflow_type=workflow_type,
                case_id=case_id,
                instrument_id=instrument_id,
                requested_as_of=as_of,
                started_at=started_at,
                completed_at=None,
                status=WorkflowRunStatus.STARTED,
                steps=(),
            )
            claim = self._repository.claim(
                claimed_run,
                idempotency_key=idempotency_key,
                request_payload_sha256=request_payload_sha256,
                heartbeat_at=started_at,
                lease_expires_at=started_at + _WORKFLOW_LEASE,
            )
            if not claim.claimed:
                if claim.record.run.status in {
                    WorkflowRunStatus.STARTED,
                    WorkflowRunStatus.RUNNING,
                }:
                    raise WorkflowRunInProgress(
                        "The same Workflow request is already running",
                        details={"run_id": claim.record.run.run_id},
                    )
                return self._render_record(request_id, claim.record)
            run_id = claim.record.run.run_id
            self._repository.mark_running(
                run_id,
                heartbeat_at=started_at,
                lease_expires_at=started_at + _WORKFLOW_LEASE,
            )
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
                else WorkflowRunStatus.SUCCEEDED
            )
            run = WorkflowRun(
                run_id=run_id,
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
            record = self._repository.complete(
                run,
                fact_data=tuple(fact_data),
                missing_capabilities=tuple(archive_missing),
            )
            return self._render_record(request_id, record)
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

    def _render_record(
        self, request_id: str, record: WorkflowRunRecord
    ) -> ToolEnvelope[WorkflowRunDTO]:
        run = record.run
        data = WorkflowRunDTO.from_domain(
            run,
            fact_data=record.fact_data,
            synthesis_contract=_synthesis_contract(run.workflow_type),
            missing_capabilities=record.missing_capabilities,
        )
        warning = WarningInfo(
            code="WORKFLOW_INCOMPLETE",
            message="One or more workflow facts are degraded, failed, or unavailable.",
            details={"status": run.status.value},
        )
        warnings = (warning,) if run.status is not WorkflowRunStatus.SUCCEEDED else ()
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=run.requested_as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            data=data,
            degraded=bool(warnings),
            warnings=warnings,
        )

    @staticmethod
    def _request_payload_sha256(request: Any) -> str:
        return canonical_payload_sha256(
            request.model_dump(mode="json", exclude={"idempotency_key"})
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
