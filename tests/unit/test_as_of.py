"""Phase 1D D7: ensure_not_after_as_of and filter_events_by_as_of."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from domain.common.as_of import ensure_not_after_as_of, filter_events_by_as_of
from domain.common.errors import DataContractError

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Event:
    name: str
    ts: datetime | None


def test_ensure_allows_equal_and_before() -> None:
    ensure_not_after_as_of(AS_OF, AS_OF)
    ensure_not_after_as_of(AS_OF - timedelta(seconds=1), AS_OF)


def test_ensure_rejects_after_as_of() -> None:
    with pytest.raises(DataContractError) as exc_info:
        ensure_not_after_as_of(AS_OF + timedelta(microseconds=1), AS_OF)
    err = exc_info.value
    assert err.details.get("rule") == "after_as_of"
    assert err.details.get("field") == "event_time"
    assert "2026" not in repr(err.details)


def test_ensure_rejects_naive() -> None:
    naive = datetime(2026, 7, 16, 15, 0)
    with pytest.raises(DataContractError) as exc_info:
        ensure_not_after_as_of(naive, AS_OF)
    assert exc_info.value.details.get("field") == "event_time"

    with pytest.raises(DataContractError) as exc_info:
        ensure_not_after_as_of(AS_OF, naive)
    assert exc_info.value.details.get("field") == "as_of"


def test_filter_preserves_order_and_drops_after() -> None:
    events = (
        _Event("a", AS_OF - timedelta(hours=2)),
        _Event("b", AS_OF + timedelta(seconds=1)),
        _Event("c", AS_OF),
        _Event("d", AS_OF - timedelta(minutes=1)),
    )
    result = filter_events_by_as_of(
        events,
        time_getter=lambda e: e.ts,
        as_of=AS_OF,
        drop_missing_timestamp=True,
    )
    assert [e.name for e in result] == ["a", "c", "d"]


def test_filter_time_getter_called_once_per_element() -> None:
    events = (_Event("a", AS_OF), _Event("b", AS_OF - timedelta(hours=1)))
    calls: list[str] = []

    def getter(e: _Event) -> datetime | None:
        calls.append(e.name)
        return e.ts

    filter_events_by_as_of(
        events,
        time_getter=getter,
        as_of=AS_OF,
        drop_missing_timestamp=True,
    )
    assert calls == ["a", "b"]


def test_filter_missing_timestamp_drop_and_keep() -> None:
    events = (
        _Event("keep_ts", AS_OF),
        _Event("missing", None),
        _Event("after", AS_OF + timedelta(seconds=1)),
    )
    dropped = filter_events_by_as_of(
        events,
        time_getter=lambda e: e.ts,
        as_of=AS_OF,
        drop_missing_timestamp=True,
    )
    assert [e.name for e in dropped] == ["keep_ts"]

    kept = filter_events_by_as_of(
        events,
        time_getter=lambda e: e.ts,
        as_of=AS_OF,
        drop_missing_timestamp=False,
    )
    assert [e.name for e in kept] == ["keep_ts", "missing"]


def test_filter_rejects_naive_timestamp() -> None:
    events = (_Event("naive", datetime(2026, 7, 16, 12, 0)),)
    with pytest.raises(DataContractError) as exc_info:
        filter_events_by_as_of(
            events,
            time_getter=lambda e: e.ts,
            as_of=AS_OF,
            drop_missing_timestamp=True,
        )
    assert exc_info.value.details.get("field") == "event_time"
    assert exc_info.value.details.get("rule") == "timezone_aware"


def test_filter_rejects_non_datetime_timestamp() -> None:
    events = ("x",)

    def getter(_e: str) -> datetime | None:
        return "not-a-datetime"  # type: ignore[return-value]

    with pytest.raises(DataContractError) as exc_info:
        filter_events_by_as_of(
            events,
            time_getter=getter,
            as_of=AS_OF,
            drop_missing_timestamp=True,
        )
    assert exc_info.value.details.get("rule") == "type"
    assert "not-a-datetime" not in repr(exc_info.value.details)


def test_filter_getter_exception_propagates_unchanged() -> None:
    class Boom(RuntimeError):
        pass

    def getter(_e: str) -> datetime | None:
        raise Boom("getter failed with secret=sk-leak")

    with pytest.raises(Boom, match="getter failed") as exc_info:
        filter_events_by_as_of(
            ("a",),
            time_getter=getter,
            as_of=AS_OF,
            drop_missing_timestamp=True,
        )
    assert not isinstance(exc_info.value, DataContractError)


def test_filter_rejects_bool_drop_flag() -> None:
    with pytest.raises(DataContractError) as exc_info:
        filter_events_by_as_of(
            (),
            time_getter=lambda _e: None,
            as_of=AS_OF,
            drop_missing_timestamp=1,  # type: ignore[arg-type]
        )
    assert exc_info.value.details.get("field") == "drop_missing_timestamp"


def test_filter_rejects_naive_as_of() -> None:
    with pytest.raises(DataContractError) as exc_info:
        filter_events_by_as_of(
            (),
            time_getter=lambda _e: None,
            as_of=datetime(2026, 7, 16, 15, 0),
            drop_missing_timestamp=True,
        )
    assert exc_info.value.details.get("field") == "as_of"
