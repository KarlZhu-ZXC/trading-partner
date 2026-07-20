"""Market-neutral technical-analysis facts for A-share and US instruments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.enums import Market
from domain.common.time import require_aware_datetime


@dataclass(frozen=True, slots=True)
class TechnicalMetric:
    name: str
    value: Decimal | None
    unit: str
    basis: str


@dataclass(frozen=True, slots=True)
class TechnicalLevel:
    kind: str
    price: Decimal
    touches: int
    basis: str


@dataclass(frozen=True, slots=True)
class TechnicalPattern:
    name: str
    direction: str
    strength: int
    basis: str


@dataclass(frozen=True, slots=True)
class TechnicalTimeframe:
    interval: str
    bar_as_of: datetime
    bar_count: int
    trend_state: str
    momentum_state: str
    volatility_state: str
    volume_state: str
    metrics: tuple[TechnicalMetric, ...]
    levels: tuple[TechnicalLevel, ...]
    patterns: tuple[TechnicalPattern, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(self.bar_as_of, field_name="bar_as_of")


@dataclass(frozen=True, slots=True)
class TechnicalAnalysis:
    instrument_id: str
    market: Market
    as_of: datetime
    timeframes: tuple[TechnicalTimeframe, ...]
    price_basis: str
    algorithm_version: str = "tp_technical_v2"
    indicator_backend: str = "TA-Lib"
    structure_method: str = "swing_cluster_atr_v1"
    historically_validated: bool = False

    def __post_init__(self) -> None:
        require_aware_datetime(self.as_of, field_name="as_of")
