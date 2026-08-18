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
_NUMBERED_POINT_PATTERN = re.compile(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])")


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
            rendered = _render_rich_message_html(rendered)
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


def _render_rich_message_html(value: str) -> str:
    """Use explicit breaks because Rich Message HTML collapses plain newlines."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "<br>")


def _format_notification_html(title: str, body: str) -> str:
    """Render a mobile-first Telegram Rich Message with a native rule table."""
    lines = body.splitlines()
    if lines and lines[0] == "POST_MARKET_SUMMARY":
        return _format_post_market_summary_html(title, lines)
    try:
        rules_index = lines.index("RULES")
    except ValueError:
        if _is_standalone_judgment(lines):
            return _format_standalone_judgment_html(title, lines)
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
        formatted
        for line in changes
        if (formatted := _format_change(line, symbol=symbol)) is not None
    )
    if formatted_changes:
        sections.append(_change_banner(changes) + "\n" + "\n".join(formatted_changes))

    price_context = _format_price_context(
        price_basis=price_basis,
        price_time=price_time,
        data_source=data_source,
        previous_price=previous_price,
        price_change=price_change,
    )
    if price_context:
        sections.append(price_context)

    if judgment_lines:
        sections.append(_format_judgment_section(judgment_lines))
    changed_conditions = frozenset(
        match.group("condition").strip()
        for line in changes
        if (match := _detailed_change_match(line)) is not None
    )
    rule_cards = _format_rule_cards(
        rows,
        symbol=symbol,
        exclude_conditions=changed_conditions,
    )
    if rule_cards:
        sections.append(rule_cards)
    if notes:
        sections.append("\n".join(_format_notification_note(note) for note in notes))
    return "\n\n".join(sections)


def _is_standalone_judgment(lines: list[str]) -> bool:
    return any(line.startswith("结论：") for line in lines) or any(
        line.startswith("状态：复合判断") for line in lines
    )


def _format_standalone_judgment_html(title: str, lines: list[str]) -> str:
    sections = [f"<b>{html.escape(title)}</b>"]
    monitor_name = _prefixed_value(lines, "监控：")
    if monitor_name is not None and len(monitor_name) <= 80:
        sections.append(f"<i>{html.escape(monitor_name)}</i>")
    sections.append(_format_judgment_section(lines))
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
        sections.append("\n".join(_format_notification_note(note) for note in notes))
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
    price_context = _format_price_context(
        price_basis=price_basis,
        price_time=price_time,
        data_source=data_source,
        previous_price=previous_price,
        price_change=price_change,
    )
    if price_context:
        parts.append(price_context)
    formatted_changes = tuple(
        formatted
        for line in changes
        if (formatted := _format_change(line, symbol=symbol)) is not None
    )
    if formatted_changes:
        parts.append(_change_banner(changes) + "\n" + "\n".join(formatted_changes))
    for note in notes:
        if note not in {price_basis}:
            parts.append(_format_notification_note(note))
    if judgment_lines:
        parts.append(_format_judgment_section(judgment_lines))
    changed_conditions = frozenset(
        match.group("condition").strip()
        for line in changes
        if (match := _detailed_change_match(line)) is not None
    )
    rule_cards = _format_rule_cards(
        rows,
        symbol=symbol,
        exclude_conditions=changed_conditions,
    )
    if rule_cards:
        parts.append(rule_cards)
    return "\n".join(parts)


def _format_price_context(
    *,
    price_basis: str | None,
    price_time: str | None,
    data_source: str | None,
    previous_price: str | None,
    price_change: str | None,
) -> str:
    """Render one short market block without repeating source and basis."""

    lines: list[str] = []
    if price_change is not None:
        lines.append(f"变化：{html.escape(price_change)}")
    if previous_price is not None:
        lines.append(f"前值：{html.escape(previous_price)}")
    source = _display_source_names(data_source) if data_source is not None else None
    if price_basis is not None and source is not None:
        source_name = source.split("（", 1)[0]
        if source_name.casefold() in price_basis.casefold() and "（" not in source:
            lines.append(f"来源：{html.escape(price_basis)}")
        else:
            lines.append(f"来源：{html.escape(source)}")
            lines.append(f"口径：{html.escape(price_basis)}")
    elif source is not None:
        lines.append(f"来源：{html.escape(source)}")
    elif price_basis is not None:
        lines.append(f"来源：{html.escape(price_basis)}")
    if price_time is not None:
        lines.append(f"时间：{html.escape(_format_price_time(price_time))}")
    return "📈 <b>行情</b>\n" + "\n".join(lines) if lines else ""


def _format_notification_note(note: str) -> str:
    labels = (
        ("数据原因：", "⛔", "数据原因"),
        ("运行错误：", "⛔", "运行错误"),
        ("数据状态：", "ℹ️", "数据状态"),
        ("数据提示：", "ℹ️", "数据提示"),
        ("价格口径：", "📐", "价格口径"),
        ("周末口径：", "📐", "周末口径"),
        ("口径：", "📐", "价格口径"),
    )
    for prefix, icon, label in labels:
        if note.startswith(prefix):
            return (
                f"{icon} <b>{label}</b>："
                f"{html.escape(note.removeprefix(prefix).strip())}"
            )
    return f"<i>{html.escape(note)}</i>"


def _format_judgment_section(lines: list[str]) -> str:
    """Turn the model's bounded fields into a scannable mobile hierarchy."""

    fields = {
        prefix: value
        for prefix in (
            "状态",
            "错误码",
            "失败模型",
            "调用路径",
            "fallback",
            "失败阶段",
            "处理",
            "阶段",
            "结论",
            "市场",
            "背离",
            "依据",
            "关注",
            "失效",
            "说明",
        )
        if (value := _prefixed_value(lines, f"{prefix}：")) is not None
    }
    if "结论" not in fields:
        status = fields.get("状态", "复合判断暂时不可用；确定性规则结果仍然有效。")
        visible = ["⚠️ <b>复合判断不可用</b>", html.escape(status)]
        if error_code := fields.get("错误码"):
            visible.append(f"错误：<code>{html.escape(error_code)}</code>")
        diagnostics = [
            f"{label}：{html.escape(fields[label])}"
            for label in ("失败模型", "调用路径", "fallback", "失败阶段", "处理")
            if label in fields
        ]
        if diagnostics:
            visible.append(
                "<details><summary>模型诊断</summary>"
                + "\n".join(diagnostics)
                + "</details>"
            )
        return "\n".join(visible)

    conclusion, _, quantity = fields["结论"].partition(" · 数量 ")
    phase, _, urgency = fields.get("阶段", "未定义 · WATCH").partition(" · ")
    heading = f"🧭 <b>判断</b>：{html.escape(conclusion)}"
    if urgency:
        heading += f" · {html.escape(urgency)}"
    visible = [heading, f"阶段：{html.escape(phase)}"]
    if quantity:
        visible.append(f"数量：{html.escape(quantity)}")

    details: list[str] = []
    market_points = _split_judgment_points(fields.get("市场") or fields.get("状态"))
    next_points = _split_judgment_points(fields.get("关注"))
    market_visible, market_more = market_points[:2], market_points[2:]
    next_visible, next_more = next_points[:2], next_points[2:]
    if market_visible:
        visible.append(_format_point_block("市场观察", market_visible))
    if next_visible:
        visible.append(_format_point_block("下一关注", next_visible))
    if market_more:
        details.append(_format_point_block("市场补充", market_more))
    if next_more:
        details.append(_format_point_block("后续关注", next_more))

    divergence = fields.get("背离")
    if divergence:
        readable_divergence = "未发现" if divergence == "NONE" else divergence
        details.append(f"<b>背离</b>：{html.escape(readable_divergence)}")
    evidence = fields.get("依据")
    if evidence:
        evidence_count = len(tuple(item for item in evidence.split(",") if item.strip()))
        details.append(f"<b>判断依据</b>：{evidence_count} 项已记录特征")
    invalidation_points = _split_judgment_points(fields.get("失效"))
    if invalidation_points:
        details.append(_format_point_block("失效条件", invalidation_points))
    if details:
        visible.append(
            "<details><summary>更多判断与失效条件</summary>"
            + "\n".join(details)
            + "</details>"
        )
    visible.append("<i>只记录判断；未修改持仓、阶段或订单。</i>")
    return "\n".join(visible)


def _split_judgment_points(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()
    compact = " ".join(value.split())
    if any(marker in compact for marker in "①②③④⑤⑥⑦⑧⑨⑩"):
        raw = _NUMBERED_POINT_PATTERN.split(compact)
    else:
        raw = re.split(r"[；;]", compact)
    normalized = tuple(
        item.lstrip("①②③④⑤⑥⑦⑧⑨⑩ ").rstrip("。；; ")
        for item in raw
        if item.lstrip("①②③④⑤⑥⑦⑧⑨⑩ ").rstrip("。；; ")
    )
    return tuple(part for item in normalized for part in _split_long_judgment_point(item))


def _split_long_judgment_point(value: str, *, target: int = 88) -> tuple[str, ...]:
    """Wrap a long model clause at Chinese commas without dropping text."""

    if len(value) <= target or "，" not in value:
        return (value,)
    chunks: list[str] = []
    current = ""
    for part in value.split("，"):
        candidate = f"{current}，{part}" if current else part
        if current and len(candidate) > target:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def _format_point_block(title: str, points: tuple[str, ...]) -> str:
    return f"<b>{title}</b>\n" + "\n".join(f"• {html.escape(item)}" for item in points)


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


def _format_change(line: str, *, symbol: str | None = None) -> str | None:
    detailed_match = _detailed_change_match(line)
    if detailed_match is not None:
        event = detailed_match.group("event")
        severity = _short_severity(detailed_match.group("severity"))
        severity_suffix = f" · {html.escape(severity)}" if severity != "INFO" else ""
        meaning = _deduplicate_rule_meaning(
            detailed_match.group("meaning"),
            condition=detailed_match.group("condition"),
            symbol=symbol,
        )
        return (
            f"{_state_emoji(event)} <b>{html.escape(detailed_match.group('condition'))}</b>"
            f" · <b>{html.escape(_state_label(event))}</b>{severity_suffix}\n"
            f"{html.escape(meaning)}"
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
        return "🟥 <b>新告警</b>"
    if event_types == {"RECOVERED"}:
        return (
            "🟩 <b>告警解除</b>\n"
            "原触发条件当前已不成立；不代表价格上涨或行情转好。"
        )
    return f"🟨 <b>监控状态变化 · {len(changes)} 项</b>"


def _display_source_names(value: str) -> str:
    labels = {
        "ig_weekend_gold": "IG Weekend Gold（Apify）",
        "binance": "Binance PAXG/USDC",
        "hyperliquid": "Hyperliquid XYZ CL/USDC",
        "dukascopy": "Dukascopy",
        "yfinance": "yfinance",
    }
    return ", ".join(labels.get(item.strip(), item.strip()) for item in value.split(","))


def _format_rule_cards(
    rows: tuple[tuple[str, ...], ...],
    *,
    symbol: str | None = None,
    exclude_conditions: frozenset[str] = frozenset(),
) -> str:
    attention = tuple(
        row
        for row in rows
        if row[4] not in {"QUIET", "RECOVERED"} and row[1] not in exclude_conditions
    )
    quiet = tuple(row for row in rows if row[4] in {"QUIET", "RECOVERED"})
    sections: list[str] = []
    if attention:
        sections.append(
            f"🔔 <b>其他需关注 · {len(attention)} 项</b>"
            + _format_rule_table(attention, symbol=symbol)
        )
    elif not exclude_conditions:
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
