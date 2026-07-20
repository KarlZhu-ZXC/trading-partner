"""Market bar, indicators, and verified snapshot domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.enums import AdjustmentMethod, TradingSession
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument


@dataclass(frozen=True, slots=True)
class MarketBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        require_aware_datetime(self.timestamp, field_name="MarketBar.timestamp")


@dataclass(frozen=True, slots=True)
class TechnicalIndicators:
    ema_10: Decimal | None
    sma_50: Decimal | None
    sma_200: Decimal | None
    rsi_14: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    atr_14: Decimal | None
    bollinger_mid: Decimal | None
    bollinger_upper: Decimal | None
    bollinger_lower: Decimal | None
    vwma: Decimal | None
    mfi: Decimal | None

    @classmethod
    def empty(cls) -> TechnicalIndicators:
        """All indicators unset (Phase 1A mock providers)."""
        return cls(
            ema_10=None,
            sma_50=None,
            sma_200=None,
            rsi_14=None,
            macd=None,
            macd_signal=None,
            macd_histogram=None,
            atr_14=None,
            bollinger_mid=None,
            bollinger_upper=None,
            bollinger_lower=None,
            vwma=None,
            mfi=None,
        )


@dataclass(frozen=True, slots=True)
class VerifiedMarketSnapshot:
    instrument: Instrument
    requested_as_of: datetime
    latest_market_row: MarketBar
    indicators: TechnicalIndicators
    recent_closes: tuple[Decimal, ...]
    adjustment: AdjustmentMethod
    session: TradingSession
    algorithm_version: str

    def __post_init__(self) -> None:
        require_aware_datetime(self.requested_as_of, field_name="requested_as_of")
