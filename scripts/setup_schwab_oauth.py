#!/usr/bin/env python3
"""Create this project's Schwab OAuth token through schwab-py."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from infrastructure.config.settings import AppSettings
from infrastructure.providers.account.schwab import SchwabPyReadClient


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


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
        raise SystemExit("Schwab token already exists; use --replace only for reauthorization")
    _secure_directory(token_path.parent)
    try:
        from schwab.auth import client_from_login_flow
    except ImportError:
        raise SystemExit("schwab-py is not installed; run uv sync") from None
    try:
        client_from_login_flow(
            settings.schwab_client_id,
            settings.schwab_client_secret,
            settings.schwab_redirect_uri,
            str(token_path),
            callback_timeout=300,
            interactive=False,
        )
    except Exception:
        raise SystemExit(
            "Schwab browser OAuth failed; no manual fallback was started. "
            "Close stale OAuth tabs and rerun this command."
        ) from None
    token_path.chmod(0o600)
    print("Schwab OAuth token created for Trading Partner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
