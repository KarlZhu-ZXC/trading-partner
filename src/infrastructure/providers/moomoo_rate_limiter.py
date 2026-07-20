"""Cross-process sliding-window admission control for Moomoo OpenD calls."""

from __future__ import annotations

import fcntl
import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TextIO


class MoomooOpenDOperation(StrEnum):
    WATCHLIST_GROUPS = "watchlist_groups"
    WATCHLIST_MEMBERS = "watchlist_members"
    WATCHLIST_MODIFY = "watchlist_modify"
    ACCOUNT_FUNDS = "account_funds"
    ACCOUNT_POSITIONS = "account_positions"
    ACCOUNT_ORDERS = "account_orders"
    ACCOUNT_HISTORY_DEALS = "account_history_deals"


class OpenDRequestLimiter(Protocol):
    def wait(
        self,
        operation: MoomooOpenDOperation,
        *,
        scope: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SlidingWindowPolicy:
    limit_count: int
    window_seconds: float
    scoped: bool = False

    def __post_init__(self) -> None:
        if self.limit_count <= 0:
            raise ValueError("limit_count must be positive")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive and finite")


DEFAULT_MOOMOO_POLICIES: Mapping[MoomooOpenDOperation, SlidingWindowPolicy] = {
    MoomooOpenDOperation.WATCHLIST_GROUPS: SlidingWindowPolicy(10, 30.0),
    MoomooOpenDOperation.WATCHLIST_MEMBERS: SlidingWindowPolicy(10, 30.0),
    MoomooOpenDOperation.WATCHLIST_MODIFY: SlidingWindowPolicy(10, 30.0),
    MoomooOpenDOperation.ACCOUNT_FUNDS: SlidingWindowPolicy(10, 30.0, scoped=True),
    MoomooOpenDOperation.ACCOUNT_POSITIONS: SlidingWindowPolicy(10, 30.0, scoped=True),
    MoomooOpenDOperation.ACCOUNT_ORDERS: SlidingWindowPolicy(10, 30.0, scoped=True),
    MoomooOpenDOperation.ACCOUNT_HISTORY_DEALS: SlidingWindowPolicy(
        10, 30.0, scoped=True
    ),
}


class MoomooOpenDRateLimiter:
    """Reserve OpenD calls in one append-only log shared by local processes."""

    def __init__(
        self,
        path: Path,
        *,
        policies: Mapping[MoomooOpenDOperation, SlidingWindowPolicy] | None = None,
        time_source: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path.resolve()
        self._policies = dict(policies or DEFAULT_MOOMOO_POLICIES)
        self._time_source = time_source
        self._sleep = sleep
        self._thread_lock = threading.Lock()

    def wait(
        self,
        operation: MoomooOpenDOperation,
        *,
        scope: str | None = None,
    ) -> None:
        policy = self._policies[operation]
        bucket = self._bucket(operation, scope=scope, scoped=policy.scoped)
        while True:
            now = self._time_source()
            if not math.isfinite(now):
                raise ValueError("time source must return a finite timestamp")
            wait_for = self._reserve_or_delay(bucket, policy=policy, now=now)
            if wait_for is None:
                return
            self._sleep(max(wait_for, 0.0) + 0.001)

    @staticmethod
    def _bucket(
        operation: MoomooOpenDOperation,
        *,
        scope: str | None,
        scoped: bool,
    ) -> str:
        if scoped and not scope:
            raise ValueError(f"scope is required for {operation.value}")
        if scope is not None and any(character in scope for character in "\t\r\n"):
            raise ValueError("scope contains an invalid control character")
        return operation.value if not scoped else f"{operation.value}:{scope}"

    def _reserve_or_delay(
        self,
        bucket: str,
        *,
        policy: SlidingWindowPolicy,
        now: float,
    ) -> float | None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            descriptor = os.open(
                self._path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(descriptor, "a+", encoding="ascii") as handle:
                os.chmod(self._path, 0o600)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    recent = self._recent_reservations(
                        handle,
                        bucket=bucket,
                        now=now,
                        window_seconds=policy.window_seconds,
                    )
                    if len(recent) < policy.limit_count:
                        handle.seek(0, os.SEEK_END)
                        handle.write(f"{bucket}\t{now:.6f}\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                        return None
                    return min(recent) + policy.window_seconds - now
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _recent_reservations(
        handle: TextIO,
        *,
        bucket: str,
        now: float,
        window_seconds: float,
    ) -> list[float]:
        handle.seek(0)
        recent: list[float] = []
        for raw_line in handle.read().splitlines():
            try:
                recorded_bucket, raw_timestamp = raw_line.split("\t", 1)
                timestamp = float(raw_timestamp)
            except (TypeError, ValueError):
                continue
            if (
                recorded_bucket == bucket
                and math.isfinite(timestamp)
                and now - timestamp < window_seconds
            ):
                recent.append(timestamp)
        return recent
