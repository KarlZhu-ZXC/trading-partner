"""A-share CategoryProvider protocol surfaces (Phase 1E §16 / §18.5).

All protocols are ``@runtime_checkable`` and extend ``CategoryProvider``.
Router callbacks must narrow with ``isinstance`` — no getattr/reflection.

Capital metrics use eight fine-grained protocols (one method each). There is
**no** fat runtime ``AShareCapitalProvider``; §16's grouped capital listing is
conceptual only (§18.5).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.a_share.enums import (
    BarInterval,
    FinancialStatementType,
    LimitPoolType,
    SentimentSourceType,
)
from domain.a_share.models import (
    AnalystReportItem,
    AnnouncementItem,
    AShareBar,
    AShareQuote,
    BlockTradeRecord,
    ChipDistributionSnapshot,
    CompanyOperatingMetricsSnapshot,
    ConsensusEstimate,
    DividendRecord,
    DragonTigerRecord,
    EtfOptionSnapshot,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    FundFlowPoint,
    IndustryCycleSnapshot,
    IndustryPerformanceRow,
    InteractiveQAItem,
    LimitUpContext,
    MarginRecord,
    MarketBoardSnapshot,
    NewsItem,
    NorthboundFlowPoint,
    OrderBookLevel,
    SentimentSignal,
    ShareholderCountRecord,
    TradeTick,
    UnlockRecord,
)
from domain.common.enums import AdjustmentMethod
from domain.instruments.models import Instrument


@runtime_checkable
class AShareQuoteProvider(CategoryProvider, Protocol):
    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[AShareQuote]: ...


@runtime_checkable
class AShareIndustryCycleProvider(CategoryProvider, Protocol):
    async def get_hog_cycle(
        self, *, lookback_months: int, as_of: datetime
    ) -> ProviderSuccess[IndustryCycleSnapshot]: ...


@runtime_checkable
class AShareCompanyOperatingMetricsProvider(CategoryProvider, Protocol):
    async def get_company_operating_metrics(
        self,
        instrument: Instrument,
        *,
        lookback_months: int,
        document_limit: int,
        metric_codes: tuple[str, ...],
        as_of: datetime,
    ) -> ProviderSuccess[CompanyOperatingMetricsSnapshot]: ...


@runtime_checkable
class AShareOhlcvProvider(CategoryProvider, Protocol):
    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: BarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AShareBar, ...]]: ...


@runtime_checkable
class AShareMarketStructureProvider(CategoryProvider, Protocol):
    async def get_order_book(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[OrderBookLevel, ...]]: ...

    async def get_ticks(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[TradeTick, ...]]: ...

    async def get_industry_performance(
        self, *, trade_date: date, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[IndustryPerformanceRow, ...]]: ...

    async def get_market_board(
        self, *, trade_date: date, as_of: datetime
    ) -> ProviderSuccess[MarketBoardSnapshot]: ...


@runtime_checkable
class AShareFundamentalsProvider(CategoryProvider, Protocol):
    async def get_fundamentals(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundamentalMetric, ...]]: ...

    async def get_f10_sections(
        self,
        instrument: Instrument,
        *,
        sections: tuple[str, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[F10Section, ...]]: ...


@runtime_checkable
class AShareFinancialStatementsProvider(CategoryProvider, Protocol):
    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        statement_types: tuple[FinancialStatementType, ...],
        periods: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]: ...


@runtime_checkable
class AShareResearchProvider(CategoryProvider, Protocol):
    async def search_reports(
        self,
        *,
        text: str | None,
        instrument: Instrument | None,
        industry_code: str | None,
        published_from: date | None,
        published_to: date | None,
        limit: int,
        offset: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AnalystReportItem, ...]]: ...

    async def get_consensus(
        self, instrument: Instrument, *, as_of: datetime
    ) -> ProviderSuccess[tuple[ConsensusEstimate, ...]]: ...


@runtime_checkable
class AShareDisclosureProvider(CategoryProvider, Protocol):
    async def get_announcements(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[AnnouncementItem, ...]]: ...

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]: ...


@runtime_checkable
class AShareNewsProvider(CategoryProvider, Protocol):
    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[NewsItem, ...]]: ...


@runtime_checkable
class AShareInteractiveProvider(CategoryProvider, Protocol):
    async def get_interactive_qa(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[InteractiveQAItem, ...]]: ...


# --- Fine-grained capital protocols (§18.5); no fat capital protocol ---


@runtime_checkable
class AShareIntradayFlowProvider(CategoryProvider, Protocol):
    async def get_intraday_flow(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]: ...


@runtime_checkable
class AShareDailyFlowProvider(CategoryProvider, Protocol):
    async def get_daily_flow(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]: ...


@runtime_checkable
class AShareNorthboundProvider(CategoryProvider, Protocol):
    async def get_northbound(
        self, *, start: date | None, end: date | None, as_of: datetime
    ) -> ProviderSuccess[tuple[NorthboundFlowPoint, ...]]: ...


@runtime_checkable
class AShareDragonTigerProvider(CategoryProvider, Protocol):
    async def get_dragon_tiger(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[DragonTigerRecord, ...]]: ...


@runtime_checkable
class AShareMarginProvider(CategoryProvider, Protocol):
    async def get_margin(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[MarginRecord, ...]]: ...


@runtime_checkable
class AShareBlockTradeProvider(CategoryProvider, Protocol):
    async def get_block_trades(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[BlockTradeRecord, ...]]: ...


@runtime_checkable
class AShareShareholderProvider(CategoryProvider, Protocol):
    async def get_shareholder_counts(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[ShareholderCountRecord, ...]]: ...


@runtime_checkable
class AShareChipProvider(CategoryProvider, Protocol):
    async def get_chip_distribution(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[ChipDistributionSnapshot]: ...


@runtime_checkable
class AShareLimitUpProvider(CategoryProvider, Protocol):
    async def get_limit_pools(
        self,
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[LimitUpContext]: ...


@runtime_checkable
class AShareSentimentProvider(CategoryProvider, Protocol):
    async def get_sentiment_signals(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        sources: tuple[SentimentSourceType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[SentimentSignal, ...]]: ...


@runtime_checkable
class AShareOptionProvider(CategoryProvider, Protocol):
    async def get_option_snapshot(
        self,
        underlying: Instrument,
        *,
        expiry: date | None,
        strike_center: Decimal | None,
        strike_count_each_side: int,
        as_of: datetime,
    ) -> ProviderSuccess[EtfOptionSnapshot]: ...


# Explicit inventory for architecture / completeness tests (order frozen).
A_SHARE_RUNTIME_PROTOCOLS: tuple[type, ...] = (
    AShareQuoteProvider,
    AShareOhlcvProvider,
    AShareMarketStructureProvider,
    AShareFundamentalsProvider,
    AShareFinancialStatementsProvider,
    AShareResearchProvider,
    AShareDisclosureProvider,
    AShareNewsProvider,
    AShareInteractiveProvider,
    AShareIntradayFlowProvider,
    AShareDailyFlowProvider,
    AShareNorthboundProvider,
    AShareDragonTigerProvider,
    AShareMarginProvider,
    AShareBlockTradeProvider,
    AShareShareholderProvider,
    AShareChipProvider,
    AShareLimitUpProvider,
    AShareSentimentProvider,
    AShareOptionProvider,
)

A_SHARE_CAPITAL_METRIC_PROTOCOLS: tuple[type, ...] = (
    AShareIntradayFlowProvider,
    AShareDailyFlowProvider,
    AShareNorthboundProvider,
    AShareDragonTigerProvider,
    AShareMarginProvider,
    AShareBlockTradeProvider,
    AShareShareholderProvider,
    AShareChipProvider,
)
