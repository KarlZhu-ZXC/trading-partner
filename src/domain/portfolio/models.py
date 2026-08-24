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
    AccountActivityCoverageStatus,
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
    ActivityAnnotationStatus,
    TradeCycleClassification,
    TradeCycleQuality,
    TradeCycleStatus,
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
    """One normalized broker activity leg in its native currency.

    The historical class name is retained for API compatibility. Instrument-less
    cash activities and explicitly unavailable fees make this the canonical A0
    activity ledger record rather than a security-trade-only model.
    """

    provider_transaction_id: str
    account_ref: str
    provider: VendorId
    instrument_id: str | None
    kind: AccountTransactionKind
    side: AccountTransactionSide | None
    quantity: Decimal | None
    price: Decimal | None
    fees: Decimal | None
    currency: str
    occurred_at: datetime
    cash_amount: Decimal | None = None
    source_type: str = "legacy"
    mapping_version: str = "account_activity_v1"

    def __post_init__(self) -> None:
        _text(self.provider_transaction_id, "provider_transaction_id", 256)
        _text(self.account_ref, "account_ref", 128)
        if not isinstance(self.provider, VendorId):
            raise DataContractError("transaction provider is invalid")
        if self.instrument_id is not None:
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
        _decimal(self.cash_amount, "cash_amount")
        if self.kind is AccountTransactionKind.TRADE:
            if self.instrument_id is None:
                raise DataContractError("trade transaction requires instrument_id")
            if self.quantity is None or self.quantity == 0:
                raise DataContractError("trade transaction quantity must be positive")
        if self.instrument_id is None and self.cash_amount is None:
            raise DataContractError("cash activity requires cash_amount")
        _text(self.currency, "currency", 16)
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        _text(self.source_type, "source_type", 128)
        _text(self.mapping_version, "mapping_version", 64)

    @property
    def transaction_key(self) -> tuple[VendorId, str, str]:
        """Exact natural key shared by activity annotations."""

        return (self.provider, self.account_ref, self.provider_transaction_id)


@dataclass(frozen=True, slots=True)
class ActivityAnnotation:
    """Append-only human annotation for one normalized account transaction.

    The broker fact is deliberately represented only by its exact natural key;
    no annotation field can alter the immutable ``AccountTransaction`` row.
    Each revision repeats that key and carries a monotonically increasing
    version.  ``TransactionDecisionLink`` is a compatibility alias for this
    model (the first implementation used both names for the same boundary).
    """

    annotation_id: str
    provider: VendorId
    account_ref: str
    provider_transaction_id: str
    version: int
    status: ActivityAnnotationStatus
    actor: str
    authorization_note: str
    idempotency_key: str
    created_at: datetime
    decision_id: str | None = None
    trade_plan_id: str | None = None
    trade_plan_version: int | None = None
    subject_id: str | None = None
    note: str | None = None
    classification: TradeCycleClassification | None = None
    order_intent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.annotation_id.startswith("activity_annotation_"):
            raise DataContractError("annotation_id must use activity_annotation_ prefix")
        if not isinstance(self.provider, VendorId):
            raise DataContractError("annotation provider is invalid")
        _text(self.account_ref, "account_ref", 128)
        _text(self.provider_transaction_id, "provider_transaction_id", 256)
        if type(self.version) is not int or self.version < 1:
            raise DataContractError("annotation version must be positive")
        if not isinstance(self.status, ActivityAnnotationStatus):
            raise DataContractError("annotation status is invalid")
        if self.classification is not None and not isinstance(
            self.classification, TradeCycleClassification
        ):
            raise DataContractError("annotation classification is invalid")
        for field_name, value, maximum in (
            ("decision_id", self.decision_id, 128),
            ("trade_plan_id", self.trade_plan_id, 128),
            ("subject_id", self.subject_id, 128),
            ("order_intent_id", self.order_intent_id, 128),
        ):
            if value is not None:
                _text(value, field_name, maximum)
        if (self.trade_plan_id is None) != (self.trade_plan_version is None):
            raise DataContractError("trade_plan_id and trade_plan_version must be paired")
        if self.trade_plan_version is not None and (
            type(self.trade_plan_version) is not int or self.trade_plan_version < 1
        ):
            raise DataContractError("trade_plan_version must be positive")
        if self.note is not None:
            _text(self.note, "note", 2_000)
        _text(self.actor, "actor", 128)
        _text(self.authorization_note, "authorization_note", 4_000)
        _text(self.idempotency_key, "idempotency_key", 200)
        require_aware_datetime(self.created_at, field_name="created_at")

        has_link = self.decision_id is not None or self.trade_plan_id is not None
        if self.status is ActivityAnnotationStatus.LINKED_DECISION_PLAN and not has_link:
            raise DataContractError(
                "LINKED_DECISION_PLAN requires a decision_id or exact Trade Plan version"
            )
        if self.status is not ActivityAnnotationStatus.LINKED_DECISION_PLAN and has_link:
            raise DataContractError(
                "non-linked activity annotation cannot carry decision or Trade Plan links"
            )
        if has_link and self.subject_id is None:
            raise DataContractError("linked activity annotation requires subject_id")

    @property
    def link_status(self) -> ActivityAnnotationStatus:
        """Compatibility spelling for callers using TransactionDecisionLink."""

        return self.status

    @property
    def transaction_key(self) -> tuple[VendorId, str, str]:
        return (self.provider, self.account_ref, self.provider_transaction_id)

    @property
    def revision_id(self) -> str:
        return self.annotation_id


# The two terms intentionally describe one minimal append-only record.
TransactionDecisionLink = ActivityAnnotation


@dataclass(frozen=True, slots=True)
class AccountActivityCoverageReceipt:
    receipt_id: str
    provider: VendorId
    account_ref: str
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
    effective_end: datetime
    earliest_event_at: datetime | None
    latest_event_at: datetime | None
    event_count: int
    inserted_count: int
    duplicate_count: int
    snapshot_count: int
    earliest_snapshot_at: datetime | None
    latest_snapshot_at: datetime | None
    mapping_version: str
    supported_kinds: tuple[AccountTransactionKind, ...]
    unavailable_kinds: tuple[AccountTransactionKind, ...]
    status: AccountActivityCoverageStatus
    gap_codes: tuple[str, ...]
    fetched_at: datetime

    def __post_init__(self) -> None:
        _text(self.receipt_id, "receipt_id", 128)
        if not isinstance(self.provider, VendorId):
            raise DataContractError("coverage provider is invalid")
        _text(self.account_ref, "account_ref", 128)
        for field_name in (
            "requested_start",
            "requested_end",
            "effective_start",
            "effective_end",
            "fetched_at",
        ):
            require_aware_datetime(getattr(self, field_name), field_name=field_name)
        for field_name in (
            "earliest_event_at",
            "latest_event_at",
            "earliest_snapshot_at",
            "latest_snapshot_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_aware_datetime(value, field_name=field_name)
        if self.requested_start > self.requested_end:
            raise DataContractError("coverage requested window is invalid")
        if self.effective_start > self.effective_end:
            raise DataContractError("coverage effective window is invalid")
        if self.effective_start < self.requested_start or self.effective_end > self.requested_end:
            raise DataContractError("coverage effective window exceeds requested window")
        for field_name in ("event_count", "inserted_count", "duplicate_count", "snapshot_count"):
            if getattr(self, field_name) < 0:
                raise DataContractError(f"{field_name} must be nonnegative")
        if self.inserted_count + self.duplicate_count != self.event_count:
            raise DataContractError("coverage event counts do not reconcile")
        if self.event_count == 0 and (
            self.earliest_event_at is not None or self.latest_event_at is not None
        ):
            raise DataContractError("empty coverage must not have event bounds")
        if self.event_count > 0 and (
            self.earliest_event_at is None or self.latest_event_at is None
        ):
            raise DataContractError("non-empty coverage requires event bounds")
        if self.snapshot_count == 0 and (
            self.earliest_snapshot_at is not None or self.latest_snapshot_at is not None
        ):
            raise DataContractError("empty snapshot coverage must not have bounds")
        if self.snapshot_count > 0 and (
            self.earliest_snapshot_at is None or self.latest_snapshot_at is None
        ):
            raise DataContractError("snapshot coverage requires bounds")
        _text(self.mapping_version, "mapping_version", 64)
        if len(self.supported_kinds) != len(set(self.supported_kinds)):
            raise DataContractError("supported_kinds must be unique")
        if len(self.unavailable_kinds) != len(set(self.unavailable_kinds)):
            raise DataContractError("unavailable_kinds must be unique")
        if set(self.supported_kinds) & set(self.unavailable_kinds):
            raise DataContractError("coverage kind sets must be disjoint")
        if not isinstance(self.status, AccountActivityCoverageStatus):
            raise DataContractError("coverage status is invalid")
        _warnings(self.gap_codes)


@dataclass(frozen=True, slots=True)
class ProviderAccountActivityCoverage:
    account_ref: str
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
    effective_end: datetime
    mapping_version: str
    supported_kinds: tuple[AccountTransactionKind, ...]
    unavailable_kinds: tuple[AccountTransactionKind, ...]
    gap_codes: tuple[str, ...]
    truncated: bool

    def __post_init__(self) -> None:
        _text(self.account_ref, "account_ref", 128)
        for field_name in (
            "requested_start",
            "requested_end",
            "effective_start",
            "effective_end",
        ):
            require_aware_datetime(getattr(self, field_name), field_name=field_name)
        if self.requested_start > self.requested_end:
            raise DataContractError("provider coverage requested window is invalid")
        if self.effective_start > self.effective_end:
            raise DataContractError("provider coverage effective window is invalid")
        if self.effective_start < self.requested_start or self.effective_end > self.requested_end:
            raise DataContractError("provider coverage exceeds requested window")
        _text(self.mapping_version, "mapping_version", 64)
        if set(self.supported_kinds) & set(self.unavailable_kinds):
            raise DataContractError("provider coverage kind sets must be disjoint")
        _warnings(self.gap_codes)


@dataclass(frozen=True, slots=True)
class AccountActivityBatch:
    transactions: tuple[AccountTransaction, ...]
    coverage: tuple[ProviderAccountActivityCoverage, ...]

    def __post_init__(self) -> None:
        if any(not isinstance(item, AccountTransaction) for item in self.transactions):
            raise DataContractError("activity batch transactions are invalid")
        if any(not isinstance(item, ProviderAccountActivityCoverage) for item in self.coverage):
            raise DataContractError("activity batch coverage is invalid")


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
            self.missing_classification_instrument_ids + self.missing_valuation_instrument_ids
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


@dataclass(frozen=True, slots=True)
class TradeCycle:
    """One deterministic, long-only projection over normalized trade activities."""

    cycle_id: str
    account_ref: str
    provider: VendorId
    instrument_id: str | None
    currency: str | None
    activity_ids: tuple[str, ...]
    opened_at: datetime | None
    closed_at: datetime | None
    status: TradeCycleStatus
    classification: TradeCycleClassification
    opening_count: int
    add_count: int
    reduce_count: int
    ending_quantity: Decimal | None
    gross_realized_pnl: Decimal | None
    net_realized_pnl: Decimal | None
    maximum_deployed_capital: Decimal | None
    holding_duration_seconds: int | None
    reentry_of_cycle_id: str | None
    quality: TradeCycleQuality
    warning_codes: tuple[str, ...]
    algorithm_version: str = "trade_cycle_v1"

    def __post_init__(self) -> None:
        _text(self.cycle_id, "cycle_id", 160)
        _text(self.account_ref, "account_ref", 128)
        if not isinstance(self.provider, VendorId):
            raise DataContractError("trade cycle provider is invalid")
        if self.instrument_id is not None:
            parse_instrument_id(self.instrument_id)
        if self.currency is not None:
            _text(self.currency, "currency", 16)
        if not isinstance(self.activity_ids, tuple) or not self.activity_ids:
            raise DataContractError("trade cycle activity_ids must be non-empty")
        _warnings(self.activity_ids)
        for field_name in ("opened_at", "closed_at"):
            value = getattr(self, field_name)
            if value is not None:
                require_aware_datetime(value, field_name=field_name)
        if (
            self.opened_at is not None
            and self.closed_at is not None
            and self.closed_at < self.opened_at
        ):
            raise DataContractError("trade cycle closed_at precedes opened_at")
        if not isinstance(self.status, TradeCycleStatus):
            raise DataContractError("trade cycle status is invalid")
        if not isinstance(self.classification, TradeCycleClassification):
            raise DataContractError("trade cycle classification is invalid")
        if not isinstance(self.quality, TradeCycleQuality):
            raise DataContractError("trade cycle quality is invalid")
        for field_name in ("opening_count", "add_count", "reduce_count"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise DataContractError(f"{field_name} must be a nonnegative int")
        _decimal(self.ending_quantity, "ending_quantity", nonnegative=True)
        _decimal(self.gross_realized_pnl, "gross_realized_pnl")
        _decimal(self.net_realized_pnl, "net_realized_pnl")
        _decimal(self.maximum_deployed_capital, "maximum_deployed_capital", nonnegative=True)
        if self.holding_duration_seconds is not None and (
            type(self.holding_duration_seconds) is not int or self.holding_duration_seconds < 0
        ):
            raise DataContractError("holding_duration_seconds must be a nonnegative int")
        if self.reentry_of_cycle_id is not None:
            _text(self.reentry_of_cycle_id, "reentry_of_cycle_id", 160)
        _warnings(self.warning_codes)
        _text(self.algorithm_version, "algorithm_version", 64)


@dataclass(frozen=True, slots=True)
class TradeCycleProjection:
    """Rebuildable collection of trade cycles and ledger-quality facts."""

    cycles: tuple[TradeCycle, ...]
    status: TradeCycleQuality
    coverage_status: AccountActivityCoverageStatus
    warning_codes: tuple[str, ...]
    algorithm_version: str = "trade_cycle_v1"

    def __post_init__(self) -> None:
        if not isinstance(self.cycles, tuple) or any(
            not isinstance(item, TradeCycle) for item in self.cycles
        ):
            raise DataContractError("trade cycle projection cycles are invalid")
        if not isinstance(self.status, TradeCycleQuality):
            raise DataContractError("trade cycle projection status is invalid")
        if not isinstance(self.coverage_status, AccountActivityCoverageStatus):
            raise DataContractError("trade cycle projection coverage status is invalid")
        _warnings(self.warning_codes)
        _text(self.algorithm_version, "algorithm_version", 64)
