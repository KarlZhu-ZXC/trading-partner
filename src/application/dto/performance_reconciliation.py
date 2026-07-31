"""Closed operational DTOs for broker-statement reconciliation preparation."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from application.dto.market import DecimalWire
from domain.attribution.reconciliation_models import (
    BrokerRealizedAccountSummary,
    BrokerRealizedStatement,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class BrokerRealizedAccountSummaryDTO(_DTO):
    statement_account_ref: str
    lot_count: int
    first_closed_date: date
    last_closed_date: date
    total_proceeds: DecimalWire
    total_cost_basis: DecimalWire | None
    total_realized_pnl: DecimalWire | None
    total_long_term_pnl: DecimalWire | None
    total_short_term_pnl: DecimalWire | None
    total_wash_sale_disallowed: DecimalWire | None

    @classmethod
    def from_domain(
        cls, value: BrokerRealizedAccountSummary
    ) -> BrokerRealizedAccountSummaryDTO:
        return cls.model_validate(value)


class BrokerRealizedStatementDTO(_DTO):
    source_sha256: str
    source_byte_count: int
    currency: str
    accounts: tuple[BrokerRealizedAccountSummaryDTO, ...]
    lot_count: int
    warning_codes: tuple[str, ...]
    parser_version: str

    @classmethod
    def from_domain(cls, value: BrokerRealizedStatement) -> BrokerRealizedStatementDTO:
        return cls(
            source_sha256=value.source_sha256,
            source_byte_count=value.source_byte_count,
            currency=value.currency,
            accounts=tuple(
                BrokerRealizedAccountSummaryDTO.from_domain(item) for item in value.accounts
            ),
            lot_count=len(value.lots),
            warning_codes=value.warning_codes,
            parser_version=value.parser_version,
        )
