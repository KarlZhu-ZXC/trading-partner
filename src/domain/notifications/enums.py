"""Closed vocabulary for deterministic outbound notifications."""

from enum import StrEnum


class NotificationSourceType(StrEnum):
    """Durable producer/source identities supported by the outbox."""

    MONITOR_EVENT = "MONITOR_EVENT"
    MONITOR_RUN = "MONITOR_RUN"
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


class NotificationChannel(StrEnum):
    TELEGRAM = "TELEGRAM"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"
    EXPIRED = "EXPIRED"


# Short alias used by integrations that refer to the discriminator as simply
# ``source`` rather than ``source_type``.
NotificationSource = NotificationSourceType
