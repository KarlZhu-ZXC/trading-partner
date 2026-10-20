#!/usr/bin/env python3
"""Measure fresh-process MCP import, synthetic composition and tool discovery.

No owner settings, database, providers or models are used. Composition mode excludes
the host handshake; stdio mode includes initialize/tool listing and idle server RSS.
Both exclude populated database queries, cold disk and memory after business tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def sample() -> dict[str, object]:
    import socket

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("synthetic startup must not contact the network")

    socket.socket.connect = forbid_network  # type: ignore[method-assign]
    socket.socket.connect_ex = forbid_network  # type: ignore[method-assign,assignment]
    started = time.perf_counter()
    from bootstrap import build_application
    from infrastructure.config.settings import AppSettings
    from interfaces.mcp.server import create_mcp_server

    imported = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="tp-mcp-startup-") as root:
        settings = AppSettings(
            _env_file=None,
            app_name="synthetic-startup",
            app_env="test",
            log_level="INFO",
            database_url="sqlite:///:memory:",
            mcp_server_name="synthetic-startup",
            default_timezone="UTC",
            provider_timeout_seconds=5.0,
            runtime_root=Path(root),
        )
        container = build_application(settings)
        composed = time.perf_counter()

        async def discover() -> dict[str, object]:
            try:
                tools = await create_mcp_server(container).list_tools()
                discovered = time.perf_counter()
                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                return {
                    "import_seconds": imported - started,
                    "composition_seconds": composed - imported,
                    "discovery_seconds": discovered - composed,
                    "total_seconds": discovered - started,
                    "peak_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
                    "tool_names": sorted(tool.name for tool in tools),
                    "loaded_numerical_modules": sorted(
                        name
                        for name in ("numpy", "pandas", "yfinance", "exchange_calendars")
                        if name in sys.modules
                    ),
                }
            finally:
                await container.aclose()

        return asyncio.run(discover())


def sample_stdio() -> dict[str, object]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix="tp-mcp-stdio-") as directory:
        env_file = Path(directory) / "synthetic.env"
        env_file.write_text(
            "APP_NAME=synthetic-startup\nAPP_ENV=test\nLOG_LEVEL=INFO\n"
            "DATABASE_URL=sqlite:///:memory:\nMCP_SERVER_NAME=synthetic-startup\n"
            "DEFAULT_TIMEZONE=UTC\nPROVIDER_TIMEOUT_SECONDS=5\n"
            f"RUNTIME_ROOT={directory}\n"
        )
        script = (
            "import os, socket, sys\n"
            "print('SYNTHETIC_PID=' + str(os.getpid()), file=sys.stderr, flush=True)\n"
            "def block(*args, **kwargs):\n"
            "    raise AssertionError('synthetic startup must not contact the network')\n"
            "socket.socket.connect = block\n"
            "socket.socket.connect_ex = block\n"
            "from interfaces.mcp.server import main\n"
            f"main(['--env-file', {str(env_file)!r}])\n"
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", script],
            cwd=directory,
            env={"PYTHONPATH": os.environ["PYTHONPATH"], "PYTHONDONTWRITEBYTECODE": "1"},
        )

        async def handshake() -> dict[str, object]:
            started = time.perf_counter()
            idle_rss = None
            with tempfile.TemporaryFile(mode="w+") as diagnostics:
                async with (
                    stdio_client(params, errlog=diagnostics) as (read, write),
                    ClientSession(read, write) as client,
                ):
                    await client.initialize()
                    initialized = time.perf_counter()
                    tools = await client.list_tools()
                    listed = time.perf_counter()
                    diagnostics.seek(0)
                    pid = next(
                        line.removeprefix("SYNTHETIC_PID=").strip()
                        for line in diagnostics
                        if line.startswith("SYNTHETIC_PID=")
                    )
                    assert pid.isdecimal()
                    try:
                        rss = subprocess.run(
                            ["ps", "-o", "rss=", "-p", pid],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            check=False,
                        )
                        if rss.returncode == 0 and rss.stdout.strip().isdecimal():
                            idle_rss = int(rss.stdout.strip()) * 1024
                    except (OSError, subprocess.TimeoutExpired):
                        pass  # A restricted host may expose only peak RSS.
            peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            return {
                "initialize_seconds": initialized - started,
                "discovery_seconds": listed - initialized,
                "total_seconds": listed - started,
                "peak_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
                "idle_rss_bytes": idle_rss,
                "idle_rss_basis": "ps_after_tool_listing" if idle_rss else "unavailable",
                "tool_names": sorted(tool.name for tool in tools.tools),
            }

        return asyncio.run(handshake())


def run(repeats: int, mode: str) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    # Allowlist excludes inherited provider credentials and AppSettings overrides.
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    environment.update(PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    samples = []
    with tempfile.TemporaryDirectory(prefix="tp-mcp-runner-") as directory:
        environment["TMPDIR"] = directory
        for _ in range(repeats):
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--sample", "--mode", mode],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            samples.append(json.loads(result.stdout))
    assert all(row["tool_names"] == samples[0]["tool_names"] for row in samples)
    return {
        "schema": "mcp-startup-benchmark-v1",
        "mode": mode,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "fixture": "empty in-memory SQLite, default disabled optional resources, nine tools",
        "boundary": (
            "fresh process import + test composition + FastMCP list_tools; warm OS cache"
            if mode == "composition"
            else "server process spawn + stdio initialize + list_tools; client SDK already loaded"
        ),
        "samples": samples,
        "medians": {
            key: (
                statistics.median(row[key] for row in samples)
                if all(isinstance(row[key], (int, float)) for row in samples)
                else None
            )
            for key in samples[0]
            if key.endswith(("_seconds", "_bytes"))
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mode", choices=("composition", "stdio"), default="composition")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be 1..10")
    result = (
        (sample() if args.mode == "composition" else sample_stdio())
        if args.sample
        else run(args.repeats, args.mode)
    )
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(encoded)
    print(encoded, end="")
