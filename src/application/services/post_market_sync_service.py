"""Run the durable account and exact Watchlist refresh after a US session."""

from __future__ import annotations

from datetime import datetime, timedelta

from application.dto.account_transactions import AccountGetTransactionsInput
from application.dto.portfolio import AccountGetSnapshotInput
from application.dto.post_market_sync import (
    PostMarketSyncDisposition,
    PostMarketSyncHealth,
    PostMarketSyncResultDTO,
    PostMarketSyncStatusDTO,
)
from application.dto.schwab_oauth import SchwabOAuthHealthDTO
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.market_session_calendar import MarketSession, MarketSessionCalendar
from application.ports.post_market_sync_run_repository import PostMarketSyncRunRepository
from application.ports.schwab_oauth_health_provider import SchwabOAuthHealthProvider
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.activity_annotation_service import ActivityAnnotationService
from application.services.daily_equity_materialization_service import (
    DailyEquityMaterializationService,
)
from application.services.external_note_sync_service import ExternalNoteSyncService
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from application.services.watchlist_hub_service import WatchlistHubService
from domain.common.ids import EntityIdPrefix
from domain.external_note.enums import NoteSyncStatus
from domain.operations.enums import PostMarketSyncRunStatus, PostMarketSyncStepStatus
from domain.operations.models import PostMarketSyncRun
from domain.performance.enums import DailyEquityMaterializationMode


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
        schwab_oauth_health: SchwabOAuthHealthProvider | None = None,
        transactions: AccountTransactionCoordinator | None = None,
        activity_annotations: ActivityAnnotationService | None = None,
        account_snapshots: AccountSnapshotRepository | None = None,
        transaction_repository: AccountTransactionRepository | None = None,
        daily_equity: DailyEquityMaterializationService | None = None,
        external_notes: ExternalNoteSyncService | None = None,
    ) -> None:
        self._calendar = calendar
        self._repository = repository
        self._portfolio = portfolio
        self._watchlist = watchlist
        self._clock = clock
        self._ids = id_generator
        self._delay = timedelta(minutes=delay_minutes)
        self._schwab_oauth_health = schwab_oauth_health
        self._transactions = transactions
        # Kept as a compatibility constructor argument. Unlinked Activity is
        # read-only and no longer materializes Review Queue work.
        _ = activity_annotations
        self._account_snapshots = account_snapshots
        self._transaction_repository = transaction_repository
        self._daily_equity = daily_equity
        self._external_notes = external_notes

    async def run_if_due(self) -> PostMarketSyncResultDTO:
        now = self._clock.now()
        schwab_oauth = self._inspect_schwab_oauth(now)
        session = self._calendar.session_at(now)
        if session is None:
            return PostMarketSyncResultDTO(
                disposition=PostMarketSyncDisposition.SKIPPED_NON_TRADING_DAY,
                schwab_oauth=schwab_oauth,
                warning_codes=schwab_oauth.warning_codes if schwab_oauth else (),
            )
        scheduled_for = session.close_at + self._delay
        if now < scheduled_for:
            return PostMarketSyncResultDTO(
                disposition=PostMarketSyncDisposition.SKIPPED_NOT_DUE,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
                schwab_oauth=schwab_oauth,
                warning_codes=schwab_oauth.warning_codes if schwab_oauth else (),
            )
        existing = self._repository.get_for_session(session.session_date)
        if existing is not None and existing.status is PostMarketSyncRunStatus.SUCCEEDED:
            return self._dto(
                existing,
                disposition=PostMarketSyncDisposition.SKIPPED_ALREADY_COMPLETED,
                schwab_oauth=schwab_oauth,
            )

        return await self._execute(
            session,
            scheduled_for,
            existing,
            schwab_oauth=schwab_oauth,
        )

    def status(self) -> PostMarketSyncStatusDTO:
        now = self._clock.now()
        schwab_oauth = self._inspect_schwab_oauth(now)
        oauth_warnings = schwab_oauth.warning_codes if schwab_oauth else ()
        expected = self._latest_due_session(now)
        latest = self._repository.get_latest()
        if expected is None:
            return PostMarketSyncStatusDTO(
                health=PostMarketSyncHealth.NO_DUE_SESSION,
                receipt_session_date=(latest.market_session_date if latest else None),
                run_status=latest.status if latest else None,
                portfolio_status=latest.portfolio_status if latest else None,
                watchlist_status=latest.watchlist_status if latest else None,
                observation_status=latest.observation_status if latest else None,
                attempt_count=latest.attempt_count if latest else None,
                schwab_oauth=schwab_oauth,
                warning_codes=tuple(
                    dict.fromkeys(
                        (latest.warning_codes if latest else ()) + oauth_warnings
                    )
                ),
                error_codes=latest.error_codes if latest else (),
            )
        receipt = self._repository.get_for_session(expected.session_date)
        if receipt is None:
            return PostMarketSyncStatusDTO(
                health=PostMarketSyncHealth.RECEIPT_MISSING,
                expected_session_date=expected.session_date,
                expected_scheduled_for=expected.close_at + self._delay,
                receipt_session_date=(latest.market_session_date if latest else None),
                schwab_oauth=schwab_oauth,
                warning_codes=oauth_warnings,
                error_codes=("POST_MARKET_SYNC_RECEIPT_MISSING",),
            )
        health = (
            PostMarketSyncHealth.HEALTHY
            if receipt.status is PostMarketSyncRunStatus.SUCCEEDED
            else PostMarketSyncHealth.RECEIPT_IMPERFECT
        )
        return PostMarketSyncStatusDTO(
            health=health,
            expected_session_date=expected.session_date,
            expected_scheduled_for=expected.close_at + self._delay,
            receipt_session_date=receipt.market_session_date,
            run_status=receipt.status,
            portfolio_status=receipt.portfolio_status,
            watchlist_status=receipt.watchlist_status,
            observation_status=receipt.observation_status,
            attempt_count=receipt.attempt_count,
            schwab_oauth=schwab_oauth,
            warning_codes=tuple(
                dict.fromkeys(receipt.warning_codes + oauth_warnings)
            ),
            error_codes=receipt.error_codes,
        )

    def recent_runs(self, limit: int = 20) -> tuple[PostMarketSyncRun, ...]:
        """Return bounded durable receipts without refreshing either upstream."""

        return self._repository.list_recent(max(1, min(limit, 100)))

    async def catch_up_latest_due(self) -> PostMarketSyncResultDTO:
        now = self._clock.now()
        schwab_oauth = self._inspect_schwab_oauth(now)
        session = self._latest_due_session(now)
        if session is None:
            return PostMarketSyncResultDTO(
                disposition=PostMarketSyncDisposition.SKIPPED_NON_TRADING_DAY,
                schwab_oauth=schwab_oauth,
                warning_codes=schwab_oauth.warning_codes if schwab_oauth else (),
            )
        scheduled_for = session.close_at + self._delay
        existing = self._repository.get_for_session(session.session_date)
        if existing is not None and existing.status is PostMarketSyncRunStatus.SUCCEEDED:
            return self._dto(
                existing,
                disposition=PostMarketSyncDisposition.SKIPPED_ALREADY_COMPLETED,
                schwab_oauth=schwab_oauth,
            )
        return await self._execute(
            session,
            scheduled_for,
            existing,
            schwab_oauth=schwab_oauth,
        )

    def _latest_due_session(self, now: datetime) -> MarketSession | None:
        candidate = self._calendar.session_on_or_before(now)
        if candidate is None:
            return None
        if candidate.close_at + self._delay <= now:
            return candidate
        return self._calendar.previous_session(candidate.session_date)

    async def _execute(
        self,
        session: MarketSession,
        scheduled_for: datetime,
        existing: PostMarketSyncRun | None,
        *,
        schwab_oauth: SchwabOAuthHealthDTO | None,
    ) -> PostMarketSyncResultDTO:
        started_at = self._clock.now()
        portfolio = await self._portfolio.get_account_snapshot(AccountGetSnapshotInput())
        transactions_ok = True
        transaction_error_codes: list[str] = []
        if self._transactions is not None:
            transaction_result = await self._transactions.get_transactions(
                AccountGetTransactionsInput(end=self._clock.now(), limit=1_000)
            )
            transactions_ok = transaction_result.ok
            transaction_error_codes.extend(item.code for item in transaction_result.errors)
        if (
            portfolio.ok
            and portfolio.data is not None
            and transactions_ok
            and self._daily_equity is not None
            and self._account_snapshots is not None
            and self._transaction_repository is not None
        ):
            try:
                if self._daily_equity.get_activation() is None:
                    self._daily_equity.activate(
                        actor="system",
                        idempotency_key="phase4_post_market_activation_v1",
                    )
                snapshots = tuple(
                    value
                    for item in portfolio.data.snapshots
                    if (value := self._account_snapshots.get_account(item.snapshot_id)) is not None
                )
                self._daily_equity.materialize(
                    snapshots=snapshots,
                    transactions=self._transaction_repository.list(
                        providers=(), start=None, end=self._clock.now(), limit=None
                    ),
                    mode=DailyEquityMaterializationMode.PERSIST,
                )
            except Exception:  # noqa: BLE001 - incomplete projection must remain visible
                transactions_ok = False
                transaction_error_codes.append("DAILY_EQUITY_MATERIALIZATION_FAILED")
        watchlist = await self._watchlist.sync_all()
        observation_receipt = None
        observation_error_codes: list[str] = []
        if self._external_notes is not None:
            try:
                observation_receipt = await self._external_notes.sync(
                    analyze=True,
                    source_code="MOOMOO_NOTE",
                )
            except Exception:  # noqa: BLE001 - other post-market steps remain usable
                observation_error_codes.append("POST_MARKET_OBSERVATION_SYNC_FAILED")
        completed_at = self._clock.now()

        portfolio_status = (
            PostMarketSyncStepStatus.SUCCEEDED
            if portfolio.ok and transactions_ok
            else PostMarketSyncStepStatus.FAILED
        )
        watchlist_status = (
            PostMarketSyncStepStatus.SUCCEEDED if watchlist.ok else PostMarketSyncStepStatus.FAILED
        )
        observation_status = (
            None
            if self._external_notes is None
            else PostMarketSyncStepStatus.SUCCEEDED
            if observation_receipt is not None
            and observation_receipt.status is not NoteSyncStatus.FAILED
            else PostMarketSyncStepStatus.FAILED
        )
        step_statuses = [portfolio_status, watchlist_status]
        if observation_status is not None:
            step_statuses.append(observation_status)
        status = (
            PostMarketSyncRunStatus.SUCCEEDED
            if all(item is PostMarketSyncStepStatus.SUCCEEDED for item in step_statuses)
            else PostMarketSyncRunStatus.FAILED
            if all(item is PostMarketSyncStepStatus.FAILED for item in step_statuses)
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
                + (
                    list(observation_receipt.warning_codes)
                    + list(observation_receipt.error_codes)
                    if observation_receipt is not None
                    and observation_status is PostMarketSyncStepStatus.SUCCEEDED
                    else []
                )
                + (
                    list(schwab_oauth.warning_codes)
                    if schwab_oauth is not None
                    else []
                )
            )
        )
        errors = tuple(
            dict.fromkeys(
                [item.code for item in portfolio.errors]
                + [item.code for item in watchlist.errors]
                + transaction_error_codes
                + (
                    list(observation_receipt.error_codes)
                    if observation_receipt is not None
                    and observation_status is PostMarketSyncStepStatus.FAILED
                    else observation_error_codes
                )
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
            observation_status=observation_status,
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
            observation_notes_seen=(
                observation_receipt.notes_seen if observation_receipt is not None else None
            ),
            observation_revisions_created=(
                observation_receipt.revisions_created
                if observation_receipt is not None
                else None
            ),
            observation_full_count=(
                observation_receipt.full_count if observation_receipt is not None else None
            ),
            observation_summary_only_count=(
                observation_receipt.summary_only_count
                if observation_receipt is not None
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
        return self._dto(
            run,
            holding_count=holding_count,
            schwab_oauth=schwab_oauth,
        )

    def _dto(
        self,
        run: PostMarketSyncRun,
        *,
        disposition: PostMarketSyncDisposition = PostMarketSyncDisposition.EXECUTED,
        holding_count: int = 0,
        schwab_oauth: SchwabOAuthHealthDTO | None = None,
    ) -> PostMarketSyncResultDTO:
        oauth_health = (
            schwab_oauth
            if schwab_oauth is not None
            else self._inspect_schwab_oauth(self._clock.now())
        )
        return PostMarketSyncResultDTO(
            disposition=disposition,
            market_session_date=run.market_session_date,
            scheduled_for=run.scheduled_for,
            run_id=run.run_id,
            run_status=run.status,
            portfolio_status=run.portfolio_status,
            watchlist_status=run.watchlist_status,
            observation_status=run.observation_status,
            account_snapshot_ids=run.account_snapshot_ids,
            holding_count=holding_count,
            watchlist_groups_synced=run.watchlist_groups_synced,
            watchlist_membership_relations_synced=run.watchlist_membership_relations_synced,
            observation_notes_seen=run.observation_notes_seen,
            observation_revisions_created=run.observation_revisions_created,
            observation_full_count=run.observation_full_count,
            observation_summary_only_count=run.observation_summary_only_count,
            schwab_oauth=oauth_health,
            warning_codes=tuple(
                dict.fromkeys(
                    run.warning_codes
                    + (
                        oauth_health.warning_codes
                        if oauth_health is not None
                        else ()
                    )
                )
            ),
            error_codes=run.error_codes,
        )

    def _inspect_schwab_oauth(self, now: datetime) -> SchwabOAuthHealthDTO | None:
        if self._schwab_oauth_health is None:
            return None
        return self._schwab_oauth_health.inspect(now=now)
