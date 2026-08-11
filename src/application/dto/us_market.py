"""Closed US market Pydantic DTOs (Phase 1F F1).

Input models implement design §4 MCP schema validation. Output DTOs mirror
frozen domain models with ``extra=forbid``, Decimal fixed-point wire strings,
and JSON-native datetimes. Reuses ``MarketBarDTO`` and ``TechnicalIndicatorsDTO``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from application.dto.market import DecimalWire, MarketBarDTO, TechnicalIndicatorsDTO
from domain.common.enums import AdjustmentMethod, AssetType, Market, TradingSession
from domain.common.errors import TradingPartnerError
from domain.common.values import parse_instrument_id
from domain.us_market.enums import USBarInterval
from domain.us_market.models import (
    USBarSeries,
    USCommunityHeatItem,
    USCompositeSnapshot,
    USMarketContext,
    USMarketProxy,
    USQuote,
    USSectorRotation,
    USTechnicalSnapshot,
)

_DATE_WIRE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Equity / ETF / index instruments for quote, bars, technical, composite.
_QUOTE_ASSET_TYPES = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX, AssetType.FUTURE})

# Phase 3A market_get_snapshot / market_get_bars quote+bars matrix.
_SNAPSHOT_BARS_ASSET_TYPES = frozenset(
    {
        AssetType.EQUITY,
        AssetType.ETF,
        AssetType.INDEX,
        AssetType.FUTURE,
        AssetType.COMMODITY_SPOT,
        AssetType.CFD,
    }
)
_SNAPSHOT_BARS_MARKETS = frozenset({Market.US, Market.KR, Market.CME, Market.DCE, Market.OTC})

# US wire adjustment values (design §5; excludes A-share forward/backward).
_US_ADJUSTMENT = frozenset(
    {
        AdjustmentMethod.NONE,
        AdjustmentMethod.SPLIT_ADJUSTED,
        AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
    }
)

# market_get_context closed operation enum (default preserves US-only callers).
_CONTEXT_OPERATIONS = frozenset({"us_market", "futures_curve", "spot_future_basis"})


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


class _FrozenForbid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_us_instrument_id(
    value: str,
    *,
    allowed_assets: frozenset[AssetType],
    field_name: str = "instrument_id",
) -> str:
    """Reject non-US and asset types outside the frozen tool matrix."""
    try:
        asset_type, market, _symbol = parse_instrument_id(value)
    except TradingPartnerError:
        raise ValueError("invalid instrument_id syntax") from None
    if market is not Market.US:
        raise ValueError(f"{field_name} must use Market.US")
    if asset_type not in allowed_assets:
        allowed = ", ".join(sorted(a.value for a in allowed_assets))
        raise ValueError(f"{field_name} asset type must be one of [{allowed}] for this tool")
    return value


def _validate_snapshot_bars_instrument_id(
    value: str,
    *,
    field_name: str = "instrument_id",
) -> str:
    """Accept US/KR exchange instruments, CME/DCE futures, and OTC spot/CFD ids."""
    try:
        asset_type, market, _symbol = parse_instrument_id(value)
    except TradingPartnerError:
        raise ValueError("invalid instrument_id syntax") from None
    if market not in _SNAPSHOT_BARS_MARKETS:
        raise ValueError(
            f"{field_name} market must be one of "
            f"[{', '.join(sorted(m.value for m in _SNAPSHOT_BARS_MARKETS))}]"
        )
    if asset_type not in _SNAPSHOT_BARS_ASSET_TYPES:
        allowed = ", ".join(sorted(a.value for a in _SNAPSHOT_BARS_ASSET_TYPES))
        raise ValueError(f"{field_name} asset type must be one of [{allowed}] for this tool")
    if market is Market.US and asset_type not in _QUOTE_ASSET_TYPES:
        raise ValueError(f"{field_name} Market.US only supports equity/etf/index/future")
    if market is Market.KR and asset_type not in {
        AssetType.EQUITY,
        AssetType.ETF,
        AssetType.INDEX,
    }:
        raise ValueError(f"{field_name} Market.KR only supports equity/etf/index")
    if market in {Market.CME, Market.DCE} and asset_type is not AssetType.FUTURE:
        raise ValueError(f"{field_name} futures exchange markets only support future")
    if market is Market.OTC and asset_type not in {
        AssetType.COMMODITY_SPOT,
        AssetType.CFD,
    }:
        raise ValueError(f"{field_name} Market.OTC only supports commodity_spot or cfd")
    return value


def _require_aware_as_of(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("as_of must be timezone-aware")
    return value


# ---------------------------------------------------------------------------
# §4 MCP input models
# ---------------------------------------------------------------------------


class MarketGetSnapshotInput(_FrozenForbid):
    instrument_id: str
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_snapshot_bars_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)


class MarketGetBatchQuotesInput(_FrozenForbid):
    instrument_ids: tuple[str, ...]
    as_of: datetime | None = None

    @field_validator("instrument_ids")
    @classmethod
    def _instrument_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(values) <= 50:
            raise ValueError("instrument_ids must contain 1..50 values")
        if len(values) != len(set(values)):
            raise ValueError("instrument_ids must be unique")
        return tuple(_validate_snapshot_bars_instrument_id(value) for value in values)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)


class MarketGetBarsInput(_FrozenForbid):
    instrument_id: str
    start: date
    end: date
    interval: USBarInterval = USBarInterval.ONE_DAY
    # None selects the asset-aware default in the coordinator: unadjusted for
    # futures/OTC, split-and-dividend-adjusted for equities/ETFs/indexes.
    adjustment: AdjustmentMethod | None = None
    # Dukascopy historical offer side; ignored for exchange instruments.
    offer_side: str | None = None
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_snapshot_bars_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("adjustment")
    @classmethod
    def _us_adjustment(cls, value: AdjustmentMethod | None) -> AdjustmentMethod | None:
        if value is None:
            return None
        if value not in _US_ADJUSTMENT:
            allowed = ", ".join(sorted(a.value for a in _US_ADJUSTMENT))
            raise ValueError(f"adjustment must be one of [{allowed}] for bars")
        return value

    @field_validator("offer_side")
    @classmethod
    def _offer_side(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in {"B", "A", "bid", "ask", "BID", "ASK"}:
            raise ValueError("offer_side must be B/A (or bid/ask)")
        return value

    @model_validator(mode="after")
    def _inclusive_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class MarketGetContextInput(_FrozenForbid):
    """US market context (default) or Phase 3A futures curve / basis operations."""

    operation: str = "us_market"
    as_of: datetime | None = None
    # futures_curve
    product_key: str | None = None
    price_basis: str = "settlement"
    trade_date: date | None = None
    contract_limit: int = Field(default=6, ge=1, le=24)
    # spot_future_basis
    left_instrument_id: str | None = None
    right_instrument_id: str | None = None
    max_observation_lag_seconds: int = Field(default=300, ge=0)

    @field_validator("operation")
    @classmethod
    def _operation(cls, value: str) -> str:
        if value not in _CONTEXT_OPERATIONS:
            allowed = ", ".join(sorted(_CONTEXT_OPERATIONS))
            raise ValueError(f"operation must be one of [{allowed}]")
        return value

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)

    @field_validator("trade_date", mode="before")
    @classmethod
    def _exact_trade_date(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @field_validator("price_basis")
    @classmethod
    def _price_basis(cls, value: str) -> str:
        allowed = {"last", "mid", "settlement"}
        if value not in allowed:
            raise ValueError(f"price_basis must be one of [{', '.join(sorted(allowed))}]")
        return value

    @field_validator("left_instrument_id", "right_instrument_id")
    @classmethod
    def _optional_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parse_instrument_id(value)
        except TradingPartnerError:
            raise ValueError("invalid instrument_id syntax") from None
        return value

    @field_validator("product_key")
    @classmethod
    def _product_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip()
        if not key:
            return None
        if ":" not in key:
            raise ValueError("product_key must match MARKET:ROOT")
        return key

    @model_validator(mode="after")
    def _operation_fields(self) -> Self:
        if self.operation == "futures_curve" and not self.product_key:
            raise ValueError("product_key is required for futures_curve")
        if self.operation == "spot_future_basis":
            if not self.left_instrument_id or not self.right_instrument_id:
                raise ValueError(
                    "left_instrument_id and right_instrument_id are required for spot_future_basis"
                )
            if self.left_instrument_id == self.right_instrument_id:
                raise ValueError("left and right instrument ids must differ")
        return self


class TechnicalGetSnapshotInput(_FrozenForbid):
    instrument_id: str
    as_of: datetime | None = None
    lookback_sessions: int = Field(default=260, ge=20, le=1000)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_instrument_id(value, allowed_assets=_QUOTE_ASSET_TYPES)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)


class USGetSnapshotInput(_FrozenForbid):
    instrument_id: str
    as_of: datetime | None = None
    lookback_sessions: int = Field(default=260, ge=20, le=1000)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_instrument_id(value, allowed_assets=_QUOTE_ASSET_TYPES)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)


# ---------------------------------------------------------------------------
# Domain-mirroring output DTOs
# ---------------------------------------------------------------------------


class USQuoteDTO(_FrozenForbid):
    instrument_id: str
    quote_at: datetime
    session: TradingSession
    last: DecimalWire
    open: DecimalWire | None
    high: DecimalWire | None
    low: DecimalWire | None
    previous_close: DecimalWire | None
    volume: DecimalWire | None
    average_volume: DecimalWire | None
    market_cap: DecimalWire | None
    beta: DecimalWire | None
    week_52_low: DecimalWire | None
    week_52_high: DecimalWire | None

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed property
    @property
    def display_price(self) -> DecimalWire:
        """Cross-asset display price; exchange quotes are last-price based."""
        return self.last

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed property
    @property
    def price_basis(self) -> Literal["last"]:
        """Disclose that ``display_price`` is a traded/quoted last, not a midpoint."""
        return "last"

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed property
    @property
    def previous_close_basis(
        self,
    ) -> Literal[
        "previous_completed_regular_session_close",
        "previous_completed_daily_bar_close",
    ]:
        """Disclose which completed period the ``previous_close`` baseline names.

        Exchange-traded futures expose the prior completed daily bar close.  US/KR
        equity, ETF, and index quotes expose the prior completed regular-session
        close.  Keeping this basis alongside the legacy numeric field prevents a
        host model from treating every baseline as a literal calendar-day close.
        """
        asset_type, _market, _symbol = parse_instrument_id(self.instrument_id)
        if asset_type is AssetType.FUTURE:
            return "previous_completed_daily_bar_close"
        return "previous_completed_regular_session_close"

    @classmethod
    def from_domain(cls, quote: USQuote) -> USQuoteDTO:
        return cls.model_validate(quote, from_attributes=True)


class USBarSeriesDTO(_FrozenForbid):
    instrument_id: str
    interval: USBarInterval
    adjustment: AdjustmentMethod
    start: date
    end: date
    bars: tuple[MarketBarDTO, ...]

    @classmethod
    def from_domain(cls, series: USBarSeries) -> USBarSeriesDTO:
        return cls(
            instrument_id=series.instrument_id,
            interval=series.interval,
            adjustment=series.adjustment,
            start=series.start,
            end=series.end,
            bars=tuple(MarketBarDTO.from_domain(bar) for bar in series.bars),
        )


class USMarketProxyDTO(_FrozenForbid):
    instrument_id: str
    latest: DecimalWire | None
    change_percent: DecimalWire | None
    quote_at: datetime | None
    session: TradingSession | None

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed property
    @property
    def change_percent_basis(
        self,
    ) -> Literal["previous_completed_regular_session_close"]:
        """Disclose the baseline used by the proxy percentage calculation."""
        return "previous_completed_regular_session_close"

    @classmethod
    def from_domain(cls, proxy: USMarketProxy) -> USMarketProxyDTO:
        return cls.model_validate(proxy, from_attributes=True)


class USSectorRotationDTO(_FrozenForbid):
    sector: str
    index_symbol: str
    return_1d: DecimalWire | None
    return_5d: DecimalWire | None
    return_20d: DecimalWire | None
    relative_spy_20d: DecimalWire | None

    @classmethod
    def from_domain(cls, row: USSectorRotation) -> USSectorRotationDTO:
        return cls.model_validate(row, from_attributes=True)

    def to_domain(self) -> USSectorRotation:
        return USSectorRotation(
            sector=self.sector,
            index_symbol=self.index_symbol,
            return_1d=self.return_1d,
            return_5d=self.return_5d,
            return_20d=self.return_20d,
            relative_spy_20d=self.relative_spy_20d,
        )


class USCommunityHeatItemDTO(_FrozenForbid):
    provider_code: str
    name: str
    rank: int
    trade_heat: DecimalWire | None
    trade_heat_change: DecimalWire | None
    search_heat: DecimalWire | None
    search_heat_change: DecimalWire | None
    news_heat: DecimalWire | None
    news_heat_change: DecimalWire | None
    average_heat: DecimalWire | None
    average_heat_change: DecimalWire | None
    related_content_type: str | None
    related_title: str | None
    related_url: str | None

    @classmethod
    def from_domain(cls, row: USCommunityHeatItem) -> USCommunityHeatItemDTO:
        return cls.model_validate(row, from_attributes=True)


class USMarketContextDTO(_FrozenForbid):
    as_of: datetime
    spy: USMarketProxyDTO
    qqq: USMarketProxyDTO
    iwm: USMarketProxyDTO
    advancing_count: int | None
    declining_count: int | None
    unchanged_count: int | None = None
    breadth_as_of: datetime | None = None
    breadth_basis: str | None = None
    breadth_universe: str | None = None
    sector_rotation: tuple[USSectorRotationDTO, ...] = ()
    community_heat_as_of: datetime | None = None
    community_heat_basis: str | None = None
    community_heat: tuple[USCommunityHeatItemDTO, ...] = ()
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, context: USMarketContext) -> USMarketContextDTO:
        return cls(
            as_of=context.as_of,
            spy=USMarketProxyDTO.from_domain(context.spy),
            qqq=USMarketProxyDTO.from_domain(context.qqq),
            iwm=USMarketProxyDTO.from_domain(context.iwm),
            advancing_count=context.advancing_count,
            declining_count=context.declining_count,
            unchanged_count=context.unchanged_count,
            breadth_as_of=context.breadth_as_of,
            breadth_basis=context.breadth_basis,
            breadth_universe=context.breadth_universe,
            sector_rotation=tuple(
                USSectorRotationDTO.from_domain(row) for row in context.sector_rotation
            ),
            community_heat_as_of=context.community_heat_as_of,
            community_heat_basis=context.community_heat_basis,
            community_heat=tuple(
                USCommunityHeatItemDTO.from_domain(row) for row in context.community_heat
            ),
            warning_codes=context.warning_codes,
        )


class USTechnicalSnapshotDTO(_FrozenForbid):
    instrument_id: str
    as_of: datetime
    bar_as_of: datetime
    indicators: TechnicalIndicatorsDTO
    support: DecimalWire | None
    resistance: DecimalWire | None
    algorithm_version: str
    historically_validated: bool
    support_resistance_method: str

    @classmethod
    def from_domain(cls, snapshot: USTechnicalSnapshot) -> USTechnicalSnapshotDTO:
        return cls(
            instrument_id=snapshot.instrument_id,
            as_of=snapshot.as_of,
            bar_as_of=snapshot.bar_as_of,
            indicators=TechnicalIndicatorsDTO.from_domain(snapshot.indicators),
            support=snapshot.support,
            resistance=snapshot.resistance,
            algorithm_version=snapshot.algorithm_version,
            historically_validated=snapshot.historically_validated,
            support_resistance_method=snapshot.support_resistance_method,
        )


class USCompositeSnapshotDTO(_FrozenForbid):
    instrument_id: str
    as_of: datetime
    quote: USQuoteDTO | None
    bars: USBarSeriesDTO | None
    technical: USTechnicalSnapshotDTO | None
    context: USMarketContextDTO | None
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, snapshot: USCompositeSnapshot) -> USCompositeSnapshotDTO:
        return cls(
            instrument_id=snapshot.instrument_id,
            as_of=snapshot.as_of,
            quote=(USQuoteDTO.from_domain(snapshot.quote) if snapshot.quote is not None else None),
            bars=(USBarSeriesDTO.from_domain(snapshot.bars) if snapshot.bars is not None else None),
            technical=(
                USTechnicalSnapshotDTO.from_domain(snapshot.technical)
                if snapshot.technical is not None
                else None
            ),
            context=(
                USMarketContextDTO.from_domain(snapshot.context)
                if snapshot.context is not None
                else None
            ),
            degraded=snapshot.degraded,
            warning_codes=snapshot.warning_codes,
        )
