"""Phase 2D technical-analysis MCP input and output DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from domain.common.enums import Market
from domain.common.time import require_aware_datetime
from domain.technical.models import TechnicalAnalysis, TechnicalTimeframe


class _FrozenForbid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


def _normalize_interval(value: object) -> object:
    """Normalize conversational technical intervals to the wire values."""
    if isinstance(value, str):
        return {
            "1d": "1d",
            "daily": "1d",
            "1w": "1w",
            "1wk": "1w",
            "1week": "1w",
            "weekly": "1w",
        }.get(value.strip().casefold(), value.strip().casefold())
    return value


TechnicalIntervalInput = Annotated[
    Literal["1d", "1w"],
    BeforeValidator(_normalize_interval),
]


class TechnicalAnalysisInput(_FrozenForbid):
    instrument_id: str = Field(min_length=1, max_length=160)
    as_of: datetime | None = None
    lookback_sessions: int = Field(default=260, ge=60, le=1000)
    intervals: tuple[TechnicalIntervalInput, ...] = ("1d", "1w")

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="as_of")
        return value

    @field_validator("intervals")
    @classmethod
    def _intervals(
        cls, value: tuple[TechnicalIntervalInput, ...]
    ) -> tuple[TechnicalIntervalInput, ...]:
        if not value or len(value) > 2 or len(set(value)) != len(value):
            raise ValueError("intervals must contain one or two unique values")
        return value


class TechnicalChartInput(_FrozenForbid):
    instrument_id: str = Field(min_length=1, max_length=160)
    as_of: datetime | None = None
    interval: TechnicalIntervalInput = "1d"
    lookback_sessions: int = Field(default=160, ge=60, le=500)

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="as_of")
        return value

    @field_validator("interval")
    @classmethod
    def _interval(cls, value: TechnicalIntervalInput) -> TechnicalIntervalInput:
        return value


class TechnicalMetricDTO(_FrozenForbid):
    name: str
    value: Decimal | None
    unit: str
    basis: str


class TechnicalLevelDTO(_FrozenForbid):
    kind: str
    price: Decimal
    touches: int
    basis: str


class TechnicalPatternDTO(_FrozenForbid):
    name: str
    direction: str
    strength: int
    basis: str


class TechnicalTimeframeDTO(_FrozenForbid):
    interval: str
    bar_as_of: datetime
    bar_count: int
    trend_state: str
    momentum_state: str
    volatility_state: str
    volume_state: str
    metrics: tuple[TechnicalMetricDTO, ...]
    levels: tuple[TechnicalLevelDTO, ...]
    patterns: tuple[TechnicalPatternDTO, ...]

    @classmethod
    def from_domain(cls, value: TechnicalTimeframe) -> Self:
        return cls.model_validate(value, from_attributes=True)


class TechnicalCompatibilityIndicatorsDTO(_FrozenForbid):
    """Phase 1F flat daily view retained for existing MCP consumers."""

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


class TechnicalAnalysisDTO(_FrozenForbid):
    instrument_id: str
    market: Market
    as_of: datetime
    timeframes: tuple[TechnicalTimeframeDTO, ...]
    price_basis: str
    bar_as_of: datetime
    indicators: TechnicalCompatibilityIndicatorsDTO
    support: Decimal | None
    resistance: Decimal | None
    algorithm_version: str
    indicator_backend: str
    structure_method: str
    historically_validated: bool

    @classmethod
    def from_domain(cls, value: TechnicalAnalysis) -> Self:
        daily = next(
            (timeframe for timeframe in value.timeframes if timeframe.interval == "1d"),
            value.timeframes[0],
        )
        metrics = {metric.name: metric.value for metric in daily.metrics}
        support = next(
            (level.price for level in daily.levels if level.kind == "support"), None
        )
        resistance = next(
            (level.price for level in daily.levels if level.kind == "resistance"), None
        )
        return cls(
            instrument_id=value.instrument_id,
            market=value.market,
            as_of=value.as_of,
            timeframes=tuple(TechnicalTimeframeDTO.from_domain(v) for v in value.timeframes),
            price_basis=value.price_basis,
            bar_as_of=daily.bar_as_of,
            indicators=TechnicalCompatibilityIndicatorsDTO(
                ema_10=metrics.get("ema_10"),
                sma_50=metrics.get("sma_50"),
                sma_200=metrics.get("sma_200"),
                rsi_14=metrics.get("rsi_14"),
                macd=metrics.get("macd"),
                macd_signal=metrics.get("macd_signal"),
                macd_histogram=metrics.get("macd_histogram"),
                atr_14=metrics.get("atr_14"),
                bollinger_mid=metrics.get("bollinger_mid"),
                bollinger_upper=metrics.get("bollinger_upper"),
                bollinger_lower=metrics.get("bollinger_lower"),
                vwma=metrics.get("vwma_20"),
                mfi=metrics.get("mfi_14"),
            ),
            support=support,
            resistance=resistance,
            algorithm_version=value.algorithm_version,
            indicator_backend=value.indicator_backend,
            structure_method=value.structure_method,
            historically_validated=value.historically_validated,
        )
