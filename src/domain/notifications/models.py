"""Immutable generic notification messages and durable outbox entries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.notifications.enums import (
    NotificationChannel,
    NotificationSourceType,
    NotificationStatus,
)

_NOTIFICATION_ID_PATTERN = re.compile(
    r"^notification_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _text(value: object, field: str, maximum: int, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise DataContractError(f"{field} must be bounded text")
    if not allow_blank and not value.strip():
        raise DataContractError(f"{field} must not be blank")
    return value.strip() if field not in {"body", "authorization_note"} else value


def _aware(value: datetime, field: str) -> None:
    require_aware_datetime(value, field_name=field)


def _source(
    source_type: NotificationSourceType,
    source_id: str,
) -> None:
    if not isinstance(source_type, NotificationSourceType):
        raise DataContractError("notification source_type is invalid")
    _text(source_id, "source_id", 200)


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """A message ready to be inserted into the durable notification outbox."""

    notification_id: str
    source_type: NotificationSourceType
    source_id: str
    channel: NotificationChannel
    title: str
    body: str
    created_at: datetime
    idempotency_key: str | None = None
    confirmed_by: str | None = None
    authorization_note: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.notification_id, "notification_id", 128)
        _source(self.source_type, self.source_id)
        if not isinstance(self.channel, NotificationChannel):
            raise DataContractError("notification channel is invalid")
        _text(self.title, "title", 200)
        # The service performs the Telegram escaped-size check. Keep the domain
        # bound large enough for plain text while rejecting unbounded payloads.
        _text(self.body, "body", 4096)
        _aware(self.created_at, "created_at")
        if self.idempotency_key is not None:
            _text(self.idempotency_key, "idempotency_key", 200)
        if self.confirmed_by is not None and self.confirmed_by not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by is invalid")
        if self.authorization_note is not None:
            _text(self.authorization_note, "authorization_note", 2000)
        if self.source_type is not NotificationSourceType.MANUAL and (
            self.confirmed_by is not None or self.authorization_note is not None
        ):
            raise DataContractError("only MANUAL notifications may carry authorization")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.created_at:
                raise DataContractError("notification expires_at must follow created_at")
        if self.source_type is NotificationSourceType.MANUAL:
            if _NOTIFICATION_ID_PATTERN.fullmatch(self.notification_id) is None:
                raise DataContractError("MANUAL notification_id must be a notification UUID7")
            if self.idempotency_key is None:
                raise DataContractError("MANUAL notification requires idempotency_key")
            if self.source_id != self.idempotency_key:
                raise DataContractError("MANUAL source_id must equal idempotency_key")
            if self.confirmed_by is None:
                raise DataContractError("MANUAL notification requires confirmed_by")
            if self.authorization_note is None:
                raise DataContractError("MANUAL notification requires authorization_note")
            if self.expires_at is None:
                raise DataContractError("MANUAL notification requires expires_at")

    # Compatibility accessors keep existing Monitor consumers readable while
    # the persisted contract uses one closed source_type/source_id pair.
    @property
    def source_event_id(self) -> str | None:
        return self.source_id if self.source_type is NotificationSourceType.MONITOR_EVENT else None

    @property
    def source_run_id(self) -> str | None:
        return self.source_id if self.source_type is NotificationSourceType.MONITOR_RUN else None


@dataclass(frozen=True, slots=True)
class NotificationOutboxEntry:
    """Durable notification state consumed by a provider adapter."""

    notification_id: str
    source_type: NotificationSourceType
    source_id: str
    channel: NotificationChannel
    title: str
    body: str
    status: NotificationStatus
    attempt_count: int
    next_attempt_at: datetime
    created_at: datetime
    last_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    provider_message_id: str | None = None
    last_error_code: str | None = None
    idempotency_key: str | None = None
    confirmed_by: str | None = None
    authorization_note: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.notification_id, "notification_id", 128)
        _source(self.source_type, self.source_id)
        if not isinstance(self.channel, NotificationChannel):
            raise DataContractError("notification channel is invalid")
        if not isinstance(self.status, NotificationStatus):
            raise DataContractError("notification status is invalid")
        _text(self.title, "title", 200)
        _text(self.body, "body", 4096)
        if type(self.attempt_count) is not int or self.attempt_count < 0:
            raise DataContractError("notification attempt_count must be nonnegative")
        _aware(self.next_attempt_at, "next_attempt_at")
        _aware(self.created_at, "created_at")
        for field, value in (
            ("last_attempt_at", self.last_attempt_at),
            ("delivered_at", self.delivered_at),
            ("expires_at", self.expires_at),
        ):
            if value is not None:
                _aware(value, field)
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise DataContractError("notification expires_at must follow created_at")
        if self.provider_message_id is not None:
            _text(self.provider_message_id, "provider_message_id", 128)
        if self.last_error_code is not None:
            _text(self.last_error_code, "last_error_code", 128)
        if self.idempotency_key is not None:
            _text(self.idempotency_key, "idempotency_key", 200)
        if self.confirmed_by is not None and self.confirmed_by not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by is invalid")
        if self.authorization_note is not None:
            _text(self.authorization_note, "authorization_note", 2000)
        if self.source_type is not NotificationSourceType.MANUAL and (
            self.confirmed_by is not None or self.authorization_note is not None
        ):
            raise DataContractError("only MANUAL notifications may carry authorization")
        if (
            self.source_type is NotificationSourceType.MANUAL
            and _NOTIFICATION_ID_PATTERN.fullmatch(self.notification_id) is None
        ):
            raise DataContractError("MANUAL notification_id must be a notification UUID7")
        if self.source_type is NotificationSourceType.MANUAL:
            if self.idempotency_key is None:
                raise DataContractError("MANUAL notification requires idempotency_key")
            if self.source_id != self.idempotency_key:
                raise DataContractError("MANUAL source_id must equal idempotency_key")
            if self.confirmed_by is None:
                raise DataContractError("MANUAL notification requires confirmed_by")
            if self.authorization_note is None:
                raise DataContractError("MANUAL notification requires authorization_note")
            if self.expires_at is None:
                raise DataContractError("MANUAL notification requires expires_at")

    # Compatibility accessors keep existing Monitor consumers readable while
    # the persisted contract uses one closed source_type/source_id pair.
    @property
    def source_event_id(self) -> str | None:
        return self.source_id if self.source_type is NotificationSourceType.MONITOR_EVENT else None

    @property
    def source_run_id(self) -> str | None:
        return self.source_id if self.source_type is NotificationSourceType.MONITOR_RUN else None
