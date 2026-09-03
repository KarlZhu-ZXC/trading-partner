"""Manage the owner-only Cookie used by Moomoo note HTTP enrichment."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Sequence

from application.services.external_note_sync_service import ExternalNoteSyncService
from domain.common.errors import DataContractError
from interfaces.cli._lifecycle import application_container

_SOURCE_CODE = "MOOMOO_NOTE"


def _status(service: ExternalNoteSyncService) -> int:
    status = service.credential_status(_SOURCE_CODE)
    print(
        json.dumps(
            {
                "ok": True,
                "configured": status.configured,
                "supported": status.supported,
                "source_code": status.source_code,
            },
            sort_keys=True,
        )
    )
    return 0


def _set(service: ExternalNoteSyncService) -> int:
    value = (
        getpass.getpass("Moomoo Cookie header: ")
        if sys.stdin.isatty()
        else sys.stdin.read()
    )
    try:
        service.configure_credential(_SOURCE_CODE, value)
    except DataContractError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "MOOMOO_NOTES_COOKIE_INVALID",
                    "message": "Cookie was not saved; provide one bounded Cookie header line.",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "configured": True,
                "source_code": _SOURCE_CODE,
                "next_action": "RUN_OBSERVATION_SYNC",
            },
            sort_keys=True,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-partner-moomoo-notes-cookie",
        description="Configure the owner-only Cookie for read-only Moomoo note enrichment.",
    )
    parser.add_argument("action", choices=("status", "set"))
    return parser


async def _run(action: str) -> int:
    async with application_container() as container:
        service = container.services.external_notes
        if action == "status":
            return _status(service)
        return _set(service)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    raise SystemExit(asyncio.run(_run(args.action)))


if __name__ == "__main__":
    main()
