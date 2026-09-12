"""Operate the local Console supervisor and Agent behavior evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from interfaces.cli.agent_behavior_evaluation import run_catalog

CONSOLE_API_LABEL = "com.trading-partner.console-api"
CONSOLE_WEB_LABEL = "com.trading-partner.console-web"
CONSOLE_API_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{CONSOLE_API_LABEL}.plist"
CONSOLE_WEB_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{CONSOLE_WEB_LABEL}.plist"
CONSOLE_LAN_PASSWORD_RELATIVE_PATH = Path("data") / "secrets" / "console-lan-password"
CONSOLE_LAN_PASSWORD_MIN_LENGTH = 16


def _project_root() -> Path:
    candidate = Path.cwd().resolve()
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path(__file__).resolve().parents[3]


def _console_api_payload(project_root: Path, uv_path: Path) -> dict[str, Any]:
    log_dir = project_root / "data" / "logs"
    return {
        "Label": CONSOLE_API_LABEL,
        "ProgramArguments": [
            str(uv_path),
            "run",
            "--directory",
            str(project_root),
            "trading-partner-console",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
        ],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_dir / "console-api.stdout.log"),
        "StandardErrorPath": str(log_dir / "console-api.stderr.log"),
    }


def _console_web_payload(
    project_root: Path,
    node_path: Path,
    *,
    lan_password_file: Path | None = None,
    lan_port: int = 3000,
) -> dict[str, Any]:
    log_dir = project_root / "data" / "logs"
    console_root = project_root / "console"
    if lan_password_file is None:
        program_arguments = [
            str(node_path),
            str(console_root / "node_modules" / "next" / "dist" / "bin" / "next"),
            "start",
            "--hostname",
            "127.0.0.1",
        ]
        environment: dict[str, str] | None = None
    else:
        program_arguments = [
            str(node_path),
            str(console_root / "scripts" / "start-lan.mjs"),
            "start",
        ]
        environment = {
            "TRADING_PARTNER_CONSOLE_LAN_PASSWORD_FILE": str(lan_password_file),
            "TRADING_PARTNER_CONSOLE_LAN_PORT": str(lan_port),
        }
    payload: dict[str, Any] = {
        "Label": CONSOLE_WEB_LABEL,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(console_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_dir / "console-web.stdout.log"),
        "StandardErrorPath": str(log_dir / "console-web.stderr.log"),
    }
    if environment is not None:
        payload["EnvironmentVariables"] = environment
    return payload


def _ensure_console_lan_password(project_root: Path) -> Path:
    path = project_root / CONSOLE_LAN_PASSWORD_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise SystemExit("Console LAN password path must be a regular file")
        password = path.read_text(encoding="utf-8").strip()
        if len(password) < CONSOLE_LAN_PASSWORD_MIN_LENGTH:
            password = os.environ.get(
                "TRADING_PARTNER_CONSOLE_LAN_PASSWORD"
            ) or secrets.token_urlsafe(24)
            if len(password) < CONSOLE_LAN_PASSWORD_MIN_LENGTH:
                raise SystemExit(
                    "TRADING_PARTNER_CONSOLE_LAN_PASSWORD must contain at least 16 characters"
                )
            path.write_text(f"{password}\n", encoding="utf-8")
        path.chmod(0o600)
        return path
    password = os.environ.get("TRADING_PARTNER_CONSOLE_LAN_PASSWORD") or secrets.token_urlsafe(24)
    if len(password) < CONSOLE_LAN_PASSWORD_MIN_LENGTH:
        raise SystemExit(
            "TRADING_PARTNER_CONSOLE_LAN_PASSWORD must contain at least 16 characters"
        )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(f"{password}\n")
    return path


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _run_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("launchctl", *args),
        check=check,
        capture_output=True,
        text=True,
    )


def _ensure_console_build(project_root: Path) -> None:
    console_root = project_root / "console"
    package_json = console_root / "package.json"
    if not package_json.is_file():
        raise SystemExit("Console production build unavailable: console/package.json is missing")
    next_dir = console_root / ".next"
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("Console production build unavailable: npm was not found on PATH")
    result = subprocess.run(
        (npm, "--prefix", str(console_root), "run", "build"),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not (next_dir / "BUILD_ID").is_file():
        raise SystemExit("Console production build failed; inspect the local build log")


def _write_plist(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(dict(payload), sort_keys=True))
    path.chmod(0o600)


def _install_job(label: str, path: Path, payload: Mapping[str, object]) -> None:
    _write_plist(path, payload)
    _run_launchctl("bootout", _domain(), str(path), check=False)
    result = _run_launchctl("bootstrap", _domain(), str(path), check=False)
    if result.returncode != 0:
        raise SystemExit(f"launchd install failed for {label}")


def _job_status(label: str, path: Path) -> dict[str, object]:
    result = _run_launchctl("print", f"{_domain()}/{label}", check=False)
    raw = f"{result.stdout}\n{result.stderr}"
    pid_match = re.search(r"(?m)^\s*pid\s*=\s*(\d+)", raw)
    start_match = re.search(r"(?m)^\s*start time\s*=\s*(.+)$", raw)
    exit_match = re.search(r"(?m)^\s*last exit code\s*=\s*(-?\d+)", raw)
    pid = int(pid_match.group(1)) if pid_match else None
    start_time = start_match.group(1).strip()[:128] if start_match else None
    if start_time is None and pid is not None:
        process = subprocess.run(
            ("ps", "-p", str(pid), "-o", "lstart="),
            check=False,
            capture_output=True,
            text=True,
        )
        candidate = process.stdout.strip()
        if process.returncode == 0 and candidate:
            start_time = candidate[:128]
    last_exit = int(exit_match.group(1)) if exit_match else None
    running = pid is not None and pid > 0
    return {
        "installed": path.is_file(),
        "loaded": result.returncode == 0,
        "running": running,
        "pid": pid,
        "start_time": start_time,
        "last_exit": last_exit,
        "last_error": (
            f"PROCESS_EXIT_{last_exit}"
            if not running and last_exit not in {None, 0}
            else None
        ),
    }


def supervisor_status_snapshot() -> dict[str, object]:
    """Return secret-safe component health for CLI and the local Console."""

    try:
        return {
            "console_api": _job_status(CONSOLE_API_LABEL, CONSOLE_API_PLIST_PATH),
            "console_web": _job_status(CONSOLE_WEB_LABEL, CONSOLE_WEB_PLIST_PATH),
        }
    except (FileNotFoundError, OSError):
        unavailable = {
            "installed": False,
            "loaded": False,
            "running": False,
            "pid": None,
            "start_time": None,
            "last_exit": None,
            "last_error": None,
        }
        return {
            "console_api": dict(unavailable),
            "console_web": dict(unavailable),
        }


def console_install(*, lan: bool = False, lan_port: int = 3000) -> int:
    root = _project_root()
    uv = shutil.which("uv")
    npm = shutil.which("npm")
    node = shutil.which("node")
    if uv is None or npm is None or node is None:
        raise SystemExit(
            "uv, npm, and node are required to install the local Console supervisor"
        )
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    _ensure_console_build(root)
    if not 1024 <= lan_port <= 65535:
        raise SystemExit("Console LAN port must be in [1024,65535]")
    lan_password_file = _ensure_console_lan_password(root) if lan else None
    _install_job(
        CONSOLE_API_LABEL,
        CONSOLE_API_PLIST_PATH,
        _console_api_payload(root, Path(uv)),
    )
    try:
        _install_job(
            CONSOLE_WEB_LABEL,
            CONSOLE_WEB_PLIST_PATH,
            _console_web_payload(
                root,
                Path(node),
                lan_password_file=lan_password_file,
                lan_port=lan_port,
            ),
        )
    except SystemExit:
        _run_launchctl("bootout", _domain(), CONSOLE_API_LABEL, check=False)
        raise
    mode = "LAN-authenticated web" if lan else "loopback web"
    print(f"installed local Console supervisor (api + {mode})")
    if lan_password_file is not None:
        print(f"LAN password file: {lan_password_file}")
    return 0


def console_uninstall() -> int:
    for path in (CONSOLE_API_PLIST_PATH, CONSOLE_WEB_PLIST_PATH):
        _run_launchctl("bootout", _domain(), str(path), check=False)
        path.unlink(missing_ok=True)
    print("uninstalled local Console supervisor")
    return 0


def console_restart() -> int:
    for label in (CONSOLE_API_LABEL, CONSOLE_WEB_LABEL):
        _restart_label(label, label)
    print("restarted local Console supervisor")
    return 0


def _restart_label(label: str, description: str) -> int:
    # ``-k`` terminates the existing instance first; without it kickstart may
    # be a no-op when launchd already considers the job running.
    result = _run_launchctl("kickstart", "-k", f"{_domain()}/{label}", check=False)
    if result.returncode != 0:
        raise SystemExit(f"launchd restart failed for {description}")
    return 0


def console_status() -> int:
    snapshot = supervisor_status_snapshot()
    payload = {"api": snapshot["console_api"], "web": snapshot["console_web"]}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Operate the local Console supervisor.")
    root = parser.add_subparsers(dest="channel", required=True)
    console = root.add_parser("console", help="Local Console supervisor")
    console.add_argument("command", choices=("install", "status", "restart", "uninstall"))
    console.add_argument(
        "--lan",
        action="store_true",
        help="Install the Web Console on 0.0.0.0 with password authentication",
    )
    console.add_argument(
        "--lan-port",
        type=int,
        default=3000,
        help="Authenticated LAN Web port (default: 3000)",
    )
    evaluation = root.add_parser("eval", help="Run the deterministic Agent behavior catalog")
    evaluation.add_argument(
        "--live",
        action="store_true",
        help="Reserved; live smoke remains disabled",
    )
    args = parser.parse_args(argv)
    if args.channel == "eval":
        try:
            receipt = asyncio.run(run_catalog(live=bool(args.live)))
        except Exception as error:  # noqa: BLE001 - CLI emits only a safe summary
            print(json.dumps({"ok": False, "error": type(error).__name__}, ensure_ascii=False))
            raise SystemExit(1) from None
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        raise SystemExit(0 if receipt["passed"] else 1)
    console_handlers = {
        "install": lambda: console_install(
            lan=bool(getattr(args, "lan", False)),
            lan_port=int(getattr(args, "lan_port", 3000)),
        ),
        "status": console_status,
        "restart": console_restart,
        "uninstall": console_uninstall,
    }
    raise SystemExit(console_handlers[args.command]())


if __name__ == "__main__":
    main()
