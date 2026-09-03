"""Run the durable post-market sync before the once-per-session US Monitor digest."""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import exchange_calendars

STATE_FILE = (
    Path.home()
    / "Library/Application Support/Trading Partner/state/us_post_market_monitor_session.txt"
)
LOCK_FILE = STATE_FILE.with_suffix(".lock")
DELAY = timedelta(minutes=10)
GRACE = timedelta(hours=18)

CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
DueContextProvider = Callable[[datetime], tuple[str, datetime] | None]


def _project_root() -> Path:
    candidate = Path.cwd().resolve()
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path(__file__).resolve().parents[3]


def _emit(**payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _command(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        tuple(args),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _json_payload(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _due_context(now: datetime) -> tuple[str, datetime] | None:
    calendar = exchange_calendars.get_calendar("XNYS")
    sessions = calendar.sessions_in_range((now - timedelta(days=7)).date(), now.date())
    if len(sessions) == 0:
        return None
    session = sessions[-1]
    return (
        session.date().isoformat(),
        calendar.session_close(session).to_pydatetime().astimezone(UTC),
    )


def _post_market_summary(payload: dict[str, Any], exit_code: int) -> dict[str, Any]:
    return {
        "exit_code": exit_code,
        "ok": payload.get("ok", exit_code == 0),
        "disposition": payload.get("disposition"),
        "run_status": payload.get("run_status"),
        "observation_status": payload.get("observation_status"),
        "observation_notes_seen": payload.get("observation_notes_seen"),
        "observation_revisions_created": payload.get("observation_revisions_created"),
        "observation_full_count": payload.get("observation_full_count"),
        "observation_summary_only_count": payload.get(
            "observation_summary_only_count"
        ),
        "warning_codes": payload.get("warning_codes", []),
        "error_codes": payload.get("error_codes", []),
    }


def run(
    *,
    now: datetime | None = None,
    command_runner: CommandRunner = _command,
    state_file: Path = STATE_FILE,
    due_context_provider: DueContextProvider = _due_context,
) -> int:
    checked_at = now or datetime.now(UTC)
    context = due_context_provider(checked_at)
    if context is None:
        _emit(
            ok=True,
            disposition="SKIPPED_NO_RECENT_XNYS_SESSION",
            checked_at=checked_at.isoformat(),
        )
        return 0
    session_date, close_at = context
    due_at = close_at + DELAY
    if checked_at < due_at:
        _emit(
            ok=True,
            disposition="SKIPPED_NOT_DUE",
            market_session_date=session_date,
            scheduled_for=due_at.isoformat(),
            checked_at=checked_at.isoformat(),
        )
        return 0
    if checked_at > due_at + GRACE:
        _emit(
            ok=True,
            disposition="SKIPPED_OUTSIDE_GRACE_WINDOW",
            market_session_date=session_date,
            scheduled_for=due_at.isoformat(),
            checked_at=checked_at.isoformat(),
        )
        return 0

    root = _project_root()
    python = sys.executable
    if not python:
        _emit(ok=False, disposition="PYTHON_RUNTIME_UNAVAILABLE")
        return 2
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_file = state_file.with_suffix(".lock")
    with lock_file.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _emit(ok=True, disposition="SKIPPED_ALREADY_RUNNING")
            return 0
        sync = command_runner(
            (
                python,
                "-m",
                "interfaces.cli.post_market_sync",
                "catch-up",
            ),
            root,
        )
        sync_payload = _json_payload(sync.stdout)
        sync_summary = _post_market_summary(sync_payload, sync.returncode)

        if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == session_date:
            _emit(
                ok=sync.returncode == 0,
                disposition="SKIPPED_MONITOR_ALREADY_COMPLETED",
                market_session_date=session_date,
                checked_at=checked_at.isoformat(),
                post_market_sync=sync_summary,
            )
            return sync.returncode

        monitor = command_runner(
            (
                python,
                "-m",
                "interfaces.cli.monitor_run",
                "--cadence",
                "US_POST_MARKET",
            ),
            root,
        )
        if monitor.returncode != 0:
            _emit(
                ok=False,
                disposition="MONITOR_RUN_FAILED",
                market_session_date=session_date,
                monitor_exit_code=monitor.returncode,
                post_market_sync=sync_summary,
            )
            return monitor.returncode

        state_file.write_text(f"{session_date}\n", encoding="utf-8")
        state_file.chmod(0o600)
        _emit(
            ok=sync.returncode == 0,
            disposition=(
                "EXECUTED" if sync.returncode == 0 else "EXECUTED_MONITOR_SYNC_DEGRADED"
            ),
            market_session_date=session_date,
            scheduled_for=due_at.isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            post_market_sync=sync_summary,
        )
        return sync.returncode


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="trading-partner-us-post-market-automation",
        description="Run post-market sync before the once-per-session US Monitor digest.",
    )
    parser.parse_args(argv)
    raise SystemExit(run())


if __name__ == "__main__":
    main()
