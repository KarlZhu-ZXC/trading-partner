from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from interfaces.cli import initialize as initialize_cli


def test_initialize_creates_owner_only_config_and_upgrades_idempotently(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime"

    first = initialize_cli.initialize(runtime_home)
    second = initialize_cli.initialize(runtime_home)

    env_file = runtime_home / "runtime.env"
    database = runtime_home / "trading_partner.db"
    assert first["created"] is True
    assert second["created"] is False
    assert env_file.is_file()
    assert database.is_file()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "API_KEY" not in env_file.read_text(encoding="utf-8")
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()
    assert revision == "0036_monitor_provider_diagnostics"
    assert first["mcp_args"] == ["--env-file", str(env_file)]


def test_main_json_receipt_contains_no_config_body(
    tmp_path: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    initialize_cli.main(["--runtime-home", str(tmp_path / "runtime"), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["next_action"] == "ADD_MCP_HOST_CONFIG_AND_CALL_SYSTEM_HEALTH"
    assert "APP_NAME" not in payload
    assert "authorization" not in payload


def test_mcp_executable_prefers_sibling_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    init_command = bin_dir / "trading-partner-init"
    mcp_command = bin_dir / "trading-partner-mcp"
    init_command.touch()
    mcp_command.touch()
    monkeypatch.setattr(initialize_cli.sys, "argv", [str(init_command)])

    assert initialize_cli._mcp_executable() == str(mcp_command)
