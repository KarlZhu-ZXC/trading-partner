"""Console/CLI DTOs for deterministic Catalyst Agenda notifications."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgendaNotificationPreviewDTO(_DTO):
    source_id: str
    title: str
    body: str
    generated_at: datetime
    expires_at: datetime
    window_days: int = Field(ge=1, le=30)
    upcoming_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0)
    coverage_gap_count: int = Field(ge=0)
    limitation_codes: tuple[str, ...] = ()


class AgendaNotificationEnqueueDTO(_DTO):
    preview: AgendaNotificationPreviewDTO
    notification_id: str
    status: str


class AgendaChangeNotificationDTO(_DTO):
    agenda_item_id: str
    version: int = Field(ge=2)
    change_type: str
    notification_id: str
    status: str


class AgendaNotificationBatchDTO(_DTO):
    daily: AgendaNotificationEnqueueDTO
    changes: tuple[AgendaChangeNotificationDTO, ...]
