"""SQLAlchemy persistence for Journal activation and Daily Equity projections."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.daily_equity_repository import (
    DailyEquityRepository,
    JournalActivationRepository,
)
from domain.common.errors import PersistenceError
from domain.common.time import require_aware_datetime
from domain.performance.daily_equity import (
    DailyEquityMaterializationWriteResult,
    DailyEquitySnapshot,
    JournalActivation,
)
from domain.performance.enums import DailyEquityCoverageStatus
from infrastructure.persistence.orm import DailyEquitySnapshotRow, JournalActivationRow
from infrastructure.persistence.repositories._mapping import (
    date_from_db,
    decimal_from_db,
    decimal_to_db,
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _activation(row: JournalActivationRow) -> JournalActivation:
    return JournalActivation(
        activation_id=row.activation_id,
        journal_activation_at=dt_from_db(
            row.journal_activation_at,
            field_name="journal_activation_at",
        ),
        recorded_at=dt_from_db(row.recorded_at, field_name="recorded_at"),
        actor=row.actor,
        idempotency_key=row.idempotency_key,
        algorithm_version=row.algorithm_version,
    )


def _daily_equity(row: DailyEquitySnapshotRow) -> DailyEquitySnapshot:
    market_session_date = date_from_db(row.market_session_date)
    assert market_session_date is not None
    return DailyEquitySnapshot(
        daily_equity_snapshot_id=row.daily_equity_snapshot_id,
        account_ref=row.account_ref,
        currency=row.currency,
        valuation_at=dt_from_db(row.valuation_at, field_name="valuation_at"),
        market_session_date=market_session_date,
        equity_value=decimal_from_db(row.equity_value),
        source_snapshot_id=row.source_snapshot_id,
        source_snapshot_as_of=dt_from_db(
            row.source_snapshot_as_of,
            field_name="source_snapshot_as_of",
        ),
        source_fetched_at=dt_from_db(
            row.source_fetched_at,
            field_name="source_fetched_at",
        ),
        valuation_basis=row.valuation_basis,
        coverage_status=DailyEquityCoverageStatus(row.coverage_status),
        quality_status=DailyEquityCoverageStatus(row.quality_status),
        materialized_at=dt_from_db(row.materialized_at, field_name="materialized_at"),
        journal_activation_at=dt_opt_from_db(
            row.journal_activation_at,
            field_name="journal_activation_at",
        ),
        cash_value=decimal_from_db(row.cash_value),
        gross_position_value=decimal_from_db(row.gross_position_value),
        net_external_cash_flow_since_previous=decimal_from_db(
            row.net_external_cash_flow_since_previous
        ),
        warning_codes=tuple(row.warning_codes),
        algorithm_version=row.algorithm_version,
    )


def _daily_row(value: DailyEquitySnapshot) -> DailyEquitySnapshotRow:
    return DailyEquitySnapshotRow(
        daily_equity_snapshot_id=value.daily_equity_snapshot_id,
        account_ref=value.account_ref,
        currency=value.currency,
        valuation_at=dt_to_db(value.valuation_at),
        market_session_date=value.market_session_date.isoformat(),
        equity_value=decimal_to_db(value.equity_value),
        cash_value=decimal_to_db(value.cash_value),
        gross_position_value=decimal_to_db(value.gross_position_value),
        net_external_cash_flow_since_previous=decimal_to_db(
            value.net_external_cash_flow_since_previous
        ),
        valuation_basis=value.valuation_basis,
        source_snapshot_id=value.source_snapshot_id,
        source_snapshot_as_of=dt_to_db(value.source_snapshot_as_of),
        source_fetched_at=dt_to_db(value.source_fetched_at),
        journal_activation_at=dt_opt_to_db(value.journal_activation_at),
        coverage_status=value.coverage_status.value,
        quality_status=value.quality_status.value,
        materialized_at=dt_to_db(value.materialized_at),
        warning_codes=value.warning_codes,
        algorithm_version=value.algorithm_version,
    )


def _same_projection(left: DailyEquitySnapshot, right: DailyEquitySnapshot) -> bool:
    """Compare source-derived payload; materialized_at is a write timestamp."""

    return (
        left.daily_equity_snapshot_id == right.daily_equity_snapshot_id
        and left.account_ref == right.account_ref
        and left.currency == right.currency
        and left.valuation_at == right.valuation_at
        and left.market_session_date == right.market_session_date
        and left.equity_value == right.equity_value
        and left.cash_value == right.cash_value
        and left.gross_position_value == right.gross_position_value
        and left.net_external_cash_flow_since_previous
        == right.net_external_cash_flow_since_previous
        and left.valuation_basis == right.valuation_basis
        and left.source_snapshot_id == right.source_snapshot_id
        and left.source_snapshot_as_of == right.source_snapshot_as_of
        and left.source_fetched_at == right.source_fetched_at
        and left.journal_activation_at == right.journal_activation_at
        and left.coverage_status == right.coverage_status
        and left.quality_status == right.quality_status
        and left.warning_codes == right.warning_codes
        and left.algorithm_version == right.algorithm_version
    )


class SqlAlchemyDailyEquityRepository(DailyEquityRepository, JournalActivationRepository):
    """Idempotent source-reference persistence for Daily Equity facts."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_activation(self) -> JournalActivation | None:
        with Session(self._engine) as session:
            row = session.get(JournalActivationRow, "journal_activation")
            return _activation(row) if row is not None else None

    def activate(self, value: JournalActivation) -> JournalActivation:
        if value.activation_id != "journal_activation":
            raise PersistenceError("Journal activation id must be journal_activation")
        try:
            with Session(self._engine) as session, session.begin():
                row = session.get(JournalActivationRow, value.activation_id)
                if row is not None:
                    existing = _activation(row)
                    if existing.journal_activation_at != value.journal_activation_at:
                        raise PersistenceError(
                            "Journal activation is already fixed",
                            code="JOURNAL_ACTIVATION_CONFLICT",
                        )
                    return existing
                session.add(
                    JournalActivationRow(
                        activation_id=value.activation_id,
                        journal_activation_at=dt_to_db(value.journal_activation_at),
                        recorded_at=dt_to_db(value.recorded_at),
                        actor=value.actor,
                        idempotency_key=value.idempotency_key,
                        algorithm_version=value.algorithm_version,
                    )
                )
                session.flush()
                return value
        except IntegrityError as exc:
            raced = self.get_activation()
            if raced is not None:
                if raced.journal_activation_at == value.journal_activation_at:
                    return raced
                raise PersistenceError(
                    "Journal activation is already fixed",
                    code="JOURNAL_ACTIVATION_CONFLICT",
                ) from exc
            raise PersistenceError("Journal activation append conflict") from exc

    # Short aliases used by materialization callers.
    get_journal_activation = get_activation
    set_activation = activate

    def get(self, daily_equity_snapshot_id: str) -> DailyEquitySnapshot | None:
        with Session(self._engine) as session:
            row = session.get(DailyEquitySnapshotRow, daily_equity_snapshot_id)
            return _daily_equity(row) if row is not None else None

    def get_by_source_snapshot(
        self,
        *,
        source_snapshot_id: str,
        algorithm_version: str = "daily_equity_v1",
    ) -> DailyEquitySnapshot | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(DailyEquitySnapshotRow).where(
                    DailyEquitySnapshotRow.source_snapshot_id == source_snapshot_id,
                    DailyEquitySnapshotRow.algorithm_version == algorithm_version,
                )
            )
            return _daily_equity(row) if row is not None else None

    def append(self, value: DailyEquitySnapshot) -> DailyEquitySnapshot:
        return self.append_many((value,)).snapshots[0]

    def append_many(
        self,
        values: tuple[DailyEquitySnapshot, ...],
    ) -> DailyEquityMaterializationWriteResult:
        if not values:
            return DailyEquityMaterializationWriteResult((), 0, 0)
        output: list[DailyEquitySnapshot] = []
        inserted_count = 0
        duplicate_count = 0
        try:
            with Session(self._engine) as session, session.begin():
                for value in values:
                    existing_row = session.scalar(
                        select(DailyEquitySnapshotRow).where(
                            DailyEquitySnapshotRow.source_snapshot_id
                            == value.source_snapshot_id,
                            DailyEquitySnapshotRow.algorithm_version
                            == value.algorithm_version,
                        )
                    )
                    if existing_row is not None:
                        existing = _daily_equity(existing_row)
                        if not _same_projection(existing, value):
                            raise PersistenceError(
                                "Daily Equity source projection conflicts with existing row",
                                code="DAILY_EQUITY_PROJECTION_CONFLICT",
                            )
                        output.append(existing)
                        duplicate_count += 1
                        continue
                    session.add(_daily_row(value))
                    session.flush()
                    output.append(value)
                    inserted_count += 1
            return DailyEquityMaterializationWriteResult(
                tuple(output),
                inserted_count,
                duplicate_count,
            )
        except IntegrityError as exc:
            raise PersistenceError("Daily Equity projection append conflict") from exc

    # Explicit spelling for shadow/materialization callers.
    append_batch = append_many

    def list(
        self,
        *,
        account_refs: tuple[str, ...] = (),
        currencies: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[DailyEquitySnapshot, ...]:
        if start is not None:
            require_aware_datetime(start, field_name="start")
        if end is not None:
            require_aware_datetime(end, field_name="end")
        if start is not None and end is not None and start > end:
            raise ValueError("start must be <= end")
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer")
        with Session(self._engine) as session:
            statement = select(DailyEquitySnapshotRow).order_by(
                DailyEquitySnapshotRow.account_ref,
                DailyEquitySnapshotRow.currency,
                DailyEquitySnapshotRow.valuation_at,
                DailyEquitySnapshotRow.daily_equity_snapshot_id,
            )
            if account_refs:
                statement = statement.where(
                    DailyEquitySnapshotRow.account_ref.in_(account_refs)
                )
            if currencies:
                statement = statement.where(
                    DailyEquitySnapshotRow.currency.in_(currencies)
                )
            if start is not None:
                statement = statement.where(
                    DailyEquitySnapshotRow.valuation_at >= dt_to_db(start)
                )
            if end is not None:
                statement = statement.where(
                    DailyEquitySnapshotRow.valuation_at <= dt_to_db(end)
                )
            if limit is not None:
                statement = statement.limit(limit)
            return tuple(_daily_equity(row) for row in session.scalars(statement))

    list_history = list


SqlAlchemyDailyEquitySnapshotRepository = SqlAlchemyDailyEquityRepository
SqlAlchemyJournalActivationRepository = SqlAlchemyDailyEquityRepository
