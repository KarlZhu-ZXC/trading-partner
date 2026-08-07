"""Operate the deterministic generic Telegram notification channel.

This CLI is intentionally outside the public MCP surface. MANUAL enqueue is a
write to the outbound outbox and requires explicit caller authorization.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from bootstrap import build_default_application


async def _run(
    command: str,
    *,
    title: str | None = None,
    idempotency_key: str | None = None,
    confirmed_by: str | None = None,
    authorization_note: str | None = None,
    flush: bool = False,
) -> int:
    container = build_default_application()
    lock = None
    lock_acquired = False
    try:
        if command == "status":
            status_receipt = container.operations.notifications.status()
            payload: dict[str, Any] = status_receipt.model_dump(mode="json")
            ok = status_receipt.enabled and status_receipt.configured
        elif command == "test":
            send_receipt = await container.operations.notifications.send_test()
            payload = send_receipt.model_dump(mode="json")
            ok = send_receipt.delivered
        elif command == "flush":
            lock = container.resources.monitor_run_lock
            if not lock.acquire():
                payload = {
                    "error_codes": ["NOTIFICATION_FLUSH_ALREADY_RUNNING"],
                }
                ok = False
            else:
                lock_acquired = True
                flush_receipt = await container.operations.notifications.flush_pending()
                payload = flush_receipt.model_dump(mode="json")
                ok = not flush_receipt.error_codes
        else:
            assert title is not None
            assert idempotency_key is not None
            assert confirmed_by is not None
            assert authorization_note is not None
            body = sys.stdin.read()
            entry = await container.operations.notifications.enqueue_text(
                title,
                body,
                idempotency_key=idempotency_key,
                confirmed_by=confirmed_by,
                authorization_note=authorization_note,
            )
            payload = container.operations.notifications.enqueue_receipt(entry).model_dump(
                mode="json"
            )
            ok = True
            if flush:
                lock = container.resources.monitor_run_lock
                if not lock.acquire():
                    payload["flush_error_codes"] = ["NOTIFICATION_FLUSH_ALREADY_RUNNING"]
                    ok = False
                else:
                    lock_acquired = True
                    flush_receipt = await container.operations.notifications.flush_pending()
                    payload["flush"] = flush_receipt.model_dump(mode="json")
                    ok = not flush_receipt.error_codes
        print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, sort_keys=True))
        return 0 if ok else 1
    finally:
        if lock is not None and lock_acquired:
            lock.release()
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect, test, flush, or explicitly enqueue Telegram notifications."
    )
    parser.add_argument("command", choices=("status", "test", "flush", "enqueue"))
    parser.add_argument("--title")
    parser.add_argument("--idempotency-key", dest="idempotency_key")
    parser.add_argument("--confirmed-by", choices=("user", "external_agent"))
    parser.add_argument("--authorization-note", dest="authorization_note")
    parser.add_argument("--flush", action="store_true", help="Flush after enqueueing text")
    args = parser.parse_args()
    if args.command == "enqueue":
        missing = [
            name
            for name, value in (
                ("--title", args.title),
                ("--idempotency-key", args.idempotency_key),
                ("--confirmed-by", args.confirmed_by),
                ("--authorization-note", args.authorization_note),
            )
            if value is None
        ]
        if missing:
            parser.error(f"enqueue requires {', '.join(missing)}")
    elif any(
        value is not None
        for value in (args.title, args.idempotency_key, args.confirmed_by, args.authorization_note)
    ) or args.flush:
        parser.error("notification options are only valid with enqueue")
    raise SystemExit(
        asyncio.run(
            _run(
                args.command,
                title=args.title,
                idempotency_key=args.idempotency_key,
                confirmed_by=args.confirmed_by,
                authorization_note=args.authorization_note,
                flush=args.flush,
            )
        )
    )


if __name__ == "__main__":
    main()
