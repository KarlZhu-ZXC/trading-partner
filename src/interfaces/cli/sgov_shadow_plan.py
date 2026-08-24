"""Operational SGOV plan CLI with one dedicated automatic-buy command."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC
from decimal import Decimal

from application.dto.broker_execution import (
    SgovAutoOrderOutcome,
    SgovShadowPlanDisposition,
    SgovShadowPlanDTO,
)
from application.dto.operational_job import OperationalJobRunDTO
from application.services.operational_job_runtime import OperationalJobOutcome
from bootstrap import build_default_application
from domain.notifications.enums import NotificationSourceType
from domain.operations.enums import OperationalJobStatus


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _render_table(result: SgovShadowPlanDTO) -> str:
    header = [
        f"SGOV {'自动买入' if result.automation_enabled else 'Shadow 购买计划'} · "
        f"{result.market_session_date or '即时预览'}",
        f"状态：{result.disposition.value} · "
        f"execution_effect={str(result.execution_effect).lower()}",
    ]
    if result.scheduled_for is not None:
        header.append(f"计划时间：{result.scheduled_for.isoformat()}")
    if result.completion_check_at is not None:
        header.append(f"完成复核：{result.completion_check_at.isoformat()}")
    if result.phase is not None:
        header.append(f"阶段：{result.phase.value}")
    for order in result.orders:
        header.append(
            f"订单：{order.account_ref} · {order.outcome.value} · "
            f"{order.quantity} 股 @ {_money(order.limit_price)} · "
            f"{order.order_intent_id or '无意图ID'}"
        )
    preview = result.preview
    if preview is None:
        if result.warning_codes:
            header.append("Warnings: " + ", ".join(result.warning_codes))
        if result.error_codes:
            header.append("Errors: " + ", ".join(result.error_codes))
        return "\n".join(header)

    header.extend(
        (
            f"SGOV bid / ask：{_money(preview.quote.bid)} / {_money(preview.quote.ask)} "
            f"({preview.quote.source}, age={preview.quote.age_seconds}s)",
            "数量按 ask 保守计算；先挂 bid，未成交则在收盘前5分钟重新刷新并用当时 ask 限价。",
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
            (
                "自动路径仅允许 SGOV BUY LIMIT · DAY · NORMAL。"
                if result.automation_enabled
                else "仅为 Shadow 计算；未提交、修改或撤销任何订单。"
            ),
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
                and item.source_id.startswith(("sgov-shadow-plan:", "sgov-auto-plan:"))
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
            if command == "auto-run":
                async def operation() -> OperationalJobOutcome[SgovShadowPlanDTO]:
                    value = await container.operations.sgov_shadow_plan.run_if_due(
                        auto_execute=True
                    )
                    if value.disposition is not SgovShadowPlanDisposition.EXECUTED:
                        return OperationalJobOutcome(
                            status=OperationalJobStatus.SKIPPED,
                            result_code=f"SGOV_AUTO_{value.disposition.value}",
                            value=value,
                        )
                    reconciliation = any(
                        item.outcome
                        in {
                            SgovAutoOrderOutcome.FAILED,
                            SgovAutoOrderOutcome.RECONCILIATION_REQUIRED,
                        }
                        for item in value.orders
                    )
                    failed = bool(value.error_codes) or reconciliation
                    error_code = (
                        value.error_codes[0]
                        if value.error_codes
                        else "SGOV_AUTO_RECONCILIATION_REQUIRED"
                        if reconciliation
                        else None
                    )
                    return OperationalJobOutcome(
                        status=(
                            OperationalJobStatus.FAILED
                            if failed
                            else OperationalJobStatus.SUCCEEDED
                        ),
                        result_code=(
                            "SGOV_AUTO_FAILED" if failed else "SGOV_AUTO_SUCCEEDED"
                        ),
                        error_code=error_code,
                        value=value,
                    )

                minute = container.context.clock.now().astimezone(UTC).strftime(
                    "%Y%m%dT%H%M"
                )
                execution = await container.operations.jobs.execute(
                    job_name="sgov.auto_run",
                    idempotency_key=f"sgov-auto:{minute}",
                    operation=operation,
                    lease_seconds=900,
                )
                result = execution.value
                if result is None:
                    print(
                        json.dumps(
                            {
                                "ok": execution.run.status
                                in {
                                    OperationalJobStatus.SUCCEEDED,
                                    OperationalJobStatus.SKIPPED,
                                },
                                "operational_job": OperationalJobRunDTO.from_domain(
                                    execution.run
                                ).model_dump(mode="json"),
                                "invoked": execution.invoked,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                    return (
                        0
                        if execution.run.status
                        in {
                            OperationalJobStatus.SUCCEEDED,
                            OperationalJobStatus.SKIPPED,
                        }
                        else 1
                    )
            elif command == "run":
                result = await container.operations.sgov_shadow_plan.run_if_due()
            else:
                result = await container.operations.sgov_shadow_plan.preview_now()
        finally:
            lock.release()
        if as_json:
            payload = result.model_dump(mode="json")
            if command == "auto-run":
                payload["operational_job"] = OperationalJobRunDTO.from_domain(
                    execution.run
                ).model_dump(mode="json")
                payload["invoked"] = execution.invoked
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(_render_table(result))
        return 1 if result.error_codes else 0
    finally:
        await container.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-sgov-plan",
        description=(
            "Generate a Schwab SGOV plan. auto-run uses the dedicated persistent "
            "SGOV-only BUY authorization; run and preview remain non-executing."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("auto-run", "run", "preview", "status"),
        default="run",
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable receipt")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    raise SystemExit(asyncio.run(_run(args.command, as_json=args.json)))


if __name__ == "__main__":
    main()
