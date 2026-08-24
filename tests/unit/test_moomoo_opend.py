from __future__ import annotations

import subprocess
from pathlib import Path

from infrastructure.providers import moomoo_opend


def _result(returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(("launchctl",), returncode, "", "")


def test_ready_port_does_not_call_launchctl(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(moomoo_opend, "_port_is_open", lambda _host, _port: True)
    monkeypatch.setattr(
        moomoo_opend,
        "_run_launchctl",
        lambda *args: calls.append(args) or _result(),
    )

    moomoo_opend.ensure_moomoo_opend_running("127.0.0.1", 11111)

    assert calls == []


def test_local_macos_call_starts_loaded_launchagent(monkeypatch) -> None:
    readiness = iter((False, False, True))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(moomoo_opend, "_is_macos", lambda: True)
    monkeypatch.setattr(moomoo_opend, "_port_is_open", lambda _host, _port: next(readiness))
    monkeypatch.setattr(moomoo_opend, "_api_is_ready", lambda _host, _port: True)
    monkeypatch.setattr(moomoo_opend.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        moomoo_opend,
        "_run_launchctl",
        lambda *args: calls.append(args) or _result(),
    )

    moomoo_opend.ensure_moomoo_opend_running("127.0.0.1", 11111)

    assert calls == [("kickstart", f"gui/{moomoo_opend.os.getuid()}/{moomoo_opend._LABEL}")]


def test_unloaded_launchagent_is_bootstrapped_then_started(monkeypatch) -> None:
    readiness = iter((False, False, True))
    calls: list[tuple[str, ...]] = []
    results = iter((_result(3), _result(), _result()))
    monkeypatch.setattr(moomoo_opend, "_is_macos", lambda: True)
    monkeypatch.setattr(moomoo_opend, "_port_is_open", lambda _host, _port: next(readiness))
    monkeypatch.setattr(moomoo_opend, "_api_is_ready", lambda _host, _port: True)
    monkeypatch.setattr(moomoo_opend.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        moomoo_opend,
        "_run_launchctl",
        lambda *args: calls.append(args) or next(results),
    )

    moomoo_opend.ensure_moomoo_opend_running("127.0.0.1", 11111)

    domain = f"gui/{moomoo_opend.os.getuid()}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{moomoo_opend._LABEL}.plist"
    assert calls == [
        ("kickstart", f"{domain}/{moomoo_opend._LABEL}"),
        ("bootstrap", domain, str(plist)),
        ("kickstart", f"{domain}/{moomoo_opend._LABEL}"),
    ]


def test_non_macos_host_retains_normal_sdk_connection_behavior(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(moomoo_opend, "_is_macos", lambda: False)
    monkeypatch.setattr(moomoo_opend, "_port_is_open", lambda _host, _port: False)
    monkeypatch.setattr(moomoo_opend, "_api_is_ready", lambda _host, _port: False)
    monkeypatch.setattr(
        moomoo_opend,
        "_run_launchctl",
        lambda *args: calls.append(args) or _result(),
    )

    moomoo_opend.ensure_moomoo_opend_running("127.0.0.1", 11111)

    assert calls == []
