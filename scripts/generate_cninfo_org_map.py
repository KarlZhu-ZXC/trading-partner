#!/usr/bin/env python3
"""Generate / validate the CNINFO A-share orgId map (Phase 1E E3).

Offline ``--check`` validates the committed snapshot without network.
Explicit ``--refresh`` / ``--write`` fetches official inventories and rewrites
the versioned snapshot under ``config/cninfo_org_map.v1.json``.

Usage:
  uv run python scripts/generate_cninfo_org_map.py --check
  uv run python scripts/generate_cninfo_org_map.py --refresh --write
  uv run python scripts/generate_cninfo_org_map.py --refresh --stdout

No credentials. Network only on --refresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "config" / "cninfo_org_map.v1.json"
SCHEMA_VERSION = "cninfo_org_map.v1"
ALLOWED_CATEGORIES = frozenset({"A股"})
CODE_RE = re.compile(r"^\d{6}$")
ORG_RE = re.compile(r"^[A-Za-z0-9]+$")
SOURCE_SZSE = "https://www.cninfo.com.cn/new/data/szse_stock.json"
SOURCE_BJ = "https://www.cninfo.com.cn/new/data/bj_stock.json"
USER_AGENT = "TradingPartner/1.0 (cninfo-org-map-generator; offline-check-safe)"
MIN_ENTRY_COUNT = 6000


def _canonical_entries_bytes(entries: list[dict[str, str]]) -> bytes:
    return json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _fetch_json(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"network fetch failed for {url}: {exc}") from exc
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON from {url}: {exc}") from exc
    if not isinstance(doc, dict):
        raise SystemExit(f"unexpected root type from {url}")
    return doc


def _rows_from_inventory(doc: dict[str, Any], *, source_name: str) -> list[dict[str, Any]]:
    rows = doc.get("stockList")
    if not isinstance(rows, list):
        raise SystemExit(f"{source_name}: missing stockList array")
    return rows


def build_map_from_inventories(
    szse_rows: list[dict[str, Any]],
    bj_rows: list[dict[str, Any]],
    *,
    generated_at: str,
    version: str,
) -> dict[str, Any]:
    by_code: dict[str, dict[str, str]] = {}
    for source_name, rows in (("szse_stock", szse_rows), ("bj_stock", bj_rows)):
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SystemExit(f"{source_name}[{idx}]: not an object")
            category = row.get("category")
            if category not in ALLOWED_CATEGORIES:
                continue
            code = row.get("code")
            org_id = row.get("orgId")
            if not isinstance(code, str) or not CODE_RE.fullmatch(code):
                raise SystemExit(f"{source_name}[{idx}]: malformed code {code!r}")
            if not isinstance(org_id, str) or not ORG_RE.fullmatch(org_id):
                raise SystemExit(f"{source_name}[{idx}]: malformed orgId for {code}")
            if code in by_code:
                prev = by_code[code]
                if prev["org_id"] != org_id:
                    raise SystemExit(
                        f"conflicting orgId for {code}: {prev['org_id']} vs {org_id}"
                    )
                continue
            by_code[code] = {
                "code": code,
                "org_id": org_id,
                "category": str(category),
                "source": source_name,
            }
    entries = [by_code[c] for c in sorted(by_code)]
    if len(entries) < MIN_ENTRY_COUNT:
        raise SystemExit(
            f"entry count {len(entries)} below threshold {MIN_ENTRY_COUNT}"
        )
    digest = hashlib.sha256(_canonical_entries_bytes(entries)).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "generated_at": generated_at,
        "source_urls": [SOURCE_SZSE, SOURCE_BJ],
        "allowed_categories": sorted(ALLOWED_CATEGORIES),
        "entry_count": len(entries),
        "content_sha256": digest,
        "entries": entries,
    }


def validate_document(doc: object) -> None:
    """Reuse runtime validator when available; else local structural checks."""
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from infrastructure.providers.a_share.cninfo_org_map import (  # noqa: PLC0415
        validate_org_map_document,
    )

    validate_org_map_document(doc)


def cmd_check(path: Path) -> int:
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    validate_document(doc)
    print(
        f"OK {path} entries={doc['entry_count']} "
        f"digest={doc['content_sha256'][:16]}… version={doc['version']}"
    )
    return 0


def cmd_refresh(*, write: bool, stdout: bool, force: bool) -> int:
    szse = _fetch_json(SOURCE_SZSE)
    bj = _fetch_json(SOURCE_BJ)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    version = f"cninfo-official-{generated_at[:10]}.v1"
    doc = build_map_from_inventories(
        _rows_from_inventory(szse, source_name="szse"),
        _rows_from_inventory(bj, source_name="bj"),
        generated_at=generated_at,
        version=version,
    )
    validate_document(doc)
    text = json.dumps(doc, ensure_ascii=True, indent=2) + "\n"
    if stdout:
        sys.stdout.write(text)
    if write:
        if DEFAULT_OUTPUT.exists() and not force:
            # Apply-safe: refuse overwrite without --force when content differs
            # only after user opts into --write; still allow write of same digest.
            existing = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
            if existing.get("content_sha256") != doc["content_sha256"] and not force:
                print(
                    "Refusing to overwrite committed map with different digest "
                    "without --force (apply-safe).",
                    file=sys.stderr,
                )
                print(
                    f"existing={existing.get('content_sha256')} new={doc['content_sha256']}",
                    file=sys.stderr,
                )
                return 2
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(text, encoding="utf-8")
        print(
            f"wrote {DEFAULT_OUTPUT} entries={doc['entry_count']} "
            f"digest={doc['content_sha256'][:16]}…"
        )
    if not write and not stdout:
        print(
            f"refreshed in-memory entries={doc['entry_count']} "
            f"digest={doc['content_sha256'][:16]}… (pass --write or --stdout)"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate committed snapshot offline (no network)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch official CNINFO inventories over the network",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write refreshed snapshot to config/cninfo_org_map.v1.json",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print refreshed snapshot JSON to stdout",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing map with a different digest",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path for --check (default: config/cninfo_org_map.v1.json)",
    )
    args = parser.parse_args(argv)
    if args.check and (args.refresh or args.write or args.stdout):
        parser.error("--check is exclusive of --refresh/--write/--stdout")
    if not args.check and not args.refresh:
        parser.error("specify --check or --refresh")
    if args.check:
        return cmd_check(args.path)
    return cmd_refresh(write=args.write, stdout=args.stdout, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
