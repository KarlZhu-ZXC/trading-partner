#!/usr/bin/env python3
"""Generate a reviewed A-share trading calendar candidate (Phase 1E E2).

Deterministic: frozen weekday closures for 2024–2026 plus weekend exclusion.
No network, no pandas, no runtime data dependency. Writes a candidate JSON
matching the loader schema; refuses to overwrite the tracked path unless
``--force`` is passed.

Usage:
  uv run python scripts/generate_a_share_trading_calendar.py --check
  uv run python scripts/generate_a_share_trading_calendar.py --stdout
  uv run python scripts/generate_a_share_trading_calendar.py --write --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "config" / "a_share_trading_calendar.v1.json"

SCHEMA_VERSION = "a_share_trading_calendar.v1"
CALENDAR_VERSION = "sse-official-2024-2026.v1"
TIMEZONE = "Asia/Shanghai"
COVERAGE_FROM = date(2024, 1, 1)
COVERAGE_TO = date(2026, 12, 31)
# Frozen generation timestamp so content_sha256 is reviewable/reproducible.
GENERATED_AT = "2026-07-17T12:00:00+00:00"

# Official SSE weekday closures (weekends are never open; makeup workdays stay closed).
# Sources: SSE closed-day announcements (see SOURCE_URLS).
WEEKDAY_CLOSURES: dict[int, frozenset[date]] = {
    2024: frozenset(
        {
            date(2024, 1, 1),
            date(2024, 2, 9),
            date(2024, 2, 12),
            date(2024, 2, 13),
            date(2024, 2, 14),
            date(2024, 2, 15),
            date(2024, 2, 16),
            date(2024, 4, 4),
            date(2024, 4, 5),
            date(2024, 5, 1),
            date(2024, 5, 2),
            date(2024, 5, 3),
            date(2024, 6, 10),
            date(2024, 9, 16),
            date(2024, 9, 17),
            date(2024, 10, 1),
            date(2024, 10, 2),
            date(2024, 10, 3),
            date(2024, 10, 4),
            date(2024, 10, 7),
        }
    ),
    2025: frozenset(
        {
            date(2025, 1, 1),
            date(2025, 1, 28),
            date(2025, 1, 29),
            date(2025, 1, 30),
            date(2025, 1, 31),
            date(2025, 2, 3),
            date(2025, 2, 4),
            date(2025, 4, 4),
            date(2025, 5, 1),
            date(2025, 5, 2),
            date(2025, 5, 5),
            date(2025, 6, 2),
            date(2025, 10, 1),
            date(2025, 10, 2),
            date(2025, 10, 3),
            date(2025, 10, 6),
            date(2025, 10, 7),
            date(2025, 10, 8),
        }
    ),
    2026: frozenset(
        {
            date(2026, 1, 1),
            date(2026, 1, 2),
            date(2026, 2, 16),
            date(2026, 2, 17),
            date(2026, 2, 18),
            date(2026, 2, 19),
            date(2026, 2, 20),
            date(2026, 2, 23),
            date(2026, 4, 6),
            date(2026, 5, 1),
            date(2026, 5, 4),
            date(2026, 5, 5),
            date(2026, 6, 19),
            date(2026, 9, 25),
            date(2026, 10, 1),
            date(2026, 10, 2),
            date(2026, 10, 5),
            date(2026, 10, 6),
            date(2026, 10, 7),
        }
    ),
}

SOURCE_URLS: tuple[str, ...] = (
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20231226_5733941.shtml",
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml",
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml",
)

# Known national makeup workdays that fall on weekends — exchanges stay closed.
# Explicitly listed so review can assert they are never open_days.
WEEKEND_MAKEUP_CLOSED: frozenset[date] = frozenset(
    {
        # 2024 Spring Festival makeups (Sunday workdays for offices, not exchanges)
        date(2024, 2, 4),
        date(2024, 2, 18),
        # 2024 National Day / Mid-Autumn makeups
        date(2024, 9, 14),
        date(2024, 10, 12),
        # 2025 Spring Festival makeups
        date(2025, 1, 26),
        date(2025, 2, 8),
        # 2025 National Day makeups
        date(2025, 9, 28),
        date(2025, 10, 11),
        # 2026 Spring Festival makeups
        date(2026, 2, 15),
        date(2026, 2, 28),
        # 2026 National Day / Mid-Autumn makeups
        date(2026, 9, 20),
        date(2026, 10, 10),
    }
)

REGULAR_SESSIONS: tuple[dict[str, str], ...] = (
    {"start": "09:30", "end": "11:30"},
    {"start": "13:00", "end": "15:00"},
)


def _all_closures() -> frozenset[date]:
    closed: set[date] = set()
    for year, days in WEEKDAY_CLOSURES.items():
        for day in days:
            if day.year != year:
                raise ValueError(f"closure {day.isoformat()} not in year {year}")
            if day.weekday() >= 5:
                raise ValueError(
                    f"closure {day.isoformat()} is a weekend; list weekdays only"
                )
            closed.add(day)
    return frozenset(closed)


def compute_open_days(
    *,
    coverage_from: date = COVERAGE_FROM,
    coverage_to: date = COVERAGE_TO,
    closures: frozenset[date] | None = None,
) -> list[date]:
    """Return sorted open weekdays in coverage excluding official closures."""
    closed = closures if closures is not None else _all_closures()
    opens: list[date] = []
    cursor = coverage_from
    while cursor <= coverage_to:
        if cursor.weekday() < 5 and cursor not in closed:
            opens.append(cursor)
        cursor += timedelta(days=1)
    return opens


def build_payload(*, open_days: list[date] | None = None) -> dict[str, object]:
    days = open_days if open_days is not None else compute_open_days()
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "version": CALENDAR_VERSION,
        "timezone": TIMEZONE,
        "coverage_from": COVERAGE_FROM.isoformat(),
        "coverage_to": COVERAGE_TO.isoformat(),
        "open_days": [d.isoformat() for d in days],
        "regular_sessions": [dict(s) for s in REGULAR_SESSIONS],
        "source": {
            "kind": "official_exchange_schedule",
            "note": (
                "Explicit weekday open_days for 2024-01-01..2026-12-31 from SSE "
                "official closed-day announcements. Weekends and national makeup "
                "workdays are never open. No 2027 coverage; out-of-range after "
                "2026-12-31 is explicit."
            ),
            "urls": list(SOURCE_URLS),
        },
        "generated_at": GENERATED_AT,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload["content_sha256"] = digest
    return payload


def render_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def verify_payload(payload: dict[str, object]) -> list[str]:
    """Reviewable verification checks; returns human-readable failures."""
    failures: list[str] = []
    expected = build_payload()
    if payload.get("schema_version") != SCHEMA_VERSION:
        failures.append("schema_version mismatch")
    if payload.get("version") != CALENDAR_VERSION:
        failures.append("version mismatch")
    if payload.get("coverage_from") != COVERAGE_FROM.isoformat():
        failures.append("coverage_from mismatch")
    if payload.get("coverage_to") != COVERAGE_TO.isoformat():
        failures.append("coverage_to mismatch")
    if payload.get("source", {}).get("kind") != "official_exchange_schedule":  # type: ignore[union-attr]
        failures.append("source.kind must be official_exchange_schedule")
    urls = payload.get("source", {}).get("urls")  # type: ignore[union-attr]
    if urls != list(SOURCE_URLS):
        failures.append("source.urls mismatch")
    if payload.get("open_days") != expected["open_days"]:
        failures.append("open_days does not match generator output")
    if payload.get("content_sha256") != expected["content_sha256"]:
        failures.append("content_sha256 mismatch")
    # Spot boundaries.
    open_set = set(payload.get("open_days") or [])
    must_open = {
        "2024-01-02",
        "2024-02-08",  # last open before Spring Festival
        "2024-02-19",  # first open after Spring Festival
        "2025-01-27",
        "2025-02-05",
        "2026-07-17",
        "2026-12-31",
    }
    must_closed = {
        "2024-01-01",
        "2024-02-09",
        "2024-02-04",  # weekend makeup
        "2024-02-18",  # weekend makeup
        "2025-01-01",
        "2025-01-28",
        "2025-10-01",
        "2026-01-01",
        "2026-02-16",
        "2026-10-01",
    }
    for day in must_open:
        if day not in open_set:
            failures.append(f"expected open day missing: {day}")
    for day in must_closed:
        if day in open_set:
            failures.append(f"expected closed day present in open_days: {day}")
    for day in WEEKEND_MAKEUP_CLOSED:
        iso = day.isoformat()
        if iso in open_set:
            failures.append(f"weekend makeup must stay closed: {iso}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write candidate to --output (refuses overwrite without --force)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing output file",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print candidate JSON to stdout",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify existing tracked file matches generator (exit 1 on drift)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)

    if not any((args.write, args.stdout, args.check)):
        parser.error("specify --write, --stdout, and/or --check")

    payload = build_payload()
    text = render_json(payload)

    if args.check:
        path = args.output
        if not path.is_file():
            print(f"FAIL: missing calendar file {path}", file=sys.stderr)
            return 1
        existing = json.loads(path.read_text(encoding="utf-8"))
        failures = verify_payload(existing)
        if failures:
            print("FAIL: calendar verification:", file=sys.stderr)
            for item in failures:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(
            f"OK: {path} matches generator "
            f"({len(payload['open_days'])} open_days, "
            f"sha256={payload['content_sha256'][:12]}…)"
        )

    if args.stdout:
        sys.stdout.write(text)

    if args.write:
        out = args.output
        if out.exists() and not args.force:
            print(
                f"REFUSE: {out} exists; pass --force to overwrite after review",
                file=sys.stderr,
            )
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out} ({len(payload['open_days'])} open_days)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
