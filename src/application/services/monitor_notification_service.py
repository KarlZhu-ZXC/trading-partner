"""Durable, retryable Monitor notification delivery without an LLM."""

from __future__ import annotations

from datetime import timedelta

from application.dto.monitor_notifications import (
    NotificationFlushDisposition,
    NotificationFlushReceipt,
    NotificationSendReceipt,
    NotificationStatusReceipt,
)
from application.ports.clock import Clock
from application.ports.monitor_notification_sender import MonitorNotificationSender
from application.ports.monitor_repository import MonitorRepository
from domain.monitoring.enums import (
    MonitorNotificationChannel,
    MonitorNotificationStatus,
)
from domain.monitoring.models import MonitorNotificationOutboxEntry

_RETRY_DELAYS_SECONDS = (60, 300, 900, 3600, 14400)


class MonitorNotificationService:
    def __init__(
        self,
        repository: MonitorRepository,
        sender: MonitorNotificationSender | None,
        clock: Clock,
        *,
        enabled: bool,
        configured: bool,
        max_attempts: int = 5,
        event_ttl_hours: int = 24,
        batch_size: int = 20,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._clock = clock
        self._enabled = enabled
        self._configured = configured
        self._max_attempts = max_attempts
        self._event_ttl = timedelta(hours=event_ttl_hours)
        self._batch_size = batch_size

    async def flush_pending(self) -> NotificationFlushReceipt:
        now = self._clock.now()
        if not self._enabled or self._sender is None:
            return NotificationFlushReceipt(
                disposition=NotificationFlushDisposition.DISABLED,
                checked_at=now,
            )
        items = self._repository.list_due_notifications(
            MonitorNotificationChannel.TELEGRAM,
            now,
            self._batch_size,
        )
        if not items:
            return NotificationFlushReceipt(
                disposition=NotificationFlushDisposition.NO_PENDING,
                checked_at=now,
            )

        delivered = retry_scheduled = dead_lettered = expired = 0
        errors: list[str] = []
        for group in _notification_groups(items):
            item = group[0]
            if now - item.created_at > self._event_ttl:
                for entry in group:
                    self._repository.record_notification_attempt(
                        entry.source_event_id,
                        entry.channel,
                        status=MonitorNotificationStatus.EXPIRED,
                        attempted_at=now,
                        next_attempt_at=now,
                        provider_message_id=None,
                        error_code="MONITOR_NOTIFICATION_EXPIRED",
                    )
                expired += 1
                errors.append("MONITOR_NOTIFICATION_EXPIRED")
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
                    self._repository.record_notification_attempt(
                        entry.source_event_id,
                        entry.channel,
                        status=MonitorNotificationStatus.DELIVERED,
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
                status = MonitorNotificationStatus.DEAD_LETTER
                next_attempt_at = now
                dead_lettered += 1
            else:
                status = MonitorNotificationStatus.PENDING
                delay_seconds = receipt.retry_after_seconds or _retry_delay_seconds(
                    attempt_no
                )
                next_attempt_at = now + timedelta(seconds=delay_seconds)
                retry_scheduled += 1
            for entry in group:
                self._repository.record_notification_attempt(
                    entry.source_event_id,
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
            MonitorNotificationOutboxEntry(
                source_event_id="monitor_event_notification_test",
                channel=MonitorNotificationChannel.TELEGRAM,
                title="✅ Trading Partner Telegram 测试",
                body=(
                    "Telegram 通知链路已连接。\n"
                    "实际 Monitor 只在 TRIGGERED、RECOVERED 或 NOT_EVALUATED "
                    "状态迁移时推送。"
                ),
                status=MonitorNotificationStatus.PENDING,
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
            )
        )

    def status(self) -> NotificationStatusReceipt:
        counts = self._repository.notification_counts(
            MonitorNotificationChannel.TELEGRAM
        )
        return NotificationStatusReceipt(
            enabled=self._enabled,
            provider="TELEGRAM",
            configured=self._configured,
            pending=counts.get(MonitorNotificationStatus.PENDING, 0),
            delivered=counts.get(MonitorNotificationStatus.DELIVERED, 0),
            dead_letter=counts.get(MonitorNotificationStatus.DEAD_LETTER, 0),
            expired=counts.get(MonitorNotificationStatus.EXPIRED, 0),
            last_delivered_at=self._repository.last_notification_delivery_at(
                MonitorNotificationChannel.TELEGRAM
            ),
        )


def _retry_delay_seconds(attempt_no: int) -> int:
    index = min(max(attempt_no, 1), len(_RETRY_DELAYS_SECONDS)) - 1
    return _RETRY_DELAYS_SECONDS[index]


def _notification_groups(
    items: tuple[MonitorNotificationOutboxEntry, ...],
) -> tuple[tuple[MonitorNotificationOutboxEntry, ...], ...]:
    grouped: dict[
        tuple[str, str, object],
        list[MonitorNotificationOutboxEntry],
    ] = {}
    for item in items:
        key = (item.title, item.body, item.created_at)
        grouped.setdefault(key, []).append(item)
    return tuple(tuple(group) for group in grouped.values())
