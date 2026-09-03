"""Immutable native-currency performance-attribution results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id


def _decimal(value: Decimal | None, field: str) -> None:
    if value is not None and (type(value) is not Decimal or not value.is_finite()):
        raise DataContractError(f"{field} must be a finite Decimal")


def _codes(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise DataContractError(f"{field} must be unique")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise DataContractError(f"{field} contains an invalid code")


@dataclass(frozen=True, slots=True)
class PositionBasisCheckpoint:
    """Owner-verified broker position basis that replaces open FIFO lots only."""

    checkpoint_id: str
    provider: VendorId
    account_ref: str
    instrument_id: str
    currency: str
    effective_at: datetime
    quantity: Decimal
    total_cost_basis: Decimal
    source_type: str
    source_ref: str
    source_document_sha256: str | None = None
    replaces_activity_id: str | None = None

    def __post_init__(self) -> None:
        if not self.checkpoint_id.strip() or not self.account_ref.strip():
            raise DataContractError("basis checkpoint identity is invalid")
        if not isinstance(self.provider, VendorId):
            raise DataContractError("basis checkpoint provider is invalid")
        parse_instrument_id(self.instrument_id)
        if not self.currency.strip():
            raise DataContractError("basis checkpoint currency is blank")
        require_aware_datetime(self.effective_at, field_name="effective_at")
        _decimal(self.quantity, "quantity")
        _decimal(self.total_cost_basis, "total_cost_basis")
        if self.quantity <= 0 or self.total_cost_basis < 0:
            raise DataContractError("basis checkpoint quantity and cost are invalid")
        if not self.source_type.strip() or not self.source_ref.strip():
            raise DataContractError("basis checkpoint source is invalid")
        if self.source_document_sha256 is not None:
            value = self.source_document_sha256
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise DataContractError("basis checkpoint document hash is invalid")
        if self.replaces_activity_id is not None and not self.replaces_activity_id.strip():
            raise DataContractError("basis checkpoint replacement activity is invalid")


@dataclass(frozen=True, slots=True)
class InstrumentPerformance:
    instrument_id: str
    currency: str
    ending_quantity: Decimal
    open_cost_basis: Decimal
    realized_pnl_before_fees: Decimal | None
    realized_pnl_after_fees: Decimal | None
    unrealized_pnl_before_fees: Decimal | None
    broker_reported_unrealized_pnl: Decimal | None
    broker_reported_realized_pnl: Decimal | None
    dividend_income: Decimal | None
    net_trading_pnl: Decimal | None
    total_pnl: Decimal | None
    known_fees: Decimal
    fees_complete: bool
    matched_quantity: Decimal
    activity_ids: tuple[str, ...]
    basis_checkpoint_ids: tuple[str, ...]
    snapshot_id: str | None
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        if not self.currency.strip():
            raise DataContractError("performance currency is blank")
        for field in (
            "ending_quantity",
            "open_cost_basis",
            "realized_pnl_before_fees",
            "realized_pnl_after_fees",
            "unrealized_pnl_before_fees",
            "broker_reported_unrealized_pnl",
            "broker_reported_realized_pnl",
            "dividend_income",
            "net_trading_pnl",
            "total_pnl",
            "known_fees",
            "matched_quantity",
        ):
            _decimal(getattr(self, field), field)
        if self.open_cost_basis < 0 or self.known_fees < 0 or self.matched_quantity < 0:
            raise DataContractError("performance basis, fees, and matched quantity are nonnegative")
        _codes(self.activity_ids, "activity_ids")
        _codes(self.basis_checkpoint_ids, "basis_checkpoint_ids")
        _codes(self.warning_codes, "warning_codes")


@dataclass(frozen=True, slots=True)
class AccountPerformance:
    account_ref: str
    provider: VendorId
    currency: str
    cost_basis_method: CostBasisMethod
    snapshot_id: str | None
    snapshot_as_of: datetime | None
    realized_pnl_before_fees: Decimal | None
    realized_pnl_after_fees: Decimal | None
    unrealized_pnl_before_fees: Decimal | None
    broker_reported_unrealized_pnl: Decimal | None
    broker_reported_realized_pnl: Decimal | None
    dividends: Decimal
    interest: Decimal
    known_fees: Decimal
    fees_complete: bool
    net_external_cash_flow: Decimal
    instruments: tuple[InstrumentPerformance, ...]
    status: AttributionStatus
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.account_ref.strip() or not self.currency.strip():
            raise DataContractError("account performance identity is invalid")
        if not isinstance(self.provider, VendorId) or not isinstance(
            self.cost_basis_method, CostBasisMethod
        ):
            raise DataContractError("account performance enum is invalid")
        if self.snapshot_as_of is not None:
            require_aware_datetime(self.snapshot_as_of, field_name="snapshot_as_of")
        for field in (
            "realized_pnl_before_fees",
            "realized_pnl_after_fees",
            "unrealized_pnl_before_fees",
            "broker_reported_unrealized_pnl",
            "broker_reported_realized_pnl",
            "dividends",
            "interest",
            "known_fees",
            "net_external_cash_flow",
        ):
            _decimal(getattr(self, field), field)
        if self.known_fees < 0:
            raise DataContractError("known_fees must be nonnegative")
        if not isinstance(self.status, AttributionStatus):
            raise DataContractError("attribution status is invalid")
        _codes(self.warning_codes, "warning_codes")


@dataclass(frozen=True, slots=True)
class PerformanceAttribution:
    start: datetime
    end: datetime
    cost_basis_method: CostBasisMethod
    accounts: tuple[AccountPerformance, ...]
    status: AttributionStatus
    warning_codes: tuple[str, ...]
    algorithm_version: str = "performance_attribution_v2"

    def __post_init__(self) -> None:
        require_aware_datetime(self.start, field_name="start")
        require_aware_datetime(self.end, field_name="end")
        if self.start > self.end:
            raise DataContractError("performance window is invalid")
        if not isinstance(self.cost_basis_method, CostBasisMethod):
            raise DataContractError("cost basis method is invalid")
        if not isinstance(self.status, AttributionStatus):
            raise DataContractError("performance status is invalid")
        _codes(self.warning_codes, "warning_codes")
        if not self.algorithm_version.strip():
            raise DataContractError("algorithm_version is blank")
