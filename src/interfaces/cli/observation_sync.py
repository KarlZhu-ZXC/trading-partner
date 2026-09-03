"""Provider-neutral external observation synchronization CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict

from domain.external_note.enums import NoteSyncStatus
from interfaces.cli._lifecycle import application_container


async def _run(args: argparse.Namespace) -> int:
    async with application_container() as container:
        service = container.services.external_notes
        receipt = await service.sync(analyze=False, source_code=args.source)
        should_analyze = args.analyze or args.reanalyze_all
        analysis = (
            await service.analyze_pending(
                limit=args.analysis_limit,
                retry_failed=args.retry_failed or args.reanalyze_all,
                reanalyze_succeeded=args.reanalyze_all,
            )
            if should_analyze
            else ()
        )
        analysis_errors = Counter(
            item.error_code or "UNKNOWN" for item in analysis if item.status == "FAILED"
        )
        print(
            json.dumps(
                {
                    "ok": receipt.status is not NoteSyncStatus.FAILED,
                    **asdict(receipt),
                    "source_code": args.source,
                    "sources": [asdict(item) for item in service.source_capabilities()],
                    "analysis_attempted": len(analysis),
                    "analysis_succeeded": sum(item.status == "SUCCEEDED" for item in analysis),
                    "analysis_failed": sum(item.status == "FAILED" for item in analysis),
                    "analysis_error_codes": dict(sorted(analysis_errors.items())),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
        return 0 if receipt.status is not NoteSyncStatus.FAILED else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-observation-sync",
        description="Import every configured external observation source.",
    )
    parser.add_argument("--source", default=None)
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--analysis-limit", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--reanalyze-all",
        action="store_true",
        help="Re-run every latest FULL revision, including prior successes and failures.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_run(_parser().parse_args(argv))))


if __name__ == "__main__":
    main()
