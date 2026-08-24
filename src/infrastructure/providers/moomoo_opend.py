"""Start the local macOS Moomoo OpenD LaunchAgent on demand."""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

_LABEL = "com.trading-partner.moomoo-opend"
_START_LOCK = threading.Lock()


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _api_is_ready(host: str, port: int) -> bool:
    try:
        import moomoo
    except ImportError:
        return False

    context = None
    try:
        moomoo.SysConfig.enable_console_log(False)
        context = moomoo.OpenQuoteContext(host=host, port=port)
        ret, state = context.get_global_state()
        return bool(
            ret == moomoo.RET_OK
            and state.get("qot_logined")
            and state.get("trd_logined")
        )
    except Exception:
        return False
    finally:
        if context is not None:
            with suppress(Exception):
                context.close()


def _run_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("/bin/launchctl", *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _is_macos() -> bool:
    return sys.platform == "darwin"


def ensure_moomoo_opend_running(
    host: str,
    port: int,
    *,
    timeout_seconds: float = 20.0,
) -> None:
    """Start the managed local OpenD when a provider first needs it.

    Remote OpenD endpoints and non-macOS hosts retain the SDK's normal
    connection behavior. The lock prevents concurrent provider calls from
    issuing duplicate launchctl requests.
    """

    if _port_is_open(host, port):
        return
    if not _is_macos() or not _is_loopback(host):
        return

    with _START_LOCK:
        if _port_is_open(host, port):
            return

        domain = f"gui/{os.getuid()}"
        service = f"{domain}/{_LABEL}"
        result = _run_launchctl("kickstart", service)
        if result.returncode != 0:
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
            _run_launchctl("bootstrap", domain, str(plist_path))
            result = _run_launchctl("kickstart", service)
        if result.returncode != 0:
            raise RuntimeError("Moomoo OpenD LaunchAgent could not be started")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if _port_is_open(host, port) and _api_is_ready(host, port):
                return
            time.sleep(0.1)
        raise RuntimeError("Moomoo OpenD API did not become ready before the timeout")
