from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from interfaces.cli import us_post_market_automation as automation

NOW = datetime(2026, 8, 28, 20, 20, tzinfo=UTC)
CLOSE = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


def _completed(
    args: tuple[str, ...], *, code: int = 0, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")


def _sync_payload(*, ok: bool = True) -> str:
    return json.dumps(
        {
            "ok": ok,
            "disposition": "EXECUTED",
            "run_status": "SUCCEEDED" if ok else "PARTIAL",
            "observation_status": "SUCCEEDED" if ok else "FAILED",
            "observation_notes_seen": 16,
            "observation_revisions_created": 2,
            "observation_full_count": 16,
            "observation_summary_only_count": 0,
            "warning_codes": [],
            "error_codes": [] if ok else ["OBSERVATION_SOURCES_UNAVAILABLE"],
        }
    )


def test_runs_sync_before_monitor_and_writes_session_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(automation.shutil, "which", lambda _name: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        command = " ".join(args)
        calls.append(command)
        return _completed(
            tuple(args),
            stdout=_sync_payload() if "post-market-sync" in command else "{}",
        )

    state = tmp_path / "state" / "session.txt"
    code = automation.run(
        now=NOW,
        command_runner=runner,
        state_file=state,
        due_context_provider=lambda _now: ("2026-08-28", CLOSE),
    )

    assert code == 0
    assert "post-market-sync catch-up" in calls[0]
    assert "monitor-run --cadence US_POST_MARKET" in calls[1]
    assert state.read_text(encoding="utf-8") == "2026-08-28\n"
    assert state.stat().st_mode & 0o777 == 0o600
    payload = json.loads(capsys.readouterr().out)
    assert payload["post_market_sync"]["observation_notes_seen"] == 16


def test_completed_monitor_still_retries_post_market_sync_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(automation.shutil, "which", lambda _name: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)
    state = tmp_path / "session.txt"
    state.write_text("2026-08-28\n", encoding="utf-8")

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        calls.append(" ".join(args))
        return _completed(tuple(args), stdout=_sync_payload())

    code = automation.run(
        now=NOW,
        command_runner=runner,
        state_file=state,
        due_context_provider=lambda _now: ("2026-08-28", CLOSE),
    )

    assert code == 0
    assert len(calls) == 1 and "post-market-sync catch-up" in calls[0]
    assert json.loads(capsys.readouterr().out)["disposition"] == (
        "SKIPPED_MONITOR_ALREADY_COMPLETED"
    )


def test_degraded_sync_does_not_block_monitor_but_returns_failure_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(automation.shutil, "which", lambda _name: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        command = " ".join(args)
        calls.append(command)
        if "post-market-sync" in command:
            return _completed(tuple(args), code=1, stdout=_sync_payload(ok=False))
        return _completed(tuple(args))

    state = tmp_path / "session.txt"
    code = automation.run(
        now=NOW,
        command_runner=runner,
        state_file=state,
        due_context_provider=lambda _now: ("2026-08-28", CLOSE),
    )

    assert code == 1
    assert len(calls) == 2
    assert state.read_text(encoding="utf-8") == "2026-08-28\n"
    payload = json.loads(capsys.readouterr().out)
    assert payload["disposition"] == "EXECUTED_MONITOR_SYNC_DEGRADED"
    assert payload["post_market_sync"]["observation_status"] == "FAILED"


def test_help_has_no_operational_side_effect(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        automation.main(["--help"])

    assert raised.value.code == 0
    assert "post-market sync" in capsys.readouterr().out
