"""Immutable broker-statement facts used for independent A1 reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


def _text(value: object, field: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise DataContractError(f"{field} is invalid")
    return value


def _decimal(value: Decimal | None, field: str) -> None:
    if value is not None and (type(value) is not Decimal or not value.is_finite()):
        raise DataContractError(f"{field} must be a finite Decimal when present")


def _codes(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise DataContractError(f"{field} must be unique")
    for value in values:
        _text(value, field, max_length=100)


@dataclass(frozen=True, slots=True)
class BrokerRealizedLot:
    """One closed broker lot; missing official cost/P&L remains ``None``."""

    statement_account_ref: str
    symbol: str
    opened_date: date | None
    closed_date: date
    quantity: Decimal
    total_proceeds: Decimal
    cost_basis: Decimal | None
    realized_pnl: Decimal | None
    long_term_pnl: Decimal | None
    short_term_pnl: Decimal | None
    term: str | None
    cost_basis_method: str | None
    wash_sale_disallowed: Decimal | None

    def __post_init__(self) -> None:
        _text(self.statement_account_ref, "statement_account_ref")
        _text(self.symbol, "symbol", max_length=64)
        if type(self.closed_date) is not date:
            raise DataContractError("closed_date must be a date")
        if self.opened_date is not None and type(self.opened_date) is not date:
            raise DataContractError("opened_date must be a date when present")
        _decimal(self.quantity, "quantity")
        if self.quantity <= 0:
            raise DataContractError("quantity must be positive")
        for field in (
            "total_proceeds",
            "cost_basis",
            "realized_pnl",
            "long_term_pnl",
            "short_term_pnl",
            "wash_sale_disallowed",
        ):
            _decimal(getattr(self, field), field)
        if self.term is not None:
            _text(self.term, "term", max_length=64)
        if self.cost_basis_method is not None:
            _text(self.cost_basis_method, "cost_basis_method", max_length=64)


@dataclass(frozen=True, slots=True)
class BrokerRealizedAccountSummary:
    statement_account_ref: str
    lot_count: int
    first_closed_date: date
    last_closed_date: date
    total_proceeds: Decimal
    total_cost_basis: Decimal | None
    total_realized_pnl: Decimal | None
    total_long_term_pnl: Decimal | None
    total_short_term_pnl: Decimal | None
    total_wash_sale_disallowed: Decimal | None

    def __post_init__(self) -> None:
        _text(self.statement_account_ref, "statement_account_ref")
        if type(self.lot_count) is not int or self.lot_count < 1:
            raise DataContractError("lot_count must be positive")
        if type(self.first_closed_date) is not date or type(self.last_closed_date) is not date:
            raise DataContractError("summary dates must be dates")
        if self.first_closed_date > self.last_closed_date:
            raise DataContractError("summary close-date range is invalid")
        for field in (
            "total_proceeds",
            "total_cost_basis",
            "total_realized_pnl",
            "total_long_term_pnl",
            "total_short_term_pnl",
            "total_wash_sale_disallowed",
        ):
            _decimal(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class BrokerRealizedStatement:
    source_sha256: str
    source_byte_count: int
    currency: str
    accounts: tuple[BrokerRealizedAccountSummary, ...]
    lots: tuple[BrokerRealizedLot, ...]
    warning_codes: tuple[str, ...]
    parser_version: str = "schwab_realized_gain_loss_csv_v1"

    def __post_init__(self) -> None:
        if (
            len(self.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_sha256)
        ):
            raise DataContractError("source_sha256 is invalid")
        if type(self.source_byte_count) is not int or self.source_byte_count < 1:
            raise DataContractError("source_byte_count must be positive")
        _text(self.currency, "currency", max_length=3)
        if not self.accounts or not self.lots:
            raise DataContractError("broker statement must contain accounts and lots")
        account_refs = tuple(item.statement_account_ref for item in self.accounts)
        if len(account_refs) != len(set(account_refs)):
            raise DataContractError("statement account summaries must be unique")
        if any(item.statement_account_ref not in account_refs for item in self.lots):
            raise DataContractError("lot statement account has no summary")
        if sum(item.lot_count for item in self.accounts) != len(self.lots):
            raise DataContractError("account lot counts do not reconcile")
        _codes(self.warning_codes, "warning_codes")
        _text(self.parser_version, "parser_version")


@dataclass(frozen=True, slots=True)
class BrokerRealizedInstrumentReconciliation:
    """One symbol-level comparison between the statement and durable FIFO ledger."""

    symbol: str
    instrument_id: str | None
    statement_lot_count: int
    statement_proceeds: Decimal
    statement_cost_basis: Decimal | None
    statement_realized_pnl: Decimal | None
    system_realized_pnl_before_fees: Decimal | None
    system_realized_pnl_after_fees: Decimal | None
    residual: Decimal | None
    absolute_residual: Decimal | None
    residual_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.symbol, "symbol", max_length=64)
        if self.instrument_id is not None:
            _text(self.instrument_id, "instrument_id", max_length=128)
        if type(self.statement_lot_count) is not int or self.statement_lot_count < 0:
            raise DataContractError("statement_lot_count must be nonnegative")
        for field in (
            "statement_proceeds",
            "statement_cost_basis",
            "statement_realized_pnl",
            "system_realized_pnl_before_fees",
            "system_realized_pnl_after_fees",
            "residual",
            "absolute_residual",
        ):
            _decimal(getattr(self, field), field)
        if self.absolute_residual is not None and self.absolute_residual < 0:
            raise DataContractError("absolute_residual must be nonnegative")
        _codes(self.residual_codes, "residual_codes")


@dataclass(frozen=True, slots=True)
class BrokerRealizedReconciliation:
    """Immutable owner-only draft; it never constitutes automatic sign-off."""

    source_sha256: str
    statement_account_ref: str
    durable_account_ref: str
    period_start: date
    period_end: date
    currency: str
    cost_basis_method: CostBasisMethod
    tolerance: Decimal
    statement_lot_count: int
    statement_total_proceeds: Decimal
    statement_total_cost_basis: Decimal | None
    statement_total_realized_pnl: Decimal | None
    system_total_realized_pnl_before_fees: Decimal | None
    system_total_realized_pnl_after_fees: Decimal | None
    residual: Decimal | None
    absolute_residual: Decimal | None
    attribution_status: AttributionStatus
    reconciliation_status: str
    comparisons: tuple[BrokerRealizedInstrumentReconciliation, ...]
    residual_codes: tuple[str, ...]
    attribution_warning_codes: tuple[str, ...]
    generated_at: datetime
    algorithm_version: str = "schwab_realized_reconciliation_v1"

    def __post_init__(self) -> None:
        if (
            len(self.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_sha256)
        ):
            raise DataContractError("source_sha256 is invalid")
        _text(self.statement_account_ref, "statement_account_ref")
        _text(self.durable_account_ref, "durable_account_ref")
        if type(self.period_start) is not date or type(self.period_end) is not date:
            raise DataContractError("reconciliation period must use dates")
        if self.period_start > self.period_end:
            raise DataContractError("reconciliation period is invalid")
        _text(self.currency, "currency", max_length=3)
        if not isinstance(self.cost_basis_method, CostBasisMethod):
            raise DataContractError("cost_basis_method is invalid")
        _decimal(self.tolerance, "tolerance")
        if self.tolerance < 0:
            raise DataContractError("tolerance must be nonnegative")
        if type(self.statement_lot_count) is not int or self.statement_lot_count < 1:
            raise DataContractError("statement_lot_count must be positive")
        for field in (
            "statement_total_proceeds",
            "statement_total_cost_basis",
            "statement_total_realized_pnl",
            "system_total_realized_pnl_before_fees",
            "system_total_realized_pnl_after_fees",
            "residual",
            "absolute_residual",
        ):
            _decimal(getattr(self, field), field)
        if self.absolute_residual is not None and self.absolute_residual < 0:
            raise DataContractError("absolute_residual must be nonnegative")
        if not isinstance(self.attribution_status, AttributionStatus):
            raise DataContractError("attribution_status is invalid")
        if self.reconciliation_status not in {"INCOMPLETE", "MATCHED", "REVIEW_REQUIRED"}:
            raise DataContractError("reconciliation_status is invalid")
        if not self.comparisons:
            raise DataContractError("reconciliation comparisons are empty")
        _codes(self.residual_codes, "residual_codes")
        _codes(self.attribution_warning_codes, "attribution_warning_codes")
        require_aware_datetime(self.generated_at, field_name="generated_at")
        _text(self.algorithm_version, "algorithm_version")
