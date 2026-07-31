"""External-scheduler CLI for market-specific active post-market monitors."""

from __future__ import annotations

import argparse
import asyncio
import json

from application.dto.monitoring import MonitorEvaluateInput, MonitorRunDTO
from bootstrap import build_default_application
from domain.monitoring.enums import MonitorCadence, MonitorRunStatus


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
            await container.operations.monitor_notifications.flush_pending()
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
        result = await container.operations.monitor_dispatch.run_due()
        print(
            json.dumps(
                {"ok": True, **result.model_dump(mode="json")},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if any(run.status is MonitorRunStatus.FAILED for run in result.runs):
            return 1
        return 0
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
