"""Operate the deterministic Monitor notification delivery channel."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from bootstrap import build_default_application


async def _run(command: str) -> int:
    container = build_default_application()
    lock = None
    lock_acquired = False
    try:
        payload: dict[str, Any]
        ok: bool
        if command == "status":
            status_receipt = container.operations.monitor_notifications.status()
            payload = status_receipt.model_dump(mode="json")
            ok = status_receipt.enabled and status_receipt.configured
        elif command == "flush":
            lock = container.resources.monitor_run_lock
            if not lock.acquire():
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error_codes": ["MONITOR_NOTIFICATION_FLUSH_ALREADY_RUNNING"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 1
            lock_acquired = True
            flush_receipt = (
                await container.operations.monitor_notifications.flush_pending()
            )
            payload = flush_receipt.model_dump(mode="json")
            ok = not flush_receipt.error_codes
        else:
            send_receipt = await container.operations.monitor_notifications.send_test()
            payload = send_receipt.model_dump(mode="json")
            ok = send_receipt.delivered
        print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, sort_keys=True))
        return 0 if ok else 1
    finally:
        if lock is not None and lock_acquired:
            lock.release()
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect, test, or flush Telegram Monitor notifications."
    )
    parser.add_argument("command", choices=("status", "test", "flush"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.command)))


if __name__ == "__main__":
    main()
