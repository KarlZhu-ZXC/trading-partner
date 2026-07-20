"""Clock port for injectable time sources."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timezone-aware datetime (UTC for system clock)."""
        ...
