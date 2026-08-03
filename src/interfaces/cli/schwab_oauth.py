"""Foreground-only Schwab OAuth status and single-browser-flow commands."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict

from application.dto.schwab_oauth import SchwabOAuthHealthState
from bootstrap import build_schwab_oauth_flow_manager


def _next_action(
    command: str,
    flow_state: str,
    token_health_state: SchwabOAuthHealthState | str,
) -> str:
    """Return the safe, command-aware follow-up for the OAuth diagnostic.

    A durable ``SUCCEEDED`` receipt describes the last browser flow; it does
    not mean that a later ``status`` invocation should replay the post-renew
    account-sync instruction.  Flow-safety guidance therefore wins first,
    while the health state drives ordinary status guidance.
    """

    if flow_state == "ACTIVE":
        return "COMPLETE_EXISTING_BROWSER_TAB_DO_NOT_RERUN"
    if flow_state in {"FAILED", "INTERRUPTED"}:
        return "CLOSE_OLD_TAB_THEN_EXPLICITLY_CONFIRM_NEW_FLOW"

    health_state = (
        token_health_state.value
        if isinstance(token_health_state, SchwabOAuthHealthState)
        else token_health_state
    )
    if health_state == "DISABLED":
        return "NO_RETRY_OAUTH_DISABLED"
    if health_state == "UNAVAILABLE":
        return "NO_RETRY_TOKEN_STATUS_UNAVAILABLE"

    if command == "renew" and flow_state == "SUCCEEDED":
        return "RETRY_ACCOUNT_SYNC_ONCE"

    if command == "status":
        if health_state == "VALID":
            return "NO_ACTION_TOKEN_VALID"
        if health_state == "EXPIRING":
            return "PLAN_ONE_RENEW_FLOW_BEFORE_DUE"
        if health_state == "REAUTH_REQUIRED":
            return "START_ONE_RENEW_FLOW"

    return "START_ONE_RENEW_FLOW_IF_NEEDED"


def _run(command: str, *, confirm_new_flow: bool = False) -> int:
    try:
        manager = build_schwab_oauth_flow_manager()
    except ValueError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_codes": ["SCHWAB_OAUTH_NOT_CONFIGURED"],
                },
                sort_keys=True,
            )
        )
        return 2

    flow = (
        manager.status()
        if command == "status"
        else manager.renew(confirm_retry_after_failure=confirm_new_flow)
    )
    health = manager.token_health()
    state = flow.state.value
    ok = state in {"IDLE", "ACTIVE", "SUCCEEDED"}
    print(
        json.dumps(
            {
                "ok": ok,
                "flow": asdict(flow),
                "token_health": health.model_dump(mode="json"),
                "next_action": _next_action(command, state, health.state),
                "browser_tab_rule": "USE_ONLY_TAB_OPENED_BY_CURRENT_COMMAND",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-schwab-auth",
        description=(
            "Inspect token age or run exactly one foreground Schwab browser OAuth flow."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "status",
        help="Inspect safe token age and any currently active browser flow.",
    )
    renew = subcommands.add_parser(
        "renew",
        help="Start one browser flow, or reuse the currently active flow.",
    )
    renew.add_argument(
        "--confirm-new-flow",
        action="store_true",
        help=(
            "After closing an old failed tab, explicitly authorize one replacement flow."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    raise SystemExit(
        _run(
            args.command,
            confirm_new_flow=bool(getattr(args, "confirm_new_flow", False)),
        )
    )


if __name__ == "__main__":
    main()
