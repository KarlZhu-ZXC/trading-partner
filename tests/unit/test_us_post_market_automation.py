from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(automation.sys, "executable", "/runtime/.venv/bin/python")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        command = " ".join(args)
        calls.append(command)
        return _completed(
            tuple(args),
            stdout=_sync_payload() if "post_market_sync" in command else "{}",
        )

    state = tmp_path / "state" / "session.txt"
    code = automation.run(
        now=NOW,
        command_runner=runner,
        state_file=state,
        due_context_provider=lambda _now: ("2026-08-28", CLOSE),
    )

    assert code == 0
    assert calls[0] == (
        "/runtime/.venv/bin/python -m interfaces.cli.post_market_sync catch-up"
    )
    assert calls[1] == (
        "/runtime/.venv/bin/python -m interfaces.cli.monitor_run --cadence US_POST_MARKET"
    )
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
    monkeypatch.setattr(automation.sys, "executable", "/runtime/.venv/bin/python")
    monkeypatch.setenv("PATH", "")
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
    assert len(calls) == 1 and "interfaces.cli.post_market_sync catch-up" in calls[0]
    assert json.loads(capsys.readouterr().out)["disposition"] == (
        "SKIPPED_MONITOR_ALREADY_COMPLETED"
    )


def test_degraded_sync_does_not_block_monitor_but_returns_failure_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(automation.sys, "executable", "/runtime/.venv/bin/python")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        command = " ".join(args)
        calls.append(command)
        if "post_market_sync" in command:
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


def test_default_state_file_is_scoped_to_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_runtime = tmp_path / "runtime-a"
    second_runtime = tmp_path / "runtime-b"

    monkeypatch.setenv("RUNTIME_ROOT", str(first_runtime))
    first_state = automation.default_state_file()
    monkeypatch.setenv("RUNTIME_ROOT", str(second_runtime))
    second_state = automation.default_state_file()

    assert first_state == first_runtime / "data" / "state" / automation.STATE_FILENAME
    assert second_state == second_runtime / "data" / "state" / automation.STATE_FILENAME
    assert first_state != second_state


def test_runtime_root_uses_app_settings_loader_for_checkout_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    configured_runtime = tmp_path / "owner-runtime"
    loader_calls: list[int] = []

    monkeypatch.delenv("RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(automation, "_project_root", lambda: checkout)

    def load_from_checkout():  # type: ignore[no-untyped-def]
        loader_calls.append(1)
        return SimpleNamespace(runtime_root=configured_runtime)

    monkeypatch.setattr(automation, "load_settings", load_from_checkout)

    assert automation._runtime_root() == configured_runtime.resolve()
    assert loader_calls == [1]


def test_runtime_root_process_env_wins_without_loading_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_runtime = tmp_path / "environment-runtime"
    monkeypatch.setenv("RUNTIME_ROOT", str(configured_runtime))

    def fail_loader():  # type: ignore[no-untyped-def]
        raise AssertionError("process RUNTIME_ROOT should be resolved first")

    monkeypatch.setattr(automation, "load_settings", fail_loader)

    assert automation._runtime_root() == configured_runtime.resolve()


def test_installed_runtime_without_explicit_root_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    monkeypatch.delenv("RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(automation, "_project_root", lambda: site_packages)
    monkeypatch.setattr(
        automation,
        "load_settings",
        lambda: SimpleNamespace(runtime_root=site_packages),
    )

    assert automation._runtime_root() is None
    with pytest.raises(RuntimeError, match="RUNTIME_ROOT"):
        automation.default_state_file()


def test_default_state_marker_does_not_cross_runtime_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(automation.sys, "executable", "/runtime/.venv/bin/python")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        calls.append(" ".join(args))
        return _completed(tuple(args), stdout=_sync_payload())

    def due(_now):  # type: ignore[no-untyped-def]
        return ("2026-08-28", CLOSE)

    first_runtime = tmp_path / "runtime-a"
    second_runtime = tmp_path / "runtime-b"

    monkeypatch.setenv("RUNTIME_ROOT", str(first_runtime))
    assert (
        automation.run(
            now=NOW,
            command_runner=runner,
            due_context_provider=due,
        )
        == 0
    )
    monkeypatch.setenv("RUNTIME_ROOT", str(second_runtime))
    assert (
        automation.run(
            now=NOW,
            command_runner=runner,
            due_context_provider=due,
        )
        == 0
    )

    first_state = automation.default_state_file(first_runtime)
    second_state = automation.default_state_file(second_runtime)
    assert first_state.read_text(encoding="utf-8") == "2026-08-28\n"
    assert second_state.read_text(encoding="utf-8") == "2026-08-28\n"
    assert len(calls) == 4


def test_attributable_legacy_marker_is_migrated_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(automation.sys, "executable", "/runtime/.venv/bin/python")
    monkeypatch.setattr(automation, "_project_root", lambda: tmp_path)
    runtime_root = tmp_path / "Library" / "Application Support" / "trading-partner"
    legacy_marker = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Trading Partner"
        / "state"
        / automation.STATE_FILENAME
    )
    legacy_marker.parent.mkdir(parents=True)
    legacy_marker.write_text("2026-08-28\n", encoding="utf-8")
    legacy_marker.chmod(0o600)
    monkeypatch.setenv("RUNTIME_ROOT", str(runtime_root))

    def runner(args, _cwd):  # type: ignore[no-untyped-def]
        calls.append(" ".join(args))
        return _completed(tuple(args), stdout=_sync_payload())

    assert (
        automation.run(
            now=NOW,
            command_runner=runner,
            due_context_provider=lambda _now: ("2026-08-28", CLOSE),
        )
        == 0
    )

    new_marker = automation.default_state_file(runtime_root)
    assert len(calls) == 1
    assert new_marker.read_text(encoding="utf-8") == "2026-08-28\n"
    assert legacy_marker.read_text(encoding="utf-8") == "2026-08-28\n"
