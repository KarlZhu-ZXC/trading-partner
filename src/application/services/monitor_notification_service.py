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
                        entry.notification_id,
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
                        entry.notification_id,
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
                delay_seconds = receipt.retry_after_seconds or _retry_delay_seconds(attempt_no)
                next_attempt_at = now + timedelta(seconds=delay_seconds)
                retry_scheduled += 1
            for entry in group:
                self._repository.record_notification_attempt(
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
            MonitorNotificationOutboxEntry(
                notification_id="monitor_notification_test",
                source_event_id="monitor_event_notification_test",
                source_run_id=None,
                channel=MonitorNotificationChannel.TELEGRAM,
                title="🧪 XAUUSD · TRIGGERED 样式预览",
                body=(
                    "通知样式预览（非真实监控事件）\n"
                    "当前价格：4044.9\n"
                    "价格时间：2026-08-01T15:53:36+00:00\n"
                    "上次价格：4046.57\n"
                    "价格变化：-1.67 (-0.04%)\n"
                    "数据来源：ig_weekend_gold\n"
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
                    "数据提示：IG_WEEKEND_GOLD_CFD_FALLBACK, WEEKEND_PROXY_NOT_SPOT\n"
                    "周末口径：IG Weekend Gold CFD 仅作为 XAUUSD 周末波动代理；"
                    "不是现货黄金或 LBMA 基准价。"
                ),
                status=MonitorNotificationStatus.PENDING,
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
            )
        )

    def status(self) -> NotificationStatusReceipt:
        counts = self._repository.notification_counts(MonitorNotificationChannel.TELEGRAM)
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
