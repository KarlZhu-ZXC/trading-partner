"""Deterministic native-currency FIFO and broker-basis performance calculation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from domain.attribution.enums import AttributionStatus, CostBasisMethod, LotDirection
from domain.attribution.models import (
    AccountPerformance,
    InstrumentPerformance,
    PositionBasisCheckpoint,
)
from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountPosition, AccountSnapshot, AccountTransaction


@dataclass(slots=True)
class _Lot:
    direction: LotDirection
    quantity: Decimal
    price: Decimal
    fee_per_unit: Decimal | None


@dataclass(slots=True)
class _InstrumentAccumulator:
    realized_gross: Decimal = Decimal(0)
    realized_net: Decimal = Decimal(0)
    realized_net_complete: bool = True
    matched_quantity: Decimal = Decimal(0)
    known_fees: Decimal = Decimal(0)
    fees_complete: bool = True
    dividend_income: Decimal = Decimal(0)
    dividend_income_complete: bool = True
    activity_ids: list[str] = field(default_factory=list)
    basis_checkpoint_ids: list[str] = field(default_factory=list)
    warning_codes: set[str] = field(default_factory=set)


class PerformanceAttributionCalculator:
    """Calculate facts only; no Provider calls, persistence, or judgment scoring."""

    def __init__(
        self, checkpoints: tuple[PositionBasisCheckpoint, ...] = ()
    ) -> None:
        self._checkpoints = checkpoints

    def calculate_account(
        self,
        *,
        account_ref: str,
        provider: VendorId,
        currency: str,
        transactions: tuple[AccountTransaction, ...],
        snapshot: AccountSnapshot | None,
        start: datetime,
        end: datetime,
        method: CostBasisMethod,
        opening_history_verified: bool,
        coverage_warning_codes: tuple[str, ...] = (),
    ) -> AccountPerformance:
        relevant = tuple(
            item
            for item in transactions
            if item.account_ref == account_ref
            and item.currency == currency
            and item.occurred_at <= end
        )
        checkpoints = tuple(
            item
            for item in self._checkpoints
            if item.account_ref == account_ref
            and item.provider is provider
            and item.currency == currency
            and item.effective_at <= end
        )
        if method is CostBasisMethod.BROKER_REPORTED:
            return self._broker_reported(
                account_ref=account_ref,
                provider=provider,
                currency=currency,
                transactions=relevant,
                snapshot=snapshot,
                start=start,
                end=end,
                coverage_warning_codes=coverage_warning_codes,
            )
        return self._fifo(
            account_ref=account_ref,
            provider=provider,
            currency=currency,
            transactions=relevant,
            snapshot=snapshot,
            start=start,
            end=end,
            opening_history_verified=opening_history_verified,
            coverage_warning_codes=coverage_warning_codes,
            checkpoints=checkpoints,
        )

    def _fifo(
        self,
        *,
        account_ref: str,
        provider: VendorId,
        currency: str,
        transactions: tuple[AccountTransaction, ...],
        snapshot: AccountSnapshot | None,
        start: datetime,
        end: datetime,
        opening_history_verified: bool,
        coverage_warning_codes: tuple[str, ...],
        checkpoints: tuple[PositionBasisCheckpoint, ...],
    ) -> AccountPerformance:
        lots: dict[str, deque[_Lot]] = defaultdict(deque)
        accumulators: dict[str, _InstrumentAccumulator] = defaultdict(
            _InstrumentAccumulator
        )
        account_warnings = set(coverage_warning_codes)
        if not opening_history_verified:
            account_warnings.add("FIFO_OPENING_HISTORY_UNVERIFIED")
        latest_checkpoints: dict[str, PositionBasisCheckpoint] = {}
        for checkpoint in checkpoints:
            current = latest_checkpoints.get(checkpoint.instrument_id)
            if current is None or checkpoint.effective_at > current.effective_at:
                latest_checkpoints[checkpoint.instrument_id] = checkpoint
        if any(
            item.kind is AccountTransactionKind.CORPORATE_ACTION
            and (
                item.instrument_id is None
                or item.instrument_id not in latest_checkpoints
                or item.occurred_at > latest_checkpoints[item.instrument_id].effective_at
            )
            for item in transactions
        ):
            account_warnings.add("CORPORATE_ACTION_LOT_EFFECT_UNSUPPORTED")
        replaced_activity_ids = {
            item.replaces_activity_id
            for item in checkpoints
            if item.replaces_activity_id is not None
        }
        events: list[
            tuple[datetime, int, str, AccountTransaction | PositionBasisCheckpoint]
        ] = [
            (item.occurred_at, 0, item.provider_transaction_id, item)
            for item in transactions
            if item.provider_transaction_id not in replaced_activity_ids
        ]
        events.extend(
            (item.effective_at, 1, item.checkpoint_id, item) for item in checkpoints
        )
        events.sort(key=lambda item: (item[0], item[1], item[2]))
        for _, _, _, event in events:
            if isinstance(event, PositionBasisCheckpoint):
                queue = lots[event.instrument_id]
                queue.clear()
                queue.append(
                    _Lot(
                        direction=LotDirection.LONG,
                        quantity=event.quantity,
                        price=event.total_cost_basis / event.quantity,
                        fee_per_unit=Decimal(0),
                    )
                )
                accumulators[event.instrument_id].basis_checkpoint_ids.append(
                    event.checkpoint_id
                )
                continue
            activity = event
            in_window = start <= activity.occurred_at <= end
            if activity.kind is AccountTransactionKind.DIVIDEND:
                if activity.instrument_id is not None and in_window:
                    accumulator = accumulators[activity.instrument_id]
                    accumulator.activity_ids.append(activity.provider_transaction_id)
                    if activity.cash_amount is None:
                        accumulator.dividend_income_complete = False
                    else:
                        accumulator.dividend_income += activity.cash_amount
                continue
            if activity.kind is not AccountTransactionKind.TRADE:
                continue
            if activity.instrument_id is None or activity.quantity is None:
                account_warnings.add("TRADE_IDENTITY_INCOMPLETE")
                continue
            accumulator = accumulators[activity.instrument_id]
            if in_window:
                accumulator.activity_ids.append(activity.provider_transaction_id)
                if activity.fees is None:
                    accumulator.fees_complete = False
                else:
                    accumulator.known_fees += activity.fees
            if activity.price is None:
                accumulator.warning_codes.add("TRADE_PRICE_UNAVAILABLE")
                continue
            fee_per_unit = (
                activity.fees / activity.quantity if activity.fees is not None else None
            )
            incoming_direction = (
                LotDirection.LONG
                if activity.side is AccountTransactionSide.BUY
                else LotDirection.SHORT
            )
            opposing = (
                LotDirection.SHORT
                if incoming_direction is LotDirection.LONG
                else LotDirection.LONG
            )
            remaining = activity.quantity
            queue = lots[activity.instrument_id]
            while remaining > 0 and queue and queue[0].direction is opposing:
                opening = queue[0]
                matched = min(remaining, opening.quantity)
                gross = (
                    (opening.price - activity.price) * matched
                    if opening.direction is LotDirection.SHORT
                    else (activity.price - opening.price) * matched
                )
                if in_window:
                    accumulator.realized_gross += gross
                    accumulator.matched_quantity += matched
                    if opening.fee_per_unit is None or fee_per_unit is None:
                        accumulator.realized_net_complete = False
                    else:
                        accumulator.realized_net += gross - (
                            opening.fee_per_unit + fee_per_unit
                        ) * matched
                opening.quantity -= matched
                remaining -= matched
                if opening.quantity == 0:
                    queue.popleft()
            if remaining > 0:
                queue.append(
                    _Lot(
                        direction=incoming_direction,
                        quantity=remaining,
                        price=activity.price,
                        fee_per_unit=fee_per_unit,
                    )
                )

        snapshot_positions = self._snapshot_positions(snapshot, currency)
        instruments: list[InstrumentPerformance] = []
        instrument_ids = set(lots) | set(accumulators) | set(snapshot_positions)
        for instrument_id in sorted(instrument_ids):
            queue = lots[instrument_id]
            accumulator = accumulators[instrument_id]
            position = snapshot_positions.get(instrument_id)
            warnings = set(accumulator.warning_codes)
            ending_quantity = sum(
                (
                    lot.quantity
                    if lot.direction is LotDirection.LONG
                    else -lot.quantity
                    for lot in queue
                ),
                Decimal(0),
            )
            open_cost_basis = sum(
                (lot.quantity * lot.price for lot in queue), Decimal(0)
            )
            expected_quantity = self._signed_snapshot_quantity(position)
            if position is not None and expected_quantity != ending_quantity:
                warnings.add("ENDING_POSITION_MISMATCH")
            elif position is None and ending_quantity != 0:
                warnings.add("VALUATION_SNAPSHOT_POSITION_UNAVAILABLE")
            valuation_price = self._valuation_price(position, snapshot, end)
            unrealized: Decimal | None
            if ending_quantity == 0:
                unrealized = Decimal(0)
            elif valuation_price is None:
                unrealized = None
                warnings.add("TIMESTAMPED_VALUATION_UNAVAILABLE")
            else:
                unrealized = sum(
                    (
                        (valuation_price - lot.price) * lot.quantity
                        if lot.direction is LotDirection.LONG
                        else (lot.price - valuation_price) * lot.quantity
                        for lot in queue
                    ),
                    Decimal(0),
                )
            realized_net = (
                accumulator.realized_net
                if accumulator.realized_net_complete and accumulator.fees_complete
                else None
            )
            if realized_net is None:
                warnings.add("TRANSACTION_FEES_UNAVAILABLE")
            dividend_income = (
                accumulator.dividend_income
                if accumulator.dividend_income_complete
                else None
            )
            if dividend_income is None:
                warnings.add("DIVIDEND_ACTIVITY_AMOUNT_UNAVAILABLE")
            net_trading_pnl = (
                realized_net + unrealized
                if realized_net is not None and unrealized is not None
                else None
            )
            total_pnl = (
                net_trading_pnl + dividend_income
                if net_trading_pnl is not None and dividend_income is not None
                else None
            )
            instruments.append(
                InstrumentPerformance(
                    instrument_id=instrument_id,
                    currency=currency,
                    ending_quantity=ending_quantity,
                    open_cost_basis=open_cost_basis,
                    realized_pnl_before_fees=accumulator.realized_gross,
                    realized_pnl_after_fees=realized_net,
                    unrealized_pnl_before_fees=unrealized,
                    broker_reported_unrealized_pnl=(
                        position.unrealized_pnl if position is not None else None
                    ),
                    broker_reported_realized_pnl=(
                        position.realized_pnl if position is not None else None
                    ),
                    dividend_income=dividend_income,
                    net_trading_pnl=net_trading_pnl,
                    total_pnl=total_pnl,
                    known_fees=accumulator.known_fees,
                    fees_complete=accumulator.fees_complete,
                    matched_quantity=accumulator.matched_quantity,
                    activity_ids=tuple(dict.fromkeys(accumulator.activity_ids)),
                    basis_checkpoint_ids=tuple(
                        dict.fromkeys(accumulator.basis_checkpoint_ids)
                    ),
                    snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
                    warning_codes=tuple(sorted(warnings)),
                )
            )
            account_warnings.update(warnings)
        return self._account_result(
            account_ref=account_ref,
            provider=provider,
            currency=currency,
            method=CostBasisMethod.FIFO,
            transactions=transactions,
            snapshot=snapshot,
            instruments=tuple(instruments),
            start=start,
            end=end,
            warning_codes=account_warnings,
        )

    def _broker_reported(
        self,
        *,
        account_ref: str,
        provider: VendorId,
        currency: str,
        transactions: tuple[AccountTransaction, ...],
        snapshot: AccountSnapshot | None,
        start: datetime,
        end: datetime,
        coverage_warning_codes: tuple[str, ...],
    ) -> AccountPerformance:
        warnings = set(coverage_warning_codes)
        warnings.add("BROKER_REPORTED_REALIZED_PERIOD_UNVERIFIED")
        positions = self._snapshot_positions(snapshot, currency)
        if snapshot is None:
            warnings.add("VALUATION_SNAPSHOT_UNAVAILABLE")
        instruments: list[InstrumentPerformance] = []
        activities_by_instrument: dict[str, list[AccountTransaction]] = defaultdict(list)
        for activity in transactions:
            if (
                start <= activity.occurred_at <= end
                and activity.instrument_id is not None
            ):
                activities_by_instrument[activity.instrument_id].append(activity)
        for instrument_id in sorted(set(positions) | set(activities_by_instrument)):
            position = positions.get(instrument_id)
            activities = activities_by_instrument[instrument_id]
            fees_complete = all(item.fees is not None for item in activities)
            known_fees = sum(
                (item.fees or Decimal(0) for item in activities), Decimal(0)
            )
            instrument_warnings: set[str] = {"BROKER_REPORTED_REALIZED_PERIOD_UNVERIFIED"}
            if position is None:
                instrument_warnings.add("VALUATION_SNAPSHOT_POSITION_UNAVAILABLE")
            if not fees_complete:
                instrument_warnings.add("TRANSACTION_FEES_UNAVAILABLE")
            ending_quantity = self._signed_snapshot_quantity(position)
            dividend_income = sum(
                (
                    item.cash_amount or Decimal(0)
                    for item in activities
                    if item.kind is AccountTransactionKind.DIVIDEND
                ),
                Decimal(0),
            )
            broker_trading_pnl = (
                (position.realized_pnl or Decimal(0))
                + (position.unrealized_pnl or Decimal(0))
                if position is not None
                and position.realized_pnl is not None
                and position.unrealized_pnl is not None
                else None
            )
            instruments.append(
                InstrumentPerformance(
                    instrument_id=instrument_id,
                    currency=currency,
                    ending_quantity=ending_quantity,
                    open_cost_basis=(
                        (position.average_cost or Decimal(0)) * position.quantity
                        if position is not None
                        else Decimal(0)
                    ),
                    realized_pnl_before_fees=None,
                    realized_pnl_after_fees=None,
                    unrealized_pnl_before_fees=None,
                    broker_reported_unrealized_pnl=(
                        position.unrealized_pnl if position is not None else None
                    ),
                    broker_reported_realized_pnl=(
                        position.realized_pnl if position is not None else None
                    ),
                    dividend_income=dividend_income,
                    net_trading_pnl=broker_trading_pnl,
                    total_pnl=(
                        broker_trading_pnl + dividend_income
                        if broker_trading_pnl is not None
                        else None
                    ),
                    known_fees=known_fees,
                    fees_complete=fees_complete,
                    matched_quantity=Decimal(0),
                    activity_ids=tuple(item.provider_transaction_id for item in activities),
                    basis_checkpoint_ids=(),
                    snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
                    warning_codes=tuple(sorted(instrument_warnings)),
                )
            )
            warnings.update(instrument_warnings)
        return self._account_result(
            account_ref=account_ref,
            provider=provider,
            currency=currency,
            method=CostBasisMethod.BROKER_REPORTED,
            transactions=transactions,
            snapshot=snapshot,
            instruments=tuple(instruments),
            start=start,
            end=end,
            warning_codes=warnings,
        )

    @staticmethod
    def _snapshot_positions(
        snapshot: AccountSnapshot | None, currency: str
    ) -> dict[str, AccountPosition]:
        if snapshot is None:
            return {}
        return {
            item.instrument_id: item
            for item in snapshot.positions
            if item.currency == currency
        }

    @staticmethod
    def _signed_snapshot_quantity(position: AccountPosition | None) -> Decimal:
        if position is None:
            return Decimal(0)
        quantity = position.quantity
        return -quantity if position.side is AccountPositionSide.SHORT else quantity

    @staticmethod
    def _valuation_price(
        position: AccountPosition | None,
        snapshot: AccountSnapshot | None,
        end: datetime,
    ) -> Decimal | None:
        if position is None:
            return None
        if (
            position.market_price is not None
            and position.market_price_at is not None
            and position.market_price_at <= end
        ):
            return position.market_price
        if (
            snapshot is not None
            and snapshot.account_as_of <= end
            and position.market_value is not None
            and position.quantity > 0
        ):
            return abs(position.market_value) / position.quantity
        return None

    @staticmethod
    def _account_result(
        *,
        account_ref: str,
        provider: VendorId,
        currency: str,
        method: CostBasisMethod,
        transactions: tuple[AccountTransaction, ...],
        snapshot: AccountSnapshot | None,
        instruments: tuple[InstrumentPerformance, ...],
        start: datetime,
        end: datetime,
        warning_codes: set[str],
    ) -> AccountPerformance:
        window = tuple(item for item in transactions if start <= item.occurred_at <= end)
        if any(
            item.kind is AccountTransactionKind.DIVIDEND and item.instrument_id is None
            for item in window
        ):
            warning_codes.add("DIVIDEND_INSTRUMENT_UNAVAILABLE")
        dividends = sum(
            (
                item.cash_amount or Decimal(0)
                for item in window
                if item.kind is AccountTransactionKind.DIVIDEND
            ),
            Decimal(0),
        )
        interest = sum(
            (
                item.cash_amount or Decimal(0)
                for item in window
                if item.kind is AccountTransactionKind.INTEREST
            ),
            Decimal(0),
        )
        external_cash = sum(
            (
                item.cash_amount or Decimal(0)
                for item in window
                if item.kind is AccountTransactionKind.TRANSFER
            ),
            Decimal(0),
        )
        fee_activities = sum(
            (
                abs(item.cash_amount or Decimal(0))
                for item in window
                if item.kind is AccountTransactionKind.FEE
            ),
            Decimal(0),
        )
        known_fees = sum((item.known_fees for item in instruments), Decimal(0)) + fee_activities
        fees_complete = all(item.fees_complete for item in instruments)
        realized_before = (
            sum(
                (
                    item.realized_pnl_before_fees
                    for item in instruments
                    if item.realized_pnl_before_fees is not None
                ),
                Decimal(0),
            )
            if method is CostBasisMethod.FIFO
            else None
        )
        realized_after = (
            sum(
                (item.realized_pnl_after_fees or Decimal(0) for item in instruments),
                Decimal(0),
            )
            if method is CostBasisMethod.FIFO
            and all(item.realized_pnl_after_fees is not None for item in instruments)
            else None
        )
        unrealized = (
            sum(
                (item.unrealized_pnl_before_fees or Decimal(0) for item in instruments),
                Decimal(0),
            )
            if method is CostBasisMethod.FIFO
            and all(item.unrealized_pnl_before_fees is not None for item in instruments)
            else None
        )
        broker_unrealized = (
            sum(
                (item.broker_reported_unrealized_pnl or Decimal(0) for item in instruments),
                Decimal(0),
            )
            if instruments
            and all(item.broker_reported_unrealized_pnl is not None for item in instruments)
            else None
        )
        broker_realized = (
            sum(
                (item.broker_reported_realized_pnl or Decimal(0) for item in instruments),
                Decimal(0),
            )
            if instruments
            and all(item.broker_reported_realized_pnl is not None for item in instruments)
            else None
        )
        return AccountPerformance(
            account_ref=account_ref,
            provider=provider,
            currency=currency,
            cost_basis_method=method,
            snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            snapshot_as_of=snapshot.account_as_of if snapshot is not None else None,
            realized_pnl_before_fees=realized_before,
            realized_pnl_after_fees=realized_after,
            unrealized_pnl_before_fees=unrealized,
            broker_reported_unrealized_pnl=broker_unrealized,
            broker_reported_realized_pnl=broker_realized,
            dividends=dividends,
            interest=interest,
            known_fees=known_fees,
            fees_complete=fees_complete,
            net_external_cash_flow=external_cash,
            instruments=instruments,
            status=(
                AttributionStatus.INCOMPLETE
                if warning_codes
                else AttributionStatus.COMPLETE
            ),
            warning_codes=tuple(sorted(warning_codes)),
        )
