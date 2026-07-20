from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.providers.moomoo_rate_limiter import (
    MoomooOpenDOperation,
    MoomooOpenDRateLimiter,
    SlidingWindowPolicy,
)


def _limiter(
    path: Path,
    now: list[float],
    *,
    operation: MoomooOpenDOperation,
    scoped: bool = False,
) -> MoomooOpenDRateLimiter:
    return MoomooOpenDRateLimiter(
        path,
        policies={operation: SlidingWindowPolicy(2, 30.0, scoped=scoped)},
        time_source=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )


def test_instances_share_one_sliding_window(tmp_path: Path) -> None:
    path = tmp_path / "moomoo.log"
    now = [100.0]
    operation = MoomooOpenDOperation.WATCHLIST_MEMBERS
    first = _limiter(path, now, operation=operation)
    second = _limiter(path, now, operation=operation)

    first.wait(operation)
    second.wait(operation)
    first.wait(operation)

    assert now[0] == pytest.approx(130.001)
    assert path.stat().st_mode & 0o777 == 0o600


def test_operation_buckets_are_independent(tmp_path: Path) -> None:
    path = tmp_path / "moomoo.log"
    now = [100.0]
    policies = {
        MoomooOpenDOperation.WATCHLIST_GROUPS: SlidingWindowPolicy(1, 30.0),
        MoomooOpenDOperation.WATCHLIST_MEMBERS: SlidingWindowPolicy(1, 30.0),
    }
    limiter = MoomooOpenDRateLimiter(
        path,
        policies=policies,
        time_source=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    limiter.wait(MoomooOpenDOperation.WATCHLIST_GROUPS)
    limiter.wait(MoomooOpenDOperation.WATCHLIST_MEMBERS)

    assert now[0] == 100.0


def test_account_scopes_are_independent_and_required(tmp_path: Path) -> None:
    path = tmp_path / "moomoo.log"
    now = [100.0]
    operation = MoomooOpenDOperation.ACCOUNT_POSITIONS
    limiter = _limiter(path, now, operation=operation, scoped=True)

    limiter.wait(operation, scope="moomoo_hash_a")
    limiter.wait(operation, scope="moomoo_hash_a")
    limiter.wait(operation, scope="moomoo_hash_b")
    assert now[0] == 100.0

    with pytest.raises(ValueError, match="scope is required"):
        limiter.wait(operation)


def test_malformed_reservation_line_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "moomoo.log"
    path.write_text("partial-line\nwatchlist_members\tnot-a-number\n", encoding="ascii")
    now = [100.0]
    operation = MoomooOpenDOperation.WATCHLIST_MEMBERS

    _limiter(path, now, operation=operation).wait(operation)

    assert now[0] == 100.0
