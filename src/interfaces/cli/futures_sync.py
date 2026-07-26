"""Explicit zero-subscription futures definition and EOD statistics sync."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, date, datetime

from bootstrap import build_default_application

_DEFAULT_PRODUCTS = ("CME:GC", "CME:MGC", "CME:SI", "CME:HG", "CME:PL", "CME:PA", "DCE:LH")


async def _run(products: tuple[str, ...], trade_date: date) -> int:
    container = build_default_application()
    outcomes: list[dict[str, object]] = []
    try:
        as_of = datetime.now(UTC)
        for product_key in products:
            chain = await container.futures_contract_service.list_contracts(
                product_key,
                as_of,
                refresh=True,
            )
            if not chain.ok or chain.data is None:
                outcomes.append(
                    {
                        "product_key": product_key,
                        "ok": False,
                        "contract_count": 0,
                        "statistics_count": 0,
                        "warning_codes": [item.code for item in chain.warnings],
                        "error_codes": (
                            [chain.error.code]
                            if chain.error
                            else ["FUTURES_CHAIN_UNAVAILABLE"]
                        ),
                    }
                )
                continue
            instrument_ids = tuple(item.instrument_id for item in chain.data)
            statistics = await container.futures_contract_service.get_statistics(
                instrument_ids,
                trade_date,
                as_of,
                persist=True,
            )
            outcomes.append(
                {
                    "product_key": product_key,
                    "ok": statistics.ok,
                    "contract_count": len(chain.data),
                    "statistics_count": len(statistics.data or ()),
                    "warning_codes": list(
                        dict.fromkeys(
                            [item.code for item in chain.warnings]
                            + [item.code for item in statistics.warnings]
                        )
                    ),
                    "error_codes": [statistics.error.code] if statistics.error else [],
                }
            )
        ok = all(bool(item["ok"]) for item in outcomes)
        print(
            json.dumps(
                {
                    "ok": ok,
                    "trade_date": trade_date.isoformat(),
                    "products": outcomes,
                    "execution_effect": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if ok else 1
    finally:
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync free CME/DCE definitions and official EOD statistics."
    )
    parser.add_argument(
        "--product",
        action="append",
        choices=_DEFAULT_PRODUCTS,
        dest="products",
        help="Product key; repeat to sync multiple products (default: all).",
    )
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=date.today(),
        help="Official statistics trade date (YYYY-MM-DD).",
    )
    args = parser.parse_args()
    products = tuple(args.products) if args.products else _DEFAULT_PRODUCTS
    raise SystemExit(asyncio.run(_run(products, args.trade_date)))


if __name__ == "__main__":
    main()
