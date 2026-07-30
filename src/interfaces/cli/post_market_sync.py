"""Scheduler entry point for US post-market account and Watchlist synchronization."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from contextlib import suppress

from application.dto.post_market_sync import PostMarketSyncDisposition
from bootstrap import build_default_application
from domain.operations.enums import PostMarketSyncRunStatus

_PROGRESS_INTERVAL_SECONDS = 15


async def _progress(command: str) -> None:
    while True:
        await asyncio.sleep(_PROGRESS_INTERVAL_SECONDS)
        print(
            json.dumps(
                {"event": "POST_MARKET_SYNC_IN_PROGRESS", "command": command},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


async def _run(command: str = "run") -> int:
    container = build_default_application()
    if command == "status":
        try:
            status_result = container.operations.post_market_sync.status()
            print(
                json.dumps(
                    {
                        "ok": status_result.healthy,
                        **status_result.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if status_result.healthy else 2
        finally:
            await container.aclose()

    lock = container.resources.post_market_sync_lock
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
    progress = asyncio.create_task(_progress(command))
    try:
        run_result = (
            await container.operations.post_market_sync.run_if_due()
            if command == "run"
            else await container.operations.post_market_sync.catch_up_latest_due()
        )
        payload = run_result.model_dump(mode="json")
        ok = run_result.run_status in {None, PostMarketSyncRunStatus.SUCCEEDED}
        print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, sort_keys=True))
        if run_result.disposition is not PostMarketSyncDisposition.EXECUTED:
            return 0
        return 0 if run_result.run_status is PostMarketSyncRunStatus.SUCCEEDED else 1
    finally:
        progress.cancel()
        with suppress(asyncio.CancelledError):
            await progress
        lock.release()
        await container.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-post-market-sync",
        description="Run, diagnose, or catch up the bounded US post-market sync.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("run", help="Run only when today's XNYS session is due.")
    subcommands.add_parser("status", help="Read receipt health without provider access.")
    subcommands.add_parser(
        "catch-up",
        help="Run only the latest due XNYS session when it lacks a successful receipt.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one explicit post-market synchronization command."""

    args = _parser().parse_args(argv)
    command = args.command or "run"
    raise SystemExit(asyncio.run(_run(command)))


if __name__ == "__main__":
    main()
