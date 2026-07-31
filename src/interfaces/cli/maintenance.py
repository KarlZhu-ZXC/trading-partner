"""Local database backup, retention preview, and maintenance status CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from bootstrap import build_default_application


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-partner-maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show storage, backup, and retention status.")
    commands.add_parser("backup", help="Create and verify an owner-only SQLite backup.")
    prune = commands.add_parser("prune-cache", help="Preview or apply expired-cache cleanup.")
    prune.add_argument("--retention-days", type=int, default=30)
    prune.add_argument(
        "--apply",
        action="store_true",
        help="Delete matching expired cache rows; omission is a dry run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    container = build_default_application()
    try:
        payload: dict[str, Any]
        if args.command == "status":
            payload = container.operations.maintenance.status().model_dump(mode="json")
        elif args.command == "backup":
            payload = container.operations.maintenance.backup().model_dump(mode="json")
        else:
            payload = container.operations.maintenance.prune_expired_cache(
                retention_days=args.retention_days, dry_run=not args.apply
            ).model_dump(mode="json")
        print(json.dumps({"ok": True, **payload}, sort_keys=True))
    finally:
        container.close()


if __name__ == "__main__":
    main()
