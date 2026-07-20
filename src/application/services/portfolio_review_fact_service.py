"""Provider-backed portfolio industry/theme and descriptive risk facts."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

from application.dto.a_share import (
    AShareGetMarketStructureInput,
    AShareGetSnapshotInput,
)
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.portfolio import (
    PortfolioEnrichmentDTO,
    PortfolioReviewDerivedDTO,
    PortfolioRiskMetricDTO,
)
from application.dto.research_context import ResearchContextBuildInput
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.dto.us_market import MarketGetBarsInput
from application.dto.us_research import FundamentalGetSnapshotInput
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.account_service import AccountService
from application.services.instrument_resolve_service import InstrumentResolveService
from application.services.portfolio_enrichment_calculator import PortfolioEnrichmentCalculator
from application.services.portfolio_risk_calculator import PortfolioRiskCalculator
from application.services.research_context_builder import ResearchContextBuilder
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from application.services.us_tool_coordinator import USToolCoordinator
from domain.a_share.enums import AShareMarketScope, AShareSnapshotDetail
from domain.common.enums import AssetType, Freshness, Market
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.portfolio.models import PortfolioClassification, PortfolioRiskMetric

_FACT_BATCH_SIZE = 3
_FACT_BATCH_INTERVAL_SECONDS = 1.05


class PortfolioReviewFactService:
    def __init__(
        self,
        accounts: AccountService,
        instrument_resolver: InstrumentResolveService,
        context: ResearchContextBuilder,
        a_share: AShareToolCoordinator,
        us_market: USToolCoordinator,
        us_research: USResearchToolCoordinator,
        risk: PortfolioRiskCalculator,
        enrichment: PortfolioEnrichmentCalculator,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._accounts = accounts
        self._instrument_resolver = instrument_resolver
        self._context = context
        self._a_share = a_share
        self._us_market = us_market
        self._us_research = us_research
        self._risk = risk
        self._enrichment = enrichment
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def build(
        self,
        *,
        account_snapshot_ids: tuple[str, ...],
        as_of: datetime,
        lookback_sessions: int,
        max_instruments: int,
    ) -> ToolEnvelope[PortfolioReviewDerivedDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            snapshots = self._accounts.get_snapshots(account_snapshot_ids)
            positions = tuple(item for snapshot in snapshots for item in snapshot.positions)
            instrument_ids = tuple(sorted({item.instrument_id for item in positions}))
            gross_value_by_instrument: dict[str, Decimal] = {}
            for position in positions:
                if position.market_value is not None:
                    gross_value_by_instrument[position.instrument_id] = (
                        gross_value_by_instrument.get(position.instrument_id, Decimal(0))
                        + abs(position.market_value)
                    )
            selected = tuple(
                sorted(
                    instrument_ids,
                    key=lambda item: (-gross_value_by_instrument.get(item, Decimal(0)), item),
                )[:max_instruments]
            )
            for instrument_id in selected:
                asset_type, market, _ = parse_instrument_id(instrument_id)
                await self._instrument_resolver.resolve_dynamic(
                    market=market,
                    query=instrument_id,
                    asset_type_hint=asset_type,
                    as_of=as_of,
                )
            markets = {parse_instrument_id(item)[1] for item in selected}
            benchmark_series = {
                market: await self._bars(
                    self._benchmark(market), as_of, lookback_sessions
                )
                for market in markets
                if market in {Market.A_SHARE, Market.US}
            }
            outcomes = await self._paced_instrument_outcomes(
                selected,
                as_of,
                lookback_sessions,
                benchmark_series,
            )
            classifications = {
                item.instrument_id: item
                for item, _metric, _sources in outcomes
                if item is not None
            }
            metrics = tuple(metric for _classification, metric, _sources in outcomes)
            sources = self._dedupe_sources(
                tuple(source for _classification, _metric, items in outcomes for source in items)
            )
            enrichment = self._enrichment.calculate(positions, classifications)
            codes: list[str] = []
            if len(instrument_ids) > len(selected):
                codes.append("PORTFOLIO_RISK_INSTRUMENT_LIMIT")
            if any(item.missing_reason for item in metrics):
                codes.append("PORTFOLIO_RISK_HISTORY_INSUFFICIENT")
            if enrichment.missing_classification_instrument_ids:
                codes.append("PORTFOLIO_CLASSIFICATION_MISSING")
            if enrichment.missing_valuation_instrument_ids:
                codes.append("PORTFOLIO_VALUATION_MISSING")
            warnings = tuple(
                WarningInfo(
                    code=code,
                    message="Portfolio review derived fact is incomplete.",
                    details={},
                )
                for code in codes
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                sources=sources,
                data=PortfolioReviewDerivedDTO(
                    risk_metrics=tuple(
                        PortfolioRiskMetricDTO.from_domain(item) for item in metrics
                    ),
                    enrichment=PortfolioEnrichmentDTO.from_domain(enrichment),
                    warning_codes=tuple(codes),
                ),
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

    async def _paced_instrument_outcomes(
        self,
        instrument_ids: tuple[str, ...],
        as_of: datetime,
        lookback_sessions: int,
        benchmark_series: dict[
            Market, tuple[dict[date, Decimal], tuple[SourceReference, ...]]
        ],
    ) -> tuple[
        tuple[
            PortfolioClassification | None,
            PortfolioRiskMetric,
            tuple[SourceReference, ...],
        ],
        ...,
    ]:
        """Bound workflow fan-out so provider admission sees no local burst."""
        outcomes: list[
            tuple[
                PortfolioClassification | None,
                PortfolioRiskMetric,
                tuple[SourceReference, ...],
            ]
        ] = []
        for offset in range(0, len(instrument_ids), _FACT_BATCH_SIZE):
            batch = instrument_ids[offset : offset + _FACT_BATCH_SIZE]
            outcomes.extend(
                await asyncio.gather(
                    *(
                        self._instrument_facts(
                            instrument_id,
                            as_of,
                            lookback_sessions,
                            benchmark_series,
                        )
                        for instrument_id in batch
                    )
                )
            )
            if offset + _FACT_BATCH_SIZE < len(instrument_ids):
                await asyncio.sleep(_FACT_BATCH_INTERVAL_SECONDS)
        return tuple(outcomes)

    async def _instrument_facts(
        self,
        instrument_id: str,
        as_of: datetime,
        lookback_sessions: int,
        benchmark_series: dict[Market, tuple[dict[date, Decimal], tuple[SourceReference, ...]]],
    ) -> tuple[PortfolioClassification | None, PortfolioRiskMetric, tuple[SourceReference, ...]]:
        _, market, _ = parse_instrument_id(instrument_id)
        closes, bar_sources = await self._bars(instrument_id, as_of, lookback_sessions)
        benchmark_id = self._benchmark(market)
        benchmark_closes, benchmark_sources = benchmark_series.get(market, ({}, ()))
        metric = self._risk.calculate(
            instrument_id=instrument_id,
            benchmark_instrument_id=benchmark_id,
            instrument_closes=closes,
            benchmark_closes=benchmark_closes,
        )
        classification, classification_sources = await self._classification(
            instrument_id, market, as_of
        )
        return (
            classification,
            metric,
            bar_sources + benchmark_sources + classification_sources,
        )

    async def _classification(
        self, instrument_id: str, market: Market, as_of: datetime
    ) -> tuple[PortfolioClassification | None, tuple[SourceReference, ...]]:
        asset_type, _, _ = parse_instrument_id(instrument_id)
        themes: tuple[str, ...] = ()
        context = self._context.build(
            ResearchContextBuildInput(instrument_id=instrument_id, token_budget=2_000)
        )
        if context.ok and context.data is not None:
            themes = context.data.case.topic_tags
        industry: str | None = None
        sources: tuple[SourceReference, ...] = ()
        if market is Market.US and asset_type is AssetType.EQUITY:
            us_result = await self._us_research.get_fundamental_snapshot(
                FundamentalGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
            )
            if (
                us_result.ok
                and us_result.data is not None
                and us_result.data.profile is not None
            ):
                industry = us_result.data.profile.industry or us_result.data.profile.sector
                sources = us_result.sources
        elif market is Market.A_SHARE:
            a_result = await self._a_share.get_snapshot(
                AShareGetSnapshotInput(
                    instrument_id=instrument_id,
                    detail=AShareSnapshotDetail.FULL,
                    as_of=as_of,
                )
            )
            if a_result.ok and a_result.data is not None:
                for item in a_result.data.fundamentals:
                    if item.name.strip().lower() in {"industry", "sector", "所属行业"}:
                        industry = str(item.value) if item.value is not None else None
                        break
                sources = a_result.sources
        if industry is None and not themes:
            return None, sources
        return PortfolioClassification(instrument_id, industry, themes), sources

    async def _bars(
        self, instrument_id: str, as_of: datetime, lookback_sessions: int
    ) -> tuple[dict[date, Decimal], tuple[SourceReference, ...]]:
        _, market, _ = parse_instrument_id(instrument_id)
        start = (as_of - timedelta(days=max(60, lookback_sessions * 2))).date()
        if market is Market.US:
            us_result = await self._us_market.get_market_bars(
                MarketGetBarsInput(
                    instrument_id=instrument_id,
                    start=start,
                    end=as_of.date(),
                    as_of=as_of,
                )
            )
            if not us_result.ok or us_result.data is None:
                return {}, us_result.sources
            return (
                {item.timestamp.date(): item.close for item in us_result.data.bars},
                us_result.sources,
            )
        if market is Market.A_SHARE:
            a_result = await self._a_share.get_market_structure(
                AShareGetMarketStructureInput(
                    scope=AShareMarketScope.INSTRUMENT,
                    instrument_id=instrument_id,
                    start=start,
                    end=as_of.date(),
                    include_bars=True,
                    include_order_book=False,
                    as_of=as_of,
                )
            )
            if not a_result.ok or a_result.data is None:
                return {}, a_result.sources
            return (
                {item.end_at.date(): item.close for item in a_result.data.bars},
                a_result.sources,
            )
        return {}, ()

    @staticmethod
    def _benchmark(market: Market) -> str:
        if market is Market.A_SHARE:
            return "index:A_SHARE:000300.SH"
        return "etf:US:SPY"

    @staticmethod
    def _dedupe_sources(values: tuple[SourceReference, ...]) -> tuple[SourceReference, ...]:
        seen: set[tuple[str, str | None]] = set()
        result: list[SourceReference] = []
        for item in values:
            key = (item.name, item.url)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return tuple(result)
