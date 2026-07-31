"""Secret-safe Telegram Bot sender for deterministic Monitor notifications."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from application.dto.monitor_notifications import NotificationSendReceipt
from domain.monitoring.models import MonitorNotificationOutboxEntry

_MAX_RESPONSE_BYTES = 64_000
_RULE_ROW_PATTERN = re.compile(r"\s{2,}")
_CHANGE_PATTERN = re.compile(r"^\u2022 \[(?P<severity>[^]]+)] (?P<rule>.+?) \u2192 (?P<event>\S+)$")


class TelegramMonitorNotificationAdapter:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_thread_id: int | None = None,
        timeout_seconds: float = 10.0,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._message_thread_id = message_thread_id
        self._owns_client = client is None
        for logger_name in ("httpx", "httpcore"):
            logging.getLogger(logger_name).disabled = True
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout_seconds,
            trust_env=False,
            proxy=proxy_url,
        )
        self._timeout_seconds = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, notification: MonitorNotificationOutboxEntry) -> NotificationSendReceipt:
        payload: dict[str, object] = {
            "chat_id": self._chat_id,
            "text": _format_notification_html(notification.title, notification.body),
            "parse_mode": "HTML",
            "disable_notification": False,
        }
        if self._message_thread_id is not None:
            payload["message_thread_id"] = self._message_thread_id
        # Telegram requires the bot token in this path. Never expose this URL in
        # logs, exceptions, receipts, or generic HttpRequest representations.
        endpoint = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        return await self._post(endpoint, json=payload)

    async def _post(
        self,
        endpoint: str,
        *,
        json: dict[str, object] | None = None,
    ) -> NotificationSendReceipt:
        try:
            response = await self._client.post(
                endpoint,
                json=json,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            return _failure("TELEGRAM_TIMEOUT", retryable=True)
        except httpx.HTTPError:
            return _failure("TELEGRAM_TRANSPORT_FAILURE", retryable=True)
        if len(response.content) > _MAX_RESPONSE_BYTES:
            return _failure("TELEGRAM_RESPONSE_TOO_LARGE", retryable=False)
        data = _safe_json_object(response)
        if response.status_code == 200 and data.get("ok") is True:
            result = data.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            return NotificationSendReceipt(
                delivered=True,
                retryable=False,
                provider_message_id=(str(message_id) if isinstance(message_id, int) else None),
            )
        if response.status_code == 429:
            retry_after = _retry_after_seconds(data)
            return NotificationSendReceipt(
                delivered=False,
                retryable=True,
                error_code="TELEGRAM_RATE_LIMITED",
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 500:
            return _failure("TELEGRAM_PROVIDER_UNAVAILABLE", retryable=True)
        error_code = {
            400: "TELEGRAM_BAD_REQUEST",
            401: "TELEGRAM_AUTH_FAILED",
            403: "TELEGRAM_CHAT_FORBIDDEN",
        }.get(response.status_code, "TELEGRAM_REQUEST_REJECTED")
        return _failure(error_code, retryable=False)


def _failure(code: str, *, retryable: bool) -> NotificationSendReceipt:
    return NotificationSendReceipt(
        delivered=False,
        retryable=retryable,
        error_code=code,
    )


def _safe_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _retry_after_seconds(data: dict[str, Any]) -> int | None:
    parameters = data.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    if isinstance(value, int) and 1 <= value <= 86400:
        return value
    return None


def _format_notification_html(title: str, body: str) -> str:
    """Render a mobile-first Telegram rule card; Telegram has no table markup."""
    lines = body.splitlines()
    if lines and lines[0] == "POST_MARKET_SUMMARY":
        return _format_post_market_summary_html(title, lines)
    try:
        rules_index = lines.index("RULES")
    except ValueError:
        return f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}"
    if rules_index + 2 >= len(lines):
        return f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}"

    monitor_name = lines[0].strip() if lines else ""
    price = _prefixed_value(lines, "当前价格：")
    price_time = _prefixed_value(lines, "价格时间：")
    changes_start = lines.index("CHANGES") + 1 if "CHANGES" in lines else rules_index
    changes = lines[changes_start:rules_index]
    rows, notes = _parse_rule_rows(lines[rules_index + 3 :])
    if not rows:
        return f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}"

    sections = [f"<b>{html.escape(_headline_with_price(title, price))}</b>"]
    if monitor_name:
        sections.append(f"<i>{html.escape(monitor_name)}</i>")

    price_lines: list[str] = []
    if price is not None:
        price_lines.append(f"💰 <b>当前价格：{html.escape(price)}</b>")
    if price_time is not None:
        price_lines.append(f"🕒 价格时间：{html.escape(_format_price_time(price_time))}")
    if price_lines:
        sections.append("\n".join(price_lines))

    formatted_changes = tuple(
        formatted for line in changes if (formatted := _format_change(line)) is not None
    )
    if formatted_changes:
        sections.append("<b>本轮结果</b>\n" + "\n".join(formatted_changes))

    sections.append("<b>全部监控规则</b>\n\n" + _format_rule_cards(rows))
    if notes:
        sections.append("\n".join(f"<i>{html.escape(note)}</i>" for note in notes))
    return "\n\n".join(sections)


def _format_post_market_summary_html(title: str, lines: list[str]) -> str:
    run_time = _prefixed_value(lines, "运行时间：")
    change_count = _prefixed_value(lines, "本轮变化：")
    sections = [f"<b>{html.escape(title)}</b>"]
    summary: list[str] = []
    if run_time is not None:
        summary.append(f"🕒 运行时间：{html.escape(_format_price_time(run_time))}")
    if change_count == "0":
        summary.append("✅ 本轮无状态变化")
    elif change_count is not None:
        summary.append(f"🔔 本轮状态变化：{html.escape(change_count)}")
    if summary:
        sections.append("\n".join(summary))

    index = 1
    notes: list[str] = []
    while index < len(lines):
        if lines[index] != "MONITOR":
            if lines[index].startswith(("数据提示：", "运行错误：", "期货价格")):
                notes.append(lines[index])
            index += 1
            continue
        end_index = index + 1
        while end_index < len(lines) and lines[end_index] != "END_MONITOR":
            end_index += 1
        block = lines[index + 1 : end_index]
        rendered = _format_digest_monitor_block(block)
        if rendered is not None:
            sections.append(rendered)
        index = end_index + 1
    if notes:
        sections.append("\n".join(f"<i>{html.escape(note)}</i>" for note in notes))
    return "\n\n".join(sections)


def _format_digest_monitor_block(lines: list[str]) -> str | None:
    if not lines:
        return None
    name = lines[0].strip()
    symbol = _prefixed_value(lines, "标的：")
    price = _prefixed_value(lines, "当前价格：")
    price_time = _prefixed_value(lines, "价格时间：")
    try:
        rules_index = lines.index("RULES")
    except ValueError:
        return None
    rows, _notes = _parse_rule_rows(lines[rules_index + 3 :])
    if not rows:
        return None
    heading = symbol or name
    if price is not None:
        heading = f"{heading} · {price}"
    parts = [f"<b>{html.escape(heading)}</b>"]
    if name and name != symbol:
        parts.append(f"<i>{html.escape(name)}</i>")
    if price_time is not None:
        parts.append(f"🕒 {html.escape(_format_price_time(price_time))}")
    parts.append(_format_rule_cards(rows))
    return "\n".join(parts)


def _parse_rule_rows(
    lines: list[str],
) -> tuple[tuple[tuple[str, str, str, str, str, str], ...], tuple[str, ...]]:
    rows: list[tuple[str, str, str, str, str, str]] = []
    notes: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("数据提示：") or stripped.startswith("期货价格"):
            notes.append(stripped)
            continue
        parts = tuple(_RULE_ROW_PATTERN.split(stripped, maxsplit=5))
        if len(parts) == 6:
            rows.append(parts)
    return tuple(rows), tuple(notes)


def _format_change(line: str) -> str | None:
    match = _CHANGE_PATTERN.fullmatch(line.strip())
    if match is None:
        return html.escape(line.strip()) if line.strip() else None
    event = match.group("event")
    emoji = _state_emoji(event)
    return (
        f"{emoji} <code>{html.escape(match.group('rule'))}</code>"
        f" · {html.escape(match.group('severity'))} · <b>{html.escape(event)}</b>"
    )


def _format_rule_cards(
    rows: tuple[tuple[str, str, str, str, str, str], ...],
) -> str:
    cards: list[str] = []
    for rule, condition, value, distance, state, level in rows:
        cards.append(
            "\n".join(
                (
                    f"{_state_emoji(state)} <b>{html.escape(condition)}</b>"
                    f" · <b>{html.escape(state)}</b> · {html.escape(level)}",
                    f"当前 <code>{html.escape(value)}</code>"
                    f" · {html.escape(_distance_label(condition, distance, state))}",
                    f"规则：<code>{html.escape(rule)}</code>",
                )
            )
        )
    return "\n\n".join(cards)


def _headline_with_price(title: str, price: str | None) -> str:
    if price is None:
        return title
    prefix, separator, event = title.rpartition(" · ")
    if not separator:
        return f"{title} · {price}"
    return f"{prefix} · {price} · {event}"


def _format_price_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    offset = parsed.utcoffset()
    if offset is None:
        return parsed.strftime("%Y-%m-%d %H:%M")
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{parsed:%Y-%m-%d %H:%M} UTC{sign}{hours:02d}:{minutes:02d}"


def _distance_label(condition: str, distance: str, state: str) -> str:
    if distance == "不可用":
        return "距阈值：不可用"
    try:
        delta = Decimal(distance)
    except InvalidOperation:
        return f"距阈值：{distance}"
    comparator = condition.split(maxsplit=1)[0] if condition else ""
    magnitude = _format_decimal(abs(delta))
    if state == "TRIGGERED":
        if comparator.startswith(">"):
            return f"已高于阈值 {magnitude}"
        if comparator.startswith("<"):
            return f"已低于阈值 {magnitude}"
    if comparator.startswith(">") and delta < 0:
        return f"距触发 {magnitude}"
    if comparator.startswith("<") and delta > 0:
        return f"距触发 {magnitude}"
    return f"距阈值：{_format_decimal(delta)}"


def _format_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _state_emoji(state: str) -> str:
    return {
        "TRIGGERED": "🔴",
        "NOT_EVALUATED": "🟠",
        "RECOVERED": "🟢",
        "QUIET": "⚪️",
    }.get(state, "🔹")


def _prefixed_value(lines: list[str], prefix: str) -> str | None:
    return next(
        (line.removeprefix(prefix).strip() for line in lines if line.startswith(prefix)),
        None,
    )
