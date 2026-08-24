"""Focused due-time and orchestration tests for the post-market sync job."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from application.dto.post_market_sync import (
    PostMarketSyncDisposition,
    PostMarketSyncHealth,
    PostMarketSyncResultDTO,
    PostMarketSyncStatusDTO,
)
from application.dto.schwab_oauth import (
    SchwabOAuthHealthDTO,
    SchwabOAuthHealthState,
)
from application.ports.market_session_calendar import MarketSession
from application.services.operational_job_runtime import OperationalJobExecution
from application.services.post_market_sync_service import PostMarketSyncService
from domain.operations.enums import PostMarketSyncRunStatus
from domain.operations.models import OperationalJobRun, PostMarketSyncRun
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.system.process_file_lock import ProcessFileLock
from interfaces.cli import post_market_sync as post_market_sync_cli


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class _Calendar:
    def __init__(self, session: MarketSession | None) -> None:
        self.session = session

    def session_at(self, moment: datetime) -> MarketSession | None:
        return self.session

    def session_on_or_before(self, moment: datetime) -> MarketSession | None:
        return self.session

    def previous_session(self, session_date: date) -> MarketSession | None:
        return self.session


class _Repository:
    def __init__(self, existing: PostMarketSyncRun | None = None) -> None:
        self.value = existing

    def get_for_session(self, session_date: date) -> PostMarketSyncRun | None:
        return self.value

    def get_latest(self) -> PostMarketSyncRun | None:
        return self.value

    def save(self, run: PostMarketSyncRun) -> PostMarketSyncRun:
        self.value = run
        return run


class _Ids:
    def new(self, prefix: object) -> str:
        return "run_00000000-0000-7000-8000-000000000001"


class _SchwabOAuthHealth:
    def inspect(self, *, now: datetime) -> SchwabOAuthHealthDTO:
        return SchwabOAuthHealthDTO(
            state=SchwabOAuthHealthState.EXPIRING,
            checked_at=now,
            token_created_at=now,
            token_age_seconds=5 * 24 * 60 * 60,
            reauthorization_due_at=now,
            seconds_until_reauthorization=2 * 24 * 60 * 60,
            warning_codes=("SCHWAB_OAUTH_REAUTH_DUE_SOON",),
        )


class _Portfolio:
    def __init__(self, calls: list[str], *, ok: bool = True) -> None:
        self.calls = calls
        self.ok = ok

    async def get_account_snapshot(self, request: object) -> SimpleNamespace:
        self.calls.append("portfolio")
        snapshots = (SimpleNamespace(snapshot_id="snapshot_1", positions=(object(), object())),)
        return SimpleNamespace(
            ok=self.ok,
            data=SimpleNamespace(snapshots=snapshots) if self.ok else None,
            warnings=(),
            errors=() if self.ok else (SimpleNamespace(code="ACCOUNT_READ_FAILED"),),
        )


class _Watchlist:
    def __init__(self, calls: list[str], *, ok: bool = True) -> None:
        self.calls = calls
        self.ok = ok

    async def sync_all(self) -> SimpleNamespace:
        self.calls.append("watchlist")
        return SimpleNamespace(
            ok=self.ok,
            data=(
                SimpleNamespace(groups_synced=24, membership_relations_synced=143)
                if self.ok
                else None
            ),
            warnings=(),
            errors=() if self.ok else (SimpleNamespace(code="WATCHLIST_READ_FAILED"),),
        )


class _Transactions:
    def __init__(self, calls: list[str], *, ok: bool = True) -> None:
        self.calls = calls
        self.ok = ok

    async def get_transactions(self, request: object) -> SimpleNamespace:
        self.calls.append("transactions")
        return SimpleNamespace(
            ok=self.ok,
            errors=() if self.ok else (SimpleNamespace(code="TRANSACTION_READ_FAILED"),),
        )


class _Annotations:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def sync_unlinked(self, *, limit: int) -> None:
        assert limit == 500
        self.calls.append("unlinked")


class _SnapshotRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def get_account(self, snapshot_id: str) -> object:
        self.calls.append("read-snapshot")
        return SimpleNamespace(snapshot_id=snapshot_id)


class _TransactionRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def list(self, **_kwargs: object) -> tuple[object, ...]:
        self.calls.append("read-transactions")
        return ()


class _DailyEquity:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def get_activation(self) -> None:
        return None

    def activate(self, **_kwargs: object) -> None:
        self.calls.append("activate-journal")

    def materialize(self, **_kwargs: object) -> None:
        self.calls.append("daily-equity")


class _ResultService:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    async def run_if_due(self) -> Any:
        self.calls += 1
        return self.result

    async def catch_up_latest_due(self) -> Any:
        self.calls += 1
        return self.result

    def status(self) -> PostMarketSyncStatusDTO:
        self.calls += 1
        return PostMarketSyncStatusDTO(
            health=PostMarketSyncHealth.HEALTHY,
            expected_session_date=date(2026, 7, 17),
            receipt_session_date=date(2026, 7, 17),
            run_status=PostMarketSyncRunStatus.SUCCEEDED,
        )


class _CliContainer:
    def __init__(self, lock_path: Path, result: Any) -> None:
        now = datetime(2026, 7, 17, 20, 10, tzinfo=UTC)
        self.resources = SimpleNamespace(post_market_sync_lock=ProcessFileLock(lock_path))
        self.context = SimpleNamespace(clock=_Clock(now))

        class Jobs:
            async def execute(self, **kwargs: Any) -> OperationalJobExecution[Any]:
                outcome = await kwargs["operation"]()
                run = OperationalJobRun(
                    job_run_id="operational_job_run_test",
                    job_name=kwargs["job_name"],
                    idempotency_key=kwargs["idempotency_key"],
                    status=outcome.status,
                    attempt=1,
                    lease_owner_hash="a" * 32,
                    lease_expires_at=now,
                    heartbeat_at=now,
                    started_at=now,
                    updated_at=now,
                    completed_at=now,
                    result_code=outcome.result_code,
                    error_code=outcome.error_code,
                    version=2,
                )
                return OperationalJobExecution(run=run, invoked=True, value=outcome.value)

        self.operations = SimpleNamespace(
            post_market_sync=_ResultService(result),
            jobs=Jobs(),
        )
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _service(
    *,
    now: datetime,
    close_at: datetime,
    repository: _Repository | None = None,
    portfolio_ok: bool = True,
    watchlist_ok: bool = True,
    schwab_oauth_health: object | None = None,
) -> tuple[PostMarketSyncService, _Repository, list[str]]:
    calls: list[str] = []
    repo = repository or _Repository()
    service = PostMarketSyncService(
        calendar=_Calendar(MarketSession(date(2026, 7, 17), close_at)),
        repository=repo,
        portfolio=_Portfolio(calls, ok=portfolio_ok),
        watchlist=_Watchlist(calls, ok=watchlist_ok),
        clock=_Clock(now),
        id_generator=_Ids(),
        schwab_oauth_health=schwab_oauth_health,
    )
    return service, repo, calls


def _run_result(
    disposition: PostMarketSyncDisposition = PostMarketSyncDisposition.EXECUTED,
    run_status: PostMarketSyncRunStatus | None = PostMarketSyncRunStatus.SUCCEEDED,
    holding_count: int = 1,
    error_codes: tuple[str, ...] = (),
    warning_codes: tuple[str, ...] = (),
) -> PostMarketSyncResultDTO:
    return PostMarketSyncResultDTO(
        disposition=disposition,
        market_session_date=date(2026, 7, 17),
        scheduled_for=datetime(2026, 7, 17, 20, 10, tzinfo=UTC),
        run_id="run_00000000-0000-7000-8000-000000000001",
        run_status=run_status,
        portfolio_status=None,
        watchlist_status=None,
        account_snapshot_ids=("snapshot_1",),
        holding_count=holding_count,
        watchlist_groups_synced=1,
        watchlist_membership_relations_synced=10,
        warning_codes=warning_codes,
        error_codes=error_codes,
    )


async def test_waits_until_ten_minutes_after_close() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    service, repository, calls = _service(
        now=datetime(2026, 7, 17, 20, 9, 59, tzinfo=UTC), close_at=close_at
    )

    result = await service.run_if_due()

    assert result.disposition is PostMarketSyncDisposition.SKIPPED_NOT_DUE
    assert repository.value is None
    assert calls == []


async def test_success_is_idempotent_for_same_session() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    calls: list[str] = []
    repository = _Repository()
    service = PostMarketSyncService(
        calendar=_Calendar(MarketSession(date(2026, 7, 17), close_at)),
        repository=repository,
        portfolio=_Portfolio(calls, ok=True),
        watchlist=_Watchlist(calls, ok=True),
        clock=_Clock(datetime(2026, 7, 17, 20, 10, tzinfo=UTC)),
        id_generator=_Ids(),
    )

    first = await service.run_if_due()
    second = await service.run_if_due()

    assert first.disposition is PostMarketSyncDisposition.EXECUTED
    assert second.disposition is PostMarketSyncDisposition.SKIPPED_ALREADY_COMPLETED
    assert calls == ["portfolio", "watchlist"]
    assert repository.value is not None and repository.value.attempt_count == 1


async def test_success_syncs_transactions_and_materializes_unlinked_before_watchlist() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    calls: list[str] = []
    service = PostMarketSyncService(
        calendar=_Calendar(MarketSession(date(2026, 7, 17), close_at)),
        repository=_Repository(),
        portfolio=_Portfolio(calls),
        transactions=_Transactions(calls),
        activity_annotations=_Annotations(calls),
        account_snapshots=_SnapshotRepository(calls),
        transaction_repository=_TransactionRepository(calls),
        daily_equity=_DailyEquity(calls),
        watchlist=_Watchlist(calls),
        clock=_Clock(datetime(2026, 7, 17, 20, 10, tzinfo=UTC)),
        id_generator=_Ids(),
    )

    result = await service.run_if_due()

    assert result.run_status is PostMarketSyncRunStatus.SUCCEEDED
    assert calls == [
        "portfolio",
        "transactions",
        "unlinked",
        "activate-journal",
        "read-snapshot",
        "read-transactions",
        "daily-equity",
        "watchlist",
    ]


async def test_transaction_failure_is_visible_and_does_not_claim_complete_run() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    calls: list[str] = []
    service = PostMarketSyncService(
        calendar=_Calendar(MarketSession(date(2026, 7, 17), close_at)),
        repository=_Repository(),
        portfolio=_Portfolio(calls),
        transactions=_Transactions(calls, ok=False),
        activity_annotations=_Annotations(calls),
        watchlist=_Watchlist(calls),
        clock=_Clock(datetime(2026, 7, 17, 20, 10, tzinfo=UTC)),
        id_generator=_Ids(),
    )

    result = await service.run_if_due()

    assert result.run_status is PostMarketSyncRunStatus.PARTIAL
    assert "TRANSACTION_READ_FAILED" in result.error_codes
    assert calls == ["portfolio", "transactions", "watchlist"]


async def test_runs_portfolio_before_exact_watchlist_sync_and_persists_receipt() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    service, repository, calls = _service(
        now=datetime(2026, 7, 17, 20, 10, tzinfo=UTC), close_at=close_at
    )

    result = await service.run_if_due()

    assert calls == ["portfolio", "watchlist"]
    assert result.disposition is PostMarketSyncDisposition.EXECUTED
    assert result.run_status is PostMarketSyncRunStatus.SUCCEEDED
    assert result.holding_count == 2
    assert result.watchlist_groups_synced == 24
    assert result.watchlist_membership_relations_synced == 143
    assert repository.value is not None


async def test_run_includes_schwab_token_age_and_persists_early_warning() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    service, repository, _ = _service(
        now=datetime(2026, 7, 17, 20, 10, tzinfo=UTC),
        close_at=close_at,
        schwab_oauth_health=_SchwabOAuthHealth(),
    )

    result = await service.run_if_due()

    assert result.schwab_oauth is not None
    assert result.schwab_oauth.state is SchwabOAuthHealthState.EXPIRING
    assert result.schwab_oauth.token_age_seconds == 5 * 24 * 60 * 60
    assert result.warning_codes == ("SCHWAB_OAUTH_REAUTH_DUE_SOON",)
    assert repository.value is not None
    assert repository.value.warning_codes == ("SCHWAB_OAUTH_REAUTH_DUE_SOON",)


async def test_partial_result_is_durable_and_retryable() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    service, repository, _ = _service(
        now=datetime(2026, 7, 17, 20, 10, tzinfo=UTC),
        close_at=close_at,
        portfolio_ok=False,
    )

    first = await service.run_if_due()
    assert first.run_status is PostMarketSyncRunStatus.PARTIAL
    assert first.error_codes == ("ACCOUNT_READ_FAILED",)
    assert repository.value is not None

    service2, _, calls = _service(
        now=datetime(2026, 7, 17, 20, 20, tzinfo=UTC),
        close_at=close_at,
        repository=repository,
    )
    second = await service2.run_if_due()
    assert second.run_status is PostMarketSyncRunStatus.SUCCEEDED
    assert repository.value is not None and repository.value.attempt_count == 2
    assert calls == ["portfolio", "watchlist"]


def test_xnys_calendar_handles_normal_close_early_close_and_holiday() -> None:
    calendar = XnysMarketSessionCalendar()

    normal = calendar.session_at(datetime(2026, 7, 2, 20, 0, tzinfo=UTC))
    winter = calendar.session_at(datetime(2026, 1, 15, 21, 0, tzinfo=UTC))
    early = calendar.session_at(datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
    holiday = calendar.session_at(datetime(2026, 12, 25, 20, 0, tzinfo=UTC))

    assert normal is not None and normal.close_at.hour == 20
    assert winter is not None and winter.close_at.hour == 21
    assert early is not None and early.close_at.hour == 18
    assert holiday is None

    latest = calendar.session_on_or_before(datetime(2026, 7, 26, 12, tzinfo=UTC))
    assert latest is not None and latest.session_date == date(2026, 7, 24)
    previous = calendar.previous_session(date(2026, 7, 24))
    assert previous is not None and previous.session_date == date(2026, 7, 23)


async def test_status_detects_missing_latest_due_receipt() -> None:
    close_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    service, _, calls = _service(
        now=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
        close_at=close_at,
    )

    result = service.status()

    assert result.health is PostMarketSyncHealth.RECEIPT_MISSING
    assert result.expected_session_date == date(2026, 7, 17)
    assert result.error_codes == ("POST_MARKET_SYNC_RECEIPT_MISSING",)
    assert calls == []


async def test_catch_up_runs_latest_due_session_once() -> None:
    close_at = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    service, repository, calls = _service(
        now=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        close_at=close_at,
    )

    first = await service.catch_up_latest_due()
    second = await service.catch_up_latest_due()

    assert first.disposition is PostMarketSyncDisposition.EXECUTED
    assert second.disposition is PostMarketSyncDisposition.SKIPPED_ALREADY_COMPLETED
    assert calls == ["portfolio", "watchlist"]
    assert repository.value is not None


def test_process_file_lock_rejects_overlapping_job(tmp_path: Path) -> None:
    first = ProcessFileLock(tmp_path / "sync.lock")
    second = ProcessFileLock(tmp_path / "sync.lock")
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        first.release()
        second.release()


async def test_cli_outputs_executed_success_payload_and_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = _CliContainer(tmp_path / "post_market_sync.lock", _run_result())
    monkeypatch.setattr(
        post_market_sync_cli,
        "build_default_application",
        lambda: container,
    )

    code = await post_market_sync_cli._run()
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert payload["ok"] is True
    assert payload["disposition"] == PostMarketSyncDisposition.EXECUTED.value
    assert payload["run_status"] == PostMarketSyncRunStatus.SUCCEEDED.value
    assert container.operations.post_market_sync.calls == 1
    assert container.aclose_calls == 1

    lock = ProcessFileLock(tmp_path / "post_market_sync.lock")
    assert lock.acquire() is True
    lock.release()


async def test_cli_reports_partial_as_failure_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = _CliContainer(
        tmp_path / "post_market_sync_partial.lock",
        _run_result(
            disposition=PostMarketSyncDisposition.EXECUTED,
            run_status=PostMarketSyncRunStatus.PARTIAL,
            error_codes=("POST_MARKET_SYNC_STEP_FAILED",),
        ),
    )
    monkeypatch.setattr(
        post_market_sync_cli,
        "build_default_application",
        lambda: container,
    )

    code = await post_market_sync_cli._run()
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 1
    assert payload["ok"] is False
    assert payload["run_status"] == PostMarketSyncRunStatus.PARTIAL.value


async def test_cli_reports_lock_contention_as_json_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock_path = tmp_path / "post_market_sync.lock"
    lock = ProcessFileLock(lock_path)
    assert lock.acquire() is True
    container = _CliContainer(lock_path, _run_result())
    try:
        monkeypatch.setattr(
            post_market_sync_cli,
            "build_default_application",
            lambda: container,
        )
        code = await post_market_sync_cli._run()
        payload = json.loads(capsys.readouterr().out.strip())
    finally:
        lock.release()

    assert code == 1
    assert payload == {"ok": False, "error_codes": ["SYNC_ALREADY_RUNNING"]}


async def test_cli_status_is_read_only_and_reports_health(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = _CliContainer(tmp_path / "post_market_sync.lock", _run_result())
    monkeypatch.setattr(post_market_sync_cli, "build_default_application", lambda: container)

    code = await post_market_sync_cli._run("status")
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert payload["ok"] is True
    assert payload["health"] == PostMarketSyncHealth.HEALTHY.value
    assert container.operations.post_market_sync.calls == 1
    assert container.aclose_calls == 1


async def test_cli_catch_up_uses_bounded_service_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = _CliContainer(tmp_path / "post_market_sync.lock", _run_result())
    monkeypatch.setattr(post_market_sync_cli, "build_default_application", lambda: container)

    code = await post_market_sync_cli._run("catch-up")
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert payload["disposition"] == PostMarketSyncDisposition.EXECUTED.value
    assert container.operations.post_market_sync.calls == 1


def test_cli_help_does_not_build_application(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        post_market_sync_cli,
        "build_default_application",
        lambda: pytest.fail("--help must not build the application"),
    )

    with pytest.raises(SystemExit) as exc_info:
        post_market_sync_cli.main(["--help"])

    assert exc_info.value.code == 0
    assert "catch-up" in capsys.readouterr().out
