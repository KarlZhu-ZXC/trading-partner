"""Persistence port for Reddit RSS cache and provider-wide cooldown state."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from application.dto.reddit_state import RedditSampleCacheEntry


class RedditStateStore(Protocol):
    def get_samples(self, instrument_id: str, config_key: str) -> RedditSampleCacheEntry | None: ...

    def set_samples(self, entry: RedditSampleCacheEntry) -> None: ...

    def get_cooldown_until(self) -> datetime | None: ...

    def set_cooldown_until(self, until: datetime, *, updated_at: datetime) -> None: ...
