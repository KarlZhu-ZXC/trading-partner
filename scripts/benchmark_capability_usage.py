"""Measure registry instrumentation using disposable counters and synthetic calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import tempfile
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from infrastructure.observability.capability_usage import SqliteCapabilityUsageStore
from interfaces.capabilities import READ_DURABLE, CompactCapabilityRegistry


async def measure(repeats: int) -> dict[str, Any]:
    async def read(value: int = 1) -> dict[str, Any]:
        return {"ok": True, "data": {"synthetic": value}}

    with tempfile.TemporaryDirectory(prefix="tp-capability-meter-") as directory:
        store = SqliteCapabilityUsageStore(Path(directory) / "diagnostics" / "usage.sqlite3")
        plain = CompactCapabilityRegistry()
        recorded = CompactCapabilityRegistry(store, source="CONSOLE")
        for registry in (plain, recorded):
            registry.add_capability(read, name="system_health", policy=READ_DURABLE)
        start = perf_counter_ns()
        await recorded.invoke_uncompacted("system_health", {})
        cold_ms = (perf_counter_ns() - start) / 1_000_000
        for registry in (plain, recorded):
            for _ in range(10):
                await registry.invoke_uncompacted("system_health", {})
        samples: dict[str, list[float]] = {"without_meter": [], "with_durable_meter": []}
        # Alternate order each round to reduce systematic order bias.
        pairs = [("without_meter", plain), ("with_durable_meter", recorded)]
        for index in range(repeats):
            for name, registry in pairs if index % 2 == 0 else reversed(pairs):
                start = perf_counter_ns()
                result = await registry.invoke_uncompacted("system_health", {})
                samples[name].append((perf_counter_ns() - start) / 1_000_000)
                assert result == {"ok": True, "data": {"synthetic": 1}}
        snapshot = store.snapshot()
        recorded_calls = sum(row["calls"] for row in snapshot["rows"])
        assert recorded_calls == repeats + 11
        assert recorded.usage_recording_failures == 0
        return {
            "scope": "synthetic no-op validated call; includes local counter commit overhead",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "repeats": repeats,
            "warmups": 10,
            "order": "alternating",
            "cold_initialization_ms": cold_ms,
            "recorded_calls": recorded_calls,
            "results": {
                name: {
                    "p50_ms": statistics.median(values),
                    "p95_ms": sorted(values)[math.ceil(0.95 * len(values)) - 1],
                    "samples_ms": values,
                }
                for name, values in samples.items()
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=100)
    args = parser.parse_args()
    if not 10 <= args.repeats <= 1000:
        parser.error("repeats must be in [10,1000]")
    result = asyncio.run(measure(args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
