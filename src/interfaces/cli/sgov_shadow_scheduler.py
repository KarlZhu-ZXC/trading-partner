"""Install and manage the dedicated SGOV automatic cash-sweep scheduler."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

LABEL = "com.trading-partner.sgov-shadow-plan"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _project_root() -> Path:
    candidate = Path.cwd().resolve()
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path(__file__).resolve().parents[3]


def _launchd_payload(project_root: Path, uv_path: Path) -> dict[str, Any]:
    log_dir = project_root / "data" / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(uv_path),
            "run",
            "--directory",
            str(project_root),
            "trading-partner-sgov-plan",
            "auto-run",
            "--json",
        ],
        "WorkingDirectory": str(project_root),
        # Wake hourly at :45 for the passive bid phase and :55 for the completion
        # phase. Application-side America/New_York due selection remains correct
        # across DST and XNYS early closes. Non-due wakes do not contact Providers.
        "StartCalendarInterval": [{"Minute": 45}, {"Minute": 55}],
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(log_dir / "sgov-shadow-plan.stderr.log"),
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _run_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("launchctl", *args),
        check=check,
        capture_output=True,
        text=True,
    )


def install() -> int:
    root = _project_root()
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required but was not found on PATH")
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(_launchd_payload(root, Path(uv)), sort_keys=True))
    PLIST_PATH.chmod(0o600)
    _run_launchctl("bootout", _domain(), str(PLIST_PATH), check=False)
    result = _run_launchctl("bootstrap", _domain(), str(PLIST_PATH), check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"launchd install failed: {detail}")
    print(f"installed {LABEL} ({PLIST_PATH})")
    return 0


def uninstall() -> int:
    _run_launchctl("bootout", _domain(), str(PLIST_PATH), check=False)
    PLIST_PATH.unlink(missing_ok=True)
    print(f"uninstalled {LABEL}")
    return 0


def status() -> int:
    result = _run_launchctl("print", f"{_domain()}/{LABEL}", check=False)
    if result.returncode != 0:
        print(f"not installed: {LABEL}")
        return 1
    print(result.stdout.rstrip())
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the dedicated Schwab SGOV automatic-buy scheduler."
    )
    parser.add_argument("command", choices=("install", "status", "uninstall"))
    args = parser.parse_args()
    handlers = {"install": install, "status": status, "uninstall": uninstall}
    raise SystemExit(handlers[args.command]())


if __name__ == "__main__":
    main()
