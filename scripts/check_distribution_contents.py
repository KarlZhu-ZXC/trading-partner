#!/usr/bin/env python3
"""Reject runtime state and generated artifacts using archive member names only.

No archive member is opened or extracted. Reports contain category counts, never
member paths or contents, so a contaminated local build does not disclose data.
"""

from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import TypedDict

CATEGORIES = (
    "runtime_data",
    "secrets",
    "owner_config",
    "dependencies",
    "build_outputs",
    "unsafe_paths",
)
CONSOLE_OUTPUTS = {
    ".vinext",
    ".wrangler",
    ".vercel",
    "out",
    "dist",
    "build",
    "outputs",
    "work",
    "coverage",
    "test-results",
    "playwright-report",
    "blob-report",
}


class DistributionReport(TypedDict):
    kind: str
    members: int
    blocked: dict[str, int]


def _category(name: str, *, sdist: bool) -> str | None:
    # Archive paths use POSIX separators. Reject alternate separators as well,
    # since downstream extraction on another platform could interpret them.
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return "unsafe_paths"
    if sdist and (
        not path.parts or not path.parts[0].startswith(("trading_partner-", "trading-partner-"))
    ):
        return "unsafe_paths"
    parts = path.parts[1:] if sdist else path.parts
    if not parts:
        return None
    filename = parts[-1]
    if parts[0] == "data":
        return "runtime_data"
    if any(
        (part == ".env" or part.startswith(".env."))
        and part != ".env.example"
        and not part.endswith(".example")
        or part == "runtime.env"
        or part.startswith("runtime.env.")
        for part in parts
    ):
        return "secrets"
    if filename == "account_basis_checkpoints.yaml":
        return "owner_config"
    if "node_modules" in parts or (
        parts[0] == "console"
        and len(parts) > 1
        and (parts[1] == ".yarn" or parts[1].startswith(".pnp"))
    ):
        return "dependencies"
    if filename.endswith(".tsbuildinfo") or (
        parts[0] == "console"
        and len(parts) > 1
        and (parts[1].startswith(".next") or parts[1] in CONSOLE_OUTPUTS)
    ):
        return "build_outputs"
    return None


def _report(names: Iterable[str], *, kind: str) -> DistributionReport:
    blocked = dict.fromkeys(CATEGORIES, 0)
    count = 0
    for name in names:
        count += 1
        category = _category(name, sdist=kind == "sdist")
        if category is not None:
            blocked[category] += 1
    if count == 0:
        raise ValueError("empty distribution")
    return {"kind": kind, "members": count, "blocked": blocked}


def inspect_distribution(archive: Path) -> DistributionReport:
    """Inspect tar/zip metadata without reading payloads or extracting files."""
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as source:
            return _report((member.name for member in source), kind="sdist")
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as source:
            return _report((member.filename for member in source.infolist()), kind="wheel")
    raise ValueError("unsupported distribution format")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    args = parser.parse_args(argv)
    failed = False
    for archive in args.archives:
        try:
            report = inspect_distribution(archive)
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
            print(json.dumps({"error": "unable to inspect distribution"}))
            failed = True
            continue
        print(json.dumps(report, sort_keys=True))
        failed |= any(report["blocked"].values())
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
