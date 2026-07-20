"""as_of historical filtering primitives (Phase 1D D7).

``ensure_not_after_as_of`` rejects future events relative to a research cutoff.
``filter_events_by_as_of`` drops (or retains missing) timestamps for pipelines.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


def ensure_not_after_as_of(event_time: datetime, as_of: datetime) -> None:
    """Require aware times; ``event_time > as_of`` is a contract error.

    Equality is allowed. Does not echo timestamps in details.
    """
    require_aware_datetime(event_time, field_name="event_time")
    require_aware_datetime(as_of, field_name="as_of")
    if event_time > as_of:
        raise DataContractError(
            "event_time must not be after as_of",
            details={"field": "event_time", "rule": "after_as_of"},
        )


def filter_events_by_as_of[T](
    events: Sequence[T],
    *,
    time_getter: Callable[[T], datetime | None],
    as_of: datetime,
    drop_missing_timestamp: bool,
) -> tuple[T, ...]:
    """Filter events so retained timestamps are aware and ``<= as_of``.

    Preserves input order. Invokes ``time_getter`` exactly once per element.
    Getter exceptions propagate unchanged (not wrapped as DataContractError).
    """
    require_aware_datetime(as_of, field_name="as_of")

    if type(drop_missing_timestamp) is not bool:
        raise DataContractError(
            "drop_missing_timestamp must be a bool",
            details={
                "field": "drop_missing_timestamp",
                "rule": "type",
                "type": type(drop_missing_timestamp).__name__,
            },
        )

    if not callable(time_getter):
        raise DataContractError(
            "time_getter must be callable",
            details={
                "field": "time_getter",
                "rule": "type",
                "type": type(time_getter).__name__,
            },
        )

    # Reject non-sequences and string/bytes (iterable but not event sequences).
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise DataContractError(
            "events must be a sequence",
            details={
                "field": "events",
                "rule": "type",
                "type": type(events).__name__,
            },
        )

    kept: list[T] = []
    for event in events:
        # Getter exceptions must propagate as-is (design §7.5).
        stamp = time_getter(event)
        if stamp is None:
            if not drop_missing_timestamp:
                kept.append(event)
            continue
        if not isinstance(stamp, datetime):
            raise DataContractError(
                "event timestamp must be a datetime or None",
                details={
                    "field": "event_time",
                    "rule": "type",
                    "type": type(stamp).__name__,
                },
            )
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise DataContractError(
                "event timestamp must be timezone-aware",
                details={"field": "event_time", "rule": "timezone_aware"},
            )
        if stamp <= as_of:
            kept.append(event)
        # stamp > as_of → drop (filter path; ensure_* raises instead)
    return tuple(kept)
