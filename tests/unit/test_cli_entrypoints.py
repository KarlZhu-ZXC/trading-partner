from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from domain.common.errors import ConfigurationError
from interfaces.cli import _lifecycle as lifecycle
from interfaces.cli import (
    agent,
    catalyst_sync,
    industry_sync,
    maintenance,
    monitor_run,
    performance_reconciliation,
)
from interfaces.console import server as console_server


class _Dump:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self._payload


class _Maintenance:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def status(self) -> _Dump:
        self.calls.append(("status", None))
        return _Dump({"kind": "status"})

    def backup(self) -> _Dump:
        self.calls.append(("backup", None))
        return _Dump({"kind": "backup"})

    def prune_expired_cache(self, *, retention_days: int, dry_run: bool) -> _Dump:
        self.calls.append(("prune", (retention_days, dry_run)))
        return _Dump({"kind": "prune"})


class _Container:
    def __init__(self, operations: Any) -> None:
        self.operations = operations
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


def test_console_server_binds_only_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(console_server.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))

    console_server.main(["--host", "localhost", "--port", "9001"])

    assert calls == [
        {
            "host": "localhost",
            "port": 9001,
            "access_log": False,
            "log_level": "warning",
        }
    ]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["status"], ("status", None)),
        (["backup"], ("backup", None)),
        (["prune-cache", "--retention-days", "45"], ("prune", (45, True))),
        (["prune-cache", "--retention-days", "45", "--apply"], ("prune", (45, False))),
    ],
)
def test_maintenance_cli_routes_and_closes(
    argv: list[str],
    expected: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _Maintenance()
    container = _Container(SimpleNamespace(maintenance=service))
    monkeypatch.setattr(maintenance, "build_default_application", lambda: container)

    maintenance.main(argv)

    assert service.calls == [expected]
    assert container.closed
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.parametrize(
    ("argv", "expected_call", "exit_code"),
    [
        (["trading-partner-monitor-run", "due"], "due", 0),
        (
            ["trading-partner-monitor-run", "run", "--cadence", "US_POST_MARKET"],
            "US_POST_MARKET",
            1,
        ),
    ],
)
def test_monitor_run_main_dispatches(
    argv: list[str],
    expected_call: str,
    exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_due() -> int:
        calls.append("due")
        return 0

    async def fake_run(cadence: Any) -> int:
        calls.append(cadence.value)
        return 1

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(monitor_run, "_run_due", fake_due)
    monkeypatch.setattr(monitor_run, "_run", fake_run)

    with pytest.raises(SystemExit) as caught:
        monitor_run.main()

    assert caught.value.code == exit_code
    assert calls == [expected_call]


def test_industry_sync_main_passes_bounded_months(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def fake_run(months: int) -> int:
        calls.append(months)
        return 0

    monkeypatch.setattr(sys, "argv", ["trading-partner-industry-sync", "--months", "36"])
    monkeypatch.setattr(industry_sync, "_run", fake_run)

    with pytest.raises(SystemExit) as caught:
        industry_sync.main()

    assert caught.value.code == 0
    assert calls == [36]
    assert industry_sync._month_count(
        industry_sync.date(2025, 12, 1), industry_sync.date(2026, 2, 1)
    ) == 3


def test_performance_reconciliation_inspect_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = SimpleNamespace(
        inspect_schwab_realized_gain_loss=lambda path: _Dump({"path": path})
    )
    container = _Container(
        SimpleNamespace(performance_reconciliation=service)
    )
    container.context = SimpleNamespace(secret_redactor=SimpleNamespace(redact=lambda value: value))
    monkeypatch.setattr(
        performance_reconciliation,
        "build_default_application",
        lambda: container,
    )

    result = performance_reconciliation.run(
        ["inspect-schwab-realized", "--realized-csv", "statement.csv"]
    )

    assert result == 0
    assert container.closed
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "data": {"path": "statement.csv"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("receipt", "expected"), [(None, 2), (_Dump({"id": "r1"}), 0)])
async def test_catalyst_sync_status_is_durable_and_closes(
    receipt: _Dump | None,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations = SimpleNamespace(catalyst_agenda_sync=SimpleNamespace(latest=lambda: receipt))
    container = _Container(operations)
    monkeypatch.setattr(lifecycle, "build_default_application", lambda: container)

    result = await catalyst_sync._run(SimpleNamespace(command="status"))

    assert result == expected
    assert container.closed
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is (receipt is not None)


@pytest.mark.asyncio
async def test_catalyst_sync_routes_sync_notifications_and_flush(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt = SimpleNamespace(
        status="COMPLETE",
        limitation_codes=("PARTIAL",),
        model_dump=lambda **_kwargs: {"status": "COMPLETE"},
    )
    captured: list[Any] = []

    async def sync(request: Any) -> Any:
        captured.append(request)
        return receipt

    async def enqueue_batch(**kwargs: Any) -> _Dump:
        captured.append(kwargs)
        return _Dump({"queued": 1})

    async def flush_pending() -> _Dump:
        return _Dump({"sent": 1})

    operations = SimpleNamespace(
        catalyst_agenda_sync=SimpleNamespace(sync=sync),
        catalyst_agenda_notifications=SimpleNamespace(enqueue_batch=enqueue_batch),
        notifications=SimpleNamespace(flush_pending=flush_pending),
    )
    container = _Container(operations)
    monkeypatch.setattr(lifecycle, "build_default_application", lambda: container)
    args = SimpleNamespace(
        command="sync",
        instrument_ids=["equity:US:NVDA"],
        fred_release_ids=[10],
        window_days=45,
        as_of=None,
        idempotency_key="sync-1",
        notify=True,
        flush=True,
    )

    assert await catalyst_sync._run(args) == 0
    assert captured[0].instrument_ids == ("equity:US:NVDA",)
    assert captured[1] == {"window_days": 30, "additional_limitations": ("PARTIAL",)}
    assert json.loads(capsys.readouterr().out)["delivery"] == {"sent": 1}
    assert container.closed


def test_catalyst_sync_main_parses_full_sync_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    async def run(args: Any) -> int:
        captured.append(args)
        return 0

    monkeypatch.setattr(catalyst_sync, "_run", run)

    with pytest.raises(SystemExit) as caught:
        catalyst_sync.main(
            [
                "sync",
                "--instrument-id",
                "equity:US:NVDA",
                "--fred-release-id",
                "10",
                "--window-days",
                "14",
                "--as-of",
                "2026-08-11T08:00:00+00:00",
                "--idempotency-key",
                "sync-main-1",
                "--notify",
                "--flush",
            ]
        )

    assert caught.value.code == 0
    assert captured[0].instrument_ids == ["equity:US:NVDA"]
    assert captured[0].fred_release_ids == [10]
    assert captured[0].window_days == 14


@pytest.mark.asyncio
async def test_catalyst_sync_returns_typed_error_without_notification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(_request: Any) -> None:
        raise ConfigurationError("missing provider")

    container = _Container(SimpleNamespace(catalyst_agenda_sync=SimpleNamespace(sync=fail)))
    monkeypatch.setattr(lifecycle, "build_default_application", lambda: container)
    args = SimpleNamespace(
        command="sync",
        instrument_ids=None,
        fred_release_ids=None,
        window_days=30,
        as_of=None,
        idempotency_key=None,
        notify=False,
        flush=False,
    )

    assert await catalyst_sync._run(args) == 1
    assert json.loads(capsys.readouterr().out)["error_codes"] == ["CONFIGURATION_ERROR"]
    assert container.closed


def test_console_supervisor_install_status_and_uninstall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_plist_path = tmp_path / "LaunchAgents" / "console-api.plist"
    web_plist_path = tmp_path / "LaunchAgents" / "console-web.plist"
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(agent, "CONSOLE_API_PLIST_PATH", api_plist_path)
    monkeypatch.setattr(agent, "CONSOLE_WEB_PLIST_PATH", web_plist_path)
    monkeypatch.setattr(agent, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(agent, "_ensure_console_build", lambda _root: None)
    monkeypatch.setattr(agent.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr(agent, "_run_launchctl", launchctl)

    assert agent.console_install() == 0
    assert api_plist_path.exists()
    assert web_plist_path.exists()
    assert agent.console_status() == 0
    assert agent.console_uninstall() == 0
    assert not api_plist_path.exists()
    assert not web_plist_path.exists()
    assert any(call[0] == "bootstrap" for call in calls)
    output = capsys.readouterr().out
    assert '"telegram"' not in output


def test_agent_main_dispatches_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "console_status", lambda: 3)

    with pytest.raises(SystemExit) as caught:
        agent.main(["console", "status"])

    assert caught.value.code == 3


@pytest.mark.parametrize("channel", ("telegram", "all"))
def test_retired_agent_channels_are_not_cli_aliases(channel: str) -> None:
    with pytest.raises(SystemExit) as caught:
        agent.main([channel, "status"])
    assert caught.value.code == 2


def test_console_install_requires_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "_project_root", lambda: Path("/tmp/trading-partner-test"))
    monkeypatch.setattr(
        agent.shutil,
        "which",
        lambda name: None if name == "uv" else "/usr/bin/tool",
    )

    with pytest.raises(SystemExit, match="uv, npm, and node are required"):
        agent.console_install()


def test_agent_project_root_falls_back_outside_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert agent._project_root().name == "trading-partner"
