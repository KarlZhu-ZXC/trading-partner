"""Canonical UTC windows for the weekly Trade Retro workflow."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta


def trade_retro_weekly_windows(
    now: datetime | None = None,
) -> tuple[datetime, datetime, datetime, datetime]:
    """Return the completed Monday-Saturday audit and next snapshot windows."""

    observed = (now or datetime.now(UTC)).astimezone(UTC)
    current_monday = datetime.combine(
        observed.date() - timedelta(days=observed.weekday()),
        time.min,
        tzinfo=UTC,
    )
    current_end = current_monday + timedelta(days=5)
    if observed >= current_end:
        review_start, review_end = current_monday, current_end
    else:
        review_start = current_monday - timedelta(days=7)
        review_end = current_end - timedelta(days=7)
    prepare_start = current_monday + timedelta(days=7)
    prepare_end = prepare_start + timedelta(days=5)
    return review_start, review_end, prepare_start, prepare_end
