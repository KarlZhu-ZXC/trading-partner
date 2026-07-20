"""Market-related DTOs with domain conversion and Decimal serialization."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer

from application.dto.instrument import InstrumentDTO
from domain.common.enums import AdjustmentMethod, TradingSession
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)

# Re-export canonical InstrumentDTO so existing imports keep working.
__all__ = [
    "DecimalWire",
    "InstrumentDTO",
    "MarketBarDTO",
    "TechnicalIndicatorsDTO",
    "VerifiedMarketSnapshotDTO",
    "decimal_to_wire_string",
]


def decimal_to_wire_string(value: Decimal) -> str:
    """Serialize Decimal as a fixed-point decimal string (no scientific notation).

    Preserves the precision present on the Decimal (e.g. ``1500.00`` stays
    ``\"1500.00\"``). Scientific notation is expanded to fixed-point form.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"expected Decimal, got {type(value).__name__}")
    # format(..., 'f') expands scientific notation; for non-scientific Decimals
    # with explicit exponent, prefer str() to keep trailing fractional zeros.
    raw = str(value)
    if "E" in raw.upper() or "e" in raw:
        return format(value, "f")
    return raw


DecimalWire = Annotated[
    Decimal,
    PlainSerializer(decimal_to_wire_string, return_type=str, when_used="json"),
]


class MarketBarDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    open: DecimalWire
    high: DecimalWire
    low: DecimalWire
    close: DecimalWire
    volume: DecimalWire

    @classmethod
    def from_domain(cls, bar: MarketBar) -> MarketBarDTO:
        return cls(
            timestamp=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )


class TechnicalIndicatorsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ema_10: DecimalWire | None
    sma_50: DecimalWire | None
    sma_200: DecimalWire | None
    rsi_14: DecimalWire | None
    macd: DecimalWire | None
    macd_signal: DecimalWire | None
    macd_histogram: DecimalWire | None
    atr_14: DecimalWire | None
    bollinger_mid: DecimalWire | None
    bollinger_upper: DecimalWire | None
    bollinger_lower: DecimalWire | None
    vwma: DecimalWire | None
    mfi: DecimalWire | None

    @classmethod
    def from_domain(cls, indicators: TechnicalIndicators) -> TechnicalIndicatorsDTO:
        return cls(
            ema_10=indicators.ema_10,
            sma_50=indicators.sma_50,
            sma_200=indicators.sma_200,
            rsi_14=indicators.rsi_14,
            macd=indicators.macd,
            macd_signal=indicators.macd_signal,
            macd_histogram=indicators.macd_histogram,
            atr_14=indicators.atr_14,
            bollinger_mid=indicators.bollinger_mid,
            bollinger_upper=indicators.bollinger_upper,
            bollinger_lower=indicators.bollinger_lower,
            vwma=indicators.vwma,
            mfi=indicators.mfi,
        )


class VerifiedMarketSnapshotDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    instrument: InstrumentDTO
    requested_as_of: datetime
    latest_market_row: MarketBarDTO
    indicators: TechnicalIndicatorsDTO
    recent_closes: tuple[DecimalWire, ...]
    adjustment: AdjustmentMethod
    session: TradingSession
    algorithm_version: str

    @classmethod
    def from_domain(cls, snapshot: VerifiedMarketSnapshot) -> VerifiedMarketSnapshotDTO:
        return cls(
            instrument=InstrumentDTO.from_domain(snapshot.instrument),
            requested_as_of=snapshot.requested_as_of,
            latest_market_row=MarketBarDTO.from_domain(snapshot.latest_market_row),
            indicators=TechnicalIndicatorsDTO.from_domain(snapshot.indicators),
            recent_closes=snapshot.recent_closes,
            adjustment=snapshot.adjustment,
            session=snapshot.session,
            algorithm_version=snapshot.algorithm_version,
        )

    def model_dump_wire(self) -> dict[str, Any]:
        """JSON-compatible dict with Decimal strings and ISO datetimes."""
        return self.model_dump(mode="json")
