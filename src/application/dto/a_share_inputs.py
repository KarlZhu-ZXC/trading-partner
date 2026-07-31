"""Closed A-share MCP input DTOs."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from application.dto.a_share_common import (
    _FrozenForbid,
    _require_exact_date_wire,
    _validate_a_share_instrument_id,
)
from application.dto.market import DecimalWire
from domain.a_share.enums import (
    AShareMarketScope,
    AShareSnapshotDetail,
    BarInterval,
    CapitalMetricType,
    FinancialStatementType,
    LimitPoolType,
    SentimentSourceType,
)
from domain.common.enums import AdjustmentMethod, AssetType

# Statically decidable asset matrix for MCP inputs; repository existence remains
# a service responsibility.
_SNAPSHOT_STRUCTURE_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_EQUITY_ONLY = frozenset({AssetType.EQUITY})
_CAPITAL_INSTRUMENT_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF})
_SENTIMENT_INSTRUMENT_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_REPORT_INSTRUMENT_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_OPTION_UNDERLYING_ASSETS = frozenset({AssetType.ETF})

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

