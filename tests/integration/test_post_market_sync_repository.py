"""Round-trip persistence for post-market synchronization receipts."""

from dataclasses import replace
from datetime import UTC, date, datetime

from sqlalchemy import create_engine

from domain.operations.enums import PostMarketSyncRunStatus, PostMarketSyncStepStatus
from domain.operations.models import PostMarketSyncRun
from infrastructure.persistence.orm import Base
from infrastructure.persistence.post_market_sync_run_repository import (
    SqlAlchemyPostMarketSyncRunRepository,
)


def _run(*, attempt_count: int = 1) -> PostMarketSyncRun:
    return PostMarketSyncRun(
        run_id="run_00000000-0000-7000-8000-000000000001",
        market_session_date=date(2026, 7, 17),
        scheduled_for=datetime(2026, 7, 17, 20, 10, tzinfo=UTC),
        started_at=datetime(2026, 7, 17, 20, 10, tzinfo=UTC),
        completed_at=datetime(2026, 7, 17, 20, 11, 3, tzinfo=UTC),
        status=PostMarketSyncRunStatus.SUCCEEDED,
        portfolio_status=PostMarketSyncStepStatus.SUCCEEDED,
        watchlist_status=PostMarketSyncStepStatus.SUCCEEDED,
        observation_status=PostMarketSyncStepStatus.SUCCEEDED,
        account_snapshot_ids=("snapshot_1", "snapshot_2"),
        watchlist_groups_synced=24,
        watchlist_membership_relations_synced=143,
        observation_notes_seen=16,
        observation_revisions_created=2,
        observation_full_count=16,
        observation_summary_only_count=0,
        warning_codes=("SCHWAB_OPEN_ORDERS_NOT_INGESTED",),
        error_codes=(),
        attempt_count=attempt_count,
    )


def test_repository_inserts_and_updates_one_receipt_per_market_session() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyPostMarketSyncRunRepository(engine)

    repository.save(_run())
    repository.save(_run(attempt_count=2))
    restored = repository.get_for_session(date(2026, 7, 17))
    latest = repository.get_latest()

    assert restored is not None
    assert latest is not None and latest.run_id == restored.run_id
    assert restored.attempt_count == 2
    assert restored.account_snapshot_ids == ("snapshot_1", "snapshot_2")
    assert restored.watchlist_membership_relations_synced == 143
    assert restored.observation_status is PostMarketSyncStepStatus.SUCCEEDED
    assert restored.observation_notes_seen == 16
    assert restored.observation_full_count == 16
    engine.dispose()


def test_get_for_session_is_none_when_no_receipt_exists() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyPostMarketSyncRunRepository(engine)

    assert repository.get_for_session(date(2026, 7, 17)) is None
    assert repository.get_latest() is None
    engine.dispose()


def test_retry_save_reuses_existing_session_and_preserves_run_identity() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyPostMarketSyncRunRepository(engine)

    first = _run()
    repository.save(first)
    replacement = replace(
        first,
        run_id="post_market_sync_00000000-0000-7000-8000-000000000099",
        attempt_count=2,
    )
    repository.save(replacement)

    restored = repository.get_for_session(date(2026, 7, 17))
    assert restored is not None
    assert restored.run_id == first.run_id
    assert restored.attempt_count == 2
    assert restored.market_session_date == first.market_session_date
    engine.dispose()
