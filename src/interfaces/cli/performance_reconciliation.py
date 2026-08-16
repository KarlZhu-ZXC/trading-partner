"""Owner-only operational CLI for A1 broker-statement reconciliation preparation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.performance_reconciliation import (
    BrokerRealizedReconciliationDTO,
    BrokerRealizedStatementDTO,
)
from bootstrap import build_default_application
from domain.common.errors import TradingPartnerError

_ET = ZoneInfo("America/New_York")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-performance-reconciliation",
        description=(
            "Inspect strictly recognized owner-only broker exports without exposing raw rows."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect-schwab-realized",
        help="Inspect a Schwab Realized Gain/Loss lot-details CSV.",
    )
    inspect.add_argument(
        "--realized-csv",
        required=True,
        metavar="RELATIVE_PATH",
        help=(
            "Path relative to data/artifacts/reconciliation; absolute paths and symlinks "
            "are rejected."
        ),
    )
    compare = commands.add_parser(
        "compare-schwab-realized",
        help="Compare one statement account/month with durable Schwab FIFO attribution.",
    )
    compare.add_argument(
        "--realized-csv",
        required=True,
        metavar="RELATIVE_PATH",
        help="Relative path below data/artifacts/reconciliation.",
    )
    compare.add_argument(
        "--account-ref",
        required=True,
        help="Stable durable Schwab account_ref returned by Trading Partner.",
    )
    compare.add_argument(
        "--statement-account-ref",
        help="Hashed statement account reference from the inspect command; required if ambiguous.",
    )
    compare.add_argument(
        "--month",
        required=True,
        metavar="YYYY-MM",
        help="US/Eastern natural month to reconcile.",
    )
    compare.add_argument(
        "--tolerance",
        type=_nonnegative_decimal,
        default=Decimal("0.01"),
        help="Maximum absolute USD residual considered matched (default: 0.01).",
    )
    compare.add_argument(
        "--no-write-draft",
        action="store_true",
        help="Return the comparison without writing an owner-only receipt draft.",
    )
    return parser


def _nonnegative_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("tolerance must be a Decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("tolerance must be a nonnegative finite Decimal")
    return parsed


def _month_window(value: str) -> tuple[datetime, datetime]:
    try:
        month = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must be YYYY-MM") from exc
    next_month = (
        date(month.year + 1, 1, 1)
        if month.month == 12
        else date(month.year, month.month + 1, 1)
    )
    start = datetime.combine(month, time.min, tzinfo=_ET)
    end = datetime.combine(next_month, time.min, tzinfo=_ET) - timedelta(microseconds=1)
    return start, end


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    container = build_default_application()
    try:
        try:
            service = container.operations.performance_reconciliation
            result: BrokerRealizedStatementDTO | BrokerRealizedReconciliationDTO
            if args.command == "inspect-schwab-realized":
                result = service.inspect_schwab_realized_gain_loss(args.realized_csv)
            elif args.command == "compare-schwab-realized":
                try:
                    period_start, period_end = _month_window(args.month)
                except argparse.ArgumentTypeError as exc:
                    parser = _parser()
                    parser.error(
                        container.context.secret_redactor.redact_text(str(exc))
                    )
                result = service.compare_schwab_realized_gain_loss(
                    relative_path=args.realized_csv,
                    durable_account_ref=args.account_ref,
                    statement_account_ref=args.statement_account_ref,
                    period_start=period_start,
                    period_end=period_end,
                    tolerance=args.tolerance,
                    write_draft=not args.no_write_draft,
                )
            else:
                raise AssertionError("unsupported reconciliation command")
            payload = {"ok": True, "data": result.model_dump(mode="json")}
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, container.context.secret_redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, container.context.secret_redactor)
            )
            print(
                json.dumps(
                    {"ok": False, "error": error.model_dump(mode="json")},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 1
    finally:
        container.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
