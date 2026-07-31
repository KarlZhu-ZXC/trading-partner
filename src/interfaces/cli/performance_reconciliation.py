"""Owner-only operational CLI for A1 broker-statement reconciliation preparation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from bootstrap import build_default_application
from domain.common.errors import TradingPartnerError


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
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    container = build_default_application()
    try:
        try:
            if args.command != "inspect-schwab-realized":
                raise AssertionError("unsupported reconciliation command")
            service = container.operations.performance_reconciliation
            result = service.inspect_schwab_realized_gain_loss(args.realized_csv)
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
