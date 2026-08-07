"""Deterministic generic notification delivery receipts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class NotificationFlushDisposition(StrEnum):
    DISABLED = "DISABLED"
    NO_PENDING = "NO_PENDING"
    ATTEMPTED = "ATTEMPTED"


class NotificationSendReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delivered: bool
    retryable: bool
    provider_message_id: str | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None


class NotificationFlushReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: NotificationFlushDisposition
    checked_at: datetime
    pending_selected: int = 0
    delivered: int = 0
    retry_scheduled: int = 0
    dead_lettered: int = 0
    expired: int = 0
    error_codes: tuple[str, ...] = ()
    execution_effect: bool = False


class NotificationStatusReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    provider: str
    configured: bool
    pending: int
    delivered: int
    dead_letter: int
    expired: int
    last_delivered_at: datetime | None
    execution_effect: bool = False


class NotificationEnqueueReceipt(BaseModel):
    """Secret-safe operational receipt for a manually enqueued message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    notification_id: str
    source_type: str
    source_id: str
    channel: str
    title: str
    status: str
    expires_at: datetime | None
    execution_effect: bool = False
