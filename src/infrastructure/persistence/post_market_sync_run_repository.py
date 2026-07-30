"""Engine-bound persistence for terminal post-market synchronization receipts."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from domain.operations.enums import PostMarketSyncRunStatus, PostMarketSyncStepStatus
from domain.operations.models import PostMarketSyncRun
from infrastructure.persistence.orm import PostMarketSyncRunRow
from infrastructure.persistence.repositories._mapping import (
    date_to_db,
    dt_from_db,
    dt_to_db,
)


class SqlAlchemyPostMarketSyncRunRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_for_session(self, session_date: date) -> PostMarketSyncRun | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(PostMarketSyncRunRow).where(
                    PostMarketSyncRunRow.market_session_date == date_to_db(session_date)
                )
            )
            return None if row is None else self._domain(row)

    def get_latest(self) -> PostMarketSyncRun | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(PostMarketSyncRunRow)
                .order_by(PostMarketSyncRunRow.market_session_date.desc())
                .limit(1)
            )
            return None if row is None else self._domain(row)

    def save(self, run: PostMarketSyncRun) -> PostMarketSyncRun:
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(PostMarketSyncRunRow).where(
                    PostMarketSyncRunRow.market_session_date
                    == date_to_db(run.market_session_date)
                )
            )
            if row is None:
                row = PostMarketSyncRunRow(
                    run_id=run.run_id,
                    market_session_date=date_to_db(run.market_session_date),
                    scheduled_for=dt_to_db(run.scheduled_for),
                    started_at=dt_to_db(run.started_at),
                    completed_at=dt_to_db(run.completed_at),
                    status=run.status.value,
                    portfolio_status=run.portfolio_status.value,
                    watchlist_status=run.watchlist_status.value,
                    account_snapshot_ids=run.account_snapshot_ids,
                    watchlist_groups_synced=run.watchlist_groups_synced,
                    watchlist_membership_relations_synced=(
                        run.watchlist_membership_relations_synced
                    ),
                    warning_codes=run.warning_codes,
                    error_codes=run.error_codes,
                    attempt_count=run.attempt_count,
                )
                session.add(row)
            else:
                row.scheduled_for = dt_to_db(run.scheduled_for)
                row.started_at = dt_to_db(run.started_at)
                row.completed_at = dt_to_db(run.completed_at)
                row.status = run.status.value
                row.portfolio_status = run.portfolio_status.value
                row.watchlist_status = run.watchlist_status.value
                row.account_snapshot_ids = run.account_snapshot_ids
                row.watchlist_groups_synced = run.watchlist_groups_synced
                row.watchlist_membership_relations_synced = (
                    run.watchlist_membership_relations_synced
                )
                row.warning_codes = run.warning_codes
                row.error_codes = run.error_codes
                row.attempt_count = run.attempt_count
        return run

    @staticmethod
    def _domain(row: PostMarketSyncRunRow) -> PostMarketSyncRun:
        return PostMarketSyncRun(
            run_id=row.run_id,
            market_session_date=date.fromisoformat(row.market_session_date),
            scheduled_for=dt_from_db(row.scheduled_for, field_name="scheduled_for"),
            started_at=dt_from_db(row.started_at, field_name="started_at"),
            completed_at=dt_from_db(row.completed_at, field_name="completed_at"),
            status=PostMarketSyncRunStatus(row.status),
            portfolio_status=PostMarketSyncStepStatus(row.portfolio_status),
            watchlist_status=PostMarketSyncStepStatus(row.watchlist_status),
            account_snapshot_ids=row.account_snapshot_ids,
            watchlist_groups_synced=row.watchlist_groups_synced,
            watchlist_membership_relations_synced=(
                row.watchlist_membership_relations_synced
            ),
            warning_codes=row.warning_codes,
            error_codes=row.error_codes,
            attempt_count=row.attempt_count,
        )
