from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from interfaces.cli import agent


def test_console_launchd_plists_are_loopback_and_keepalive(tmp_path: Path) -> None:
    api = agent._console_api_payload(tmp_path, Path("/opt/homebrew/bin/uv"))
    web = agent._console_web_payload(tmp_path, Path("/opt/homebrew/bin/node"))
    assert api["Label"] == agent.CONSOLE_API_LABEL
    assert api["KeepAlive"] is True
    assert "--host" in api["ProgramArguments"]
    assert "127.0.0.1" in api["ProgramArguments"]
    assert web["Label"] == agent.CONSOLE_WEB_LABEL
    assert web["KeepAlive"] is True
    assert web["ProgramArguments"][0] == "/opt/homebrew/bin/node"
    assert web["ProgramArguments"][1].endswith("node_modules/next/dist/bin/next")
    assert "start" in web["ProgramArguments"]
    assert "127.0.0.1" in web["ProgramArguments"]
    assert "8765" in api["ProgramArguments"]


def test_console_lan_launchd_uses_owner_only_password_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_PARTNER_CONSOLE_LAN_PASSWORD", "test-lan-password-123456")
    password_file = agent._ensure_console_lan_password(tmp_path)
    web = agent._console_web_payload(
        tmp_path,
        Path("/opt/homebrew/bin/node"),
        lan_password_file=password_file,
        lan_port=3000,
    )

    assert password_file.read_text(encoding="utf-8").strip() == "test-lan-password-123456"
    assert password_file.stat().st_mode & 0o777 == 0o600
    assert web["ProgramArguments"][1].endswith("console/scripts/start-lan.mjs")
    assert web["ProgramArguments"][-1] == "start"
    assert web["EnvironmentVariables"] == {
        "TRADING_PARTNER_CONSOLE_LAN_PASSWORD_FILE": str(password_file),
        "TRADING_PARTNER_CONSOLE_LAN_PORT": "3000",
    }
    assert "test-lan-password-123456" not in str(web)


def test_console_lan_password_file_preserves_existing_password(
    tmp_path: Path,
) -> None:
    path = tmp_path / agent.CONSOLE_LAN_PASSWORD_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("existing-password-123\n", encoding="utf-8")

    result = agent._ensure_console_lan_password(tmp_path)

    assert result == path
    assert path.read_text(encoding="utf-8").strip() == "existing-password-123"
    assert path.stat().st_mode & 0o777 == 0o600


def test_console_lan_password_file_rotates_legacy_short_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / agent.CONSOLE_LAN_PASSWORD_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("short-password\n", encoding="utf-8")
    monkeypatch.delenv("TRADING_PARTNER_CONSOLE_LAN_PASSWORD", raising=False)

    agent._ensure_console_lan_password(tmp_path)

    password = path.read_text(encoding="utf-8").strip()
    assert password != "short-password"
    assert len(password) >= agent.CONSOLE_LAN_PASSWORD_MIN_LENGTH
    assert path.stat().st_mode & 0o777 == 0o600


def test_supervisor_status_parses_safe_launchctl_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = tmp_path / "job.plist"
    plist.write_text("plist", encoding="utf-8")

    def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            "pid = 4242\nstart time = Thu Aug 13 12:00:00\nlast exit code = 143\n",
            "",
        )

    monkeypatch.setattr(agent, "_run_launchctl", launchctl)
    value = agent._job_status("com.example.safe", plist)
    assert value["running"] is True
    assert value["pid"] == 4242
    assert value["last_exit"] == 143
    assert value["last_error"] is None
    assert value["start_time"] == "Thu Aug 13 12:00:00"
    assert "ProgramArguments" not in value


def test_supervisor_status_reports_nonzero_exit_only_when_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plist = tmp_path / "job.plist"
    plist.write_text("plist", encoding="utf-8")

    def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "last exit code = 9\n", "")

    monkeypatch.setattr(agent, "_run_launchctl", launchctl)
    value = agent._job_status("com.example.stopped", plist)
    assert value["running"] is False
    assert value["last_exit"] == 9
    assert value["last_error"] == "PROCESS_EXIT_9"


def test_console_restart_reports_launchctl_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "failed")

    monkeypatch.setattr(agent, "_run_launchctl", launchctl)
    with pytest.raises(SystemExit, match="restart failed"):
        agent.console_restart()


def test_install_job_boots_out_exact_launchd_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(agent, "_run_launchctl", launchctl)
    monkeypatch.setattr(agent, "_domain", lambda: "gui/501")
    agent._install_job(
        "com.example.console",
        tmp_path / "console.plist",
        {"Label": "com.example.console", "ProgramArguments": ["/bin/true"]},
    )

    assert calls[0] == ("bootout", "gui/501", str(tmp_path / "console.plist"))
    assert calls[1][:2] == ("bootstrap", "gui/501")
