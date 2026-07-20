"""Run the durable account and exact Watchlist refresh after a US session."""

from __future__ import annotations

from datetime import timedelta

from application.dto.portfolio import AccountGetSnapshotInput
from application.dto.post_market_sync import (
    PostMarketSyncDisposition,
    PostMarketSyncResultDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.market_session_calendar import MarketSessionCalendar
from application.ports.post_market_sync_run_repository import PostMarketSyncRunRepository
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from application.services.watchlist_hub_service import WatchlistHubService
from domain.common.ids import EntityIdPrefix
from domain.operations.enums import PostMarketSyncRunStatus, PostMarketSyncStepStatus
from domain.operations.models import PostMarketSyncRun


class PostMarketSyncService:
    def __init__(
        self,
        *,
        calendar: MarketSessionCalendar,
        repository: PostMarketSyncRunRepository,
        portfolio: PortfolioToolCoordinator,
        watchlist: WatchlistHubService,
        clock: Clock,
        id_generator: IdGenerator,
        delay_minutes: int = 10,
    ) -> None:
        self._calendar = calendar
        self._repository = repository
        self._portfolio = portfolio
        self._watchlist = watchlist
        self._clock = clock
        self._ids = id_generator
        self._delay = timedelta(minutes=delay_minutes)

    async def run_if_due(self) -> PostMarketSyncResultDTO:
        now = self._clock.now()
        session = self._calendar.session_at(now)
        if session is None:
            return PostMarketSyncResultDTO(
                disposition=PostMarketSyncDisposition.SKIPPED_NON_TRADING_DAY
            )
        scheduled_for = session.close_at + self._delay
        if now < scheduled_for:
            return PostMarketSyncResultDTO(
                disposition=PostMarketSyncDisposition.SKIPPED_NOT_DUE,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
            )
        existing = self._repository.get_for_session(session.session_date)
        if existing is not None and existing.status is PostMarketSyncRunStatus.SUCCEEDED:
            return self._dto(
                existing,
                disposition=PostMarketSyncDisposition.SKIPPED_ALREADY_COMPLETED,
            )

        started_at = self._clock.now()
        portfolio = await self._portfolio.get_account_snapshot(AccountGetSnapshotInput())
        watchlist = await self._watchlist.sync_all()
        completed_at = self._clock.now()

        portfolio_status = (
            PostMarketSyncStepStatus.SUCCEEDED if portfolio.ok else PostMarketSyncStepStatus.FAILED
        )
        watchlist_status = (
            PostMarketSyncStepStatus.SUCCEEDED if watchlist.ok else PostMarketSyncStepStatus.FAILED
        )
        status = (
            PostMarketSyncRunStatus.SUCCEEDED
            if portfolio.ok and watchlist.ok
            else PostMarketSyncRunStatus.FAILED
            if not portfolio.ok and not watchlist.ok
            else PostMarketSyncRunStatus.PARTIAL
        )
        account_snapshot_ids = (
            tuple(item.snapshot_id for item in portfolio.data.snapshots)
            if portfolio.ok and portfolio.data is not None
            else ()
        )
        warnings = tuple(
            dict.fromkeys(
                [item.code for item in portfolio.warnings]
                + [item.code for item in watchlist.warnings]
            )
        )
        errors = tuple(
            dict.fromkeys(
                [item.code for item in portfolio.errors]
                + [item.code for item in watchlist.errors]
            )
        )
        if status is not PostMarketSyncRunStatus.SUCCEEDED and not errors:
            errors = ("POST_MARKET_SYNC_STEP_FAILED",)
        run = PostMarketSyncRun(
            run_id=(
                existing.run_id
                if existing is not None
                else self._ids.new(EntityIdPrefix.RUN)
            ),
            market_session_date=session.session_date,
            scheduled_for=scheduled_for,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            portfolio_status=portfolio_status,
            watchlist_status=watchlist_status,
            account_snapshot_ids=account_snapshot_ids,
            watchlist_groups_synced=(
                watchlist.data.groups_synced
                if watchlist.ok and watchlist.data is not None
                else None
            ),
            watchlist_membership_relations_synced=(
                watchlist.data.membership_relations_synced
                if watchlist.ok and watchlist.data is not None
                else None
            ),
            warning_codes=warnings,
            error_codes=errors,
            attempt_count=1 if existing is None else existing.attempt_count + 1,
        )
        self._repository.save(run)
        holding_count = (
            sum(len(item.positions) for item in portfolio.data.snapshots)
            if portfolio.ok and portfolio.data is not None
            else 0
        )
        return self._dto(run, holding_count=holding_count)

    @staticmethod
    def _dto(
        run: PostMarketSyncRun,
        *,
        disposition: PostMarketSyncDisposition = PostMarketSyncDisposition.EXECUTED,
        holding_count: int = 0,
    ) -> PostMarketSyncResultDTO:
        return PostMarketSyncResultDTO(
            disposition=disposition,
            market_session_date=run.market_session_date,
            scheduled_for=run.scheduled_for,
            run_id=run.run_id,
            run_status=run.status,
            portfolio_status=run.portfolio_status,
            watchlist_status=run.watchlist_status,
            account_snapshot_ids=run.account_snapshot_ids,
            holding_count=holding_count,
            watchlist_groups_synced=run.watchlist_groups_synced,
            watchlist_membership_relations_synced=run.watchlist_membership_relations_synced,
            warning_codes=run.warning_codes,
            error_codes=run.error_codes,
        )
