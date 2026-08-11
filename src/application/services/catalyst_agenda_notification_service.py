"""Deterministic, mobile-first Catalyst Agenda notification orchestration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from application.dto.catalyst_agenda import AgendaItemDTO, AgendaQueryInput
from application.dto.catalyst_agenda_notifications import (
    AgendaChangeNotificationDTO,
    AgendaNotificationBatchDTO,
    AgendaNotificationEnqueueDTO,
    AgendaNotificationPreviewDTO,
)
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.clock import Clock
from application.services.catalyst_agenda_service import CatalystAgendaService
from application.services.notification_service import NotificationService
from domain.catalyst_agenda.enums import AgendaItemStatus
from domain.catalyst_agenda.models import CatalystAgendaVersion
from domain.common.errors import DataContractError
from domain.notifications.rendering import (
    TELEGRAM_MAX_TEXT_LENGTH,
    rendered_plain_text_length,
)

_OUTCOME_UNVERIFIED = "AGENDA_EVENT_OUTCOME_UNVERIFIED"


@dataclass(frozen=True, slots=True)
class _Change:
    current: CatalystAgendaVersion
    previous: CatalystAgendaVersion
    change_type: str


class CatalystAgendaNotificationService:
    """Render and enqueue Agenda summaries without an LLM or MCP expansion."""

    def __init__(
        self,
        agenda: CatalystAgendaService,
        repository: CatalystAgendaRepository,
        notifications: NotificationService,
        clock: Clock,
    ) -> None:
        self._agenda = agenda
        self._repository = repository
        self._notifications = notifications
        self._clock = clock

    def preview_daily(
        self,
        *,
        window_days: int = 7,
        as_of: datetime | None = None,
        additional_limitations: tuple[str, ...] = (),
    ) -> AgendaNotificationPreviewDTO:
        if not 1 <= window_days <= 30:
            raise DataContractError("window_days must be between 1 and 30")
        generated_at = as_of or self._clock.now()
        result = self._agenda.query(
            AgendaQueryInput(as_of=generated_at, window_days=window_days, limit=200)
        )
        if not result.ok or result.data is None:
            codes = tuple(error.code for error in result.errors) or ("AGENDA_QUERY_FAILED",)
            raise DataContractError(
                "durable Catalyst Agenda could not be rendered",
                details={"error_codes": codes},
            )

        items = result.data.items
        upcoming = tuple(
            item
            for item in items
            if item.status is AgendaItemStatus.UPCOMING
            and not _is_overdue(item, generated_at)
        )
        overdue = tuple(
            item
            for item in items
            if item.status is AgendaItemStatus.UPCOMING and _is_overdue(item, generated_at)
        )
        occurred = tuple(item for item in items if item.status is AgendaItemStatus.OCCURRED)
        coverage_gaps = sum(1 for item in result.data.coverage if item.status == "UNAVAILABLE")
        limitations = tuple(
            dict.fromkeys((*result.data.limitation_codes, *additional_limitations))
        )
        date_key = generated_at.date().isoformat()
        source_id = f"catalyst-agenda:daily:{date_key}:w{window_days}"
        expires_at = datetime.combine(
            generated_at.date() + timedelta(days=1),
            time.min,
            tzinfo=generated_at.tzinfo,
        )
        title = f"催化事项 · 未来 {window_days} 天"
        body = _render_daily_body(
            generated_at=generated_at,
            upcoming=upcoming,
            overdue=overdue,
            occurred=occurred,
            coverage_gap_count=coverage_gaps,
            limitations=limitations,
        )
        return AgendaNotificationPreviewDTO(
            source_id=source_id,
            title=title,
            body=_fit_telegram(title, body),
            generated_at=generated_at,
            expires_at=expires_at,
            window_days=window_days,
            upcoming_count=len(upcoming),
            overdue_count=len(overdue),
            coverage_gap_count=coverage_gaps,
            limitation_codes=limitations,
        )

    async def enqueue_daily(
        self,
        *,
        window_days: int = 7,
        as_of: datetime | None = None,
        additional_limitations: tuple[str, ...] = (),
    ) -> AgendaNotificationEnqueueDTO:
        preview = self.preview_daily(
            window_days=window_days,
            as_of=as_of,
            additional_limitations=additional_limitations,
        )
        existing = self._notifications.system_entry_by_idempotency_key(preview.source_id)
        if existing is not None:
            return AgendaNotificationEnqueueDTO(
                preview=preview,
                notification_id=existing.notification_id,
                status=existing.status.value,
            )
        entry = await self._notifications.enqueue_system_text(
            source_id=preview.source_id,
            title=preview.title,
            body=preview.body,
            idempotency_key=preview.source_id,
            expires_at=preview.expires_at,
        )
        return AgendaNotificationEnqueueDTO(
            preview=preview,
            notification_id=entry.notification_id,
            status=entry.status.value,
        )

    async def enqueue_changes(
        self,
        *,
        as_of: datetime | None = None,
        lookback_hours: int = 24,
    ) -> tuple[AgendaChangeNotificationDTO, ...]:
        if not 1 <= lookback_hours <= 168:
            raise DataContractError("lookback_hours must be between 1 and 168")
        checked_at = as_of or self._clock.now()
        changes = _material_changes(
            self._repository.list_visible(as_of=checked_at),
            since=checked_at - timedelta(hours=lookback_hours),
        )
        receipts: list[AgendaChangeNotificationDTO] = []
        for change in changes:
            current = change.current
            source_id = f"catalyst-agenda:change:{current.agenda_item_id}:v{current.version}"
            entry = await self._notifications.enqueue_system_text(
                source_id=source_id,
                title=f"催化事项 · {change.change_type}",
                body=_render_change_body(change),
                idempotency_key=source_id,
                expires_at=current.recorded_at + timedelta(hours=24),
            )
            receipts.append(
                AgendaChangeNotificationDTO(
                    agenda_item_id=current.agenda_item_id,
                    version=current.version,
                    change_type=change.change_type,
                    notification_id=entry.notification_id,
                    status=entry.status.value,
                )
            )
        return tuple(receipts)

    async def enqueue_batch(
        self,
        *,
        window_days: int = 7,
        as_of: datetime | None = None,
        additional_limitations: tuple[str, ...] = (),
    ) -> AgendaNotificationBatchDTO:
        checked_at = as_of or self._clock.now()
        changes = await self.enqueue_changes(as_of=checked_at)
        daily = await self.enqueue_daily(
            window_days=window_days,
            as_of=checked_at,
            additional_limitations=additional_limitations,
        )
        return AgendaNotificationBatchDTO(daily=daily, changes=changes)


def _is_overdue(item: AgendaItemDTO, as_of: datetime) -> bool:
    return item.window_end is not None and item.window_end < as_of


def _render_daily_body(
    *,
    generated_at: datetime,
    upcoming: tuple[AgendaItemDTO, ...],
    overdue: tuple[AgendaItemDTO, ...],
    occurred: tuple[AgendaItemDTO, ...],
    coverage_gap_count: int,
    limitations: tuple[str, ...],
) -> str:
    lines = [f"截至：{generated_at.isoformat(timespec='minutes')}"]
    lines.extend(_render_section("未来事项", upcoming))
    lines.extend(_render_section("逾期未闭环", overdue))
    lines.extend(_render_section("已关联结果", occurred))
    lines.append(f"覆盖缺口：{coverage_gap_count}")
    if limitations:
        lines.append("限制：" + ", ".join(limitations))
    if not upcoming and not overdue and not occurred:
        lines.append("当前 durable 范围内没有可展示事项；这不等于上游没有事件。")
    return "\n".join(lines)


def _render_section(title: str, items: tuple[AgendaItemDTO, ...]) -> list[str]:
    if not items:
        return []
    lines = [f"\n{title}（{len(items)}）"]
    for item in items[:25]:
        when = _window_label(item.window_start, item.window_end)
        subject = item.instrument_id or item.subject_id or "未绑定"
        lines.append(f"• {when} · {subject} · {item.title} [{item.date_certainty.value}]")
        if item.expected_question:
            lines.append(f"  要回答：{item.expected_question}")
    if len(items) > 25:
        lines.append(f"• 另有 {len(items) - 25} 项，请在 Console 查看")
    return lines


def _window_label(start: datetime | None, end: datetime | None) -> str:
    if start is None:
        return "日期未知"
    if end is None or start == end:
        return start.date().isoformat()
    if start.date() == end.date():
        return start.date().isoformat()
    return f"{start.date().isoformat()}–{end.date().isoformat()}"


def _fit_telegram(title: str, body: str) -> str:
    suffix = "\n…内容已截断，请在 Console 查看完整 Agenda。"
    candidate = body
    while rendered_plain_text_length(title, candidate) > TELEGRAM_MAX_TEXT_LENGTH:
        if len(candidate) <= len(suffix) + 64:
            raise DataContractError("Agenda notification cannot fit Telegram's text limit")
        candidate = candidate[: max(64, len(candidate) - 256)].rstrip() + suffix
    return candidate


def _material_changes(
    values: tuple[CatalystAgendaVersion, ...],
    *,
    since: datetime,
) -> tuple[_Change, ...]:
    grouped: dict[str, list[CatalystAgendaVersion]] = defaultdict(list)
    for value in values:
        grouped[value.agenda_item_id].append(value)
    changes: list[_Change] = []
    for versions in grouped.values():
        versions.sort(key=lambda value: value.version)
        if len(versions) < 2:
            continue
        current, previous = versions[-1], versions[-2]
        if current.recorded_at <= since:
            continue
        if (
            previous.status is AgendaItemStatus.UPCOMING
            and current.status is AgendaItemStatus.CANCELLED
        ):
            changes.append(_Change(current, previous, "已取消"))
        elif (
            current.status is AgendaItemStatus.UPCOMING
            and (
                current.window_start != previous.window_start
                or current.window_end != previous.window_end
                or current.date_certainty is not previous.date_certainty
            )
        ):
            changes.append(_Change(current, previous, "日期变更"))
    changes.sort(key=lambda item: (item.current.recorded_at, item.current.agenda_item_id))
    return tuple(changes)


def _render_change_body(change: _Change) -> str:
    current, previous = change.current, change.previous
    subject = current.instrument_id or current.subject_id or "未绑定"
    lines = [
        f"{subject} · {current.title}",
        f"变化：{change.change_type}",
        f"原窗口：{_window_label(previous.window_start, previous.window_end)}",
        f"新窗口：{_window_label(current.window_start, current.window_end)}",
        f"确定性：{previous.date_certainty.value} → {current.date_certainty.value}",
        f"来源：{current.source_vendor}",
    ]
    return "\n".join(lines)
