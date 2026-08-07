"""Application port for durable, generic notification outbox operations."""

from datetime import datetime
from typing import Protocol

from domain.notifications.enums import NotificationChannel, NotificationStatus
from domain.notifications.models import NotificationMessage, NotificationOutboxEntry


class NotificationOutboxRepository(Protocol):
    def enqueue(self, message: NotificationMessage) -> NotificationOutboxEntry: ...

    def get_notification_by_idempotency_key(self, key: str) -> NotificationOutboxEntry | None: ...

    def list_due(
        self,
        channel: NotificationChannel,
        as_of: datetime,
        limit: int,
    ) -> tuple[NotificationOutboxEntry, ...]: ...

    def list_recent(
        self,
        channel: NotificationChannel,
        limit: int,
    ) -> tuple[NotificationOutboxEntry, ...]: ...

    def record_attempt(
        self,
        notification_id: str,
        channel: NotificationChannel,
        *,
        status: NotificationStatus,
        attempted_at: datetime,
        next_attempt_at: datetime,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> NotificationOutboxEntry: ...

    def counts(self, channel: NotificationChannel) -> dict[NotificationStatus, int]: ...

    def last_delivery_at(self, channel: NotificationChannel) -> datetime | None: ...
