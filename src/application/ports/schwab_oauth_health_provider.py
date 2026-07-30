"""Application port for safe Schwab OAuth token-age inspection."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from application.dto.schwab_oauth import SchwabOAuthHealthDTO


class SchwabOAuthHealthProvider(Protocol):
    def inspect(self, *, now: datetime) -> SchwabOAuthHealthDTO:
        """Return safe metadata without refreshing a token or opening a browser."""
