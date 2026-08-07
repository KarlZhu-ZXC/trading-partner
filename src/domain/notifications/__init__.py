"""Generic outbound notification domain types."""

from domain.notifications.enums import (
    NotificationChannel,
    NotificationSource,
    NotificationSourceType,
    NotificationStatus,
)
from domain.notifications.models import NotificationMessage, NotificationOutboxEntry
from domain.notifications.rendering import (
    TELEGRAM_MAX_TEXT_LENGTH,
    render_plain_text_html,
    rendered_plain_text_length,
)

__all__ = [
    "NotificationChannel",
    "NotificationSourceType",
    "NotificationSource",
    "NotificationStatus",
    "NotificationMessage",
    "NotificationOutboxEntry",
    "TELEGRAM_MAX_TEXT_LENGTH",
    "render_plain_text_html",
    "rendered_plain_text_length",
]
