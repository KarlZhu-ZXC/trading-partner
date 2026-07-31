"""Closed operational DTOs for broker-statement reconciliation preparation."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from application.dto.market import DecimalWire
from domain.attribution.reconciliation_models import (
    BrokerRealizedAccountSummary,
    BrokerRealizedInstrumentReconciliation,
    BrokerRealizedReconciliation,
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


class BrokerRealizedInstrumentReconciliationDTO(_DTO):
    symbol: str
    instrument_id: str | None
    statement_lot_count: int
    statement_proceeds: DecimalWire
    statement_cost_basis: DecimalWire | None
    statement_realized_pnl: DecimalWire | None
    system_realized_pnl_before_fees: DecimalWire | None
    system_realized_pnl_after_fees: DecimalWire | None
    residual: DecimalWire | None
    absolute_residual: DecimalWire | None
    residual_codes: tuple[str, ...]

    @classmethod
    def from_domain(
        cls, value: BrokerRealizedInstrumentReconciliation
    ) -> BrokerRealizedInstrumentReconciliationDTO:
        return cls.model_validate(value)


class BrokerRealizedReconciliationDTO(_DTO):
    source_sha256: str
    statement_account_ref: str
    durable_account_ref: str
    period_start: date
    period_end: date
    currency: str
    cost_basis_method: str
    tolerance: DecimalWire
    statement_lot_count: int
    statement_total_proceeds: DecimalWire
    statement_total_cost_basis: DecimalWire | None
    statement_total_realized_pnl: DecimalWire | None
    system_total_realized_pnl_before_fees: DecimalWire | None
    system_total_realized_pnl_after_fees: DecimalWire | None
    residual: DecimalWire | None
    absolute_residual: DecimalWire | None
    attribution_status: str
    reconciliation_status: str
    comparisons: tuple[BrokerRealizedInstrumentReconciliationDTO, ...]
    residual_codes: tuple[str, ...]
    attribution_warning_codes: tuple[str, ...]
    generated_at: datetime
    algorithm_version: str
    draft_artifact: str | None = None

    @classmethod
    def from_domain(
        cls,
        value: BrokerRealizedReconciliation,
        *,
        draft_artifact: str | None = None,
    ) -> BrokerRealizedReconciliationDTO:
        return cls(
            source_sha256=value.source_sha256,
            statement_account_ref=value.statement_account_ref,
            durable_account_ref=value.durable_account_ref,
            period_start=value.period_start,
            period_end=value.period_end,
            currency=value.currency,
            cost_basis_method=value.cost_basis_method.value,
            tolerance=value.tolerance,
            statement_lot_count=value.statement_lot_count,
            statement_total_proceeds=value.statement_total_proceeds,
            statement_total_cost_basis=value.statement_total_cost_basis,
            statement_total_realized_pnl=value.statement_total_realized_pnl,
            system_total_realized_pnl_before_fees=(
                value.system_total_realized_pnl_before_fees
            ),
            system_total_realized_pnl_after_fees=value.system_total_realized_pnl_after_fees,
            residual=value.residual,
            absolute_residual=value.absolute_residual,
            attribution_status=value.attribution_status.value,
            reconciliation_status=value.reconciliation_status,
            comparisons=tuple(
                BrokerRealizedInstrumentReconciliationDTO.from_domain(item)
                for item in value.comparisons
            ),
            residual_codes=value.residual_codes,
            attribution_warning_codes=value.attribution_warning_codes,
            generated_at=value.generated_at,
            algorithm_version=value.algorithm_version,
            draft_artifact=draft_artifact,
        )
