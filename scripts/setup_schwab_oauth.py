#!/usr/bin/env python3
"""Create this project's Schwab OAuth token through schwab-py."""

from __future__ import annotations

import argparse
from collections.abc import Mapping

from infrastructure.config.settings import AppSettings
from infrastructure.providers.account.schwab import SchwabPyReadClient
from interfaces.cli.schwab_oauth import _run as run_oauth_command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Allow schwab-py to replace an existing project token after reauthorization.",
    )
    parser.add_argument(
        "--list-account-hashes",
        action="store_true",
        help="Print encrypted account hashes only; plain account numbers stay hidden.",
    )
    parser.add_argument(
        "--confirm-new-flow",
        action="store_true",
        help="After closing a failed old tab, allow one replacement OAuth flow.",
    )
    args = parser.parse_args()
    settings = AppSettings.load()
    if not settings.schwab_client_id or not settings.schwab_client_secret:
        raise SystemExit("Schwab client credentials are not configured in project .env")
    token_path = settings.schwab_token_path
    if args.list_account_hashes:
        if not token_path.is_file():
            raise SystemExit("Schwab token does not exist; run OAuth setup first")
        client = SchwabPyReadClient(
            client_id=settings.schwab_client_id,
            client_secret=settings.schwab_client_secret,
            redirect_uri=settings.schwab_redirect_uri,
            token_path=token_path,
        )
        rows = client.account_numbers()
        if not isinstance(rows, list):
            raise SystemExit("Schwab account-number response is invalid")
        hashes = [
            str(row["hashValue"])
            for row in rows
            if isinstance(row, Mapping) and row.get("hashValue")
        ]
        if not hashes:
            raise SystemExit("No Schwab encrypted account hashes were returned")
        for index, account_hash in enumerate(hashes, start=1):
            print(f"schwab_account_hash_{index}={account_hash}")
        return 0
    if token_path.exists() and not args.replace:
        raise SystemExit(
            "Schwab token already exists; use --replace only for reauthorization"
        )
    result = run_oauth_command(
        "renew",
        confirm_new_flow=args.confirm_new_flow,
    )
    if result != 0:
        raise SystemExit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
