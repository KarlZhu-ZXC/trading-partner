"""Compatibility import for the generic notification application service."""

from application.services.notification_service import NotificationService

MonitorNotificationService = NotificationService

__all__ = ["MonitorNotificationService"]
