"""Resumable Console refresh using existing operational and note receipts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial

from application.services.external_note_review_draft_service import ExternalNoteReviewDraftService
from application.services.external_note_sync_service import ExternalNoteSyncService
from application.services.operational_job_runtime import (
    DurableOperationalJobRuntime,
    OperationalJobOutcome,
)
from domain.external_note.enums import NoteCoverage, NoteSyncStatus
from domain.operations.enums import OperationalJobStatus
from domain.operations.models import OperationalJobRun

_JOB = "observation.refresh"
_STAGES = ("capture", "interpret", "review")


class ObservationRefreshService:
    def __init__(
        self,
        notes: ExternalNoteSyncService,
        reviews: ExternalNoteReviewDraftService,
        jobs: DurableOperationalJobRuntime,
    ) -> None:
        self.notes = notes
        self.reviews = reviews
        self.jobs = jobs

    async def status(self, key: str) -> dict[str, object]:
        await self.jobs.recover_expired()

        def public(run: OperationalJobRun | None) -> dict[str, object] | None:
            if run is None:
                return None
            return {
                "job_run_id": run.job_run_id,
                "status": run.status.value,
                "attempt": run.attempt,
                "result_code": run.result_code,
                "error_code": run.error_code,
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }

        run = await self.jobs.get_by_key(_JOB, key)
        stages = {
            stage: public(await self.jobs.get_by_key(f"{_JOB}.{stage}", key)) for stage in _STAGES
        }
        return {"request_id": key, "run": public(run), "stages": stages}

    async def run(self, key: str, *, on_claim: Callable[[OperationalJobRun], None]) -> None:
        async def operation() -> OperationalJobOutcome[None]:
            degraded = False
            for stage in _STAGES:
                result = await self.jobs.execute(
                    job_name=f"{_JOB}.{stage}",
                    idempotency_key=key,
                    operation=partial(self._stage, stage),
                )
                if result.run.status is OperationalJobStatus.RUNNING:
                    return OperationalJobOutcome(
                        status=OperationalJobStatus.FAILED,
                        result_code="OBSERVATION_REFRESH_STAGE_BUSY",
                        error_code="OBSERVATION_REFRESH_STAGE_BUSY",
                    )
                if result.run.status in {
                    OperationalJobStatus.FAILED,
                    OperationalJobStatus.INTERRUPTED,
                }:
                    return OperationalJobOutcome(
                        status=OperationalJobStatus.FAILED,
                        result_code="OBSERVATION_REFRESH_FAILED",
                        error_code=result.run.error_code or "OBSERVATION_REFRESH_FAILED",
                    )
                degraded |= result.run.result_code in {
                    "OBSERVATION_STAGE_DEGRADED",
                    "OBSERVATION_ANALYSIS_DISABLED",
                }
            return OperationalJobOutcome(
                status=OperationalJobStatus.SUCCEEDED,
                result_code="OBSERVATION_REFRESH_DEGRADED"
                if degraded
                else "OBSERVATION_REFRESH_SUCCEEDED",
            )

        await self.jobs.execute(
            job_name=_JOB,
            idempotency_key=key,
            operation=operation,
            on_claim=on_claim,
        )

    async def _stage(self, stage: str) -> OperationalJobOutcome[None]:
        degraded = False
        if stage == "capture":
            # Provider scans have their own bounded request policy. Do not cancel a
            # to_thread capture and release its process lock while it still runs.
            receipt = await self.notes.sync(analyze=False)
            if receipt.status is NoteSyncStatus.FAILED:
                return OperationalJobOutcome(
                    status=OperationalJobStatus.FAILED,
                    result_code="OBSERVATION_CAPTURE_FAILED",
                    error_code="OBSERVATION_CAPTURE_FAILED",
                )
            # Pending interpretation is expected between stages, not a coverage gap.
            degraded = bool(receipt.error_codes) or any(
                code != "MOOMOO_NOTE_INTERPRETATION_PENDING" for code in receipt.warning_codes
            )
        else:
            if not self.notes.interpretation_enabled:
                return OperationalJobOutcome(
                    status=OperationalJobStatus.SKIPPED,
                    result_code="OBSERVATION_ANALYSIS_DISABLED",
                )
            items = self.notes.inbox(limit=201)
            # Explicit bounded work: report coverage when the intake reaches its cap.
            degraded = len(items) > 200
            items = items[:200]
            requests = 0
            async with asyncio.timeout(600):
                for item in items:
                    if item.revision.coverage is not NoteCoverage.FULL:
                        continue
                    if stage == "interpret":
                        if (
                            item.interpretation is not None
                            and item.interpretation.status == "SUCCEEDED"
                        ):
                            continue
                        if requests >= 100:
                            degraded = True
                            break
                        requests += 1
                        value = await self.notes.analyze_revision(
                            item.revision.note_revision_id,
                            retry_failed=True,
                            deep_review=False,
                        )
                        degraded |= value.status != "SUCCEEDED"
                    elif (
                        item.interpretation is not None
                        and item.interpretation.status == "SUCCEEDED"
                    ):
                        async with self.notes.exclusive_session():
                            if not self.notes.materialize_existing_review(
                                item.revision.note_revision_id
                            ):
                                continue
                            if not self.reviews.requires_deep_review(
                                item.revision.note_revision_id
                            ):
                                continue
                            if requests >= 100:
                                degraded = True
                                break
                            requests += 1
                            draft = await self.reviews.review(item.revision.note_revision_id)
                            degraded |= draft is not None and draft.status != "SUCCEEDED"
                            degraded |= draft is None and not self.reviews.review_enabled
        return OperationalJobOutcome(
            status=OperationalJobStatus.SUCCEEDED,
            result_code="OBSERVATION_STAGE_DEGRADED" if degraded else "OBSERVATION_STAGE_SUCCEEDED",
        )
