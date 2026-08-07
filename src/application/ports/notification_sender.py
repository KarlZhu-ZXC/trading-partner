"""Outbound generic notification provider port."""

from typing import Protocol

from application.dto.notifications import NotificationSendReceipt
from domain.notifications.models import NotificationOutboxEntry


class NotificationSender(Protocol):
    async def send(self, notification: NotificationOutboxEntry) -> NotificationSendReceipt: ...
