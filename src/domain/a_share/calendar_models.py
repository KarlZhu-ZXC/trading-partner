"""A-share trading-session window domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.a_share.model_validation import _require_enum
from domain.common.enums import TradingSession
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# ---------------------------------------------------------------------------
# Calendar window (port return type)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradingSessionWindow:
    session: TradingSession
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        _require_enum(self.session, TradingSession, field="session")
        require_aware_datetime(self.start_at, field_name="start_at")
        require_aware_datetime(self.end_at, field_name="end_at")
        if self.end_at <= self.start_at:
            raise DataContractError(
                "end_at must be > start_at",
                details={"field": "end_at", "rule": "range_order"},
            )

