"""Stdio integration for instrument resolution behavior (Phase 1D)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from interfaces.mcp.server import PHASE1D_TOOL_NAMES, PUBLIC_TOOL_NAMES


def _alembic_config(project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    return cfg


def _migrate(database_url: str, project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Alembic env.py loads URL via AppSettings — set DATABASE_URL."""
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "trading-partner-instrument-stdio-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "trading-partner-instrument-stdio-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")
    command.upgrade(_alembic_config(project_root), "head")


def _stdio_env(database_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "APP_NAME": "trading-partner-instrument-stdio-test",
        "APP_ENV": "test",
        "LOG_LEVEL": "INFO",
        "DATABASE_URL": f"sqlite:///{database_path}",
        "MCP_SERVER_NAME": "trading-partner-instrument-stdio-test",
        "DEFAULT_TIMEZONE": "UTC",
        "PROVIDER_TIMEOUT_SECONDS": "5",
    }


def _parse_envelope(result: Any) -> dict[str, Any]:
    assert result.isError is False
    assert result.content, "tool result missing content"
    payload = json.loads(result.content[0].text)
    assert isinstance(payload, dict)
    assert "ok" in payload
    return payload


@pytest.mark.asyncio
async def test_stdio_instrument_resolve_behavioral_samples(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "instrument_stdio.db"
    database_url = f"sqlite:///{database_path}"
    _migrate(database_url, project_root, monkeypatch)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "interfaces.mcp.server"],
        cwd=str(project_root),
        env=_stdio_env(database_path),
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert "instrument_resolve" in names
        assert names == set(PUBLIC_TOOL_NAMES)
        assert names.issubset(set(PUBLIC_TOOL_NAMES))
        assert set(PUBLIC_TOOL_NAMES) >= PHASE1D_TOOL_NAMES

        a_share = _parse_envelope(
            await session.call_tool(
                "instrument_resolve",
                {
                    "market": "A_SHARE",
                    "query": "600519.SH",
                },
            )
        )
        assert a_share["ok"] is True
        assert a_share["data"]["instrument"]["instrument_id"] == "equity:A_SHARE:600519.SH"
        assert a_share["data"]["match_type"] == "exact_symbol"

        us_equity = _parse_envelope(
            await session.call_tool(
                "instrument_resolve",
                {
                    "market": "US",
                    "query": "NVDA",
                },
            )
        )
        assert us_equity["ok"] is True
        assert us_equity["data"]["instrument"]["instrument_id"] == "equity:US:NVDA"

        normalized = _parse_envelope(
            await session.call_tool(
                "instrument_resolve",
                {
                    "market": "A_SHARE",
                    "query": "600519",
                },
            )
        )
        assert normalized["ok"] is True
        assert normalized["data"]["instrument"]["instrument_id"] == "equity:A_SHARE:600519.SH"
