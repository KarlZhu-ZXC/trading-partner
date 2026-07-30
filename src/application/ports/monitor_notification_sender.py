"""Outbound Monitor notification provider port."""

from typing import Protocol

from application.dto.monitor_notifications import NotificationSendReceipt
from domain.monitoring.models import MonitorNotificationOutboxEntry


class MonitorNotificationSender(Protocol):
    async def send(
        self, notification: MonitorNotificationOutboxEntry
    ) -> NotificationSendReceipt: ...
