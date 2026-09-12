"""Run the durable post-market sync before the once-per-session US Monitor digest."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import exchange_calendars

from bootstrap import load_settings

STATE_FILENAME = "us_post_market_monitor_session.txt"
DELAY = timedelta(minutes=10)
GRACE = timedelta(hours=18)

CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
DueContextProvider = Callable[[datetime], tuple[str, datetime] | None]


def _project_root() -> Path:
    candidate = Path.cwd().resolve()
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path(__file__).resolve().parents[3]


def _runtime_root() -> Path | None:
    """Resolve the runtime root using the same loader as child commands.

    A process-level ``RUNTIME_ROOT`` wins in both the outer task and its child
    processes. When it is absent, ``load_settings`` reads the source checkout's
    ``.env`` just as ``build_default_application`` does. Installed wheels must
    provide an explicit runtime root; their package directory is code, not a
    writable owner-state location.
    """

    configured = os.environ.get("RUNTIME_ROOT")
    if configured is not None and configured.strip():
        return Path(configured).expanduser().resolve()

    root = _project_root()
    source_layout = (root / "pyproject.toml").is_file()
    try:
        settings = load_settings()
    except Exception:
        # With no source .env, source checkouts retain AppSettings' project-root
        # default for lightweight diagnostics. An installed layout has no safe
        # implicit state root and must fail closed.
        if source_layout and not (root / ".env").is_file():
            return root
        raise
    if not source_layout:
        return None
    return settings.runtime_root


def default_state_file(runtime_root: Path | None = None) -> Path:
    """Return the per-runtime post-market session marker path."""

    root = runtime_root or _runtime_root()
    if root is None:
        raise RuntimeError("RUNTIME_ROOT is required for an installed runtime")
    root = root.expanduser().resolve()
    return root / "data" / "state" / STATE_FILENAME


# Keep the import-time constants for callers that inspect the installed CLI,
# while ``run`` resolves the default at call time so separate runtimes in the
# same Python process never share a session marker.
def _import_time_state_file() -> Path | None:
    """Expose the legacy constants without choosing an installed code path."""

    configured = os.environ.get("RUNTIME_ROOT")
    if configured is not None and configured.strip():
        return default_state_file(Path(configured))
    root = _project_root()
    return default_state_file(root) if (root / "pyproject.toml").is_file() else None


STATE_FILE = _import_time_state_file()
LOCK_FILE = STATE_FILE.with_suffix(".lock") if STATE_FILE is not None else None


def _legacy_state_file(runtime_root: Path) -> Path | None:
    """Return the old marker only for the attributable default macOS runtime.

    The former path was ``~/Library/Application Support/Trading Partner``.
    Deriving that sibling from a configured ``.../trading-partner`` root keeps
    custom runtimes isolated and avoids probing or copying a user's home path
    for every new runtime.
    """

    root = runtime_root.expanduser().resolve()
    support_dir = root.parent
    if not (
        root.name == "trading-partner"
        and support_dir.name == "Application Support"
        and support_dir.parent.name == "Library"
    ):
        return None
    candidate = support_dir / "Trading Partner" / "state" / STATE_FILENAME
    try:
        stat_result = candidate.stat()
    except OSError:
        return None
    if candidate.is_symlink() or not candidate.is_file() or stat_result.st_mode & 0o077:
        return None
    return candidate


def _marker_matches(path: Path, session_date: str) -> bool:
    try:
        return path.read_text(encoding="utf-8").strip() == session_date
    except (OSError, UnicodeError):
        return False


def _legacy_marker_matches(
    *,
    runtime_root: Path | None,
    state_file_was_explicit: bool,
    state_file: Path,
    session_date: str,
) -> bool:
    if runtime_root is None or state_file_was_explicit or _marker_matches(state_file, session_date):
        return False
    legacy = _legacy_state_file(runtime_root)
    if legacy is None or not _marker_matches(legacy, session_date):
        return False
    # Move only the matching marker into its attributable runtime. Keep the old
    # file intact for rollback/readability; a failed local write still retains
    # the idempotency decision for this invocation.
    try:
        state_file.write_text(f"{session_date}\n", encoding="utf-8")
        state_file.chmod(0o600)
    except OSError:
        pass
    return True


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
    state_file: Path | None = None,
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
    state_file_was_explicit = state_file is not None
    try:
        runtime_root = None if state_file_was_explicit else _runtime_root()
    except Exception:
        # ``load_settings`` already keeps the underlying validation safe, but
        # the outer scheduler must also avoid printing a configuration error
        # that could contain a path or another ambient value.
        _emit(ok=False, disposition="RUNTIME_ROOT_UNAVAILABLE")
        return 2
    if runtime_root is None and not state_file_was_explicit:
        _emit(ok=False, disposition="RUNTIME_ROOT_UNAVAILABLE")
        return 2
    effective_state_file = state_file or default_state_file(runtime_root)
    effective_state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_file = effective_state_file.with_suffix(".lock")
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

        marker_matches = _marker_matches(effective_state_file, session_date)
        marker_matches = marker_matches or _legacy_marker_matches(
            runtime_root=runtime_root,
            state_file_was_explicit=state_file_was_explicit,
            state_file=effective_state_file,
            session_date=session_date,
        )
        if marker_matches:
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

        effective_state_file.write_text(f"{session_date}\n", encoding="utf-8")
        effective_state_file.chmod(0o600)
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
