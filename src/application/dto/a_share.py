"""Closed A-share Pydantic DTOs (Phase 1E E1).

Input models implement §8 MCP schema validation (including §19.1 scope matrix).
Output DTOs mirror frozen domain models with ``extra=forbid`` and Decimal wire
serialization. No open Mapping fields.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.a_share_provenance import AShareComponentProvenanceDTO
from application.dto.financial_quality import (
    FinancialQualityMetricDTO,
    derive_financial_quality_metrics,
)
from application.dto.market import DecimalWire
from domain.a_share.enums import (
    AShareComponentType,
    AShareMarketScope,
    AShareSnapshotDetail,
    BarInterval,
    CapitalMetricType,
    CompanyDocumentParseStatus,
    CompanyDocumentType,
    FinancialStatementType,
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
    LimitPoolType,
    OptionType,
    SentimentSourceType,
    TickDirection,
)
from domain.a_share.models import (
    AnalystReportItem,
    AnnouncementItem,
    AShareBar,
    AShareQuote,
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    CompanyOperatingMetricObservation,
    CompanyOperatingMetricsSnapshot,
    ConsensusEstimate,
    DividendRecord,
    DocumentParseReceipt,
    DragonTigerRecord,
    DragonTigerSeat,
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    FundFlowPoint,
    IndustryCycleSnapshot,
    IndustryMetricObservation,
    IndustryPerformanceRow,
    InteractiveQAItem,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    MarginRecord,
    MarketBoardSnapshot,
    NewsItem,
    NorthboundFlowPoint,
    OptionGreeks,
    OrderBookLevel,
    SentimentSignal,
    ShareholderCountRecord,
    TradeTick,
    UnlockRecord,
)
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    Market,
    ReliabilityLevel,
    TradingSession,
    VendorId,
)
from domain.common.errors import TradingPartnerError
from domain.common.values import parse_instrument_id

_DATE_WIRE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_exact_date_wire(value: object) -> object:
    if value is None:
        return value
    if isinstance(value, datetime):
        raise ValueError("must be an exact date, not datetime")
    if isinstance(value, str) and not _DATE_WIRE_RE.fullmatch(value):
        raise ValueError("must use YYYY-MM-DD date format")
    if not isinstance(value, (date, str)):
        raise ValueError("must be an exact date")
    return value


# Statically decidable asset matrix for MCP inputs (design §19).
# Repository existence remains a service responsibility.
_SNAPSHOT_STRUCTURE_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_EQUITY_ONLY = frozenset({AssetType.EQUITY})
_CAPITAL_INSTRUMENT_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF})
_SENTIMENT_INSTRUMENT_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_REPORT_INSTRUMENT_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_OPTION_UNDERLYING_ASSETS = frozenset({AssetType.ETF})


class _FrozenForbid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_a_share_instrument_id(
    value: str,
    *,
    allowed_assets: frozenset[AssetType],
    field_name: str = "instrument_id",
) -> str:
    """Reject non-A_SHARE and asset types outside the frozen tool matrix."""
    try:
        asset_type, market, _symbol = parse_instrument_id(value)
    except TradingPartnerError:
        raise ValueError("invalid instrument_id syntax") from None
    if market is not Market.A_SHARE:
        raise ValueError(f"{field_name} must use Market.A_SHARE")
    if asset_type not in allowed_assets:
        allowed = ", ".join(sorted(a.value for a in allowed_assets))
        raise ValueError(f"{field_name} asset type must be one of [{allowed}] for this tool")
    return value


# ---------------------------------------------------------------------------
# §8 MCP input models
# ---------------------------------------------------------------------------


class AShareGetSnapshotInput(_FrozenForbid):
    instrument_id: str
    as_of: datetime | None = None
    detail: AShareSnapshotDetail = AShareSnapshotDetail.SUMMARY

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_a_share_instrument_id(value, allowed_assets=_SNAPSHOT_STRUCTURE_ASSETS)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value


A_SHARE_DEFAULT_FINANCIAL_METRICS: tuple[str, ...] = (
    "cash_and_equivalents",
    "short_term_investments",
    "accounts_receivable",
    "inventory",
    "current_assets",
    "total_assets",
    "short_term_debt",
    "current_portion_long_term_debt",
    "current_liabilities",
    "long_term_debt",
    "bonds_payable",
    "total_liabilities",
    "stockholders_equity",
    "total_revenue",
    "revenue",
    "cost_of_revenue",
    "research_and_development",
    "selling_expense",
    "general_and_administrative_expense",
    "finance_expense",
    "operating_income",
    "net_income",
    "net_income_attributable_parent",
    "eps_basic",
    "eps_diluted",
    "operating_cash_flow",
    "capital_expenditure",
    "investing_cash_flow",
    "financing_cash_flow",
    "cash_change",
)
_FINANCIAL_METRIC_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AShareGetFinancialStatementsInput(_FrozenForbid):
    instrument_id: str
    statement_types: tuple[FinancialStatementType, ...] = tuple(FinancialStatementType)
    periods: int = Field(default=8, ge=1, le=20)
    metric_codes: tuple[str, ...] = Field(default=(), max_length=30)
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_a_share_instrument_id(value, allowed_assets=_EQUITY_ONLY)

    @field_validator("statement_types")
    @classmethod
    def _statement_types(
        cls, value: tuple[FinancialStatementType, ...]
    ) -> tuple[FinancialStatementType, ...]:
        if not value:
            raise ValueError("statement_types must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("statement_types must be unique")
        return value

    @field_validator("metric_codes")
    @classmethod
    def _metric_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metric_codes must be unique")
        if any(_FINANCIAL_METRIC_CODE_RE.fullmatch(code) is None for code in value):
            raise ValueError("metric_codes must use lower_snake_case")
        return value

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value


_INDUSTRY_METRIC_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class AShareGetIndustryCycleInput(_FrozenForbid):
    """Industry-cycle facts input.

    ``lookback_months`` controls provider/repository history depth. MCP output is
    always bounded by ``view`` / ``metric_codes`` / ``offset`` / ``limit`` so a
    240-month request never dumps the full raw series into one response.
    """

    cycle: Literal["hog"] = "hog"
    lookback_months: int = Field(default=12, ge=3, le=240)
    view: Literal["compact", "series"] = "compact"
    metric_codes: tuple[str, ...] = Field(default=(), max_length=100)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("metric_codes")
    @classmethod
    def _metric_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metric_codes must not contain duplicates")
        for code in value:
            if _INDUSTRY_METRIC_CODE_RE.fullmatch(code) is None:
                raise ValueError("metric_codes must use lower_snake_case")
        return value


class AShareGetCompanyOperatingMetricsInput(_FrozenForbid):
    """Company operating metrics from official disclosures (not industry-cycle national series)."""

    instrument_id: str
    lookback_months: int = Field(default=12, ge=3, le=120)
    document_limit: int = Field(default=10, ge=1, le=30)
    metric_codes: tuple[str, ...] = Field(default=(), max_length=100)
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_a_share_instrument_id(value, allowed_assets=_EQUITY_ONLY)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("metric_codes")
    @classmethod
    def _metric_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metric_codes must not contain duplicates")
        for code in value:
            if _INDUSTRY_METRIC_CODE_RE.fullmatch(code) is None:
                raise ValueError("metric_codes must use lower_snake_case")
        return value


class AShareGetMarketStructureInput(_FrozenForbid):
    scope: AShareMarketScope = AShareMarketScope.INSTRUMENT
    instrument_id: str | None = None
    trade_date: date | None = None
    start: date | None = None
    end: date | None = None
    interval: BarInterval = BarInterval.ONE_DAY
    adjustment: AdjustmentMethod = AdjustmentMethod.FORWARD_ADJUSTED
    include_bars: bool | None = None
    include_order_book: bool | None = None
    include_ticks: bool = False
    include_industries: bool | None = None
    include_market_board: bool | None = None
    industry_limit: int = Field(default=20, ge=1, le=100)
    tick_limit: int = Field(default=100, ge=1, le=1000)
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _optional_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_a_share_instrument_id(value, allowed_assets=_SNAPSHOT_STRUCTURE_ASSETS)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("trade_date", "start", "end", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @model_validator(mode="after")
    def _scope_matrix(self) -> Self:
        scope = self.scope

        # Apply scope defaults for unset include_* flags (§19.1).
        if scope is AShareMarketScope.INSTRUMENT:
            include_bars = True if self.include_bars is None else self.include_bars
            include_order_book = (
                True if self.include_order_book is None else self.include_order_book
            )
            include_industries = (
                False if self.include_industries is None else self.include_industries
            )
            include_market_board = (
                False if self.include_market_board is None else self.include_market_board
            )
            if self.instrument_id is None:
                raise ValueError("instrument_id is required for instrument scope")
            if include_bars and (self.start is None or self.end is None):
                raise ValueError("start and end are required when include_bars=True")
            if self.start is not None and self.end is not None and self.end < self.start:
                raise ValueError("end must be >= start")
            # Bars/book/ticks allowed; market board/industries only if explicit.
            object.__setattr__(self, "include_bars", include_bars)
            object.__setattr__(self, "include_order_book", include_order_book)
            object.__setattr__(self, "include_industries", include_industries)
            object.__setattr__(self, "include_market_board", include_market_board)
            if not any(
                (
                    include_bars,
                    include_order_book,
                    self.include_ticks,
                    include_industries,
                    include_market_board,
                )
            ):
                raise ValueError("at least one structure component is required")
            return self

        # industry / market scopes
        if self.instrument_id is not None:
            raise ValueError("instrument_id is forbidden for industry/market scope")
        include_bars = False if self.include_bars is None else self.include_bars
        include_order_book = False if self.include_order_book is None else self.include_order_book
        include_ticks = self.include_ticks
        if include_bars or include_order_book or include_ticks:
            raise ValueError("bars/order_book/ticks are forbidden for industry/market scope")

        if scope is AShareMarketScope.INDUSTRY:
            include_industries = (
                True if self.include_industries is None else self.include_industries
            )
            include_market_board = (
                False if self.include_market_board is None else self.include_market_board
            )
            if not include_industries:
                raise ValueError(
                    "include_industries cannot be false for industry scope "
                    "when conflicting with defaults"
                )
        else:  # MARKET
            include_market_board = (
                True if self.include_market_board is None else self.include_market_board
            )
            include_industries = (
                False if self.include_industries is None else self.include_industries
            )
            if not include_market_board:
                raise ValueError(
                    "include_market_board cannot be false for market scope "
                    "when conflicting with defaults"
                )

        object.__setattr__(self, "include_bars", include_bars)
        object.__setattr__(self, "include_order_book", include_order_book)
        object.__setattr__(self, "include_industries", include_industries)
        object.__setattr__(self, "include_market_board", include_market_board)
        return self


class AShareGetCapitalSnapshotInput(_FrozenForbid):
    instrument_id: str | None = None
    metrics: tuple[CapitalMetricType, ...] = ()
    start: date | None = None
    end: date | None = None
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _optional_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_a_share_instrument_id(value, allowed_assets=_CAPITAL_INSTRUMENT_ASSETS)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("start", "end", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("metrics")
    @classmethod
    def _unique_metrics(cls, value: tuple[CapitalMetricType, ...]) -> tuple[CapitalMetricType, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metrics must not contain duplicates")
        return value

    @model_validator(mode="after")
    def _instrument_rules(self) -> Self:
        metrics = self.metrics
        northbound_only = metrics == (CapitalMetricType.NORTHBOUND,)
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must be >= start")
        if northbound_only:
            return self
        if self.instrument_id is None:
            raise ValueError("instrument_id is required unless metrics is exactly (northbound,)")
        return self


class AShareGetLimitUpContextInput(_FrozenForbid):
    trade_date: date
    pools: tuple[LimitPoolType, ...] = ()
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("trade_date", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("pools")
    @classmethod
    def _unique_pools(cls, value: tuple[LimitPoolType, ...]) -> tuple[LimitPoolType, ...]:
        if len(set(value)) != len(value):
            raise ValueError("pools must not contain duplicates")
        return value


class AShareGetSentimentSnapshotInput(_FrozenForbid):
    instrument_id: str | None = None
    sources: tuple[SentimentSourceType, ...] = ()
    trade_date: date | None = None
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _optional_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_a_share_instrument_id(value, allowed_assets=_SENTIMENT_INSTRUMENT_ASSETS)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("trade_date", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("sources")
    @classmethod
    def _unique_sources(
        cls, value: tuple[SentimentSourceType, ...]
    ) -> tuple[SentimentSourceType, ...]:
        if len(set(value)) != len(value):
            raise ValueError("sources must not contain duplicates")
        return value


class AShareGetEtfOptionSnapshotInput(_FrozenForbid):
    underlying_instrument_id: str
    expiry: date | None = None
    strike_center: DecimalWire | None = None
    strike_count_each_side: int = Field(default=5, ge=0, le=20)
    as_of: datetime | None = None

    @field_validator("underlying_instrument_id")
    @classmethod
    def _underlying(cls, value: str) -> str:
        return _validate_a_share_instrument_id(
            value,
            allowed_assets=_OPTION_UNDERLYING_ASSETS,
            field_name="underlying_instrument_id",
        )

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("expiry", mode="before")
    @classmethod
    def _exact_expiry(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("strike_center")
    @classmethod
    def _positive_finite_strike(cls, value: DecimalWire | None) -> DecimalWire | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("strike_center must be finite and > 0")
        return value


class ResearchSearchReportsInput(_FrozenForbid):
    text: str | None = Field(default=None, max_length=500)
    instrument_id: str | None = None
    industry_code: str | None = Field(default=None, max_length=64)
    published_from: date | None = None
    published_to: date | None = None
    include_consensus: bool = True
    as_of: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("instrument_id")
    @classmethod
    def _optional_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_a_share_instrument_id(value, allowed_assets=_REPORT_INSTRUMENT_ASSETS)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @field_validator("published_from", "published_to", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("text", "industry_code", mode="before")
    @classmethod
    def _strip_optional_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _at_least_one_filter(self) -> Self:
        text_ok = self.text is not None and bool(self.text.strip())
        if not text_ok and self.instrument_id is None and self.industry_code is None:
            raise ValueError("at least one of text, instrument_id, or industry_code is required")
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_to < self.published_from
        ):
            raise ValueError("published_to must be >= published_from")
        return self


# ---------------------------------------------------------------------------
# Domain-mirroring output DTOs
# ---------------------------------------------------------------------------


class AShareQuoteDTO(_FrozenForbid):
    instrument_id: str
    quote_at: datetime
    session: TradingSession
    last: DecimalWire
    open: DecimalWire | None
    high: DecimalWire | None
    low: DecimalWire | None
    previous_close: DecimalWire | None
    change: DecimalWire | None
    change_percent: DecimalWire | None
    volume_shares: int | None
    turnover_amount_cny: DecimalWire | None
    turnover_rate: DecimalWire | None
    pe_ttm: DecimalWire | None
    pb: DecimalWire | None
    total_market_cap_cny: DecimalWire | None
    float_market_cap_cny: DecimalWire | None
    limit_up_price: DecimalWire | None
    limit_down_price: DecimalWire | None

    @classmethod
    def from_domain(cls, quote: AShareQuote) -> AShareQuoteDTO:
        return cls.model_validate(quote, from_attributes=True)


class OrderBookLevelDTO(_FrozenForbid):
    level: int
    bid_price: DecimalWire | None
    bid_volume_shares: int | None
    ask_price: DecimalWire | None
    ask_volume_shares: int | None

    @classmethod
    def from_domain(cls, level: OrderBookLevel) -> OrderBookLevelDTO:
        return cls.model_validate(level, from_attributes=True)


class TradeTickDTO(_FrozenForbid):
    occurred_at: datetime
    price: DecimalWire
    volume_shares: int
    direction: TickDirection

    @classmethod
    def from_domain(cls, tick: TradeTick) -> TradeTickDTO:
        return cls.model_validate(tick, from_attributes=True)


class AShareBarDTO(_FrozenForbid):
    start_at: datetime
    end_at: datetime
    interval: BarInterval
    open: DecimalWire
    high: DecimalWire
    low: DecimalWire
    close: DecimalWire
    volume_shares: int
    turnover_amount_cny: DecimalWire | None
    adjustment: AdjustmentMethod

    @classmethod
    def from_domain(cls, bar: AShareBar) -> AShareBarDTO:
        return cls.model_validate(bar, from_attributes=True)


class FundamentalMetricDTO(_FrozenForbid):
    name: str
    value: DecimalWire | str | int | None
    unit: str | None
    period_end: date | None
    published_at: datetime | None

    @classmethod
    def from_domain(cls, metric: FundamentalMetric) -> FundamentalMetricDTO:
        return cls.model_validate(metric, from_attributes=True)


class FinancialStatementLineDTO(_FrozenForbid):
    statement_type: FinancialStatementType
    period_end: date
    published_at: datetime | None
    item_code: str
    item_name: str
    value: DecimalWire | None
    unit: str

    @classmethod
    def from_domain(cls, line: FinancialStatementLine) -> FinancialStatementLineDTO:
        return cls.model_validate(line, from_attributes=True)


class AShareFinancialMetricDTO(_FrozenForbid):
    statement_type: FinancialStatementType
    metric_code: str
    item_name: str
    value: DecimalWire | None
    unit: str
    published_at: datetime | None


class AShareFinancialPeriodDTO(_FrozenForbid):
    period_end: date
    basis: Literal["q1_ytd", "h1_ytd", "nine_month_ytd", "annual", "reported"]
    metrics: tuple[AShareFinancialMetricDTO, ...]


class AShareFinancialStatementsDTO(_FrozenForbid):
    """Bounded, canonical A-share statement facts plus deterministic ratios."""

    instrument_id: str
    as_of: datetime
    requested_periods: int
    metric_codes: tuple[str, ...]
    periods: tuple[AShareFinancialPeriodDTO, ...]
    quality_metrics: tuple[FinancialQualityMetricDTO, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_lines(
        cls,
        *,
        instrument_id: str,
        as_of: datetime,
        requested_periods: int,
        metric_codes: tuple[str, ...],
        lines: tuple[FinancialStatementLine, ...],
        provenance: tuple[AShareComponentProvenanceDTO, ...],
    ) -> AShareFinancialStatementsDTO:
        selected_codes = metric_codes or A_SHARE_DEFAULT_FINANCIAL_METRICS
        allowed = frozenset(selected_codes)
        grouped: dict[date, list[FinancialStatementLine]] = {}
        all_by_period: dict[date, dict[str, Decimal | None]] = {}
        for line in lines:
            all_by_period.setdefault(line.period_end, {})[line.item_code] = line.value
            if line.item_code in allowed:
                grouped.setdefault(line.period_end, []).append(line)

        periods: list[AShareFinancialPeriodDTO] = []
        quality: list[FinancialQualityMetricDTO] = []
        for period_end in sorted(all_by_period, reverse=True)[:requested_periods]:
            selected = grouped.get(period_end, [])
            selected.sort(key=lambda line: (line.statement_type.value, line.item_code))
            periods.append(
                AShareFinancialPeriodDTO(
                    period_end=period_end,
                    basis=_a_share_statement_basis(period_end),
                    metrics=tuple(
                        AShareFinancialMetricDTO(
                            statement_type=line.statement_type,
                            metric_code=line.item_code,
                            item_name=line.item_name,
                            value=line.value,
                            unit=line.unit,
                            published_at=line.published_at,
                        )
                        for line in selected
                    ),
                )
            )
            quality.extend(
                derive_financial_quality_metrics(
                    period_end=period_end,
                    line_items=all_by_period[period_end],
                    currency="CNY",
                )
            )
        return cls(
            instrument_id=instrument_id,
            as_of=as_of,
            requested_periods=requested_periods,
            metric_codes=selected_codes,
            periods=tuple(periods),
            quality_metrics=tuple(quality),
            provenance=provenance,
        )


def _a_share_statement_basis(
    period_end: date,
) -> Literal["q1_ytd", "h1_ytd", "nine_month_ytd", "annual", "reported"]:
    if (period_end.month, period_end.day) == (3, 31):
        return "q1_ytd"
    if (period_end.month, period_end.day) == (6, 30):
        return "h1_ytd"
    if (period_end.month, period_end.day) == (9, 30):
        return "nine_month_ytd"
    if (period_end.month, period_end.day) == (12, 31):
        return "annual"
    return "reported"


class F10SectionDTO(_FrozenForbid):
    section: str
    title: str
    body: str
    as_of: datetime

    @classmethod
    def from_domain(cls, section: F10Section) -> F10SectionDTO:
        return cls.model_validate(section, from_attributes=True)


class ConsensusEstimateDTO(_FrozenForbid):
    fiscal_year: int
    metric: str
    mean: DecimalWire | None
    high: DecimalWire | None
    low: DecimalWire | None
    institution_count: int | None

    @classmethod
    def from_domain(cls, estimate: ConsensusEstimate) -> ConsensusEstimateDTO:
        return cls.model_validate(estimate, from_attributes=True)


class AnalystReportItemDTO(_FrozenForbid):
    report_key: str
    title: str
    institution: str | None
    analyst_names: tuple[str, ...]
    published_at: datetime
    rating: str | None
    target_price: DecimalWire | None
    eps_forecasts: tuple[ConsensusEstimateDTO, ...]
    source_url: str | None
    pdf_url: str | None

    @classmethod
    def from_domain(cls, item: AnalystReportItem) -> AnalystReportItemDTO:
        return cls(
            report_key=item.report_key,
            title=item.title,
            institution=item.institution,
            analyst_names=item.analyst_names,
            published_at=item.published_at,
            rating=item.rating,
            target_price=item.target_price,
            eps_forecasts=tuple(ConsensusEstimateDTO.from_domain(e) for e in item.eps_forecasts),
            source_url=item.source_url,
            pdf_url=item.pdf_url,
        )


class AnnouncementItemDTO(_FrozenForbid):
    announcement_key: str
    title: str
    published_at: datetime
    category: str | None
    source_url: str
    pdf_url: str | None

    @classmethod
    def from_domain(cls, item: AnnouncementItem) -> AnnouncementItemDTO:
        return cls.model_validate(item, from_attributes=True)


class NewsItemDTO(_FrozenForbid):
    news_key: str
    title: str
    summary: str | None
    published_at: datetime
    source_name: str
    source_url: str | None

    @classmethod
    def from_domain(cls, item: NewsItem) -> NewsItemDTO:
        return cls.model_validate(item, from_attributes=True)


class InteractiveQAItemDTO(_FrozenForbid):
    qa_key: str
    question: str
    asked_at: datetime | None
    answer: str
    answered_at: datetime
    source_url: str | None

    @classmethod
    def from_domain(cls, item: InteractiveQAItem) -> InteractiveQAItemDTO:
        return cls.model_validate(item, from_attributes=True)


class IndustryPerformanceRowDTO(_FrozenForbid):
    industry_code: str
    industry_name: str
    trade_date: date
    change_percent: DecimalWire
    advancing_count: int
    declining_count: int
    unchanged_count: int
    leading_instrument_id: str | None
    leading_change_percent: DecimalWire | None
    turnover_amount_cny: DecimalWire | None

    @classmethod
    def from_domain(cls, row: IndustryPerformanceRow) -> IndustryPerformanceRowDTO:
        return cls.model_validate(row, from_attributes=True)


class MarketBoardSnapshotDTO(_FrozenForbid):
    trade_date: date
    advancing_count: int
    declining_count: int
    unchanged_count: int
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    total_turnover_cny: DecimalWire | None
    median_change_percent: DecimalWire | None
    industries: tuple[IndustryPerformanceRowDTO, ...]

    @classmethod
    def from_domain(cls, board: MarketBoardSnapshot) -> MarketBoardSnapshotDTO:
        return cls(
            trade_date=board.trade_date,
            advancing_count=board.advancing_count,
            declining_count=board.declining_count,
            unchanged_count=board.unchanged_count,
            limit_up_count=board.limit_up_count,
            limit_down_count=board.limit_down_count,
            broken_limit_count=board.broken_limit_count,
            total_turnover_cny=board.total_turnover_cny,
            median_change_percent=board.median_change_percent,
            industries=tuple(IndustryPerformanceRowDTO.from_domain(r) for r in board.industries),
        )


class FundFlowPointDTO(_FrozenForbid):
    occurred_at: datetime
    interval: BarInterval
    main_net_cny: DecimalWire | None
    super_large_net_cny: DecimalWire | None
    large_net_cny: DecimalWire | None
    medium_net_cny: DecimalWire | None
    small_net_cny: DecimalWire | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, point: FundFlowPoint) -> FundFlowPointDTO:
        return cls.model_validate(point, from_attributes=True)


class NorthboundFlowPointDTO(_FrozenForbid):
    trade_date: date
    channel: str
    net_buy_cny: DecimalWire | None
    buy_cny: DecimalWire | None
    sell_cny: DecimalWire | None
    disclosure_note: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, point: NorthboundFlowPoint) -> NorthboundFlowPointDTO:
        return cls.model_validate(point, from_attributes=True)


class DragonTigerSeatDTO(_FrozenForbid):
    rank: int
    side: str
    branch_name: str
    amount_cny: DecimalWire
    is_institution: bool | None

    @classmethod
    def from_domain(cls, seat: DragonTigerSeat) -> DragonTigerSeatDTO:
        return cls.model_validate(seat, from_attributes=True)


class DragonTigerRecordDTO(_FrozenForbid):
    trade_date: date
    instrument_id: str
    reason: str
    buy_total_cny: DecimalWire
    sell_total_cny: DecimalWire
    net_buy_cny: DecimalWire
    seats: tuple[DragonTigerSeatDTO, ...]
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: DragonTigerRecord) -> DragonTigerRecordDTO:
        return cls(
            trade_date=record.trade_date,
            instrument_id=record.instrument_id,
            reason=record.reason,
            buy_total_cny=record.buy_total_cny,
            sell_total_cny=record.sell_total_cny,
            net_buy_cny=record.net_buy_cny,
            seats=tuple(DragonTigerSeatDTO.from_domain(s) for s in record.seats),
            source_vendor=record.source_vendor,
            reliability=record.reliability,
            is_authoritative=record.is_authoritative,
        )


class MarginRecordDTO(_FrozenForbid):
    trade_date: date
    financing_balance_cny: DecimalWire
    financing_buy_cny: DecimalWire
    financing_repayment_cny: DecimalWire
    securities_lending_balance_cny: DecimalWire | None
    securities_lending_sell_shares: int | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: MarginRecord) -> MarginRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class BlockTradeRecordDTO(_FrozenForbid):
    trade_date: date
    price: DecimalWire
    volume_shares: int
    amount_cny: DecimalWire
    premium_percent: DecimalWire | None
    buyer_branch: str | None
    seller_branch: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: BlockTradeRecord) -> BlockTradeRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class ShareholderCountRecordDTO(_FrozenForbid):
    period_end: date
    published_at: datetime | None
    shareholder_count: int
    change_percent: DecimalWire | None
    average_holding_shares: DecimalWire | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: ShareholderCountRecord) -> ShareholderCountRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class ChipDistributionBinDTO(_FrozenForbid):
    price_low: DecimalWire
    price_high: DecimalWire
    holding_ratio: DecimalWire

    @classmethod
    def from_domain(cls, bin_row: ChipDistributionBin) -> ChipDistributionBinDTO:
        return cls.model_validate(bin_row, from_attributes=True)


class ChipDistributionSnapshotDTO(_FrozenForbid):
    as_of: datetime
    bins: tuple[ChipDistributionBinDTO, ...]
    profit_ratio: DecimalWire | None
    average_cost: DecimalWire | None
    concentration_90: DecimalWire | None = Field(
        description=(
            "Relative 90% cost-band width; lower values mean holdings are more concentrated."
        )
    )
    concentration_70: DecimalWire | None = Field(
        description=(
            "Relative 70% cost-band width; lower values mean holdings are more concentrated."
        )
    )
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool
    calculation_method: str
    algorithm_version: str
    lookback_sessions: int
    input_adjustment: AdjustmentMethod
    bar_trade_date: date

    @classmethod
    def from_domain(cls, snapshot: ChipDistributionSnapshot) -> ChipDistributionSnapshotDTO:
        return cls(
            as_of=snapshot.as_of,
            bins=tuple(ChipDistributionBinDTO.from_domain(b) for b in snapshot.bins),
            profit_ratio=snapshot.profit_ratio,
            average_cost=snapshot.average_cost,
            concentration_90=snapshot.concentration_90,
            concentration_70=snapshot.concentration_70,
            source_vendor=snapshot.source_vendor,
            reliability=snapshot.reliability,
            is_authoritative=snapshot.is_authoritative,
            calculation_method=snapshot.calculation_method,
            algorithm_version=snapshot.algorithm_version,
            lookback_sessions=snapshot.lookback_sessions,
            input_adjustment=snapshot.input_adjustment,
            bar_trade_date=snapshot.bar_trade_date,
        )


class UnlockRecordDTO(_FrozenForbid):
    unlock_date: date
    published_at: datetime | None
    unlock_type: str | None
    unlock_shares: int | None
    tradable_shares: int | None
    market_value_cny: DecimalWire | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: UnlockRecord) -> UnlockRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class DividendRecordDTO(_FrozenForbid):
    fiscal_year: int
    plan_status: str
    ex_date: date | None
    cash_per_share: DecimalWire | None
    bonus_shares_per_share: DecimalWire | None
    transfer_shares_per_share: DecimalWire | None
    published_at: datetime | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: DividendRecord) -> DividendRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class LimitPoolEntryDTO(_FrozenForbid):
    pool_type: LimitPoolType
    trade_date: date
    instrument_id: str
    name: str
    last: DecimalWire
    change_percent: DecimalWire
    consecutive_limit_count: int | None
    days_and_boards: str | None
    first_seal_at: datetime | None
    last_seal_at: datetime | None
    seal_amount_cny: DecimalWire | None
    broken_count: int | None
    industry: str | None
    reason_tags: tuple[str, ...]
    source_vendor: VendorId
    reliability: ReliabilityLevel

    @classmethod
    def from_domain(cls, entry: LimitPoolEntry) -> LimitPoolEntryDTO:
        return cls.model_validate(entry, from_attributes=True)


class LimitUpLadderRungDTO(_FrozenForbid):
    consecutive_limit_count: int
    instrument_count: int
    instrument_ids: tuple[str, ...]

    @classmethod
    def from_domain(cls, rung: LimitUpLadderRung) -> LimitUpLadderRungDTO:
        return cls.model_validate(rung, from_attributes=True)


class LimitUpContextDTO(_FrozenForbid):
    trade_date: date
    entries: tuple[LimitPoolEntryDTO, ...]
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    broken_rate: DecimalWire | None
    max_consecutive_count: int | None
    promotion_rate: DecimalWire | None
    ladder: tuple[LimitUpLadderRungDTO, ...]

    @classmethod
    def from_domain(cls, context: LimitUpContext) -> LimitUpContextDTO:
        return cls(
            trade_date=context.trade_date,
            entries=tuple(LimitPoolEntryDTO.from_domain(e) for e in context.entries),
            limit_up_count=context.limit_up_count,
            limit_down_count=context.limit_down_count,
            broken_limit_count=context.broken_limit_count,
            broken_rate=context.broken_rate,
            max_consecutive_count=context.max_consecutive_count,
            promotion_rate=context.promotion_rate,
            ladder=tuple(LimitUpLadderRungDTO.from_domain(r) for r in context.ladder),
        )


class SentimentSignalDTO(_FrozenForbid):
    source_type: SentimentSourceType
    trade_date: date
    instrument_id: str | None
    rank: int | None
    rank_change: int | None
    heat_value: DecimalWire | None
    concept_tags: tuple[str, ...]
    label: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool
    source_item_id: str | None
    observed_at: datetime | None

    @classmethod
    def from_domain(cls, signal: SentimentSignal) -> SentimentSignalDTO:
        return cls.model_validate(signal, from_attributes=True)


class EtfOptionContractDTO(_FrozenForbid):
    instrument_id: str
    underlying_instrument_id: str
    option_type: OptionType
    expiry: date
    strike: DecimalWire
    multiplier: DecimalWire | None

    @classmethod
    def from_domain(cls, contract: EtfOptionContract) -> EtfOptionContractDTO:
        return cls.model_validate(contract, from_attributes=True)


class EtfOptionQuoteDTO(_FrozenForbid):
    contract: EtfOptionContractDTO
    quote_at: datetime
    last: DecimalWire | None
    bid_prices: tuple[DecimalWire, ...]
    bid_volumes: tuple[int, ...]
    ask_prices: tuple[DecimalWire, ...]
    ask_volumes: tuple[int, ...]
    volume_contracts: int | None
    open_interest: int | None

    @classmethod
    def from_domain(cls, quote: EtfOptionQuote) -> EtfOptionQuoteDTO:
        return cls(
            contract=EtfOptionContractDTO.from_domain(quote.contract),
            quote_at=quote.quote_at,
            last=quote.last,
            bid_prices=quote.bid_prices,
            bid_volumes=quote.bid_volumes,
            ask_prices=quote.ask_prices,
            ask_volumes=quote.ask_volumes,
            volume_contracts=quote.volume_contracts,
            open_interest=quote.open_interest,
        )


class OptionGreeksDTO(_FrozenForbid):
    contract_instrument_id: str
    as_of: datetime
    delta: DecimalWire | None
    gamma: DecimalWire | None
    theta: DecimalWire | None
    vega: DecimalWire | None
    implied_volatility: DecimalWire | None
    theoretical_value: DecimalWire | None
    source_provided: Literal[True] = True

    @classmethod
    def from_domain(cls, greeks: OptionGreeks) -> OptionGreeksDTO:
        return cls.model_validate(greeks, from_attributes=True)


class EtfOptionSnapshotDTO(_FrozenForbid):
    underlying_instrument_id: str
    expiry: date
    quotes: tuple[EtfOptionQuoteDTO, ...]
    greeks: tuple[OptionGreeksDTO, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_domain(
        cls,
        snapshot: EtfOptionSnapshot,
        *,
        provenance: tuple[AShareComponentProvenanceDTO, ...],
    ) -> EtfOptionSnapshotDTO:
        if snapshot.expiry is None:
            raise ValueError("successful ETF option snapshot requires exact expiry")
        return cls(
            underlying_instrument_id=snapshot.underlying_instrument_id,
            expiry=snapshot.expiry,
            quotes=tuple(EtfOptionQuoteDTO.from_domain(q) for q in snapshot.quotes),
            greeks=tuple(OptionGreeksDTO.from_domain(g) for g in snapshot.greeks),
            provenance=provenance,
        )


class AShareCompositeSnapshotDTO(_FrozenForbid):
    """Product composite for snapshot service (components optional when partial)."""

    instrument_id: str
    detail: AShareSnapshotDetail
    as_of: datetime
    quote: AShareQuoteDTO | None = None
    fundamentals: tuple[FundamentalMetricDTO, ...] = ()
    statements: tuple[FinancialStatementLineDTO, ...] = ()
    f10_sections: tuple[F10SectionDTO, ...] = ()
    announcements: tuple[AnnouncementItemDTO, ...] = ()
    news: tuple[NewsItemDTO, ...] = ()
    unlocks: tuple[UnlockRecordDTO, ...] = ()
    dividends: tuple[DividendRecordDTO, ...] = ()
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class AShareMarketStructureSnapshotDTO(_FrozenForbid):
    scope: AShareMarketScope
    instrument_id: str | None
    trade_date: date | None
    as_of: datetime
    included_components: tuple[AShareComponentType, ...]
    bars: tuple[AShareBarDTO, ...] = ()
    order_book: tuple[OrderBookLevelDTO, ...] = ()
    ticks: tuple[TradeTickDTO, ...] = ()
    industries: tuple[IndustryPerformanceRowDTO, ...] = ()
    market_board: MarketBoardSnapshotDTO | None = None
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class AShareCapitalSnapshotDTO(_FrozenForbid):
    """Product composite for capital service (metrics optional when partial)."""

    instrument_id: str | None
    as_of: datetime
    metrics: tuple[CapitalMetricType, ...]
    intraday_flow: tuple[FundFlowPointDTO, ...] = ()
    daily_flow: tuple[FundFlowPointDTO, ...] = ()
    northbound: tuple[NorthboundFlowPointDTO, ...] = ()
    dragon_tiger: tuple[DragonTigerRecordDTO, ...] = ()
    margin: tuple[MarginRecordDTO, ...] = ()
    block_trades: tuple[BlockTradeRecordDTO, ...] = ()
    shareholder_counts: tuple[ShareholderCountRecordDTO, ...] = ()
    chip_distribution: ChipDistributionSnapshotDTO | None = None
    unlocks: tuple[UnlockRecordDTO, ...] = ()
    dividends: tuple[DividendRecordDTO, ...] = ()
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class IndustryMetricObservationDTO(_FrozenForbid):
    metric_code: str
    value: DecimalWire
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    published_at: datetime
    source_url: str
    geography: str
    measurement_basis: IndustryMeasurementBasis
    is_estimated: bool
    methodology_version: str
    methodology_break: str | None

    @classmethod
    def from_domain(cls, value: IndustryMetricObservation) -> IndustryMetricObservationDTO:
        return cls.model_validate(value, from_attributes=True)


class IndustryMetricCoverageDTO(_FrozenForbid):
    """Deterministic per-metric coverage over the selected filtered history."""

    metric_code: str
    count: int = Field(ge=0)
    first_period: date
    last_period: date


class IndustryCycleSnapshotDTO(_FrozenForbid):
    """Bounded industry-cycle fact package.

    Provider/repository may retain the full requested lookback. MCP payloads use
    ``view=compact`` (default: latest visible observation per selected metric) or
    ``view=series`` (offset/limit page, max 200 rows). Coverage always describes
    the full selected filtered history, not only the returned page.
    """

    cycle: IndustryCycleType
    dataset_code: str
    as_of: datetime
    view: Literal["compact", "series"] = "compact"
    observations: tuple[IndustryMetricObservationDTO, ...]
    coverage: tuple[IndustryMetricCoverageDTO, ...]
    total_observations: int = Field(ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    has_more: bool = False
    interpretation_owner: Literal["external_host"] = "external_host"
    missing_components: tuple[str, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_domain(
        cls,
        value: IndustryCycleSnapshot,
        *,
        provenance: tuple[AShareComponentProvenanceDTO, ...],
        view: Literal["compact", "series"] = "compact",
        metric_codes: tuple[str, ...] = (),
        offset: int = 0,
        limit: int = 50,
    ) -> IndustryCycleSnapshotDTO:
        selected = _filter_industry_observations(value.observations, metric_codes)
        coverage = _industry_metric_coverage(selected)
        total = len(selected)
        if view == "compact":
            page = _compact_industry_observations(selected)
            page_offset = 0
            page_limit = limit
            has_more = False
        else:
            page_offset = offset
            page_limit = limit
            page = selected[page_offset : page_offset + page_limit]
            has_more = page_offset + len(page) < total
        return cls(
            cycle=value.cycle,
            dataset_code=value.dataset_code,
            as_of=value.as_of,
            view=view,
            observations=tuple(IndustryMetricObservationDTO.from_domain(item) for item in page),
            coverage=coverage,
            total_observations=total,
            offset=page_offset,
            limit=page_limit,
            has_more=has_more,
            missing_components=value.missing_components,
            provenance=provenance,
        )


def _filter_industry_observations(
    observations: tuple[IndustryMetricObservation, ...],
    metric_codes: tuple[str, ...],
) -> tuple[IndustryMetricObservation, ...]:
    if not metric_codes:
        return observations
    allowed = frozenset(metric_codes)
    return tuple(item for item in observations if item.metric_code in allowed)


def _industry_metric_coverage(
    observations: tuple[IndustryMetricObservation, ...],
) -> tuple[IndustryMetricCoverageDTO, ...]:
    by_code: dict[str, list[IndustryMetricObservation]] = {}
    for item in observations:
        by_code.setdefault(item.metric_code, []).append(item)
    return tuple(
        IndustryMetricCoverageDTO(
            metric_code=code,
            count=len(items),
            first_period=min(item.period_end for item in items),
            last_period=max(item.period_end for item in items),
        )
        for code, items in sorted(by_code.items(), key=lambda pair: pair[0])
    )


def _compact_industry_observations(
    observations: tuple[IndustryMetricObservation, ...],
) -> tuple[IndustryMetricObservation, ...]:
    latest: dict[str, IndustryMetricObservation] = {}
    for item in observations:
        current = latest.get(item.metric_code)
        if current is None or (item.period_end, item.published_at) > (
            current.period_end,
            current.published_at,
        ):
            latest[item.metric_code] = item
    return tuple(sorted(latest.values(), key=lambda item: (item.period_end, item.metric_code)))


class AShareLimitUpContextProductDTO(_FrozenForbid):
    """Product composite for limit-up service."""

    trade_date: date
    as_of: datetime
    pools: tuple[LimitPoolType, ...]
    context: LimitUpContextDTO
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class AShareSentimentSnapshotDTO(_FrozenForbid):
    """Product composite for sentiment service (all sources optional/partial)."""

    instrument_id: str | None
    trade_date: date
    as_of: datetime
    sources: tuple[SentimentSourceType, ...]
    signals: tuple[SentimentSignalDTO, ...] = ()
    interactive_qa: tuple[InteractiveQAItemDTO, ...] = ()
    company_news: tuple[NewsItemDTO, ...] = ()
    market_news: tuple[NewsItemDTO, ...] = ()
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class ResearchReportSearchDTO(_FrozenForbid):
    instrument_id: str | None
    industry_code: str | None
    published_from: date | None
    published_to: date | None
    include_consensus: bool
    limit: int
    offset: int
    reports: tuple[AnalystReportItemDTO, ...]
    consensus: tuple[ConsensusEstimateDTO, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]


class DocumentParseReceiptDTO(_FrozenForbid):
    announcement_key: str
    title: str
    document_type: CompanyDocumentType
    published_at: datetime
    source_url: str
    pdf_url: str | None
    parser_version: str
    page_count: int | None
    status: CompanyDocumentParseStatus
    extracted_metric_count: int
    warning_code: str | None = None

    @classmethod
    def from_domain(cls, value: DocumentParseReceipt) -> DocumentParseReceiptDTO:
        return cls.model_validate(value, from_attributes=True)


class CompanyOperatingMetricObservationDTO(_FrozenForbid):
    instrument_id: str
    metric_code: str
    value: DecimalWire
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    measurement_basis: IndustryMeasurementBasis
    published_at: datetime
    source_url: str
    parser_version: str
    pdf_url: str | None
    announcement_key: str | None
    is_audited: bool
    is_estimated: bool

    @classmethod
    def from_domain(
        cls, value: CompanyOperatingMetricObservation
    ) -> CompanyOperatingMetricObservationDTO:
        return cls.model_validate(value, from_attributes=True)


class CompanyOperatingMetricsSnapshotDTO(_FrozenForbid):
    """Bounded company operating-metric package (max 200 observations)."""

    instrument_id: str
    as_of: datetime
    lookback_months: int
    observations: tuple[CompanyOperatingMetricObservationDTO, ...]
    documents: tuple[DocumentParseReceiptDTO, ...]
    missing_metric_codes: tuple[str, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_domain(
        cls,
        value: CompanyOperatingMetricsSnapshot,
        *,
        provenance: tuple[AShareComponentProvenanceDTO, ...],
        metric_codes: tuple[str, ...] = (),
    ) -> CompanyOperatingMetricsSnapshotDTO:
        selected = value.observations
        if metric_codes:
            allowed = frozenset(metric_codes)
            selected = tuple(item for item in value.observations if item.metric_code in allowed)
        present = {item.metric_code for item in selected}
        missing = (
            tuple(code for code in metric_codes if code not in present)
            if metric_codes
            else value.missing_metric_codes
        )
        # Preserve newest-first order from the domain snapshot.
        return cls(
            instrument_id=value.instrument_id,
            as_of=value.as_of,
            lookback_months=value.lookback_months,
            observations=tuple(
                CompanyOperatingMetricObservationDTO.from_domain(item) for item in selected
            ),
            documents=tuple(DocumentParseReceiptDTO.from_domain(item) for item in value.documents),
            missing_metric_codes=missing,
            provenance=provenance,
        )
