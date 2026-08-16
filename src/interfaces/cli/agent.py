"""Operate the opt-in Telegram Agent long poller and its launchd job."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import plistlib
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from application.services.agent_context_service import AgentContextService
from application.services.agent_handoff_service import AgentHandoffService
from application.services.agent_preferences_service import AgentPreferencesService
from application.services.agent_runtime_service import AgentRuntimeService
from bootstrap import (
    ApplicationContainer,
    build_default_application,
    build_telegram_agent_client,
    build_telegram_agent_lock,
    load_settings,
)
from domain.common.errors import ConfigurationError
from interfaces.agent.action_gateway import AgentActionGateway
from interfaces.agent.capability_gateway import AgentCapabilityGateway
from interfaces.agent.prompts import build_agent_system_prompt
from interfaces.cli.agent_behavior_evaluation import run_catalog
from interfaces.mcp.server import create_capability_registry
from interfaces.telegram.agent_poller import (
    TelegramAgentPoller,
    validate_agent_chat_id,
    validate_agent_user_id,
)

LABEL = "com.trading-partner.agent-telegram"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
CONSOLE_API_LABEL = "com.trading-partner.console-api"
CONSOLE_WEB_LABEL = "com.trading-partner.console-web"
CONSOLE_API_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{CONSOLE_API_LABEL}.plist"
CONSOLE_WEB_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{CONSOLE_WEB_LABEL}.plist"


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
            "trading-partner-agent",
            "telegram",
            "run",
        ],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(log_dir / "agent-telegram.stderr.log"),
    }


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


def _console_web_payload(project_root: Path, node_path: Path) -> dict[str, Any]:
    log_dir = project_root / "data" / "logs"
    console_root = project_root / "console"
    next_cli = console_root / "node_modules" / "next" / "dist" / "bin" / "next"
    return {
        "Label": CONSOLE_WEB_LABEL,
        "ProgramArguments": [
            str(node_path),
            str(next_cli),
            "start",
            "--hostname",
            "127.0.0.1",
        ],
        "WorkingDirectory": str(console_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_dir / "console-web.stdout.log"),
        "StandardErrorPath": str(log_dir / "console-web.stderr.log"),
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


def _configuration_status(settings: Any) -> dict[str, object]:
    token_configured = settings.telegram_bot_token is not None
    chat_id = validate_agent_chat_id(settings.telegram_chat_id)
    chat_configured = chat_id is not None
    user_id = validate_agent_user_id(getattr(settings, "telegram_agent_user_id", None))
    user_configured = chat_id is not None and (
        not chat_id.startswith("-") or user_id is not None
    )
    model_configured = False
    try:
        model_configured = settings.resolved_llm_config is not None
    except ConfigurationError:
        model_configured = False
    enabled = bool(settings.telegram_agent_enabled)
    diagnostics: list[dict[str, str]] = []
    if not enabled:
        diagnostics.append(
            {"code": "TELEGRAM_AGENT_DISABLED", "message": "Telegram Agent is disabled."}
        )
    elif not token_configured or not chat_configured or not user_configured:
        diagnostics.append(
            {
                "code": "TELEGRAM_AGENT_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "Telegram Agent needs a bot token, numeric chat id, and for group chats "
                    "a numeric TELEGRAM_AGENT_USER_ID."
                ),
            }
        )
    elif not model_configured:
        diagnostics.append(
            {
                "code": "AGENT_CONFIGURATION_UNAVAILABLE",
                "message": "Telegram Agent model endpoint is not configured.",
            }
        )
    return {
        "channel": "TELEGRAM",
        "enabled": enabled,
        "configured": token_configured and chat_configured and user_configured,
        "model_configured": model_configured,
        "available": (
            enabled
            and token_configured
            and chat_configured
            and user_configured
            and model_configured
        ),
        "state": (
            "READY"
            if enabled
            and token_configured
            and chat_configured
            and user_configured
            and model_configured
            else ("DISABLED" if not enabled else "UNAVAILABLE")
        ),
        "diagnostics": diagnostics,
    }


def _build_poller(
    container: ApplicationContainer,
) -> tuple[TelegramAgentPoller, Any]:
    settings = container.settings
    chat_id = validate_agent_chat_id(settings.telegram_chat_id)
    if not settings.telegram_agent_enabled:
        raise ConfigurationError("Telegram Agent is disabled")
    if settings.telegram_bot_token is None or chat_id is None:
        raise ConfigurationError("Telegram Agent requires bot token and numeric chat id")
    configured_user_id = validate_agent_user_id(
        getattr(settings, "telegram_agent_user_id", None)
    )
    if chat_id.startswith("-") and configured_user_id is None:
        raise ConfigurationError("Telegram group Agent requires numeric user id")
    model_provider = container.resources.agent_model_provider
    repository = container.operations.agent_conversations
    if model_provider is None:
        raise ConfigurationError("Telegram Agent model endpoint is not configured")
    registry = create_capability_registry(container)
    gateway = AgentCapabilityGateway(registry)
    action_gateway = AgentActionGateway.from_dependencies(
        repository=container.operations.agent_pending_actions,
        registry=registry,
        clock=container.context.clock,
        id_generator=container.context.id_generator,
    )
    context = AgentContextService(
        repository=repository,
        clock=container.context.clock,
        id_generator=container.context.id_generator,
    )
    preferences_service = AgentPreferencesService(
        container.operations.agent_preferences,
        container.context.clock,
        container.context.id_generator,
    )
    runtime = AgentRuntimeService(
        repository=repository,
        context_service=context,
        model_provider=model_provider,
        tool_gateway=gateway,
        clock=container.context.clock,
        id_generator=container.context.id_generator,
        system_prompt=build_agent_system_prompt(),
        pending_action_gateway=action_gateway,
        preferences_service=preferences_service,
        turn_lock_factory=getattr(container.resources, "agent_turn_lock_factory", None),
    )
    client = build_telegram_agent_client(container)
    return (
        TelegramAgentPoller(
            repository=repository,
            context_service=context,
            runtime=runtime,
            handoff_service=AgentHandoffService(
                container.operations.agent_handoffs,
                container.context.clock,
                container.context.id_generator,
            ),
            client=client,
            authorized_chat_id=chat_id,
            action_gateway=action_gateway,
            preferences_service=preferences_service,
            authorized_user_id=configured_user_id,
            clock=container.context.clock,
            id_generator=container.context.id_generator,
        ),
        client,
    )


async def _run_poller() -> int:
    settings = load_settings()
    status = _configuration_status(settings)
    if not status["available"]:
        print(json.dumps({"ok": status["state"] == "DISABLED", **status}, ensure_ascii=False))
        return 0 if status["state"] == "DISABLED" else 1
    container = build_default_application()
    poller: TelegramAgentPoller | None = None
    client: Any = None
    lock = build_telegram_agent_lock(container.settings.telegram_agent_lock_path)
    if not lock.acquire():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_codes": ["TELEGRAM_AGENT_ALREADY_RUNNING"],
                },
                ensure_ascii=False,
            )
        )
        await container.aclose()
        return 1
    try:
        poller, client = _build_poller(container)
        await poller.run_forever()
    except KeyboardInterrupt:
        return 0
    except ConfigurationError as error:
        print(
            json.dumps(
                {"ok": False, "error_codes": [error.code], "message": error.message},
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        if client is not None:
            await client.aclose()
        lock.release()
        await container.aclose()
    return 0


def install() -> int:
    root = _project_root()
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required but was not found on PATH")
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(_launchd_payload(root, Path(uv)), sort_keys=True))
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
    settings = load_settings()
    payload = _configuration_status(settings)
    result = _run_launchctl("print", f"{_domain()}/{LABEL}", check=False)
    payload["launchd_installed"] = result.returncode == 0
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["state"] != "UNAVAILABLE" else 1


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
            "telegram": _job_status(LABEL, PLIST_PATH),
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
            "telegram": dict(unavailable),
        }


def console_install() -> int:
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
    _install_job(
        CONSOLE_API_LABEL,
        CONSOLE_API_PLIST_PATH,
        _console_api_payload(root, Path(uv)),
    )
    try:
        _install_job(
            CONSOLE_WEB_LABEL,
            CONSOLE_WEB_PLIST_PATH,
            _console_web_payload(root, Path(node)),
        )
    except SystemExit:
        _run_launchctl("bootout", _domain(), CONSOLE_API_LABEL, check=False)
        raise
    print("installed local Console supervisor (api + web)")
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


def all_install() -> int:
    console_install()
    return install()


def all_uninstall() -> int:
    console_uninstall()
    return uninstall()


def all_restart() -> int:
    console_restart()
    _restart_label(LABEL, "Telegram Agent")
    print("restarted local Console + Telegram Agent")
    return 0


def all_status() -> int:
    snapshot = supervisor_status_snapshot()
    payload = {
        "console": {"api": snapshot["console_api"], "web": snapshot["console_web"]},
        "telegram": snapshot["telegram"],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the authorized Telegram Agent poller.")
    root = parser.add_subparsers(dest="channel", required=True)
    telegram = root.add_parser("telegram", help="Telegram Agent channel")
    telegram.add_argument("command", choices=("run", "install", "status", "restart", "uninstall"))
    console = root.add_parser("console", help="Local Console supervisor")
    console.add_argument("command", choices=("install", "status", "restart", "uninstall"))
    combined = root.add_parser("all", help="Local Console and Telegram supervisor")
    combined.add_argument("command", choices=("install", "status", "restart", "uninstall"))
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
    handlers = {
        "install": install,
        "status": status,
        "restart": lambda: _restart_label(LABEL, "Telegram Agent"),
        "uninstall": uninstall,
    }
    console_handlers = {
        "install": console_install,
        "status": console_status,
        "restart": console_restart,
        "uninstall": console_uninstall,
    }
    all_handlers = {
        "install": all_install,
        "status": all_status,
        "restart": all_restart,
        "uninstall": all_uninstall,
    }
    if args.command == "run":
        raise SystemExit(asyncio.run(_run_poller()))
    selected = (
        handlers
        if args.channel == "telegram"
        else console_handlers
        if args.channel == "console"
        else all_handlers
    )
    raise SystemExit(selected[args.command]())


if __name__ == "__main__":
    main()
