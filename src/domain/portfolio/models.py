"""Immutable read-only account and deterministic portfolio facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)

FROZEN_PORTFOLIO_MODEL_NAMES: tuple[str, ...] = (
    "AccountSnapshot",
    "PortfolioSnapshot",
)


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be a bounded non-blank string")
    return value


def _decimal(value: object, field: str, *, nonnegative: bool = False) -> None:
    if value is None:
        return
    if type(value) is not Decimal or not value.is_finite():
        raise DataContractError(f"{field} must be finite Decimal")
    if nonnegative and value < 0:
        raise DataContractError(f"{field} must be nonnegative")


def _warnings(value: tuple[str, ...]) -> None:
    if not isinstance(value, tuple) or len(value) != len(set(value)):
        raise DataContractError("warning_codes must be a unique tuple")
    for item in value:
        _text(item, "warning_code", 128)


@dataclass(frozen=True, slots=True)
class AccountPosition:
    instrument_id: str
    side: AccountPositionSide
    quantity: Decimal
    sellable_quantity: Decimal | None
    average_cost: Decimal | None
    diluted_cost: Decimal | None
    market_price: Decimal | None
    market_price_at: datetime | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    currency: str

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        if not isinstance(self.side, AccountPositionSide):
            raise DataContractError("side must be AccountPositionSide")
        _decimal(self.quantity, "quantity", nonnegative=True)
        if self.quantity == 0:
            raise DataContractError("quantity must be positive")
        _decimal(self.sellable_quantity, "sellable_quantity", nonnegative=True)
        _decimal(self.average_cost, "average_cost", nonnegative=True)
        _decimal(self.diluted_cost, "diluted_cost", nonnegative=True)
        _decimal(self.market_price, "market_price", nonnegative=True)
        _decimal(self.market_value, "market_value")
        _decimal(self.unrealized_pnl, "unrealized_pnl")
        _decimal(self.realized_pnl, "realized_pnl")
        if self.market_price_at is not None:
            require_aware_datetime(self.market_price_at, field_name="market_price_at")
        if self.market_price is not None and self.market_price_at is None:
            raise DataContractError("market_price requires market_price_at")
        _text(self.currency, "currency", 16)


@dataclass(frozen=True, slots=True)
class AccountOpenOrder:
    provider_order_id: str
    instrument_id: str
    side: AccountOpenOrderSide
    status: AccountOpenOrderStatus
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    submitted_at: datetime | None

    def __post_init__(self) -> None:
        _text(self.provider_order_id, "provider_order_id", 256)
        parse_instrument_id(self.instrument_id)
        if not isinstance(self.side, AccountOpenOrderSide) or not isinstance(
            self.status, AccountOpenOrderStatus
        ):
            raise DataContractError("order enums are invalid")
        _decimal(self.quantity, "quantity", nonnegative=True)
        _decimal(self.filled_quantity, "filled_quantity", nonnegative=True)
        _decimal(self.limit_price, "limit_price", nonnegative=True)
        if self.filled_quantity > self.quantity:
            raise DataContractError("filled_quantity exceeds quantity")
        if self.submitted_at is not None:
            require_aware_datetime(self.submitted_at, field_name="submitted_at")


@dataclass(frozen=True, slots=True)
class AccountTransaction:
    provider_transaction_id: str
    account_ref: str
    provider: VendorId
    instrument_id: str
    kind: AccountTransactionKind
    side: AccountTransactionSide | None
    quantity: Decimal
    price: Decimal | None
    fees: Decimal
    currency: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _text(self.provider_transaction_id, "provider_transaction_id", 256)
        _text(self.account_ref, "account_ref", 128)
        if not isinstance(self.provider, VendorId):
            raise DataContractError("transaction provider is invalid")
        parse_instrument_id(self.instrument_id)
        if not isinstance(self.kind, AccountTransactionKind):
            raise DataContractError("transaction kind is invalid")
        if self.side is not None and not isinstance(self.side, AccountTransactionSide):
            raise DataContractError("transaction side is invalid")
        if self.kind is AccountTransactionKind.TRADE and self.side is None:
            raise DataContractError("trade transaction requires side")
        if self.kind is not AccountTransactionKind.TRADE and self.side is not None:
            raise DataContractError("non-trade transaction must not have side")
        _decimal(self.quantity, "quantity", nonnegative=True)
        _decimal(self.price, "price", nonnegative=True)
        _decimal(self.fees, "fees", nonnegative=True)
        if self.kind is AccountTransactionKind.TRADE and self.quantity == 0:
            raise DataContractError("trade transaction quantity must be positive")
        _text(self.currency, "currency", 16)
        require_aware_datetime(self.occurred_at, field_name="occurred_at")


@dataclass(frozen=True, slots=True)
class PortfolioRiskMetric:
    instrument_id: str
    benchmark_instrument_id: str
    aligned_observations: int
    correlation: Decimal | None
    beta: Decimal | None
    missing_reason: str | None
    algorithm_version: str = "daily_return_risk_v1"

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        parse_instrument_id(self.benchmark_instrument_id)
        if self.aligned_observations < 0:
            raise DataContractError("aligned_observations must be nonnegative")
        _decimal(self.correlation, "correlation")
        _decimal(self.beta, "beta")
        if self.correlation is not None and not Decimal("-1") <= self.correlation <= Decimal("1"):
            raise DataContractError("correlation must be in [-1,1]")
        if (self.correlation is None) != (self.beta is None):
            raise DataContractError("correlation and beta must be present together")
        if self.correlation is None:
            _text(self.missing_reason, "missing_reason", 256)
        elif self.missing_reason is not None:
            raise DataContractError("available metric must not have missing_reason")
        _text(self.algorithm_version, "algorithm_version", 64)


@dataclass(frozen=True, slots=True)
class PortfolioClassification:
    instrument_id: str
    industry: str | None
    themes: tuple[str, ...]

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        if self.industry is not None:
            _text(self.industry, "industry", 128)
        if len(self.themes) != len(set(self.themes)):
            raise DataContractError("classification themes must be unique")
        for theme in self.themes:
            _text(theme, "theme", 128)


@dataclass(frozen=True, slots=True)
class PortfolioEnrichedExposure:
    dimension: str
    key: str
    currency: str
    value: Decimal
    weight_within_currency: Decimal

    def __post_init__(self) -> None:
        if self.dimension not in {"industry", "theme"}:
            raise DataContractError("enriched exposure dimension is invalid")
        _text(self.key, "key", 128)
        _text(self.currency, "currency", 16)
        _decimal(self.value, "value", nonnegative=True)
        _decimal(self.weight_within_currency, "weight_within_currency", nonnegative=True)
        if self.weight_within_currency > 1:
            raise DataContractError("weight_within_currency must be in [0,1]")


@dataclass(frozen=True, slots=True)
class PortfolioEnrichment:
    exposures: tuple[PortfolioEnrichedExposure, ...]
    missing_classification_instrument_ids: tuple[str, ...]
    missing_valuation_instrument_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(not isinstance(item, PortfolioEnrichedExposure) for item in self.exposures):
            raise DataContractError("enriched exposures are invalid")
        for instrument_id in (
            self.missing_classification_instrument_ids
            + self.missing_valuation_instrument_ids
        ):
            parse_instrument_id(instrument_id)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    snapshot_id: str
    account_ref: str
    provider: VendorId
    environment: AccountEnvironment
    base_currency: str
    account_as_of: datetime
    fetched_at: datetime
    cash: Decimal | None
    buying_power: Decimal | None
    net_assets: Decimal | None
    margin_used: Decimal | None
    positions: tuple[AccountPosition, ...]
    open_orders: tuple[AccountOpenOrder, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.snapshot_id, "snapshot_id", 128)
        _text(self.account_ref, "account_ref", 128)
        if not isinstance(self.provider, VendorId) or not isinstance(
            self.environment, AccountEnvironment
        ):
            raise DataContractError("account provider/environment is invalid")
        _text(self.base_currency, "base_currency", 16)
        require_aware_datetime(self.account_as_of, field_name="account_as_of")
        require_aware_datetime(self.fetched_at, field_name="fetched_at")
        if self.account_as_of > self.fetched_at:
            raise DataContractError("account_as_of must be <= fetched_at")
        for name in ("cash", "buying_power", "net_assets", "margin_used"):
            _decimal(getattr(self, name), name)
        if not isinstance(self.positions, tuple) or not isinstance(self.open_orders, tuple):
            raise DataContractError("account collections must be tuples")
        if any(not isinstance(item, AccountPosition) for item in self.positions):
            raise DataContractError("positions contain invalid values")
        if len({item.instrument_id for item in self.positions}) != len(self.positions):
            raise DataContractError("position instrument_id must be unique")
        if any(not isinstance(item, AccountOpenOrder) for item in self.open_orders):
            raise DataContractError("open_orders contain invalid values")
        if type(self.degraded) is not bool:
            raise DataContractError("degraded must be bool")
        _warnings(self.warning_codes)


@dataclass(frozen=True, slots=True)
class PortfolioExposure:
    dimension: str
    key: str
    value: Decimal
    weight: Decimal | None

    def __post_init__(self) -> None:
        _text(self.dimension, "dimension", 32)
        _text(self.key, "key", 128)
        _decimal(self.value, "value")
        _decimal(self.weight, "weight")
        if self.weight is not None and not Decimal(0) <= self.weight <= Decimal(1):
            raise DataContractError("weight must be in [0,1]")


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    portfolio_snapshot_id: str
    account_snapshot_ids: tuple[str, ...]
    as_of: datetime
    base_currency: str
    total_value: Decimal | None
    exposures: tuple[PortfolioExposure, ...]
    missing_instrument_ids: tuple[str, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.portfolio_snapshot_id, "portfolio_snapshot_id", 128)
        if not isinstance(self.account_snapshot_ids, tuple) or not self.account_snapshot_ids:
            raise DataContractError("account_snapshot_ids must be non-empty tuple")
        require_aware_datetime(self.as_of, field_name="as_of")
        _text(self.base_currency, "base_currency", 16)
        _decimal(self.total_value, "total_value")
        if not isinstance(self.exposures, tuple) or any(
            not isinstance(item, PortfolioExposure) for item in self.exposures
        ):
            raise DataContractError("exposures are invalid")
        if not isinstance(self.missing_instrument_ids, tuple):
            raise DataContractError("missing_instrument_ids must be tuple")
        if type(self.degraded) is not bool:
            raise DataContractError("degraded must be bool")
        _warnings(self.warning_codes)


@dataclass(frozen=True, slots=True)
class PortfolioSimulation:
    before: PortfolioSnapshot
    after: PortfolioSnapshot
    added_instrument_id: str
    added_quantity: Decimal
    assumed_price: Decimal
    currency: str
    execution_effect: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.before, PortfolioSnapshot) or not isinstance(
            self.after, PortfolioSnapshot
        ):
            raise DataContractError("simulation snapshots are invalid")
        parse_instrument_id(self.added_instrument_id)
        _decimal(self.added_quantity, "added_quantity", nonnegative=True)
        _decimal(self.assumed_price, "assumed_price", nonnegative=True)
        _text(self.currency, "currency", 16)
        if self.execution_effect is not False:
            raise DataContractError("portfolio simulation must not execute")
