"""Secret-safe ordered key selection for Alpha Vantage adapters."""

from __future__ import annotations

from collections.abc import Sequence
from threading import Lock
from typing import Literal


class AlphaVantageKeyPool:
    """Keep one active key and expose each configured key at most once per request."""

    __slots__ = ("_active_index", "_keys", "_lock")

    def __init__(self, keys: Sequence[str] = ()) -> None:
        normalized: list[str] = []
        for raw in keys:
            key = raw.strip()
            if key and key not in normalized:
                normalized.append(key)
        self._keys = tuple(normalized)
        self._active_index = 0
        self._lock = Lock()

    @property
    def size(self) -> int:
        return len(self._keys)

    def is_configured(self) -> bool:
        return bool(self._keys)

    def ordered_candidates(self) -> tuple[tuple[int, str], ...]:
        """Return the active key first, followed by the remaining keys in order."""
        with self._lock:
            start = self._active_index
        return tuple(
            (index, self._keys[index]) for index in ((*range(start, self.size), *range(0, start)))
        )

    def mark_success(self, index: int) -> None:
        if index < 0 or index >= self.size:
            raise IndexError("Alpha Vantage key index is out of range")
        with self._lock:
            self._active_index = index

    def __repr__(self) -> str:
        return f"AlphaVantageKeyPool(size={self.size})"


def classify_alpha_vantage_notice(
    notice: str,
) -> Literal["rate_limit", "api_key", "notice"]:
    """Classify provider prose without returning or persisting the raw notice."""
    lowered = notice.casefold()
    rate_limit_markers = (
        "rate limit",
        "requests per day",
        "call frequency",
        "premium",
    )
    if any(marker in lowered for marker in rate_limit_markers):
        return "rate_limit"
    if "api key" in lowered or "apikey" in lowered:
        return "api_key"
    return "notice"
