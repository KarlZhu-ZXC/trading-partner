"""Compatibility import for the generic outbound notification port."""

from application.ports.notification_sender import NotificationSender

MonitorNotificationSender = NotificationSender

__all__ = ["MonitorNotificationSender"]
