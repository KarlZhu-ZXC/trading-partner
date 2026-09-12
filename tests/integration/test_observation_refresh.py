from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine

from application.services.observation_refresh_service import ObservationRefreshService
from application.services.operational_job_runtime import DurableOperationalJobRuntime
from domain.external_note.enums import NoteCoverage, NoteSyncStatus
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.operational_job_repository import SqlAlchemyOperationalJobRepository


class IDs:
    def __init__(self):
        self.counter = 0

    def new(self, prefix):
        self.counter += 1
        return f"{prefix.value}_{self.counter}"


@asynccontextmanager
async def session():
    yield


def setup(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'refresh.db'}")
    Base.metadata.create_all(engine)
    clock = SimpleNamespace(now=lambda: datetime(2026, 9, 8, tzinfo=UTC))
    jobs = DurableOperationalJobRuntime(SqlAlchemyOperationalJobRepository(engine), clock, IDs())
    items = [
        SimpleNamespace(
            revision=SimpleNamespace(note_revision_id=f"revision_{i}", coverage=NoteCoverage.FULL),
            interpretation=None,
        )
        for i in range(2)
    ]
    notes = SimpleNamespace(
        interpretation_enabled=True,
        exclusive_session=session,
        materialize_existing_review=lambda _: True,
        sync=AsyncMock(
            return_value=SimpleNamespace(
                status=NoteSyncStatus.PARTIAL,
                error_codes=(),
                warning_codes=("MOOMOO_NOTE_INTERPRETATION_PENDING",),
            )
        ),
        inbox=lambda limit: items,
    )

    async def analyze(revision_id, **kwargs):
        item = next(item for item in items if item.revision.note_revision_id == revision_id)
        item.interpretation = SimpleNamespace(status="SUCCEEDED")
        return item.interpretation

    notes.analyze_revision = AsyncMock(side_effect=analyze)
    reviews = SimpleNamespace(
        review_enabled=True,
        requires_deep_review=lambda _: True,
        review=AsyncMock(return_value=SimpleNamespace(status="SUCCEEDED")),
    )
    return ObservationRefreshService(notes, reviews, jobs), notes, reviews, items


@pytest.mark.asyncio
async def test_refresh_preserves_success_and_resumes_failed_stage(tmp_path):
    service, notes, reviews, items = setup(tmp_path)
    normal = notes.analyze_revision.side_effect

    async def fail_second(revision_id, **kwargs):
        if revision_id == "revision_1":
            raise RuntimeError("private provider body must never be persisted")
        return await normal(revision_id, **kwargs)

    notes.analyze_revision.side_effect = fail_second
    claims = []
    await service.run("request-1", on_claim=claims.append)
    failed = await service.status("request-1")
    assert failed["run"]["status"] == "FAILED"
    assert "private provider" not in str(failed)
    assert claims and items[0].interpretation.status == "SUCCEEDED"
    assert notes.sync.await_count == 1

    notes.analyze_revision.reset_mock(side_effect=True)
    notes.analyze_revision.side_effect = normal
    await service.run("request-1", on_claim=claims.append)
    resumed = await service.status("request-1")
    assert resumed["run"]["status"] == "SUCCEEDED"
    assert resumed["run"]["attempt"] == 2
    assert resumed["run"]["result_code"] == "OBSERVATION_REFRESH_SUCCEEDED"
    assert notes.sync.await_count == 1
    assert notes.analyze_revision.await_count == 1
    assert notes.analyze_revision.await_args.args == ("revision_1",)
    assert reviews.review.await_count == 2
    # Replaying an already successful request does no work.
    await service.run("request-1", on_claim=claims.append)
    assert reviews.review.await_count == 2


@pytest.mark.asyncio
async def test_rejected_draft_is_quality_degradation_not_execution_success_green(tmp_path):
    service, _, reviews, _ = setup(tmp_path)
    reviews.review.return_value = SimpleNamespace(status="FAILED")
    await service.run("request-2", on_claim=lambda _: None)
    value = await service.status("request-2")
    assert value["run"]["status"] == "SUCCEEDED"
    assert value["run"]["result_code"] == "OBSERVATION_REFRESH_DEGRADED"
    assert value["stages"]["review"]["result_code"] == "OBSERVATION_STAGE_DEGRADED"


@pytest.mark.asyncio
async def test_capture_failure_never_runs_model_stages(tmp_path):
    service, notes, reviews, _ = setup(tmp_path)
    notes.sync.return_value.status = NoteSyncStatus.FAILED
    await service.run("request-3", on_claim=lambda _: None)
    value = await service.status("request-3")
    assert value["run"]["status"] == "FAILED"
    assert value["stages"]["interpret"] is None
    notes.analyze_revision.assert_not_awaited()
    reviews.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_only_never_reaches_model(tmp_path):
    service, notes, reviews, items = setup(tmp_path)
    for item in items:
        item.revision.coverage = NoteCoverage.SUMMARY_ONLY
    notes.sync.return_value.warning_codes = ("MOOMOO_NOTES_SUMMARY_ONLY",)
    await service.run("request-4", on_claim=lambda _: None)
    value = await service.status("request-4")
    assert value["run"]["result_code"] == "OBSERVATION_REFRESH_DEGRADED"
    notes.analyze_revision.assert_not_awaited()
    reviews.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_cancellation_keeps_lock_until_worker_stops():
    import asyncio
    import threading

    from application.services.external_note_sync_service import ExternalNoteSyncService

    entered, release = threading.Event(), threading.Event()
    unlocked = []

    def scan():
        entered.set()
        assert release.wait(timeout=3)
        return SimpleNamespace(snapshots=(), cache_files_scanned=0, warning_codes=())

    service = ExternalNoteSyncService(
        SimpleNamespace(capability=SimpleNamespace(source_code="SYNTHETIC"), scan=scan),
        SimpleNamespace(),
        SimpleNamespace(now=lambda: datetime(2026, 9, 8, tzinfo=UTC)),
        IDs(),
        process_lock=SimpleNamespace(acquire=lambda: True, release=lambda: unlocked.append(True)),
    )
    task = asyncio.create_task(service.sync())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert unlocked == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert unlocked == [True]


@pytest.mark.asyncio
async def test_required_review_without_provider_is_degraded(tmp_path):
    service, _, reviews, _ = setup(tmp_path)
    reviews.review_enabled = False
    reviews.review.return_value = None
    reviews.requires_deep_review = lambda _: True
    await service.run("disabled-review", on_claim=lambda _: None)
    assert (await service.status("disabled-review"))["run"][
        "result_code"
    ] == "OBSERVATION_REFRESH_DEGRADED"
    assert reviews.review.await_count == 2


@pytest.mark.asyncio
async def test_disabled_interpretation_is_explicitly_skipped(tmp_path):
    service, notes, reviews, _ = setup(tmp_path)
    notes.interpretation_enabled = False
    await service.run("disabled-analysis", on_claim=lambda _: None)
    value = await service.status("disabled-analysis")
    assert value["run"]["result_code"] == "OBSERVATION_REFRESH_DEGRADED"
    assert value["stages"]["interpret"]["status"] == "SKIPPED"
    notes.analyze_revision.assert_not_awaited()
    reviews.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_child_never_allows_parent_to_report_completion(tmp_path):
    import asyncio

    from application.services.operational_job_runtime import OperationalJobOutcome
    from domain.operations.enums import OperationalJobStatus

    service, _, reviews, _ = setup(tmp_path)
    claimed, release = asyncio.Event(), asyncio.Event()

    async def in_progress():
        await release.wait()
        return OperationalJobOutcome(status=OperationalJobStatus.SUCCEEDED, result_code="DONE")

    child = asyncio.create_task(
        service.jobs.execute(
            job_name="observation.refresh.interpret",
            idempotency_key="busy-child",
            operation=in_progress,
            on_claim=lambda _: claimed.set(),
        )
    )
    try:
        await claimed.wait()
        await service.run("busy-child", on_claim=lambda _: None)
        value = await service.status("busy-child")
        assert value["run"]["status"] == "FAILED"
        assert value["run"]["error_code"] == "OBSERVATION_REFRESH_STAGE_BUSY"
        reviews.review.assert_not_awaited()
    finally:
        release.set()
        await child
