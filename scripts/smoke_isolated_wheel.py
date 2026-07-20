#!/usr/bin/env python3
"""Isolated wheel smoke (Phase 1D v1.30).

Build a wheel, install it into a temp venv *outside* the repo, set required
process environment keys, call ``AppSettings.load()`` (installed
layout — no implicit cwd ``.env``), assert packaged vendor-chain resolution,
``build_application``, then exercise both routed mock snapshots.

Import-only or constructor-only checks are insufficient — the real console/MCP
startup path uses ``AppSettings.load()`` under an installed layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROBE_SCRIPT = r"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from bootstrap import build_application
from domain.common.enums import AppEnvironment, LogLevel, Market
from infrastructure.config.settings import (
    PACKAGED_A_SHARE_TRADING_CALENDAR_PATH,
    PACKAGED_CNINFO_ORG_MAP_PATH,
    PACKAGED_VENDOR_CHAIN_PATH,
    AppSettings,
)
from infrastructure.providers.a_share.cninfo_org_map import load_cninfo_org_map

as_of = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)

# Installed layout: packaged YAML must be present next to infrastructure.config.
assert PACKAGED_VENDOR_CHAIN_PATH.is_file(), (
    f"packaged vendor chain missing: {PACKAGED_VENDOR_CHAIN_PATH}"
)
# E1 calendar fixture is packaged the same way; path only (no bootstrap wiring).
assert PACKAGED_A_SHARE_TRADING_CALENDAR_PATH.is_file(), (
    f"packaged A-share trading calendar missing: "
    f"{PACKAGED_A_SHARE_TRADING_CALENDAR_PATH}"
)
# E3 CNINFO org map: packaged force-include only (not bootstrap-wired).
assert PACKAGED_CNINFO_ORG_MAP_PATH.is_file(), (
    f"packaged CNINFO org map missing: {PACKAGED_CNINFO_ORG_MAP_PATH}"
)
# Public loader API against the packaged file; keys are 6-digit codes.
org_map = load_cninfo_org_map(PACKAGED_CNINFO_ORG_MAP_PATH)
for symbol, expected_org in (
    ("600519.SH", "gssh0600519"),
    ("000001.SZ", "gssz0000001"),
):
    code6 = symbol.split(".", 1)[0]
    assert org_map[code6] == expected_org, (symbol, org_map.get(code6), expected_org)

# Real console/MCP path: load from process environment only (no cwd .env).
settings = AppSettings.load()
assert settings.vendor_chain_path == PACKAGED_VENDOR_CHAIN_PATH.resolve(), (
    settings.vendor_chain_path,
    PACKAGED_VENDOR_CHAIN_PATH,
)
assert settings.app_name == "trading-partner-wheel-smoke"
assert settings.database_url.startswith("sqlite:///")

# Optional constructor sanity: packaged default still resolves without kwargs env.
ctor = AppSettings(
    _env_file=None,  # type: ignore[call-arg]
    app_name="ctor-check",
    app_env=AppEnvironment.TEST,
    log_level=LogLevel.INFO,
    database_url=settings.database_url,
    mcp_server_name="ctor-check",
    default_timezone="UTC",
    provider_timeout_seconds=5.0,
)
assert ctor.vendor_chain_path == PACKAGED_VENDOR_CHAIN_PATH.resolve()

container = build_application(settings)
try:
    async def _run() -> None:
        us = await container.mock_market_snapshot_coordinator.get_snapshot(
            Market.US, "NVDA", as_of
        )
        assert us.ok is True, us
        assert us.degraded is True
        assert us.warnings[0].code == "MOCK_DATA"
        assert us.data is not None
        assert us.data.latest_market_row.close == Decimal("173.00")
        assert us.data.instrument.instrument_id == "equity:US:NVDA"

        a = await container.mock_market_snapshot_coordinator.get_snapshot(
            Market.A_SHARE, "600519.SH", as_of
        )
        assert a.ok is True, a
        assert a.degraded is True
        assert a.warnings[0].code == "MOCK_DATA"
        assert a.data is not None
        assert a.data.latest_market_row.close == Decimal("1505.00")
        assert a.data.instrument.instrument_id == "equity:A_SHARE:600519.SH"

    asyncio.run(_run())
finally:
    container.close()

print("ISOLATED_WHEEL_SMOKE_OK")
"""


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="tp-wheel-smoke-"))
    # Ensure the isolated tree is outside the project root.
    assert not work.is_relative_to(PROJECT_ROOT), work
    try:
        dist_dir = work / "dist"
        venv_dir = work / "venv"
        dist_dir.mkdir()

        # Prefer uv when available (project standard); fall back to pip+build.
        if shutil.which("uv"):
            _run(
                ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
                cwd=PROJECT_ROOT,
            )
        else:
            _run(
                [sys.executable, "-m", "pip", "wheel", ".", "-w", str(dist_dir), "--no-deps"],
                cwd=PROJECT_ROOT,
            )

        wheels = sorted(dist_dir.glob("trading_partner-*.whl"))
        if not wheels:
            raise SystemExit(f"no wheel produced under {dist_dir}")
        wheel = wheels[-1]
        print(f"wheel={wheel}", flush=True)

        venv.create(venv_dir, with_pip=True, clear=True)
        if sys.platform == "win32":
            python = venv_dir / "Scripts" / "python.exe"
        else:
            python = venv_dir / "bin" / "python"

        # Install only the wheel — no editable, no PYTHONPATH into the repo.
        _run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
        _run([str(python), "-m", "pip", "install", str(wheel)])

        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        # Clear every project key documented by .env.example so ambient shell
        # configuration cannot steer the isolated probe now that keys are unprefixed.
        settings_keys = {
            line.partition("=")[0].strip()
            for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }
        for key in settings_keys:
            env.pop(key, None)

        # Required process env for installed AppSettings.load() (console/MCP path).
        db_path = work / "smoke.db"
        env.update(
            {
                "APP_NAME": "trading-partner-wheel-smoke",
                "APP_ENV": "test",
                "LOG_LEVEL": "INFO",
                "DATABASE_URL": f"sqlite:///{db_path}",
                "MCP_SERVER_NAME": "trading-partner-wheel-smoke",
                "DEFAULT_TIMEZONE": "UTC",
                "PROVIDER_TIMEOUT_SECONDS": "5.0",
            }
        )

        # Run probe with cwd outside the repo so relative config cannot leak.
        outside_cwd = work / "cwd"
        outside_cwd.mkdir()
        # Plant a decoy cwd .env that must be ignored under installed layout.
        (outside_cwd / ".env").write_text(
            "APP_NAME=from-cwd-must-not-win\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(python), "-c", PROBE_SCRIPT],
            cwd=str(outside_cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode
        if "ISOLATED_WHEEL_SMOKE_OK" not in result.stdout:
            print("smoke probe did not print success marker", file=sys.stderr)
            return 1
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
