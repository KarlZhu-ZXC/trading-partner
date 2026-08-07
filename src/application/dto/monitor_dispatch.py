"""Deterministic due-dispatch result for unified Monitor schedules."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from application.dto.monitoring import MonitorRunDTO
from application.dto.notifications import NotificationFlushReceipt


class MonitorDispatchDisposition(StrEnum):
    EXECUTED = "EXECUTED"
    NO_DUE_MONITORS = "NO_DUE_MONITORS"


class MonitorDispatchDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: MonitorDispatchDisposition
    checked_at: datetime
    due_monitor_ids: tuple[str, ...] = ()
    next_due_at: datetime | None = None
    run: MonitorRunDTO | None = None
    runs: tuple[MonitorRunDTO, ...] = ()
    notification_delivery: NotificationFlushReceipt | None = None
    execution_effect: bool = False
