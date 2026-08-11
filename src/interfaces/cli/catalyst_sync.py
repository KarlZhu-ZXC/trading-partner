"""Explicit Catalyst Agenda Provider synchronization CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import datetime

from application.dto.catalyst_agenda_sync import CatalystAgendaSyncInput
from bootstrap import build_default_application
from domain.common.errors import TradingPartnerError


async def _run(args: argparse.Namespace) -> int:
    container = build_default_application()
    try:
        if args.command == "status":
            receipt = container.operations.catalyst_agenda_sync.latest()
            print(
                json.dumps(
                    {
                        "ok": receipt is not None,
                        "receipt": receipt.model_dump(mode="json") if receipt else None,
                        "error_codes": [] if receipt else ["CATALYST_AGENDA_SYNC_RECEIPT_MISSING"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if receipt is not None else 2
        request = CatalystAgendaSyncInput(
            instrument_ids=tuple(args.instrument_ids or ()),
            fred_release_ids=tuple(args.fred_release_ids or ()),
            window_days=args.window_days,
            as_of=args.as_of,
            idempotency_key=args.idempotency_key,
        )
        receipt = await container.operations.catalyst_agenda_sync.sync(request)
        payload: dict[str, object] = receipt.model_dump(mode="json")
        if args.notify:
            notification_batch = (
                await container.operations.catalyst_agenda_notifications.enqueue_batch(
                    window_days=min(args.window_days, 30),
                    additional_limitations=receipt.limitation_codes,
                )
            )
            payload["notifications"] = notification_batch.model_dump(mode="json")
        if args.flush:
            delivery = await container.operations.notifications.flush_pending()
            payload["delivery"] = delivery.model_dump(mode="json")
        print(
            json.dumps(
                {"ok": receipt.status != "FAILED", **payload},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if receipt.status != "FAILED" else 1
    except TradingPartnerError as exc:
        print(
            json.dumps(
                {"ok": False, "error_codes": [exc.code], "execution_effect": False},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    finally:
        await container.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-catalyst-sync",
        description="Refresh current-only Yahoo and selected FRED Catalyst Agenda dates.",
    )
    subcommands = parser.add_subparsers(dest="command")
    sync = subcommands.add_parser("sync", help="Run one explicit Provider synchronization.")
    sync.add_argument(
        "--instrument-id",
        action="append",
        dest="instrument_ids",
        help="Durable US equity/ETF Instrument ID; repeatable. Empty uses durable scope.",
    )
    sync.add_argument(
        "--fred-release-id",
        action="append",
        type=int,
        dest="fred_release_ids",
        help="Positive FRED release ID; repeatable. Empty skips macro calendars.",
    )
    sync.add_argument("--window-days", type=int, default=30)
    sync.add_argument("--as-of", type=datetime.fromisoformat)
    sync.add_argument("--idempotency-key")
    sync.add_argument(
        "--notify",
        action="store_true",
        help="Queue one daily Agenda summary and material date/cancel changes.",
    )
    sync.add_argument(
        "--flush",
        action="store_true",
        help="Flush the generic Telegram Outbox after synchronization.",
    )
    subcommands.add_parser(
        "status", help="Read the latest durable receipt without Provider access."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    args.command = args.command or "sync"
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
