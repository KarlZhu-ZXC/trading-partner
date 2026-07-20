"""External-scheduler CLI for market-specific active post-market monitors."""

from __future__ import annotations

import argparse
import asyncio
import json

from application.dto.monitoring import MonitorEvaluateInput
from bootstrap import build_default_application
from domain.monitoring.enums import MonitorCadence, MonitorRunStatus


async def _run(cadence: MonitorCadence) -> int:
    container = build_default_application()
    lock = container.monitor_run_lock
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
        result = await container.monitor_evaluation_service.evaluate(
            MonitorEvaluateInput(cadence=cadence)
        )
        print(
            json.dumps(
                {
                    "ok": result.status is not MonitorRunStatus.FAILED,
                    "run_id": result.run_id,
                    "status": result.status.value,
                    "monitors_evaluated": result.monitors_evaluated,
                    "rules_evaluated": result.rules_evaluated,
                    "events_created": result.events_created,
                    "warning_codes": result.warning_codes,
                    "error_codes": result.error_codes,
                    "execution_effect": result.execution_effect,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1 if result.status is MonitorRunStatus.FAILED else 0
    finally:
        lock.release()
        await container.aclose()


def main() -> None:
    """Evaluate active post-market monitors once and emit one JSON receipt."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cadence",
        required=True,
        choices=(
            MonitorCadence.A_SHARE_POST_MARKET.value,
            MonitorCadence.US_POST_MARKET.value,
        ),
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(MonitorCadence(args.cadence))))


if __name__ == "__main__":
    main()
