"""Secret-safe Telegram Bot sender for deterministic Monitor notifications."""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from application.dto.monitor_notifications import NotificationSendReceipt
from domain.monitoring.models import MonitorNotificationOutboxEntry

_MAX_RESPONSE_BYTES = 64_000
_RULE_ROW_PATTERN = re.compile(r"\s{2,}")
_CHANGE_PATTERN = re.compile(
    r"^\u2022 \[(?P<severity>[^]]+)] (?P<rule>.+?) \u2192 (?P<event>\S+)$"
)


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

    async def send(
        self, notification: MonitorNotificationOutboxEntry
    ) -> NotificationSendReceipt:
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
                provider_message_id=(
                    str(message_id) if isinstance(message_id, int) else None
                ),
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
    """Render a Telegram-native vertical rule card; Telegram has no table markup."""
    lines = body.splitlines()
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

    sections = [f"<b>{html.escape(title)}</b>"]
    if monitor_name:
        sections.append(f"<i>{html.escape(monitor_name)}</i>")

    formatted_changes = tuple(
        formatted for line in changes if (formatted := _format_change(line)) is not None
    )
    if formatted_changes:
        sections.append("<b>本轮结果</b>\n" + "\n".join(formatted_changes))

    table_lines: list[str] = []
    if price is not None:
        table_lines.append(f"PRICE  {price}")
    if price_time is not None:
        table_lines.append(f"TIME   {price_time}")
    if table_lines:
        table_lines.append("")
    table_lines.append(_format_rule_table(rows))
    sections.append(
        "<b>价格与规则</b>\n<pre>" + html.escape("\n".join(table_lines)) + "</pre>"
    )
    if notes:
        sections.append("\n".join(f"<i>{html.escape(note)}</i>" for note in notes))
    return "\n\n".join(sections)


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


def _format_rule_table(rows: tuple[tuple[str, str, str, str, str, str], ...]) -> str:
    headers = ("RULE", "COND", "VALUE", "DIST", "STATE", "LEVEL")
    widths = tuple(
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    )

    def format_row(row: tuple[str, ...]) -> str:
        return "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ).rstrip()

    separator = "  ".join("-" * width for width in widths)
    return "\n".join((format_row(headers), separator, *(format_row(row) for row in rows)))


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
