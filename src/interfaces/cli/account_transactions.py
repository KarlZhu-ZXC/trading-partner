"""Operational CLI for normalized durable account transaction facts."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from application.dto.account_transactions import AccountGetTransactionsInput
from bootstrap import build_default_application
from domain.common.enums import VendorId

_ET = ZoneInfo("America/New_York")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh, persist, and return normalized read-only account transactions "
            "through Trading Partner providers."
        )
    )
    parser.add_argument("--date", required=True, help="US trade date (YYYY-MM-DD).")
    parser.add_argument(
        "--provider",
        action="append",
        choices=(VendorId.SCHWAB.value, VendorId.MOOMOO.value),
        help="Provider to include; repeat for multiple. Defaults to Schwab and Moomoo.",
    )
    parser.add_argument("--limit", type=int, default=1_000, choices=range(1, 1_001))
    return parser


def _window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=_ET)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=_ET) - timedelta(
        microseconds=1
    )
    return start, end


async def _run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        day = date.fromisoformat(args.date)
    except ValueError as exc:
        raise SystemExit("--date must be YYYY-MM-DD") from exc
    start, end = _window(day)
    providers = tuple(
        VendorId(value)
        for value in (args.provider or (VendorId.SCHWAB.value, VendorId.MOOMOO.value))
    )
    container = build_default_application()
    try:
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
    finally:
        await container.aclose()


def main() -> None:
    """Run one normalized transaction refresh and durable query."""

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
