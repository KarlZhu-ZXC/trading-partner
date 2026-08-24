"""Project-owned CLI for durable Trade Retro and Obsidian projection."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from application.dto.account_transactions import TradeCycleQueryInput
from application.dto.behavior_review import (
    BehaviorActionInputDTO,
    BehaviorReviewRunInput,
)
from application.dto.tool_envelope import ToolEnvelope
from application.dto.trade_retro import TradeRetroHistoryInput
from application.services.trade_retro_schedule import trade_retro_weekly_windows
from domain.behavior_review.enums import BehaviorReviewPeriodKind
from interfaces.cli._lifecycle import application_container


def _period(start: str | None, end: str | None) -> tuple[datetime, datetime]:
    if (start is None) != (end is None):
        raise ValueError("--start and --end must be supplied together")
    if start is not None and end is not None:
        start_at = datetime.combine(date.fromisoformat(start), time.min, tzinfo=UTC)
        end_at = datetime.combine(date.fromisoformat(end), time.min, tzinfo=UTC)
    else:
        today = datetime.now(UTC).date()
        current_monday = today - timedelta(days=today.weekday())
        end_at = datetime.combine(current_monday, time.min, tzinfo=UTC)
        start_at = end_at - timedelta(days=7)
    if start_at >= end_at:
        raise ValueError("Trade Retro period must be non-empty")
    return start_at, end_at


def _weekly_windows(now: datetime | None = None) -> tuple[datetime, datetime, datetime, datetime]:
    """Return the last completed US trading week and the next snapshot window.

    The review window is Monday 00:00 UTC through Saturday 00:00 UTC. At the
    configured Saturday Asia/Shanghai cadence this includes the completed Friday
    US session without pretending that the non-trading weekend needs coverage.
    """

    return trade_retro_weekly_windows(now)


def _week_key(prefix: str, start: datetime) -> str:
    iso = start.isocalendar()
    return f"{prefix}-{iso.year}-w{iso.week:02d}"


def _markdown_section(path: Path, heading: str = "## 2. Retro") -> str:
    if path.suffix.lower() != ".md":
        raise ValueError("Trade Retro import requires a Markdown file")
    lines = path.read_text(encoding="utf-8").splitlines()
    start_index = next(
        (index for index, line in enumerate(lines) if line.strip() == heading),
        None,
    )
    if start_index is None:
        raise ValueError(f"Markdown section was not found: {heading}")
    end_index = next(
        (
            index
            for index in range(start_index + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    section = "\n".join(lines[start_index:end_index]).strip()
    if not section:
        raise ValueError("Markdown Trade Retro section is empty")
    if len(section) > 50_000:
        raise ValueError("Markdown Trade Retro section exceeds 50000 characters")
    return section


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare, generate, inspect, or export deterministic Trade Retro records."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "run"):
        item = subparsers.add_parser(command)
        item.add_argument("--start", help="UTC start date, YYYY-MM-DD")
        item.add_argument("--end", help="UTC exclusive end date, YYYY-MM-DD")
        item.add_argument("--idempotency-key", required=True)
        if command == "run":
            item.add_argument("--no-llm", action="store_true")
            item.add_argument("--export-obsidian", action="store_true")
    history = subparsers.add_parser("history")
    history.add_argument("--run-id")
    history.add_argument("--limit", type=int, default=20)
    export = subparsers.add_parser("export")
    export.add_argument("--run-id", required=True)
    export.add_argument("--idempotency-key", required=True)
    imported = subparsers.add_parser(
        "import-markdown",
        help="Import one historical Markdown retro as an explicitly labelled legacy run.",
    )
    imported.add_argument("--path", required=True)
    imported.add_argument("--start", required=True, help="UTC start date, YYYY-MM-DD")
    imported.add_argument("--end", required=True, help="UTC exclusive end date, YYYY-MM-DD")
    imported.add_argument("--idempotency-key", required=True)
    weekly = subparsers.add_parser(
        "weekly",
        help="Audit the completed US trading week and snapshot next week's plans.",
    )
    weekly.add_argument("--no-llm", action="store_true")
    weekly.add_argument("--export-obsidian", action="store_true")
    weekly.add_argument(
        "--idempotency-key",
        help="Explicit run key for an authorized rerun; default is the stable weekly key.",
    )
    args = parser.parse_args(argv)

    async with application_container() as container:
            envelope: ToolEnvelope[Any]
            if args.command == "history":
                envelope = container.services.trade_retro.history(
                    TradeRetroHistoryInput(run_id=args.run_id, limit=args.limit)
                )
            elif args.command == "export":
                envelope = container.services.trade_retro.export(
                    run_id=args.run_id,
                    idempotency_key=args.idempotency_key,
                )
            elif args.command == "import-markdown":
                start_at, end_at = _period(args.start, args.end)
                path = Path(args.path).expanduser().resolve(strict=True)
                envelope = container.services.trade_retro.import_legacy_markdown(
                    start=start_at,
                    end=end_at,
                    generated_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                    summary_markdown=_markdown_section(path),
                    idempotency_key=args.idempotency_key,
                )
            elif args.command == "weekly":
                review_start, review_end, prepare_start, prepare_end = _weekly_windows()
                rerun_key = args.idempotency_key.strip() if args.idempotency_key else None
                if args.idempotency_key is not None and not rerun_key:
                    parser.error("--idempotency-key must not be blank")
                run_key = rerun_key or _week_key("retro-run", review_start)
                run_envelope = await container.services.trade_retro.run(
                    start=review_start,
                    end=review_end,
                    idempotency_key=run_key,
                    use_llm=not args.no_llm,
                )
                export_envelope = None
                if args.export_obsidian and run_envelope.ok and run_envelope.data is not None:
                    export_envelope = container.services.trade_retro.export(
                        run_id=run_envelope.data.run_id,
                        idempotency_key=f"{run_key}-obsidian",
                    )
                prepare_envelope = container.services.trade_retro.prepare(
                    start=prepare_start,
                    end=prepare_end,
                    idempotency_key=(
                        f"{rerun_key}-plan"
                        if rerun_key is not None
                        else _week_key("retro-plan", prepare_start)
                    ),
                )
                behavior_end = review_start + timedelta(days=7)
                try:
                    review_items = container.services.review_items.list_open(limit=500)
                    action_items = tuple(
                        BehaviorActionInputDTO(
                            action_text=item.detail or item.title,
                            review_item_source_keys=(item.source_key,),
                        )
                        for item in review_items
                        if item.source_type == "TRADE_RETRO"
                    )
                    source_complete = len(review_items) < 500
                    source_error = (
                        None if source_complete else "REVIEW_ITEM_SOURCE_LIMIT_REACHED"
                    )
                except Exception:  # noqa: BLE001 - failed reads never imply resolution
                    action_items = ()
                    source_complete = False
                    source_error = "REVIEW_ITEM_SOURCE_UNAVAILABLE"
                cycles = container.services.account_transactions.get_trade_cycles(
                    TradeCycleQueryInput(start=review_start, end=behavior_end, limit=500)
                )
                behavior_review = container.services.behavior_reviews.run(
                    BehaviorReviewRunInput(
                        period_kind=BehaviorReviewPeriodKind.WEEKLY,
                        period_start=review_start,
                        period_end=behavior_end,
                        strategy_code="strategy_v1",
                        cycle_ids=(
                            tuple(item.cycle_id for item in cycles.data.cycles)
                            if cycles.ok and cycles.data is not None
                            else ()
                        ),
                        retro_run_ids=(
                            (run_envelope.data.run_id,)
                            if run_envelope.ok and run_envelope.data is not None
                            else ()
                        ),
                        action_items=action_items,
                        source_read_complete=source_complete,
                        source_error_code=source_error,
                        idempotency_key=(
                            f"{rerun_key}-behavior"
                            if rerun_key is not None
                            else _week_key("behavior-review", review_start)
                        ),
                    )
                )
                ok = run_envelope.ok and prepare_envelope.ok and (
                    export_envelope is None or export_envelope.ok
                )
                print(
                    json.dumps(
                        {
                            "ok": ok,
                            "run": run_envelope.model_dump(mode="json"),
                            "export": (
                                export_envelope.model_dump(mode="json")
                                if export_envelope is not None
                                else None
                            ),
                            "prepare": prepare_envelope.model_dump(mode="json"),
                            "behavior_review": behavior_review.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0 if ok else 1
            else:
                start_at, end_at = _period(args.start, args.end)
                if args.command == "prepare":
                    envelope = container.services.trade_retro.prepare(
                        start=start_at,
                        end=end_at,
                        idempotency_key=args.idempotency_key,
                    )
                else:
                    envelope = await container.services.trade_retro.run(
                        start=start_at,
                        end=end_at,
                        idempotency_key=args.idempotency_key,
                        use_llm=not args.no_llm,
                    )
                    if envelope.ok and args.export_obsidian and envelope.data is not None:
                        export_envelope = container.services.trade_retro.export(
                            run_id=envelope.data.run_id,
                            idempotency_key=f"{args.idempotency_key}-obsidian",
                        )
                        payload = envelope.model_dump(mode="json")
                        payload["export"] = export_envelope.model_dump(mode="json")
                        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                        return 0 if export_envelope.ok else 1
            print(json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
            return 0 if envelope.ok else 1

def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
