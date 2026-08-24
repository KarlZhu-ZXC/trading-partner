"""External-scheduler CLI for market-specific active post-market monitors."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC

from application.dto.monitor_dispatch import MonitorDispatchDTO
from application.dto.monitoring import MonitorEvaluateInput, MonitorRunDTO
from application.dto.operational_job import OperationalJobRunDTO
from application.services.operational_job_runtime import OperationalJobOutcome
from bootstrap import build_default_application
from domain.monitoring.enums import MonitorCadence, MonitorRunStatus
from domain.operations.enums import OperationalJobStatus


async def _run(cadence: MonitorCadence) -> int:
    container = build_default_application()
    lock = container.resources.monitor_run_lock
    if not lock.acquire():
        print(
            json.dumps(
                {"ok": False, "error_codes": ["MONITOR_RUN_ALREADY_RUNNING"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        await container.aclose()
        return 1
    try:
        result = await container.operations.monitor_evaluation.evaluate(
            MonitorEvaluateInput(cadence=cadence)
        )
        notification_delivery = (
            await container.operations.notifications.flush_pending()
        )
        print(
            json.dumps(
                {
                    "ok": result.status is not MonitorRunStatus.FAILED,
                    **MonitorRunDTO.from_domain(result).model_dump(mode="json"),
                    "notification_delivery": notification_delivery.model_dump(
                        mode="json"
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1 if result.status is MonitorRunStatus.FAILED else 0
    finally:
        lock.release()
        await container.aclose()


async def _run_due() -> int:
    container = build_default_application()
    lock = container.resources.monitor_run_lock
    if not lock.acquire():
        print(
            json.dumps(
                {"ok": False, "error_codes": ["MONITOR_RUN_ALREADY_RUNNING"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        await container.aclose()
        return 1
    try:
        async def operation() -> OperationalJobOutcome[MonitorDispatchDTO]:
            value = await container.operations.monitor_dispatch.run_due()
            failed = any(run.status is MonitorRunStatus.FAILED for run in value.runs)
            return OperationalJobOutcome(
                status=(
                    OperationalJobStatus.FAILED
                    if failed
                    else OperationalJobStatus.SUCCEEDED
                ),
                result_code=(
                    "MONITOR_DUE_COMPLETED_WITH_FAILURES"
                    if failed
                    else "MONITOR_DUE_SUCCEEDED"
                ),
                error_code="MONITOR_DUE_RUN_FAILED" if failed else None,
                value=value,
            )

        hour = container.context.clock.now().astimezone(UTC).strftime("%Y%m%dT%H")
        execution = await container.operations.jobs.execute(
            job_name="monitor.due",
            idempotency_key=f"monitor.due:{hour}",
            operation=operation,
            lease_seconds=300,
        )
        result = execution.value
        payload = {
            "ok": execution.run.status is OperationalJobStatus.SUCCEEDED,
            "operational_job": OperationalJobRunDTO.from_domain(execution.run).model_dump(
                mode="json"
            ),
            "invoked": execution.invoked,
        }
        if result is not None:
            payload.update(result.model_dump(mode="json"))
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if execution.run.status is OperationalJobStatus.SUCCEEDED else 1
    finally:
        lock.release()
        await container.aclose()


def main() -> None:
    """Evaluate active post-market monitors once and emit one JSON receipt."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "due"),
        default="run",
    )
    parser.add_argument(
        "--cadence",
        required=False,
        choices=(
            MonitorCadence.A_SHARE_POST_MARKET.value,
            MonitorCadence.US_POST_MARKET.value,
            MonitorCadence.KR_POST_MARKET.value,
        ),
    )
    args = parser.parse_args()
    if args.command == "due":
        if args.cadence is not None:
            parser.error("due cannot be combined with --cadence")
        raise SystemExit(asyncio.run(_run_due()))
    if args.cadence is None:
        parser.error("run requires --cadence")
    raise SystemExit(asyncio.run(_run(MonitorCadence(args.cadence))))


if __name__ == "__main__":
    main()
