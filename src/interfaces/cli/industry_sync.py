"""Explicit historical industry dataset synchronization command."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from application.dto.a_share import AShareGetIndustryCycleInput
from domain.a_share.enums import IndustryCycleType
from interfaces.cli._lifecycle import application_container


def _month_count(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month + 1


async def _run(months: int) -> int:
    async with application_container() as container:
            envelope = await container.services.a_share.get_industry_cycle(
                AShareGetIndustryCycleInput(cycle="hog", lookback_months=months)
            )
            if not envelope.ok or envelope.data is None:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "errors": [error.code for error in envelope.errors],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 1
            stored = container.operations.industry_metrics.list_visible(
                cycle=IndustryCycleType.HOG,
                as_of=envelope.as_of,
            )
            monthly_periods = sorted(
                {
                    item.period_end
                    for item in stored
                    if item.metric_code == "live_hog_cny_per_kg"
                }
            )
            span_months = (
                _month_count(monthly_periods[0], monthly_periods[-1])
                if monthly_periods
                else 0
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "cycle": "hog",
                        "requested_months": months,
                        "stored_observations": len(stored),
                        "monthly_price_periods": len(monthly_periods),
                        "first_month": monthly_periods[0].strftime("%Y-%m")
                        if monthly_periods
                        else None,
                        "last_month": monthly_periods[-1].strftime("%Y-%m")
                        if monthly_periods
                        else None,
                        "missing_months_within_span": span_months - len(monthly_periods),
                        "warning_codes": [warning.code for warning in envelope.warnings],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=240, choices=range(3, 241))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.months)))


if __name__ == "__main__":
    main()
