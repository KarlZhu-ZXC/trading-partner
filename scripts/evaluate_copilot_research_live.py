#!/usr/bin/env python3
"""Opt-in live Copilot acceptance; fixed synthetic data, no business runtime startup."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

from infrastructure.composition.runtime import build_agent_model_provider
from infrastructure.config.settings import AppSettings
from interfaces.cli.copilot_research_live_evaluation import CASES, run_live_research_acceptance


async def run(
    output: Path,
    case_ids: list[str] | None,
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None,
) -> int:
    try:
        settings = AppSettings.load()
        provider = build_agent_model_provider(settings)
    except Exception:  # noqa: BLE001 - settings errors may contain private values
        print("EVALUATION_CONFIGURATION_UNAVAILABLE")
        return 2
    if provider is None:
        print("EVALUATION_MODEL_UNAVAILABLE")
        return 2
    try:
        result = await run_live_research_acceptance(
            provider,
            cases=tuple(case for case in CASES if case_ids is None or case.case_id in case_ids),
            reasoning_effort=reasoning_effort,
            on_case=lambda row: print(
                json.dumps({k: row[k] for k in ("case_id", "passed", "errors")}), flush=True
            ),
        )
    finally:
        await provider.aclose()
    config = settings.resolved_llm_config
    result["configuration"] = {
        "provider": settings.resolved_llm_provider_id,
        "model": config.model if config else None,
        "reasoning_mode": config.reasoning_mode if config else None,
        "reasoning_effort": config.reasoning_effort if config else None,
        "requested_effort": reasoning_effort,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Synthetic acceptance report: {output}")
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Authorize configured model calls on synthetic data"
    )
    parser.add_argument("--case", action="append", choices=[case.case_id for case in CASES])
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"])
    parser.add_argument("--output", type=Path, default=Path("artifacts/copilot-research-live.json"))
    args = parser.parse_args()
    if not args.live:
        print("No model calls. Cases: " + ", ".join(case.case_id for case in CASES))
        return 0
    return asyncio.run(run(args.output, args.case, args.reasoning_effort))


if __name__ == "__main__":
    raise SystemExit(main())
