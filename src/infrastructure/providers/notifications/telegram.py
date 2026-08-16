"""Secret-safe Telegram Bot sender for deterministic notifications."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from application.dto.notifications import NotificationSendReceipt
from domain.notifications.enums import NotificationSourceType
from domain.notifications.models import NotificationOutboxEntry
from domain.notifications.rendering import render_plain_text_html

_MAX_RESPONSE_BYTES = 64_000
_RULE_ROW_PATTERN = re.compile(r"\s{2,}")
_CHANGE_PATTERN = re.compile(r"^\u2022 \[(?P<severity>[^]]+)] (?P<rule>.+?) \u2192 (?P<event>\S+)$")
_DETAILED_CHANGE_PATTERN = re.compile(
    r"^\u2022 \[(?P<severity>[^]]+)] (?P<rule>.+?) · "
    r"条件：(?P<condition>.*?) · 含义：(?P<meaning>.*?)"
    r" \u2192 (?P<event>TRIGGERED|RECOVERED|NOT_EVALUATED)$"
)
_COMPACT_CARD_PATTERN = re.compile(
    r"^\u2022\s*状态：(?P<state>\S+)\s*·\s*"
    r"条件：(?P<condition>.*?)\s*·\s*"
    r"含义：(?P<meaning>.*?)(?:\s*·\s*级别：(?P<level>\S+))?$"
)


class TelegramNotificationAdapter:
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

    async def send(self, notification: NotificationOutboxEntry) -> NotificationSendReceipt:
        is_monitor = notification.source_type in {
            NotificationSourceType.MONITOR_EVENT,
            NotificationSourceType.MONITOR_RUN,
        }
        rendered = _render_notification_html(notification)
        payload: dict[str, object] = {"chat_id": self._chat_id, "disable_notification": False}
        if is_monitor:
            payload["rich_message"] = {
                "html": rendered,
                "skip_entity_detection": True,
            }
            method = "sendRichMessage"
        else:
            payload.update({"text": rendered, "parse_mode": "HTML"})
            method = "sendMessage"
        if self._message_thread_id is not None:
            payload["message_thread_id"] = self._message_thread_id
        # Telegram requires the bot token in this path. Never expose this URL in
        # logs, exceptions, receipts, or generic HttpRequest representations.
        endpoint = f"https://api.telegram.org/bot{self._bot_token}/{method}"
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


def _render_notification_html(notification: NotificationOutboxEntry) -> str:
    """Render the formatter selected by the closed notification source type."""
    if notification.source_type in {
        NotificationSourceType.MONITOR_EVENT,
        NotificationSourceType.MONITOR_RUN,
    }:
        return _format_notification_html(notification.title, notification.body)
    return render_plain_text_html(notification.title, notification.body)


def _format_notification_html(title: str, body: str) -> str:
    """Render a mobile-first Telegram Rich Message with a native rule table."""
    lines = body.splitlines()
    if lines and lines[0] == "POST_MARKET_SUMMARY":
        return _format_post_market_summary_html(title, lines)
    try:
        rules_index = lines.index("RULES")
    except ValueError:
        return render_plain_text_html(title, body)
    if rules_index + 1 >= len(lines):
        return render_plain_text_html(title, body)

    monitor_name = lines[0].strip() if lines else ""
    symbol = _prefixed_value(lines, "标的：")
    price = _prefixed_value(lines, "当前价格：")
    previous_valid_price = _prefixed_value(lines, "上一有效价格：")
    price_basis = _prefixed_value(lines, "价格口径：")
    price_time = _prefixed_value(lines, "价格时间：")
    data_source = _prefixed_value(lines, "数据来源：")
    previous_price = _prefixed_value(lines, "上次价格：")
    price_change = _prefixed_value(lines, "价格变化：")
    changes_start = lines.index("CHANGES") + 1 if "CHANGES" in lines else rules_index
    changes = lines[changes_start:rules_index]
    judgment_index = lines.index("JUDGMENT") if "JUDGMENT" in lines else None
    rule_end = judgment_index if judgment_index is not None else len(lines)
    rows, notes = _parse_rule_rows(lines[rules_index + 1 : rule_end])
    judgment_lines = lines[judgment_index + 1 :] if judgment_index is not None else []
    if not rows:
        return render_plain_text_html(title, body)

    display_price = price
    if display_price in {None, "不可用", "N/A", "—"} and previous_valid_price is not None:
        display_price = previous_valid_price
        price_basis = price_basis or "上一有效价格（当前不可用）"
    sections = [f"<b>{html.escape(_headline_with_symbol_price(title, symbol, display_price))}</b>"]
    if monitor_name and monitor_name != symbol and len(monitor_name) <= 48:
        sections.append(f"<i>{html.escape(monitor_name)}</i>")

    formatted_changes = tuple(
        formatted for line in changes if (formatted := _format_change(line)) is not None
    )
    if formatted_changes:
        sections.append(_change_banner(changes) + "\n" + "\n".join(formatted_changes))

    price_lines: list[str] = []
    if price_basis is not None:
        price_lines.append(f"⚠️ 价格口径：{html.escape(price_basis)}")
    observation_meta: list[str] = []
    if price_time is not None:
        observation_meta.append(f"🕒 {html.escape(_format_price_time(price_time))}")
    if data_source is not None:
        observation_meta.append(
            f"📡 <b>{html.escape(_display_source_names(data_source))}</b>"
        )
    if observation_meta:
        price_lines.append(" · ".join(observation_meta))
    comparison_meta: list[str] = []
    if previous_price is not None:
        comparison_meta.append(f"↩️ 上次 {html.escape(previous_price)}")
    if price_change is not None:
        comparison_meta.append(f"较上次 <b>{html.escape(price_change)}</b>")
    if comparison_meta:
        price_lines.append(" · ".join(comparison_meta))
    if price_lines:
        sections.append("\n".join(price_lines))

    sections.append("<b>规则概览</b>\n" + _format_rule_cards(rows, symbol=symbol))
    if notes:
        sections.append("\n".join(f"<i>{html.escape(note)}</i>" for note in notes))
    if judgment_lines:
        sections.append(
            "🧭 <b>复合判断</b>\n"
            + "\n".join(html.escape(line) for line in judgment_lines if line.strip())
        )
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
        summary.append(f"🚨 <b>本轮出现 {html.escape(change_count)} 项新状态变化</b>")
    if summary:
        sections.append("\n".join(summary))

    index = 1
    notes: list[str] = []
    while index < len(lines):
        if lines[index] != "MONITOR":
            if lines[index].startswith(
                (
                    "数据提示：",
                    "数据原因：",
                    "运行错误：",
                    "口径：",
                    "期货价格",
                    "周末口径：",
                )
            ):
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
    previous_valid_price = _prefixed_value(lines, "上一有效价格：")
    price_basis = _prefixed_value(lines, "价格口径：")
    price_time = _prefixed_value(lines, "价格时间：")
    data_source = _prefixed_value(lines, "数据来源：")
    previous_price = _prefixed_value(lines, "上次价格：")
    price_change = _prefixed_value(lines, "价格变化：")
    changes_index = lines.index("CHANGES") if "CHANGES" in lines else None
    try:
        rules_index = lines.index("RULES")
    except ValueError:
        return None
    changes = (
        lines[changes_index + 1 : rules_index]
        if changes_index is not None and changes_index < rules_index
        else []
    )
    judgment_index = lines.index("JUDGMENT") if "JUDGMENT" in lines else None
    rule_end = judgment_index if judgment_index is not None else len(lines)
    rows, notes = _parse_rule_rows(lines[rules_index + 1 : rule_end])
    judgment_lines = lines[judgment_index + 1 :] if judgment_index is not None else []
    if not rows:
        return None
    heading = symbol or name
    display_price = price
    if display_price in {None, "不可用", "N/A", "—"} and previous_valid_price is not None:
        display_price = previous_valid_price
        price_basis = price_basis or "上一有效价格（当前不可用）"
    if display_price is not None:
        heading = f"{heading} · {display_price}"
    parts = [f"<b>{html.escape(heading)}</b>"]
    if name and name != symbol and len(name) <= 48:
        parts.append(f"<i>{html.escape(name)}</i>")
    observation_meta: list[str] = []
    if price_time is not None:
        observation_meta.append(f"🕒 {html.escape(_format_price_time(price_time))}")
    if data_source is not None:
        observation_meta.append(
            f"📡 <b>{html.escape(_display_source_names(data_source))}</b>"
        )
    if observation_meta:
        parts.append(" · ".join(observation_meta))
    if price_basis is not None:
        parts.append(f"⚠️ {html.escape(price_basis)}")
    comparison_meta: list[str] = []
    if previous_price is not None:
        comparison_meta.append(f"↩️ 上次 {html.escape(previous_price)}")
    if price_change is not None:
        comparison_meta.append(f"较上次 <b>{html.escape(price_change)}</b>")
    if comparison_meta:
        parts.append(" · ".join(comparison_meta))
    formatted_changes = tuple(
        formatted for line in changes if (formatted := _format_change(line)) is not None
    )
    if formatted_changes:
        parts.append(_change_banner(changes) + "\n" + "\n".join(formatted_changes))
    for note in notes:
        if note not in {price_basis}:
            parts.append(f"<i>{html.escape(note)}</i>")
    if judgment_lines:
        parts.append(
            "⚠️ <b>复合判断状态</b>\n"
            + "\n".join(html.escape(line) for line in judgment_lines if line.strip())
        )
    parts.append("<b>规则概览</b>\n" + _format_rule_cards(rows, symbol=symbol))
    return "\n".join(parts)


def _parse_rule_rows(
    lines: list[str],
) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    notes: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        compact_match = _COMPACT_CARD_PATTERN.fullmatch(stripped)
        if compact_match is not None:
            rows.append(
                (
                    "",
                    compact_match.group("condition").strip(),
                    "",
                    "",
                    compact_match.group("state").strip(),
                    (compact_match.group("level") or "INFO").strip(),
                    compact_match.group("meaning").strip(),
                )
            )
            continue
        if stripped.startswith(
            (
                "数据提示：",
                "数据原因：",
                "数据状态：",
                "运行错误：",
                "口径：",
                "期货价格",
                "周末口径：",
                "价格口径：",
            )
        ):
            notes.append(stripped)
            continue
        if stripped == "RULES" or set(stripped) <= {"-", " "}:
            continue
        if stripped.startswith("RULE "):
            continue
        parts = tuple(_RULE_ROW_PATTERN.split(stripped, maxsplit=5))
        if len(parts) == 6:
            rows.append(parts)
    return tuple(rows), tuple(notes)


def _format_change(line: str) -> str | None:
    detailed_match = _detailed_change_match(line)
    if detailed_match is not None:
        event = detailed_match.group("event")
        severity = _short_severity(detailed_match.group("severity"))
        return (
            f"{_state_emoji(event)} <b>{html.escape(detailed_match.group('condition'))}</b>"
            f" · <b>{html.escape(_state_label(event))}</b> · "
            f"{html.escape(severity)}\n"
            f"含义：{html.escape(detailed_match.group('meaning'))}"
        )
    match = _CHANGE_PATTERN.fullmatch(line.strip())
    if match is None:
        return html.escape(line.strip()) if line.strip() else None
    event = match.group("event")
    emoji = _state_emoji(event)
    rule = match.group("rule").strip()
    if rule in {"状态变化", ""}:
        return f"{emoji} <b>{html.escape(_state_label(event))}</b>"
    return (
        f"{emoji} <code>{html.escape(rule)}</code>"
        f" · {html.escape(match.group('severity'))} · <b>{html.escape(event)}</b>"
    )


def _detailed_change_match(line: str) -> re.Match[str] | None:
    stripped = line.strip()
    return _DETAILED_CHANGE_PATTERN.fullmatch(stripped)


def _change_event(line: str) -> str | None:
    detailed_match = _detailed_change_match(line)
    if detailed_match is not None:
        return detailed_match.group("event")
    legacy_match = _CHANGE_PATTERN.fullmatch(line.strip())
    return legacy_match.group("event") if legacy_match is not None else None


def _change_banner(changes: list[str]) -> str:
    event_types = {event for line in changes if (event := _change_event(line)) is not None}
    if event_types == {"TRIGGERED"}:
        return "🟥 <b>新告警触发</b> · 状态较上次发生变化"
    if event_types == {"RECOVERED"}:
        return (
            "🟩 <b>告警已解除</b> · 状态较上次发生变化\n"
            "原触发条件当前已不成立；不代表价格上涨或行情转好。"
        )
    return f"🟨 <b>监控状态变化 · {len(changes)} 项</b>"


def _display_source_names(value: str) -> str:
    labels = {
        "ig_weekend_gold": "IG Weekend Gold（Apify）",
        "binance": "Binance PAXG/USDC",
        "hyperliquid": "Hyperliquid XYZ CL/USDC",
        "yfinance": "yfinance",
    }
    return ", ".join(labels.get(item.strip(), item.strip()) for item in value.split(","))


def _format_rule_cards(
    rows: tuple[tuple[str, ...], ...],
    *,
    symbol: str | None = None,
) -> str:
    attention = tuple(row for row in rows if row[4] not in {"QUIET", "RECOVERED"})
    quiet = tuple(row for row in rows if row[4] in {"QUIET", "RECOVERED"})
    sections: list[str] = []
    if attention:
        sections.append(
            f"<b>需关注 · {len(attention)} 项</b>" + _format_rule_table(attention, symbol=symbol)
        )
    else:
        sections.append("✅ <b>当前无触发或数据异常规则</b>")
    if quiet:
        sections.append(
            f"<details><summary>⚪ 未触发 · {len(quiet)} 项</summary>"
            f"{_format_rule_table(quiet, symbol=symbol)}</details>"
        )
    return "\n".join(sections)


def _format_rule_table(
    rows: tuple[tuple[str, ...], ...],
    *,
    symbol: str | None,
) -> str:
    table_rows = ["<table bordered striped>", "<tr><th>状态</th><th>规则与含义</th></tr>"]
    for row in rows:
        _rule, condition, _value, _distance, state, level = row[:6]
        meaning = row[6] if len(row) >= 7 else None
        state_cell = f"{_state_emoji(state)} <b>{html.escape(_compact_state_label(state))}</b>"
        if level != "INFO":
            state_cell += f" · {html.escape(_short_severity(level))}"
        display_meaning = _deduplicate_rule_meaning(
            meaning or "未提供规则说明",
            condition=condition,
            symbol=symbol,
        )
        rule_cell = f"<b>{html.escape(condition)}</b> · {html.escape(display_meaning)}"
        table_rows.append(
            "<tr>"
            f"<td>{state_cell}</td>"
            f"<td>{rule_cell}</td>"
            "</tr>"
        )
    table_rows.append("</table>")
    return "".join(table_rows)


def _deduplicate_rule_meaning(
    meaning: str,
    *,
    condition: str,
    symbol: str | None,
) -> str:
    """Drop a repeated same-symbol condition prefix from the human meaning."""
    if symbol is None or not symbol.strip():
        return meaning
    for separator in ("：", ":"):
        prefix, found, remainder = meaning.partition(separator)
        if not found or not remainder.strip():
            continue
        normalized_prefix = "".join(prefix.upper().split())
        normalized_symbol = "".join(symbol.upper().split())
        normalized_condition = "".join(condition.upper().split())
        if (
            normalized_prefix.startswith(normalized_symbol)
            and normalized_condition in normalized_prefix
        ):
            return remainder.strip()
    return meaning


def _headline_with_price(title: str, price: str | None) -> str:
    if price is None:
        return title
    prefix, separator, event = title.rpartition(" · ")
    if not separator:
        return f"{title} · {price}"
    return f"{prefix} · {price} · {event}"


def _headline_with_symbol_price(
    title: str,
    symbol: str | None,
    price: str | None,
) -> str:
    headline = title
    if symbol is not None and symbol.strip() and symbol.strip() not in title:
        prefix, separator, event = title.rpartition(" · ")
        headline = (
            f"{prefix} · {symbol.strip()} · {event}" if separator else f"{title} · {symbol.strip()}"
        )
    return _headline_with_price(headline, price)


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


def _state_emoji(state: str) -> str:
    return {
        "TRIGGERED": "🔴",
        "NOT_EVALUATED": "🟠",
        "RECOVERED": "🟢",
        "QUIET": "⚪️",
    }.get(state, "🔹")


def _state_label(state: str) -> str:
    return {
        "TRIGGERED": "已触发",
        "NOT_EVALUATED": "数据不可用",
        "RECOVERED": "告警解除",
        "QUIET": "未触发",
    }.get(state, state)


def _compact_state_label(state: str) -> str:
    return {
        "TRIGGERED": "触发",
        "NOT_EVALUATED": "不可用",
        "RECOVERED": "解除",
        "QUIET": "未触发",
    }.get(state, state)


def _short_severity(value: str) -> str:
    return {
        "M": "中",
        "MEDIUM": "中",
        "H": "高",
        "HIGH": "高",
    }.get(value.strip(), value.strip())


def _prefixed_value(lines: list[str], prefix: str) -> str | None:
    return next(
        (line.removeprefix(prefix).strip() for line in lines if line.startswith(prefix)),
        None,
    )


# Compatibility name retained for Monitor-only callers and old operational
# scripts. The adapter itself accepts any generic NotificationOutboxEntry.
TelegramMonitorNotificationAdapter = TelegramNotificationAdapter
