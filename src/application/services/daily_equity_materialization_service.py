"""Deterministic Daily Equity materialization and activation service."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import cast

from application.dto.daily_equity import DailyEquitySnapshotDTO
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.clock import Clock
from application.ports.daily_equity_repository import (
    DailyEquityRepository,
    JournalActivationRepository,
)
from domain.common.time import require_aware_datetime
from domain.performance.daily_equity import (
    DailyEquityMaterializationReceipt,
    DailyEquitySnapshot,
    JournalActivation,
)
from domain.performance.enums import (
    DailyEquityCoverageStatus,
    DailyEquityMaterializationMode,
)
from domain.portfolio.enums import AccountTransactionKind
from domain.portfolio.models import AccountSnapshot, AccountTransaction


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _normal_mode(value: DailyEquityMaterializationMode | str) -> DailyEquityMaterializationMode:
    if isinstance(value, DailyEquityMaterializationMode):
        return value
    return DailyEquityMaterializationMode(str(value))


def _stable_id(source_snapshot_id: str, algorithm_version: str) -> str:
    digest = hashlib.sha256(
        f"{source_snapshot_id}|{algorithm_version}".encode()
    ).hexdigest()[:32]
    return f"daily_equity_{digest}"


def _receipt_id(
    *,
    mode: DailyEquityMaterializationMode,
    source_snapshot_ids: tuple[str, ...],
    journal_activation_at: datetime | None,
    algorithm_version: str,
) -> str:
    payload = "|".join(
        (
            mode.value,
            algorithm_version,
            journal_activation_at.isoformat() if journal_activation_at else "",
            *source_snapshot_ids,
        )
    )
    return f"daily_equity_receipt_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


class DailyEquityMaterializationService:
    """Materialize exact ``AccountSnapshot.net_assets`` without NAV inference."""

    algorithm_version = "daily_equity_v1"

    def __init__(
        self,
        repository: DailyEquityRepository,
        *,
        activation_repository: JournalActivationRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._activation = cast(
            JournalActivationRepository,
            activation_repository or repository,
        )
        self._clock = clock or _SystemClock()

    def get_activation(self) -> JournalActivation | None:
        return self._activation.get_activation()

    def history(
        self,
        *,
        account_refs: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> tuple[DailyEquitySnapshotDTO, ...]:
        return tuple(
            DailyEquitySnapshotDTO.from_domain(item)
            for item in self._repository.list(
                account_refs=account_refs, start=start, end=end, limit=limit
            )
        )

    def activate(
        self,
        *,
        journal_activation_at: datetime | None = None,
        actor: str = "system",
        idempotency_key: str = "journal_activation_v1",
    ) -> JournalActivation:
        recorded_at = require_aware_datetime(self._clock.now(), field_name="recorded_at")
        activation_at = require_aware_datetime(
            journal_activation_at or recorded_at,
            field_name="journal_activation_at",
        )
        value = JournalActivation(
            activation_id="journal_activation",
            journal_activation_at=activation_at,
            recorded_at=recorded_at,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        return self._activation.activate(value)

    def materialize(
        self,
        *,
        snapshots: tuple[AccountSnapshot, ...],
        mode: DailyEquityMaterializationMode | str = DailyEquityMaterializationMode.SHADOW,
        journal_activation_at: datetime | None = None,
        account_refs: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        transactions: tuple[AccountTransaction, ...] | None = None,
        receipt_id: str | None = None,
    ) -> DailyEquityMaterializationReceipt:
        started = monotonic()
        selected_mode = _normal_mode(mode)
        if start is not None:
            require_aware_datetime(start, field_name="start")
        if end is not None:
            require_aware_datetime(end, field_name="end")
        if start is not None and end is not None and start > end:
            raise ValueError("start must be <= end")
        activation = self._activation.get_activation()
        activation_at = (
            require_aware_datetime(journal_activation_at, field_name="journal_activation_at")
            if journal_activation_at is not None
            else activation.journal_activation_at if activation is not None else None
        )
        warnings: set[str] = set()
        if activation_at is None:
            warnings.add("JOURNAL_ACTIVATION_UNAVAILABLE")

        selected = tuple(
            sorted(
                (
                    item
                    for item in snapshots
                    if (not account_refs or item.account_ref in account_refs)
                    and (start is None or item.account_as_of >= start)
                    and (end is None or item.account_as_of <= end)
                ),
                key=lambda item: (
                    item.account_ref,
                    item.base_currency,
                    item.account_as_of,
                    item.snapshot_id,
                ),
            )
        )
        activity_map = self._flows_by_group(
            transactions=transactions,
            snapshots=selected,
            warnings=warnings,
        )
        values = tuple(
            self._project_one(
                snapshot=item,
                previous=self._previous_snapshot(item, selected),
                flows=activity_map.get((item.account_ref, item.base_currency)),
                activation_at=activation_at,
                warnings=warnings,
            )
            for item in selected
        )
        source_ids = tuple(item.snapshot_id for item in selected)
        if selected_mode is DailyEquityMaterializationMode.PERSIST:
            write = self._repository.append_many(values)
            output_values = write.snapshots
            inserted_count = write.inserted_count
            duplicate_count = write.duplicate_count
            persisted = True
            would_insert_count = inserted_count
        else:
            output_values = values
            inserted_count = 0
            duplicate_count = 0
            persisted = False
            would_insert_count = len(values)
        coverage_status = self._aggregate_coverage(output_values)
        warnings.update(
            code
            for item in output_values
            for code in item.warning_codes
        )
        materialized_ids = tuple(item.daily_equity_snapshot_id for item in output_values)
        elapsed_ms = max(0, int((monotonic() - started) * 1000))
        return DailyEquityMaterializationReceipt(
            receipt_id=(
                receipt_id
                or _receipt_id(
                    mode=selected_mode,
                    source_snapshot_ids=source_ids,
                    journal_activation_at=activation_at,
                    algorithm_version=self.algorithm_version,
                )
            ),
            mode=selected_mode,
            generated_at=require_aware_datetime(
                self._clock.now(),
                field_name="generated_at",
            ),
            journal_activation_at=activation_at,
            account_refs=tuple(sorted({item.account_ref for item in selected})),
            source_snapshot_ids=source_ids,
            materialized_snapshot_ids=materialized_ids,
            candidate_count=len(selected),
            inserted_count=inserted_count,
            duplicate_count=duplicate_count,
            skipped_count=0,
            coverage_status=coverage_status,
            warning_codes=tuple(sorted(warnings)),
            algorithm_version=self.algorithm_version,
            persisted=persisted,
            wall_clock_ms=elapsed_ms,
            would_insert_count=would_insert_count,
        )

    # Names used by historical backfill callers.
    historical_shadow = materialize
    dry_run = materialize

    def materialize_history(
        self,
        *,
        snapshot_repository: AccountSnapshotRepository,
        account_ref: str,
        start: datetime,
        end: datetime,
        mode: DailyEquityMaterializationMode | str = DailyEquityMaterializationMode.SHADOW,
        journal_activation_at: datetime | None = None,
        transactions: tuple[AccountTransaction, ...] | None = None,
        receipt_id: str | None = None,
    ) -> DailyEquityMaterializationReceipt:
        """Read an exact durable AccountSnapshot history, then project it."""

        snapshots = snapshot_repository.list_account_history(
            account_ref=account_ref,
            start=start,
            end=end,
        )
        return self.materialize(
            snapshots=snapshots,
            mode=mode,
            journal_activation_at=journal_activation_at,
            account_refs=(account_ref,),
            start=start,
            end=end,
            transactions=transactions,
            receipt_id=receipt_id,
        )

    materialize_account_history = materialize_history

    @staticmethod
    def _previous_snapshot(
        snapshot: AccountSnapshot,
        snapshots: tuple[AccountSnapshot, ...],
    ) -> AccountSnapshot | None:
        group = tuple(
            item
            for item in snapshots
            if item.account_ref == snapshot.account_ref
            and item.base_currency == snapshot.base_currency
        )
        previous: AccountSnapshot | None = None
        for item in group:
            if item.snapshot_id == snapshot.snapshot_id:
                return previous
            previous = item
        return None

    @staticmethod
    def _flows_by_group(
        *,
        transactions: tuple[AccountTransaction, ...] | None,
        snapshots: tuple[AccountSnapshot, ...],
        warnings: set[str],
    ) -> dict[tuple[str, str], tuple[AccountTransaction, ...] | None]:
        groups = {(item.account_ref, item.base_currency) for item in snapshots}
        if transactions is None:
            for _group in groups:
                warnings.add("EXTERNAL_CASH_FLOW_COVERAGE_UNAVAILABLE")
                # None means the field remains explicitly unavailable; an
                # empty tuple would incorrectly claim that no transfer occurred.
                # The group key is still materialized for valuation coverage.
            return {group: None for group in groups}
        by_group: dict[tuple[str, str], list[AccountTransaction]] = {
            group: [] for group in groups
        }
        for item in transactions:
            key = (item.account_ref, item.currency)
            if key in by_group and item.kind is AccountTransactionKind.TRANSFER:
                by_group[key].append(item)
        output: dict[
            tuple[str, str], tuple[AccountTransaction, ...] | None
        ] = {}
        for key, values in by_group.items():
            ordered = tuple(
                sorted(
                    values,
                    key=lambda item: (item.occurred_at, item.provider_transaction_id),
                )
            )
            group_snapshots = tuple(
                item
                for item in snapshots
                if (item.account_ref, item.base_currency) == key
            )
            if group_snapshots and any(
                item.occurred_at <= group_snapshots[0].account_as_of
                or item.occurred_at > group_snapshots[-1].account_as_of
                for item in ordered
            ):
                warnings.add("EXTERNAL_CASH_FLOW_BOUNDARY_UNAVAILABLE")
            output[key] = ordered
        return output

    def _project_one(
        self,
        *,
        snapshot: AccountSnapshot,
        previous: AccountSnapshot | None,
        flows: tuple[AccountTransaction, ...] | None,
        activation_at: datetime | None,
        warnings: set[str],
    ) -> DailyEquitySnapshot:
        point_warnings = set(snapshot.warning_codes)
        if snapshot.degraded:
            point_warnings.add("SNAPSHOT_DEGRADED")
        if snapshot.net_assets is None:
            point_warnings.add("EQUITY_VALUE_UNAVAILABLE")
        if activation_at is None or snapshot.account_as_of < activation_at:
            point_warnings.add("RETROSPECTIVE_ENTRY")
        flow_value: Decimal | None
        if flows is None:
            flow_value = None
        else:
            lower = previous.account_as_of if previous is not None else snapshot.account_as_of
            flow_value = sum(
                (
                    item.cash_amount or Decimal(0)
                    for item in flows
                    if lower < item.occurred_at <= snapshot.account_as_of
                ),
                Decimal(0),
            )
        if flow_value is None:
            point_warnings.add("EXTERNAL_CASH_FLOW_COVERAGE_UNAVAILABLE")
        warnings.update(point_warnings)
        coverage = self._point_coverage(snapshot, point_warnings, flow_value)
        return DailyEquitySnapshot(
            daily_equity_snapshot_id=_stable_id(snapshot.snapshot_id, self.algorithm_version),
            account_ref=snapshot.account_ref,
            currency=snapshot.base_currency,
            valuation_at=snapshot.account_as_of,
            market_session_date=snapshot.account_as_of.date(),
            equity_value=snapshot.net_assets,
            source_snapshot_id=snapshot.snapshot_id,
            source_snapshot_as_of=snapshot.account_as_of,
            source_fetched_at=snapshot.fetched_at,
            valuation_basis="BROKER_NET_ASSETS",
            coverage_status=coverage,
            quality_status=coverage,
            materialized_at=require_aware_datetime(
                self._clock.now(),
                field_name="materialized_at",
            ),
            journal_activation_at=activation_at,
            # Cash is a source fact, but it never participates in equity_value.
            cash_value=snapshot.cash,
            gross_position_value=None,
            net_external_cash_flow_since_previous=flow_value,
            warning_codes=tuple(sorted(point_warnings)),
            algorithm_version=self.algorithm_version,
        )

    @staticmethod
    def _point_coverage(
        snapshot: AccountSnapshot,
        warnings: set[str],
        flow_value: object,
    ) -> DailyEquityCoverageStatus:
        if snapshot.net_assets is None:
            return DailyEquityCoverageStatus.UNAVAILABLE
        if snapshot.degraded or snapshot.warning_codes or flow_value is None:
            return DailyEquityCoverageStatus.PARTIAL
        if warnings:
            return DailyEquityCoverageStatus.PARTIAL
        return DailyEquityCoverageStatus.COMPLETE

    @staticmethod
    def _aggregate_coverage(
        values: tuple[DailyEquitySnapshot, ...],
    ) -> DailyEquityCoverageStatus:
        if not values:
            return DailyEquityCoverageStatus.UNAVAILABLE
        statuses = {item.coverage_status for item in values}
        if statuses == {DailyEquityCoverageStatus.COMPLETE}:
            return DailyEquityCoverageStatus.COMPLETE
        if statuses == {DailyEquityCoverageStatus.UNAVAILABLE}:
            return DailyEquityCoverageStatus.UNAVAILABLE
        if DailyEquityCoverageStatus.INCOMPLETE in statuses:
            return DailyEquityCoverageStatus.INCOMPLETE
        return DailyEquityCoverageStatus.PARTIAL


__all__ = ["DailyEquityMaterializationService"]
