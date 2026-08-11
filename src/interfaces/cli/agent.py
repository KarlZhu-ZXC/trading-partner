"""Operate the opt-in Telegram Agent long poller and its launchd job."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import plistlib
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from application.services.agent_context_service import AgentContextService
from application.services.agent_handoff_service import AgentHandoffService
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
from interfaces.mcp.server import create_capability_registry
from interfaces.telegram.agent_poller import (
    TelegramAgentPoller,
    validate_agent_chat_id,
    validate_agent_user_id,
)

LABEL = "com.trading-partner.agent-telegram"
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
            "trading-partner-agent",
            "telegram",
            "run",
        ],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(log_dir / "agent-telegram.stderr.log"),
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
    runtime = AgentRuntimeService(
        repository=repository,
        context_service=context,
        model_provider=model_provider,
        tool_gateway=gateway,
        clock=container.context.clock,
        id_generator=container.context.id_generator,
        system_prompt=build_agent_system_prompt(),
        pending_action_gateway=action_gateway,
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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the authorized Telegram Agent poller.")
    root = parser.add_subparsers(dest="channel", required=True)
    telegram = root.add_parser("telegram", help="Telegram Agent channel")
    telegram.add_argument("command", choices=("run", "install", "status", "uninstall"))
    args = parser.parse_args(argv)
    handlers = {
        "install": install,
        "status": status,
        "uninstall": uninstall,
    }
    if args.command == "run":
        raise SystemExit(asyncio.run(_run_poller()))
    raise SystemExit(handlers[args.command]())


if __name__ == "__main__":
    main()
