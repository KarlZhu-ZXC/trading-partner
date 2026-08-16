"""Operational CLI for normalized durable account transaction facts."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from application.dto.account_transactions import AccountGetTransactionsInput
from domain.common.enums import VendorId
from interfaces.cli._lifecycle import application_container

_ET = ZoneInfo("America/New_York")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh, persist, and return normalized read-only account transactions "
            "through Trading Partner providers."
        )
    )
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--date", help="One US trade date (YYYY-MM-DD).")
    window.add_argument("--start-date", help="Inclusive US start date (YYYY-MM-DD).")
    parser.add_argument(
        "--end-date",
        help="Inclusive US end date; valid only with --start-date (defaults to start).",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=(VendorId.SCHWAB.value, VendorId.MOOMOO.value),
        help="Provider to include; repeat for multiple. Defaults to Schwab and Moomoo.",
    )
    parser.add_argument(
        "--limit",
        type=_bounded_limit,
        default=1_000,
        metavar="1..1000",
        help="Maximum normalized activities returned and persisted (default: 1000).",
    )
    return parser


def _bounded_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= parsed <= 1_000:
        raise argparse.ArgumentTypeError("limit must be in [1,1000]")
    return parsed


def _window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=_ET)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=_ET) - timedelta(
        microseconds=1
    )
    return start, end


async def _run(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        start_day = date.fromisoformat(args.date or args.start_date)
        end_day = date.fromisoformat(args.end_date) if args.end_date else start_day
    except ValueError as exc:
        raise SystemExit("transaction dates must be YYYY-MM-DD") from exc
    if args.date and args.end_date:
        parser.error("--end-date requires --start-date")
    if start_day > end_day:
        parser.error("--start-date must be <= --end-date")
    start, _ = _window(start_day)
    _, end = _window(end_day)
    providers = tuple(
        VendorId(value)
        for value in (args.provider or (VendorId.SCHWAB.value, VendorId.MOOMOO.value))
    )
    async with application_container() as container:
            result = await container.services.account_transactions.get_transactions(
                AccountGetTransactionsInput(
                    providers=providers,
                    start=start,
                    end=end,
                    limit=args.limit,
                )
            )
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
            return 0 if result.ok else 1

def main() -> None:
    """Run one normalized transaction refresh and durable query."""

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
