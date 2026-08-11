"""Operational SGOV Shadow plan CLI; calculation and notification only."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from decimal import Decimal

from application.dto.broker_execution import SgovShadowPlanDTO
from bootstrap import build_default_application
from domain.notifications.enums import NotificationSourceType


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _render_table(result: SgovShadowPlanDTO) -> str:
    header = [
        f"SGOV Shadow 购买计划 · {result.market_session_date or '即时预览'}",
        f"状态：{result.disposition.value} · execution_effect=false",
    ]
    if result.scheduled_for is not None:
        header.append(f"计划时间：{result.scheduled_for.isoformat()}")
    preview = result.preview
    if preview is None:
        if result.warning_codes:
            header.append("Warnings: " + ", ".join(result.warning_codes))
        if result.error_codes:
            header.append("Errors: " + ", ".join(result.error_codes))
        return "\n".join(header)

    header.extend(
        (
            f"SGOV 参考限价：{_money(preview.quote.reference_limit_price)} "
            f"({preview.quote.source}/{preview.quote.price_basis}, "
            f"age={preview.quote.age_seconds}s)",
            "",
            "| Schwab 账户 | cashBalance | 未成交 BUY 预留 | 现金底线 | "
            "缓冲 | Shadow 股数 | 预计金额 | 预计剩余现金 | 状态 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        )
    )
    for account in preview.accounts:
        blockers = ", ".join(account.blocker_codes) if account.blocker_codes else "—"
        header.append(
            f"| {account.account_ref} | {_money(account.cash_balance)} | "
            f"{_money(account.open_buy_order_reserve)} | "
            f"{_money(account.hard_cash_floor)} | {_money(account.operational_buffer)} | "
            f"{account.quantity} | {_money(account.estimated_notional)} | "
            f"{_money(account.projected_cash_after_all_open_buys)} | "
            f"{account.status}{' · ' + blockers if blockers != '—' else ''} |"
        )
    header.extend(
        (
            "",
            f"合计：{preview.total_quantity} 股 / {_money(preview.total_estimated_notional)}",
            "仅为 Shadow 计算；未提交、修改或撤销任何订单。",
        )
    )
    return "\n".join(header)


async def _run(command: str, *, as_json: bool) -> int:
    container = build_default_application()
    try:
        if command == "status":
            entries = tuple(
                item
                for item in container.operations.notifications.recent_entries(100)
                if item.source_type is NotificationSourceType.SYSTEM
                and item.source_id.startswith("sgov-shadow-plan:")
            )
            payload = {
                "ok": True,
                "latest": (
                    {
                        "source_id": entries[0].source_id,
                        "title": entries[0].title,
                        "status": entries[0].status.value,
                        "created_at": entries[0].created_at.isoformat(),
                        "delivered_at": (
                            entries[0].delivered_at.isoformat()
                            if entries[0].delivered_at is not None
                            else None
                        ),
                        "last_error_code": entries[0].last_error_code,
                    }
                    if entries
                    else None
                ),
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        # Both operational jobs refresh accounts; serialize them through the
        # existing account/Watchlist sync lock instead of creating another lane.
        lock = container.resources.post_market_sync_lock
        if not lock.acquire():
            print(
                json.dumps(
                    {"ok": False, "error_codes": ["SGOV_SHADOW_PLAN_ALREADY_RUNNING"]},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 1
        try:
            result = (
                await container.operations.sgov_shadow_plan.run_if_due()
                if command == "run"
                else await container.operations.sgov_shadow_plan.preview_now()
            )
        finally:
            lock.release()
        if as_json:
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        else:
            print(_render_table(result))
        return 1 if result.error_codes else 0
    finally:
        await container.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-sgov-plan",
        description=(
            "Generate a fresh Schwab SGOV Shadow plan. It never submits, replaces, "
            "or cancels an order."
        ),
    )
    parser.add_argument("command", nargs="?", choices=("run", "preview", "status"), default="run")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable receipt")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    raise SystemExit(asyncio.run(_run(args.command, as_json=args.json)))


if __name__ == "__main__":
    main()
