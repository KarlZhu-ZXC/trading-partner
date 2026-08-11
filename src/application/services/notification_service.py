"""Durable, retryable generic notification delivery without an LLM."""

from __future__ import annotations

from datetime import datetime, timedelta

from uuid6 import uuid7

from application.dto.notifications import (
    NotificationEnqueueReceipt,
    NotificationFlushDisposition,
    NotificationFlushReceipt,
    NotificationSendReceipt,
    NotificationStatusReceipt,
)
from application.ports.clock import Clock
from application.ports.notification_outbox_repository import NotificationOutboxRepository
from application.ports.notification_sender import NotificationSender
from domain.common.errors import DataContractError, IdempotencyConflict
from domain.notifications.enums import (
    NotificationChannel,
    NotificationSourceType,
    NotificationStatus,
)
from domain.notifications.models import NotificationMessage, NotificationOutboxEntry
from domain.notifications.rendering import (
    TELEGRAM_MAX_TEXT_LENGTH,
    rendered_plain_text_length,
)

_RETRY_DELAYS_SECONDS = (60, 300, 900, 3600, 14400)


class NotificationService:
    """Application service shared by Monitor and explicitly authorized manual producers."""

    def __init__(
        self,
        repository: NotificationOutboxRepository,
        sender: NotificationSender | None,
        clock: Clock,
        *,
        enabled: bool,
        configured: bool,
        max_attempts: int = 5,
        ttl_hours: int = 24,
        batch_size: int = 20,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._clock = clock
        self._enabled = enabled
        self._configured = configured
        self._max_attempts = max_attempts
        self._ttl = timedelta(hours=ttl_hours)
        self._batch_size = batch_size

    async def enqueue_text(
        self,
        title: str,
        body: str,
        *,
        idempotency_key: str,
        confirmed_by: str,
        authorization_note: str,
        expires_at: datetime | None = None,
    ) -> NotificationOutboxEntry:
        """Persist an explicitly authorized plain-text MANUAL notification.

        The idempotency key is the stable MANUAL source identity. Replays with
        an identical payload return the original durable entry; reusing the key
        for a different payload is rejected.
        """

        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise DataContractError("title must be non-blank and at most 200 characters")
        if not isinstance(body, str) or not body.strip() or len(body) > TELEGRAM_MAX_TEXT_LENGTH:
            raise DataContractError("body must be non-blank and at most 4096 characters")
        key = _bounded_text(idempotency_key, "idempotency_key", 200)
        actor = confirmed_by.strip() if isinstance(confirmed_by, str) else ""
        if actor not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by must be user or external_agent")
        note = _bounded_text(authorization_note, "authorization_note", 2000)
        now = self._clock.now()
        explicit_expiry = expires_at is not None
        expiry = expires_at if expires_at is not None else now + self._ttl
        if expiry <= now:
            raise DataContractError("expires_at must follow enqueue time")
        # Telegram HTML escaping expands the payload. Leave no way for a
        # provider adapter to receive a body that Telegram will reject.
        if rendered_plain_text_length(title.strip(), body) > TELEGRAM_MAX_TEXT_LENGTH:
            raise DataContractError("escaped Telegram title and body exceed 4096 characters")

        existing = self._get_by_idempotency_key(key)
        if existing is not None:
            if (
                existing.source_type is not NotificationSourceType.MANUAL
                or existing.source_id != key
                or existing.idempotency_key != key
                or existing.title != title.strip()
                or existing.body != body
                or existing.confirmed_by != actor
                or existing.authorization_note != note
                or (explicit_expiry and existing.expires_at != expiry)
            ):
                raise IdempotencyConflict(
                    "idempotency_key already belongs to a different notification"
                )
            return existing

        message = NotificationMessage(
            notification_id=_notification_id(key),
            source_type=NotificationSourceType.MANUAL,
            source_id=key,
            channel=NotificationChannel.TELEGRAM,
            title=title.strip(),
            body=body,
            created_at=now,
            idempotency_key=key,
            confirmed_by=actor,
            authorization_note=note,
            expires_at=expiry,
        )
        try:
            return self._enqueue(message)
        except Exception as exc:  # pragma: no cover - race fallback is DB-specific
            replay = self._get_by_idempotency_key(key)
            if replay is not None:
                if (
                    replay.source_type is NotificationSourceType.MANUAL
                    and replay.source_id == key
                    and replay.idempotency_key == key
                    and replay.title == message.title
                    and replay.body == message.body
                    and replay.confirmed_by == message.confirmed_by
                    and replay.authorization_note == message.authorization_note
                    and (not explicit_expiry or replay.expires_at == message.expires_at)
                ):
                    return replay
                raise IdempotencyConflict(
                    "idempotency_key already belongs to a different notification"
                ) from exc
            raise exc

    async def enqueue_system_text(
        self,
        *,
        source_id: str,
        title: str,
        body: str,
        idempotency_key: str,
        expires_at: datetime,
    ) -> NotificationOutboxEntry:
        """Persist one deterministic internal SYSTEM notification.

        SYSTEM producers are intentionally separate from explicitly authorized
        MANUAL writes: they carry a stable producer identity but never impersonate
        a user or retain an authorization note.
        """

        source = _bounded_text(source_id, "source_id", 200)
        clean_title = _bounded_text(title, "title", 200)
        if not isinstance(body, str) or not body.strip() or len(body) > TELEGRAM_MAX_TEXT_LENGTH:
            raise DataContractError("body must be non-blank and at most 4096 characters")
        key = _bounded_text(idempotency_key, "idempotency_key", 200)
        now = self._clock.now()
        if expires_at <= now:
            raise DataContractError("expires_at must follow enqueue time")
        if rendered_plain_text_length(clean_title, body) > TELEGRAM_MAX_TEXT_LENGTH:
            raise DataContractError("escaped Telegram title and body exceed 4096 characters")

        existing = self._get_by_idempotency_key(key)
        if existing is not None:
            if not _same_system_notification(
                existing,
                source_id=source,
                title=clean_title,
                body=body,
                idempotency_key=key,
                expires_at=expires_at,
            ):
                raise IdempotencyConflict(
                    "idempotency_key already belongs to a different notification"
                )
            return existing

        message = NotificationMessage(
            notification_id=_notification_id(key),
            source_type=NotificationSourceType.SYSTEM,
            source_id=source,
            channel=NotificationChannel.TELEGRAM,
            title=clean_title,
            body=body,
            created_at=now,
            idempotency_key=key,
            expires_at=expires_at,
        )
        try:
            return self._enqueue(message)
        except Exception as exc:  # pragma: no cover - race fallback is DB-specific
            replay = self._get_by_idempotency_key(key)
            if replay is not None:
                if _same_system_notification(
                    replay,
                    source_id=source,
                    title=clean_title,
                    body=body,
                    idempotency_key=key,
                    expires_at=expires_at,
                ):
                    return replay
                raise IdempotencyConflict(
                    "idempotency_key already belongs to a different notification"
                ) from exc
            raise

    def system_entry_by_idempotency_key(
        self, idempotency_key: str
    ) -> NotificationOutboxEntry | None:
        """Return an existing SYSTEM producer entry without weakening idempotency."""

        key = _bounded_text(idempotency_key, "idempotency_key", 200)
        entry = self._get_by_idempotency_key(key)
        if entry is None or entry.source_type is not NotificationSourceType.SYSTEM:
            return None
        return entry

    async def flush_pending(self) -> NotificationFlushReceipt:
        now = self._clock.now()
        if not self._enabled or self._sender is None:
            return NotificationFlushReceipt(
                disposition=NotificationFlushDisposition.DISABLED,
                checked_at=now,
            )
        items = self._list_due(NotificationChannel.TELEGRAM, now, self._batch_size)
        if not items:
            return NotificationFlushReceipt(
                disposition=NotificationFlushDisposition.NO_PENDING,
                checked_at=now,
            )

        delivered = retry_scheduled = dead_lettered = expired = 0
        errors: list[str] = []
        for group in _notification_groups(items):
            item = group[0]
            expiry = item.expires_at or item.created_at + self._ttl
            if now >= expiry:
                for entry in group:
                    self._record_attempt(
                        entry.notification_id,
                        entry.channel,
                        status=NotificationStatus.EXPIRED,
                        attempted_at=now,
                        next_attempt_at=now,
                        provider_message_id=None,
                        error_code="NOTIFICATION_EXPIRED",
                    )
                expired += 1
                errors.append("NOTIFICATION_EXPIRED")
                continue
            try:
                receipt = await self._sender.send(item)
            except Exception:  # noqa: BLE001 - provider boundary is sanitized here
                receipt = NotificationSendReceipt(
                    delivered=False,
                    retryable=True,
                    error_code="TELEGRAM_TRANSPORT_FAILURE",
                )
            if receipt.delivered:
                for entry in group:
                    self._record_attempt(
                        entry.notification_id,
                        entry.channel,
                        status=NotificationStatus.DELIVERED,
                        attempted_at=now,
                        next_attempt_at=now,
                        provider_message_id=receipt.provider_message_id,
                        error_code=None,
                    )
                delivered += 1
                continue

            error_code = receipt.error_code or "TELEGRAM_DELIVERY_FAILED"
            errors.append(error_code)
            attempt_no = max(entry.attempt_count for entry in group) + 1
            terminal = not receipt.retryable or attempt_no >= self._max_attempts
            if terminal:
                status = NotificationStatus.DEAD_LETTER
                next_attempt_at = now
                dead_lettered += 1
            else:
                status = NotificationStatus.PENDING
                delay_seconds = receipt.retry_after_seconds or _retry_delay_seconds(attempt_no)
                next_attempt_at = now + timedelta(seconds=delay_seconds)
                retry_scheduled += 1
            for entry in group:
                self._record_attempt(
                    entry.notification_id,
                    entry.channel,
                    status=status,
                    attempted_at=now,
                    next_attempt_at=next_attempt_at,
                    provider_message_id=None,
                    error_code=error_code,
                )

        return NotificationFlushReceipt(
            disposition=NotificationFlushDisposition.ATTEMPTED,
            checked_at=now,
            pending_selected=len(items),
            delivered=delivered,
            retry_scheduled=retry_scheduled,
            dead_lettered=dead_lettered,
            expired=expired,
            error_codes=tuple(dict.fromkeys(errors)),
        )

    async def send_test(self) -> NotificationSendReceipt:
        if not self._enabled or self._sender is None:
            return NotificationSendReceipt(
                delivered=False,
                retryable=False,
                error_code="TELEGRAM_NOTIFICATIONS_DISABLED",
            )
        now = self._clock.now()
        return await self._sender.send(
            NotificationOutboxEntry(
                notification_id="notification_00000000-0000-7000-8000-000000000000",
                # Exercise the Monitor mobile renderer without persisting a
                # fake Monitor event or Outbox row.
                source_type=NotificationSourceType.MONITOR_EVENT,
                source_id="monitor_event_notification_test",
                channel=NotificationChannel.TELEGRAM,
                title="🧪 XAUUSD · TRIGGERED 样式预览",
                body=(
                    "通知样式预览（非真实监控事件）\n"
                    "当前价格：4044.9\n"
                    "价格时间：2026-08-01T15:53:36+00:00\n"
                    "上次价格：4046.57\n"
                    "价格变化：-1.67 (-0.04%)\n"
                    "数据来源：binance\n"
                    "CHANGES\n"
                    "• [MEDIUM] XAU_PULLBACK_ALERT_4080 → TRIGGERED\n"
                    "RULES\n"
                    "RULE                               COND    VALUE   DIST    STATE      LEVEL\n"
                    "---------------------------------  ------  ------  ------  ---------  ------\n"
                    "XAU_BREAKOUT_TEST_4116             > 4116  4044.9  -71.1   QUIET      MEDIUM\n"
                    "XAU_DAILY_BREAKOUT_CONFIRM_4146    > 4146  4044.9  -101.1  QUIET      HIGH\n"
                    "XAU_TREND_REPAIR_4248              > 4248  4044.9  -203.1  QUIET      HIGH\n"
                    "XAU_PULLBACK_ALERT_4080             "
                    "< 4080  4044.9  -35.1   TRIGGERED  MEDIUM\n"
                    "XAU_SUPPORT_FAIL_4024               "
                    "< 4024  4044.9  20.9    QUIET      MEDIUM\n"
                    "XAU_WEEKLY_STRUCTURE_FAIL_3914      < 3914  4044.9  130.9   QUIET      HIGH\n"
                    "数据提示：PAXG_USDC_WEEKEND_PROXY, "
                    "WEEKEND_PROXY_NOT_XAUUSD_SPOT\n"
                    "周末口径：Binance PAXG/USDC 仅作为 XAUUSD 周末波动代理；"
                    "它是代币化黄金现货，不是 XAUUSD 或 LBMA 基准价。"
                ),
                status=NotificationStatus.PENDING,
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
            )
        )

    def status(self) -> NotificationStatusReceipt:
        counts = self._counts(NotificationChannel.TELEGRAM)
        return NotificationStatusReceipt(
            enabled=self._enabled,
            provider="TELEGRAM",
            configured=self._configured,
            pending=counts.get(NotificationStatus.PENDING, 0),
            delivered=counts.get(NotificationStatus.DELIVERED, 0),
            dead_letter=counts.get(NotificationStatus.DEAD_LETTER, 0),
            expired=counts.get(NotificationStatus.EXPIRED, 0),
            last_delivered_at=self._last_delivery_at(NotificationChannel.TELEGRAM),
        )

    def recent_entries(self, limit: int = 50) -> tuple[NotificationOutboxEntry, ...]:
        """Return bounded durable Outbox metadata for a secret-safe operator view."""

        return self._repository.list_recent(
            NotificationChannel.TELEGRAM,
            max(1, min(limit, 200)),
        )

    def enqueue_receipt(self, entry: NotificationOutboxEntry) -> NotificationEnqueueReceipt:
        return NotificationEnqueueReceipt(
            notification_id=entry.notification_id,
            source_type=entry.source_type.value,
            source_id=entry.source_id,
            channel=entry.channel.value,
            title=entry.title,
            status=entry.status.value,
            expires_at=entry.expires_at,
        )

    def _get_by_idempotency_key(self, key: str) -> NotificationOutboxEntry | None:
        return self._repository.get_notification_by_idempotency_key(key)

    def _enqueue(self, message: NotificationMessage) -> NotificationOutboxEntry:
        return self._repository.enqueue(message)

    def _list_due(
        self, channel: NotificationChannel, as_of: datetime, limit: int
    ) -> tuple[NotificationOutboxEntry, ...]:
        return self._repository.list_due(channel, as_of, limit)

    def _record_attempt(
        self,
        notification_id: str,
        channel: NotificationChannel,
        *,
        status: NotificationStatus,
        attempted_at: datetime,
        next_attempt_at: datetime,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> NotificationOutboxEntry:
        return self._repository.record_attempt(
            notification_id,
            channel,
            status=status,
            attempted_at=attempted_at,
            next_attempt_at=next_attempt_at,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )

    def _counts(self, channel: NotificationChannel) -> dict[NotificationStatus, int]:
        return self._repository.counts(channel)

    def _last_delivery_at(self, channel: NotificationChannel) -> datetime | None:
        return self._repository.last_delivery_at(channel)


def _bounded_text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise DataContractError(f"{field} must be non-blank and bounded")
    return value.strip()


def _notification_id(key: str) -> str:
    # The source tuple and idempotency key provide stability; the row identity
    # itself must not embed arbitrary caller text. ``key`` is retained in the
    # signature for the deterministic call-site contract and intentionally not
    # interpolated into the ID.
    del key
    return f"notification_{uuid7()}"


def _same_system_notification(
    entry: NotificationOutboxEntry,
    *,
    source_id: str,
    title: str,
    body: str,
    idempotency_key: str,
    expires_at: datetime,
) -> bool:
    return (
        entry.source_type is NotificationSourceType.SYSTEM
        and entry.source_id == source_id
        and entry.title == title
        and entry.body == body
        and entry.idempotency_key == idempotency_key
        and entry.expires_at == expires_at
        and entry.confirmed_by is None
        and entry.authorization_note is None
    )


def _retry_delay_seconds(attempt_no: int) -> int:
    index = min(max(attempt_no, 1), len(_RETRY_DELAYS_SECONDS)) - 1
    return _RETRY_DELAYS_SECONDS[index]


def _notification_groups(
    items: tuple[NotificationOutboxEntry, ...],
) -> tuple[tuple[NotificationOutboxEntry, ...], ...]:
    grouped: dict[tuple[object, ...], list[NotificationOutboxEntry]] = {}
    for item in items:
        # Historical Monitor transitions can contain duplicate outbox rows for
        # one rendered card and are intentionally coalesced. Independent
        # MANUAL/SYSTEM messages must each be delivered exactly once, even when
        # their text and timestamps happen to match.
        key = (
            (item.source_type, item.title, item.body, item.created_at)
            if item.source_type is NotificationSourceType.MONITOR_EVENT
            else (item.source_type, item.notification_id)
        )
        grouped.setdefault(key, []).append(item)
    return tuple(tuple(group) for group in grouped.values())
