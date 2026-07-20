"""Scheduler entry point for US post-market account and Watchlist synchronization."""

from __future__ import annotations

import asyncio
import json

from application.dto.post_market_sync import PostMarketSyncDisposition
from bootstrap import build_default_application
from domain.operations.enums import PostMarketSyncRunStatus


async def _run() -> int:
    container = build_default_application()
    lock = container.post_market_sync_lock
    if not lock.acquire():
        print(
            json.dumps(
                {"ok": False, "error_codes": ["SYNC_ALREADY_RUNNING"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        await container.aclose()
        return 1
    try:
        result = await container.post_market_sync_service.run_if_due()
        payload = result.model_dump(mode="json")
        ok = result.run_status in {None, PostMarketSyncRunStatus.SUCCEEDED}
        print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, sort_keys=True))
        if result.disposition is not PostMarketSyncDisposition.EXECUTED:
            return 0
        return 0 if result.run_status is PostMarketSyncRunStatus.SUCCEEDED else 1
    finally:
        lock.release()
        await container.aclose()


def main() -> None:
    """Run one due-checked post-market synchronization."""

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
