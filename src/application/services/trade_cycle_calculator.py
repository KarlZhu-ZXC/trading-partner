"""Deterministic long-only Trade Cycle projection over durable account activities."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
    TradeCycleClassification,
    TradeCycleQuality,
    TradeCycleStatus,
)
from domain.portfolio.models import AccountTransaction, TradeCycle, TradeCycleProjection


@dataclass(slots=True)
class _Lot:
    quantity: Decimal
    price: Decimal | None
    fee_per_unit: Decimal | None


@dataclass(slots=True)
class _Cycle:
    cycle_id: str
    account_ref: str
    provider: VendorId
    instrument_id: str
    currency: str
    opened_at: datetime | None
    reentry_of_cycle_id: str | None
    activity_ids: list[str] = field(default_factory=list)
    lots: deque[_Lot] = field(default_factory=deque)
    opening_count: int = 0
    add_count: int = 0
    reduce_count: int = 0
    quantity: Decimal = Decimal(0)
    gross_realized: Decimal = Decimal(0)
    net_realized: Decimal = Decimal(0)
    gross_complete: bool = True
    net_complete: bool = True
    deployed_complete: bool = True
    maximum_deployed: Decimal = Decimal(0)
    unresolved: bool = False
    warnings: set[str] = field(default_factory=set)

    def current_deployed(self) -> Decimal | None:
        if not self.deployed_complete or any(lot.price is None for lot in self.lots):
            return None
        return sum(
            (lot.quantity * lot.price for lot in self.lots if lot.price is not None),
            Decimal(0),
        )


class TradeCycleCalculator:
    """Reconstruct long-only cycles without Provider access or durable mutation."""

    algorithm_version = "trade_cycle_v1"

    def calculate(
        self,
        *,
        transactions: tuple[AccountTransaction, ...],
        as_of: datetime,
        coverage_status: AccountActivityCoverageStatus,
        start: datetime | None = None,
        limit: int = 200,
        coverage_warning_codes: tuple[str, ...] = (),
    ) -> TradeCycleProjection:
        groups: dict[tuple[str, str, str], _Cycle] = {}
        last_cycle_ids: dict[tuple[str, str, str], str] = {}
        completed: list[TradeCycle] = []
        ordered = sorted(
            (item for item in transactions if item.kind is AccountTransactionKind.TRADE),
            key=lambda item: (item.occurred_at, item.provider_transaction_id),
        )
        for activity in ordered:
            assert activity.instrument_id is not None
            assert activity.quantity is not None
            key = (activity.account_ref, activity.instrument_id, activity.currency)
            current = groups.get(key)
            if activity.side is AccountTransactionSide.BUY:
                if current is None:
                    current = _Cycle(
                        cycle_id=self._cycle_id(
                            activity.provider,
                            *key,
                            activity.provider_transaction_id,
                        ),
                        account_ref=activity.account_ref,
                        provider=activity.provider,
                        instrument_id=activity.instrument_id,
                        currency=activity.currency,
                        opened_at=activity.occurred_at,
                        reentry_of_cycle_id=last_cycle_ids.get(key),
                        opening_count=1,
                    )
                    groups[key] = current
                else:
                    current.add_count += 1
                self._buy(current, activity)
                continue

            if current is None:
                unresolved = _Cycle(
                    cycle_id=self._cycle_id(
                        activity.provider,
                        *key,
                        activity.provider_transaction_id,
                    ),
                    account_ref=activity.account_ref,
                    provider=activity.provider,
                    instrument_id=activity.instrument_id,
                    currency=activity.currency,
                    opened_at=None,
                    reentry_of_cycle_id=last_cycle_ids.get(key),
                    reduce_count=1,
                    unresolved=True,
                    gross_complete=False,
                    net_complete=False,
                    deployed_complete=False,
                )
                unresolved.activity_ids.append(activity.provider_transaction_id)
                unresolved.warnings.add("SELL_WITHOUT_OPEN_LONG")
                if activity.price is None:
                    unresolved.warnings.add("TRADE_PRICE_UNAVAILABLE")
                if activity.fees is None:
                    unresolved.warnings.add("TRANSACTION_FEES_UNAVAILABLE")
                value = self._finish(unresolved, closed_at=activity.occurred_at, as_of=as_of)
                completed.append(value)
                last_cycle_ids[key] = value.cycle_id
                continue

            self._sell(current, activity)
            oversold = (
                current.unresolved
                and "OVERSELL_SHORT_UNSUPPORTED" in current.warnings
            )
            if current.quantity == 0 or oversold:
                value = self._finish(current, closed_at=activity.occurred_at, as_of=as_of)
                completed.append(value)
                last_cycle_ids[key] = value.cycle_id
                groups.pop(key, None)

        completed.extend(
            self._finish(value, closed_at=None, as_of=as_of)
            for value in groups.values()
        )
        if start is not None:
            completed = [
                item
                for item in completed
                if item.closed_at is None or item.closed_at >= start
            ]
        completed.sort(
            key=lambda item: (
                item.opened_at or item.closed_at or as_of,
                item.cycle_id,
            ),
            reverse=True,
        )
        warning_codes = set(coverage_warning_codes)
        if not completed:
            warning_codes.add("TRADE_CYCLE_INPUTS_UNAVAILABLE")
        if len(completed) > limit:
            warning_codes.add("TRADE_CYCLE_RESULTS_TRUNCATED")
            completed = completed[:limit]
        warning_codes.update(code for item in completed for code in item.warning_codes)
        status = (
            TradeCycleQuality.INCOMPLETE
            if coverage_status is AccountActivityCoverageStatus.INCOMPLETE
            or warning_codes
            or any(item.quality is TradeCycleQuality.INCOMPLETE for item in completed)
            else TradeCycleQuality.COMPLETE
        )
        return TradeCycleProjection(
            cycles=tuple(completed),
            status=status,
            coverage_status=coverage_status,
            warning_codes=tuple(sorted(warning_codes)),
            algorithm_version=self.algorithm_version,
        )

    @staticmethod
    def _cycle_id(
        provider: VendorId,
        account_ref: str,
        instrument_id: str,
        currency: str,
        opening_activity_id: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                f"{provider.value}|{account_ref}|{instrument_id}|{currency}|"
                f"{opening_activity_id}"
            ).encode()
        ).hexdigest()[:24]
        return f"trade_cycle_{digest}"

    @staticmethod
    def _buy(current: _Cycle, activity: AccountTransaction) -> None:
        assert activity.quantity is not None
        current.activity_ids.append(activity.provider_transaction_id)
        current.quantity += activity.quantity
        if activity.price is None:
            current.gross_complete = False
            current.net_complete = False
            current.deployed_complete = False
            current.unresolved = True
            current.warnings.add("TRADE_PRICE_UNAVAILABLE")
        if activity.fees is None:
            current.net_complete = False
            current.warnings.add("TRANSACTION_FEES_UNAVAILABLE")
        fee_per_unit = (
            activity.fees / activity.quantity if activity.fees is not None else None
        )
        current.lots.append(
            _Lot(
                quantity=activity.quantity,
                price=activity.price,
                fee_per_unit=fee_per_unit,
            )
        )
        deployed = current.current_deployed()
        if deployed is None:
            current.deployed_complete = False
        else:
            current.maximum_deployed = max(current.maximum_deployed, deployed)

    @staticmethod
    def _sell(current: _Cycle, activity: AccountTransaction) -> None:
        assert activity.quantity is not None
        current.activity_ids.append(activity.provider_transaction_id)
        current.reduce_count += 1
        if activity.fees is None:
            current.net_complete = False
            current.warnings.add("TRANSACTION_FEES_UNAVAILABLE")
        if activity.price is None:
            current.gross_complete = False
            current.net_complete = False
            current.unresolved = True
            current.warnings.add("TRADE_PRICE_UNAVAILABLE")
        sell_fee_per_unit = (
            activity.fees / activity.quantity if activity.fees is not None else None
        )
        remaining = activity.quantity
        matched_total = Decimal(0)
        while remaining > 0 and current.lots:
            lot = current.lots[0]
            matched = min(remaining, lot.quantity)
            if activity.price is None or lot.price is None:
                current.gross_complete = False
                current.net_complete = False
            else:
                gross = (activity.price - lot.price) * matched
                current.gross_realized += gross
                if lot.fee_per_unit is None or sell_fee_per_unit is None:
                    current.net_complete = False
                else:
                    current.net_realized += gross - (
                        lot.fee_per_unit + sell_fee_per_unit
                    ) * matched
            lot.quantity -= matched
            remaining -= matched
            matched_total += matched
            if lot.quantity == 0:
                current.lots.popleft()
        current.quantity = max(Decimal(0), current.quantity - matched_total)
        if remaining > 0:
            current.unresolved = True
            current.gross_complete = False
            current.net_complete = False
            current.deployed_complete = False
            current.warnings.add("OVERSELL_SHORT_UNSUPPORTED")
            current.quantity = Decimal(0)
            current.lots.clear()

    def _finish(
        self,
        current: _Cycle,
        *,
        closed_at: datetime | None,
        as_of: datetime,
    ) -> TradeCycle:
        status = (
            TradeCycleStatus.UNRESOLVED
            if current.unresolved
            else TradeCycleStatus.CLOSED
            if closed_at is not None
            else TradeCycleStatus.OPEN
        )
        quality = (
            TradeCycleQuality.INCOMPLETE
            if current.unresolved
            or current.warnings
            or not current.gross_complete
            or not current.net_complete
            or not current.deployed_complete
            else TradeCycleQuality.COMPLETE
        )
        duration = (
            round(((closed_at or as_of) - current.opened_at).total_seconds())
            if current.opened_at is not None
            else None
        )
        return TradeCycle(
            cycle_id=current.cycle_id,
            account_ref=current.account_ref,
            provider=current.provider,
            instrument_id=current.instrument_id,
            currency=current.currency,
            activity_ids=tuple(current.activity_ids),
            opened_at=current.opened_at,
            closed_at=closed_at,
            status=status,
            classification=(
                TradeCycleClassification.CASH_MANAGEMENT
                if current.instrument_id == "etf:US:SGOV"
                else TradeCycleClassification.UNCLASSIFIED
            ),
            opening_count=current.opening_count,
            add_count=current.add_count,
            reduce_count=current.reduce_count,
            ending_quantity=current.quantity,
            gross_realized_pnl=(
                current.gross_realized if current.gross_complete else None
            ),
            net_realized_pnl=(current.net_realized if current.net_complete else None),
            maximum_deployed_capital=(
                current.maximum_deployed if current.deployed_complete else None
            ),
            holding_duration_seconds=duration,
            reentry_of_cycle_id=current.reentry_of_cycle_id,
            quality=quality,
            warning_codes=tuple(sorted(current.warnings)),
            algorithm_version=self.algorithm_version,
        )


__all__ = ["TradeCycleCalculator"]
