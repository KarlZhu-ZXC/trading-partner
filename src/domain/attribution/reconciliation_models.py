"""Immutable broker-statement facts used for independent A1 reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.common.errors import DataContractError


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
