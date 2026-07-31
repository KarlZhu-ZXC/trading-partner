"""CLI-only independent comparison of broker statements and durable attribution."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from application.dto.performance_attribution import (
    AccountPerformanceDTO,
    InstrumentPerformanceDTO,
    PerformanceAttributionInput,
)
from application.dto.performance_reconciliation import (
    BrokerRealizedReconciliationDTO,
    BrokerRealizedStatementDTO,
)
from application.ports.broker_reconciliation_writer import BrokerReconciliationWriter
from application.ports.broker_statement_parser import BrokerStatementParser
from application.ports.clock import Clock
from application.ports.performance_attribution_reader import PerformanceAttributionReader
from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.attribution.reconciliation_models import (
    BrokerRealizedInstrumentReconciliation,
    BrokerRealizedLot,
    BrokerRealizedReconciliation,
)
from domain.common.enums import Market, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id


def _complete_sum(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), Decimal(0))


def _statement_symbol(value: str) -> str:
    """Normalize only Schwab/Yahoo share-class punctuation; never infer aliases."""

    return value.strip().upper().replace("/", ".")


def _is_fifo_method(value: str) -> bool:
    normalized = " ".join(value.strip().lower().replace("-", " ").split())
    return normalized in {"fifo", "first in first out", "fifo lot"}


class PerformanceReconciliationService:
    def __init__(
        self,
        parser: BrokerStatementParser,
        attribution: PerformanceAttributionReader,
        writer: BrokerReconciliationWriter,
        clock: Clock,
    ) -> None:
        self._parser = parser
        self._attribution = attribution
        self._writer = writer
        self._clock = clock

    def inspect_schwab_realized_gain_loss(
        self, relative_path: str
    ) -> BrokerRealizedStatementDTO:
        """Return only redacted account summaries; raw rows never cross the Provider."""

        return BrokerRealizedStatementDTO.from_domain(
            self._parser.parse_realized_gain_loss(relative_path)
        )

    def compare_schwab_realized_gain_loss(
        self,
        *,
        relative_path: str,
        durable_account_ref: str,
        period_start: datetime,
        period_end: datetime,
        statement_account_ref: str | None = None,
        tolerance: Decimal = Decimal("0.01"),
        write_draft: bool = True,
    ) -> BrokerRealizedReconciliationDTO:
        """Compare statement lots with the durable FIFO ledger; never refresh a broker."""

        require_aware_datetime(period_start, field_name="period_start")
        require_aware_datetime(period_end, field_name="period_end")
        if period_start > period_end:
            raise DataContractError(
                "reconciliation period is invalid",
                code="SCHWAB_RECONCILIATION_INPUT_ERROR",
            )
        if not durable_account_ref.strip():
            raise DataContractError(
                "durable account reference is required",
                code="SCHWAB_RECONCILIATION_INPUT_ERROR",
            )
        if type(tolerance) is not Decimal or not tolerance.is_finite() or tolerance < 0:
            raise DataContractError(
                "reconciliation tolerance must be a nonnegative Decimal",
                code="SCHWAB_RECONCILIATION_INPUT_ERROR",
            )

        statement = self._parser.parse_realized_gain_loss(relative_path)
        selected_ref = self._select_statement_account(
            statement_account_ref,
            tuple(item.statement_account_ref for item in statement.accounts),
        )
        selected_lots = tuple(
            item
            for item in statement.lots
            if item.statement_account_ref == selected_ref
            and period_start.date() <= item.closed_date <= period_end.date()
        )
        if not selected_lots:
            raise DataContractError(
                "statement contains no closed lots for the selected account and period",
                code="SCHWAB_RECONCILIATION_NO_DATA",
            )

        attribution = self._attribution.get_performance_attribution(
            PerformanceAttributionInput(
                start=period_start,
                end=period_end,
                cost_basis_method=CostBasisMethod.FIFO,
                providers=(VendorId.SCHWAB,),
                account_refs=(durable_account_ref,),
            )
        )
        if not attribution.ok or attribution.data is None:
            raise DataContractError(
                "durable performance attribution is unavailable",
                code="SCHWAB_RECONCILIATION_ATTRIBUTION_UNAVAILABLE",
                details={"error_codes": tuple(item.code for item in attribution.errors)},
            )
        account = next(
            (
                item
                for item in attribution.data.accounts
                if item.provider is VendorId.SCHWAB
                and item.account_ref == durable_account_ref
                and item.currency == statement.currency
            ),
            None,
        )
        if account is None:
            raise DataContractError(
                "selected durable Schwab account has no matching currency attribution",
                code="SCHWAB_RECONCILIATION_ACCOUNT_UNAVAILABLE",
            )

        result = self._compare(
            source_sha256=statement.source_sha256,
            statement_account_ref=selected_ref,
            durable_account_ref=durable_account_ref,
            period_start=period_start.date(),
            period_end=period_end.date(),
            currency=statement.currency,
            tolerance=tolerance,
            statement_lots=selected_lots,
            account=account,
            attribution_status=AttributionStatus(attribution.data.status),
            attribution_warning_codes=attribution.data.warning_codes,
        )
        artifact = self._writer.write_draft(result) if write_draft else None
        return BrokerRealizedReconciliationDTO.from_domain(result, draft_artifact=artifact)

    @staticmethod
    def _select_statement_account(
        requested: str | None, available: tuple[str, ...]
    ) -> str:
        if requested is not None:
            if requested not in available:
                raise DataContractError(
                    "statement account reference is not present in the export",
                    code="SCHWAB_RECONCILIATION_ACCOUNT_UNAVAILABLE",
                    details={"available_statement_account_refs": available},
                )
            return requested
        if len(available) != 1:
            raise DataContractError(
                "multiple statement accounts require an explicit statement account reference",
                code="SCHWAB_RECONCILIATION_ACCOUNT_AMBIGUOUS",
                details={"available_statement_account_refs": available},
            )
        return available[0]

    def _compare(
        self,
        *,
        source_sha256: str,
        statement_account_ref: str,
        durable_account_ref: str,
        period_start: date,
        period_end: date,
        currency: str,
        tolerance: Decimal,
        statement_lots: tuple[BrokerRealizedLot, ...],
        account: AccountPerformanceDTO,
        attribution_status: AttributionStatus,
        attribution_warning_codes: tuple[str, ...],
    ) -> BrokerRealizedReconciliation:
        statement_by_symbol: dict[str, list[BrokerRealizedLot]] = defaultdict(list)
        for lot in statement_lots:
            statement_by_symbol[_statement_symbol(lot.symbol)].append(lot)

        system_by_symbol: dict[str, list[InstrumentPerformanceDTO]] = defaultdict(list)
        for item in account.instruments:
            _, market, symbol = parse_instrument_id(item.instrument_id)
            if market is not Market.US or not self._has_realized_activity(item):
                continue
            system_by_symbol[_statement_symbol(symbol)].append(item)

        comparisons: list[BrokerRealizedInstrumentReconciliation] = []
        aggregate_codes: set[str] = set(attribution_warning_codes)
        for symbol in sorted(set(statement_by_symbol) | set(system_by_symbol)):
            lots = statement_by_symbol.get(symbol, [])
            instruments = system_by_symbol.get(symbol, [])
            codes: set[str] = set()
            if not lots:
                codes.add("SYSTEM_ONLY_SYMBOL")
            if not instruments:
                codes.add("STATEMENT_ONLY_SYMBOL")
            if len(instruments) > 1:
                codes.add("SYSTEM_SYMBOL_AMBIGUOUS")
            if any((item.wash_sale_disallowed or Decimal(0)) != 0 for item in lots):
                codes.add("STATEMENT_WASH_SALE_ADJUSTMENT_PRESENT")
            if lots and any(item.cost_basis_method is None for item in lots):
                codes.add("STATEMENT_COST_BASIS_METHOD_UNAVAILABLE")
            elif any(
                item.cost_basis_method is not None
                and not _is_fifo_method(item.cost_basis_method)
                for item in lots
            ):
                codes.add("STATEMENT_COST_BASIS_METHOD_DIFFERS_FROM_FIFO")

            statement_pnl = _complete_sum([item.realized_pnl for item in lots]) if lots else None
            system_before = (
                _complete_sum([item.realized_pnl_before_fees for item in instruments])
                if instruments
                else None
            )
            system_after = (
                _complete_sum([item.realized_pnl_after_fees for item in instruments])
                if instruments
                else None
            )
            if statement_pnl is None:
                codes.add("STATEMENT_REALIZED_PNL_UNAVAILABLE")
            if system_after is None:
                codes.add("SYSTEM_REALIZED_PNL_AFTER_FEES_UNAVAILABLE")
            residual = (
                system_after - statement_pnl
                if system_after is not None and statement_pnl is not None
                else None
            )
            if residual is not None and abs(residual) > tolerance:
                codes.add("ABSOLUTE_RESIDUAL_ABOVE_TOLERANCE")
            aggregate_codes.update(codes)
            comparisons.append(
                BrokerRealizedInstrumentReconciliation(
                    symbol=symbol,
                    instrument_id=(
                        instruments[0].instrument_id if len(instruments) == 1 else None
                    ),
                    statement_lot_count=len(lots),
                    statement_proceeds=sum(
                        (item.total_proceeds for item in lots), Decimal(0)
                    ),
                    statement_cost_basis=(
                        _complete_sum([item.cost_basis for item in lots]) if lots else None
                    ),
                    statement_realized_pnl=statement_pnl,
                    system_realized_pnl_before_fees=system_before,
                    system_realized_pnl_after_fees=system_after,
                    residual=residual,
                    absolute_residual=abs(residual) if residual is not None else None,
                    residual_codes=tuple(sorted(codes)),
                )
            )

        statement_total_pnl = _complete_sum(
            [item.realized_pnl for item in statement_lots]
        )
        system_total_before = account.realized_pnl_before_fees
        system_total_after = account.realized_pnl_after_fees
        residual = (
            system_total_after - statement_total_pnl
            if system_total_after is not None and statement_total_pnl is not None
            else None
        )
        if residual is None:
            aggregate_codes.add("ACCOUNT_RESIDUAL_NOT_EVALUATED")
        elif abs(residual) > tolerance:
            aggregate_codes.add("ABSOLUTE_RESIDUAL_ABOVE_TOLERANCE")

        incomplete = (
            attribution_status is AttributionStatus.INCOMPLETE
            or statement_total_pnl is None
            or system_total_after is None
            or any(
                code
                in {
                    "STATEMENT_ONLY_SYMBOL",
                    "SYSTEM_ONLY_SYMBOL",
                    "SYSTEM_SYMBOL_AMBIGUOUS",
                    "STATEMENT_COST_BASIS_METHOD_UNAVAILABLE",
                    "STATEMENT_COST_BASIS_METHOD_DIFFERS_FROM_FIFO",
                }
                for code in aggregate_codes
            )
        )
        instrument_residual_requires_review = any(
            "ABSOLUTE_RESIDUAL_ABOVE_TOLERANCE" in item.residual_codes
            for item in comparisons
        )
        status = (
            "INCOMPLETE"
            if incomplete
            else "MATCHED"
            if residual is not None
            and abs(residual) <= tolerance
            and not instrument_residual_requires_review
            else "REVIEW_REQUIRED"
        )
        return BrokerRealizedReconciliation(
            source_sha256=source_sha256,
            statement_account_ref=statement_account_ref,
            durable_account_ref=durable_account_ref,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            cost_basis_method=CostBasisMethod.FIFO,
            tolerance=tolerance,
            statement_lot_count=len(statement_lots),
            statement_total_proceeds=sum(
                (item.total_proceeds for item in statement_lots), Decimal(0)
            ),
            statement_total_cost_basis=_complete_sum(
                [item.cost_basis for item in statement_lots]
            ),
            statement_total_realized_pnl=statement_total_pnl,
            system_total_realized_pnl_before_fees=system_total_before,
            system_total_realized_pnl_after_fees=system_total_after,
            residual=residual,
            absolute_residual=abs(residual) if residual is not None else None,
            attribution_status=attribution_status,
            reconciliation_status=status,
            comparisons=tuple(comparisons),
            residual_codes=tuple(sorted(aggregate_codes)),
            attribution_warning_codes=tuple(sorted(attribution_warning_codes)),
            generated_at=self._clock.now(),
        )

    @staticmethod
    def _has_realized_activity(value: InstrumentPerformanceDTO) -> bool:
        return bool(
            value.matched_quantity > 0
            or value.realized_pnl_before_fees not in (None, Decimal(0))
            or value.realized_pnl_after_fees not in (None, Decimal(0))
        )
