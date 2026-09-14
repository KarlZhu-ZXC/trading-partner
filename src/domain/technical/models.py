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
class SmartMoneySwing:
    scope: str
    kind: str
    label: str
    price: Decimal
    occurred_at: datetime
    confirmed_at: datetime

    def __post_init__(self) -> None:
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")


@dataclass(frozen=True, slots=True)
class SmartMoneyStructureEvent:
    scope: str
    event: str
    direction: str
    price: Decimal
    occurred_at: datetime
    broken_swing_at: datetime
    basis: str

    def __post_init__(self) -> None:
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        require_aware_datetime(self.broken_swing_at, field_name="broken_swing_at")


@dataclass(frozen=True, slots=True)
class SmartMoneyZone:
    kind: str
    scope: str
    direction: str | None
    low: Decimal
    high: Decimal
    created_at: datetime
    confirmed_at: datetime
    status: str
    basis: str

    def __post_init__(self) -> None:
        require_aware_datetime(self.created_at, field_name="created_at")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")
        if self.low > self.high:
            raise ValueError("smart-money zone low must not exceed high")


@dataclass(frozen=True, slots=True)
class SmartMoneyLiquidity:
    kind: str
    scope: str
    price: Decimal
    first_swing_at: datetime
    second_swing_at: datetime
    confirmed_at: datetime
    status: str
    tolerance: Decimal

    def __post_init__(self) -> None:
        require_aware_datetime(self.first_swing_at, field_name="first_swing_at")
        require_aware_datetime(self.second_swing_at, field_name="second_swing_at")
        require_aware_datetime(self.confirmed_at, field_name="confirmed_at")


@dataclass(frozen=True, slots=True)
class SmartMoneyAnalysis:
    trend: str
    atr_200_ready: bool
    limitations: tuple[str, ...]
    swings: tuple[SmartMoneySwing, ...]
    structure_events: tuple[SmartMoneyStructureEvent, ...]
    order_blocks: tuple[SmartMoneyZone, ...]
    fair_value_gaps: tuple[SmartMoneyZone, ...]
    liquidity_levels: tuple[SmartMoneyLiquidity, ...]
    value_zones: tuple[SmartMoneyZone, ...]
    algorithm_version: str = "tp_smc_v1"
    reference: str = "LuxAlgo SMC defaults; independent Python implementation"
    historically_validated: bool = False


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
    smart_money: SmartMoneyAnalysis | None = None

    def __post_init__(self) -> None:
        require_aware_datetime(self.bar_as_of, field_name="bar_as_of")


@dataclass(frozen=True, slots=True)
class TechnicalAnalysis:
    instrument_id: str
    market: Market
    as_of: datetime
    timeframes: tuple[TechnicalTimeframe, ...]
    price_basis: str
    algorithm_version: str = "tp_technical_v3"
    indicator_backend: str = "TA-Lib"
    structure_method: str = "swing_cluster_atr_v1"
    historically_validated: bool = False

    def __post_init__(self) -> None:
        require_aware_datetime(self.as_of, field_name="as_of")
