"""Notification-message rendering for Monitor evaluation runs.

Pure rendering over already-persisted run/event facts: transition cards,
post-market digests, price-context basis lines, and the session lookup the
digest timing uses. Extracted from monitor_evaluation_service so rule
evaluation and message formatting evolve independently; contracts unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from application.ports.id_generator import IdGenerator
from application.ports.market_session_calendar import MarketSession, MarketSessionCalendar
from application.services.monitor_event_analysis_service import (
    MONITOR_EVENT_ANALYSIS_MAX_CHARS,
)
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
    MonitorRunObservation,
)
from domain.notifications.enums import NotificationChannel, NotificationSourceType
from domain.notifications.models import NotificationMessage
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import TradePlanFactType


@dataclass(frozen=True, slots=True)
class _NotificationPriceContext:
    instrument_id: str | None
    symbol: str
    price: str
    price_time: str
    current_available: bool
    previous_price: Decimal | None = None
    previous_price_time: datetime | None = None


_RISK_RANK = {
    RiskOverallStatus.PASS: 0,
    RiskOverallStatus.WARN: 1,
    RiskOverallStatus.BREACH: 2,
}

_DUKASCOPY_PROVENANCE_WARNINGS = frozenset(
    {
        "DUKASCOPY_SWFX_NOT_LBMA",
        "OTC_BROKER_FEED",
        "VOLUME_BEST_BID_ASK_NOT_EXCHANGE",
        "DUKASCOPY_MINUTE_CLOSE_QUOTE_PROXY",
    }
)

_WEEKEND_PROXY_WARNINGS = frozenset(
    {
        "IG_WEEKEND_GOLD_CFD_FALLBACK",
        "WEEKEND_PROXY_NOT_SPOT",
        "IG_BROWSER_SCRAPE",
        "IG_WEEKEND_PRICE_SEPARATE_FROM_WEEKDAY_SPOT",
        "PAXG_USDC_WEEKEND_PROXY",
        "WEEKEND_PROXY_NOT_XAUUSD_SPOT",
        "TOKENIZED_GOLD_BASIS_RISK",
        "CL_USDC_WEEKEND_PROXY",
        "WEEKEND_PROXY_NOT_WTI_SPOT",
        "HIP3_PERPETUAL_BASIS_RISK",
        "USDC_PEG_RISK",
        "PRICE_TIME_IS_SCRAPE_TIME",
        "PRICE_TIME_IS_FETCH_TIME",
    }
)

_POST_MARKET_CADENCES = frozenset(
    {
        MonitorCadence.A_SHARE_POST_MARKET,
        MonitorCadence.US_POST_MARKET,
        MonitorCadence.KR_POST_MARKET,
    }
)


def _latest_completed_session(
    calendar: MarketSessionCalendar,
    moment: datetime,
) -> MarketSession | None:
    session = calendar.session_on_or_before(moment)
    if session is not None and session.close_at > moment:
        return calendar.previous_session(session.session_date)
    return session


def _notification_messages(
    monitor: MonitorDefinition,
    events: tuple[MonitorEvent, ...],
    observations: tuple[MonitorRunObservation, ...],
    previous_states: dict[str, MonitorRuleState],
    data_sources: tuple[str, ...],
    id_generator: IdGenerator,
    *,
    source_id: str | None = None,
    created_at: datetime | None = None,
    event_label_override: str | None = None,
    emoji_override: str | None = None,
) -> tuple[NotificationMessage, ...]:
    event_types = {event.event_type for event in events}
    if events and event_types == {MonitorEventType.NOT_EVALUATED}:
        return (
            _data_interruption_message(
                monitor=monitor,
                events=events,
                observations=observations,
                previous_states=previous_states,
                data_sources=data_sources,
                id_generator=id_generator,
                source_id=source_id,
                created_at=created_at,
            ),
        )
    unavailable_event_count = sum(
        item.event_type is MonitorEventType.NOT_EVALUATED for item in events
    )
    if unavailable_event_count:
        events = tuple(
            item for item in events if item.event_type is not MonitorEventType.NOT_EVALUATED
        )
        event_types = {event.event_type for event in events}
    emoji = emoji_override or (
        "🚨"
        if MonitorEventType.TRIGGERED in event_types
        else "⚠️"
        if MonitorEventType.NOT_EVALUATED in event_types
        else "✅"
    )
    rules_by_code = {item.rule_code: item for item in monitor.rules}
    context = _notification_price_context(monitor, observations, previous_states)
    warning_codes = tuple(
        dict.fromkeys(code for observation in observations for code in observation.warning_codes)
    )
    lines = [monitor.name, f"标的：{context.symbol}", f"当前价格：{context.price}"]
    provenance_basis, remaining_warning_codes = _notification_warning_lines(warning_codes)
    price_basis = _notification_price_basis(
        context,
        warning_codes,
        provenance_basis=provenance_basis,
    )
    if price_basis is not None:
        lines.append(f"价格口径：{price_basis}")
    lines.append(f"价格时间：{context.price_time}")
    lines.extend(_notification_price_change_lines(context))
    if data_sources:
        lines.append(f"数据来源：{', '.join(data_sources)}")
    if unavailable_event_count:
        lines.append(
            f"数据状态：部分中断 · {unavailable_event_count} 条规则暂停计算；未使用旧值改变其结论"
        )
    recovered_count = sum(
        1
        for item in observations
        if item.state is not MonitorRuleStateValue.NOT_EVALUATED
        and (prior := previous_states.get(item.rule_code)) is not None
        and prior.state is MonitorRuleStateValue.NOT_EVALUATED
    )
    if recovered_count:
        lines.append(f"数据状态：已恢复并重新计算 {recovered_count} 条规则")
    lines.append("CHANGES")
    lines.extend(
        _format_notification_change(
            event,
            rules_by_code[event.rule_code],
        )
        for event in events
    )
    lines.append("RULES")
    for observation in observations:
        if unavailable_event_count and observation.state is MonitorRuleStateValue.NOT_EVALUATED:
            continue
        rule = rules_by_code[observation.rule_code]
        lines.append(_format_notification_rule_card(rule, observation))
    error_codes = tuple(
        dict.fromkeys(code for observation in observations for code in observation.error_codes)
    )
    not_evaluated = tuple(
        item for item in observations if item.state is MonitorRuleStateValue.NOT_EVALUATED
    )
    if error_codes:
        lines.append(f"数据原因：{'、'.join(error_codes)}")
    elif not_evaluated:
        causes = tuple(
            dict.fromkeys(_notification_unavailable_cause(item) for item in not_evaluated)
        )
        lines.append(f"数据原因：{'; '.join(_notification_text(item, 160) for item in causes)}")
    if remaining_warning_codes:
        lines.append(f"数据提示：{'、'.join(remaining_warning_codes)}")
    if context.instrument_id is not None and context.instrument_id.startswith("future:"):
        lines.append("期货价格并非现货；连续合约存在换月风险。")
    event_label = event_label_override or _notification_event_label(events, rules_by_code)
    if unavailable_event_count:
        event_label = f"数据部分中断 · {event_label}"
        emoji = "⚠️"
    title = _notification_title(
        emoji,
        _notification_event_symbol(events, rules_by_code, context.symbol),
        event_label,
    )
    body = "\n".join(lines)
    first_event = events[0] if events else None
    notification_source_id = source_id or (first_event.event_id if first_event else None)
    notification_created_at = created_at or (first_event.created_at if first_event else None)
    if notification_source_id is None or notification_created_at is None:
        raise DataContractError("monitor notification requires an event or explicit source context")
    return (
        NotificationMessage(
            notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
            source_type=NotificationSourceType.MONITOR_EVENT,
            source_id=notification_source_id,
            channel=NotificationChannel.TELEGRAM,
            title=title,
            body=body,
            created_at=notification_created_at,
        ),
    )


def _data_interruption_message(
    *,
    monitor: MonitorDefinition,
    events: tuple[MonitorEvent, ...],
    observations: tuple[MonitorRunObservation, ...],
    previous_states: dict[str, MonitorRuleState],
    data_sources: tuple[str, ...],
    id_generator: IdGenerator,
    source_id: str | None,
    created_at: datetime | None,
) -> NotificationMessage:
    affected = tuple(
        item for item in observations if item.state is MonitorRuleStateValue.NOT_EVALUATED
    )
    context = _notification_price_context(monitor, observations, previous_states)
    error_codes = tuple(dict.fromkeys(code for item in affected for code in item.error_codes))
    diagnostics = tuple(
        dict.fromkeys(
            (item.provider, item.stage, item.error_code)
            for observation in affected
            for item in observation.diagnostics
        )
    )
    lines = [
        f"监控：{monitor.name}",
        f"标的：{context.symbol}",
        "数据状态：中断",
        f"影响：{len(affected)} 条规则暂停计算；未改变原有触发结论",
    ]
    if context.previous_price is not None:
        lines.extend(
            (
                f"上一有效价格：{context.previous_price}",
                "价格时间："
                + (
                    context.previous_price_time.isoformat()
                    if context.previous_price_time
                    else "不可用"
                ),
            )
        )
    if diagnostics:
        lines.append(
            "诊断："
            + "；".join(f"{provider} / {stage} / {code}" for provider, stage, code in diagnostics)
        )
    elif error_codes:
        lines.append("错误：" + ", ".join(error_codes))
    if data_sources:
        lines.append(f"已取得的其他来源：{', '.join(data_sources)}")
    lines.append("处理：等待下一轮自动重试；不会使用旧价格判定这些规则。")
    first_event = events[0]
    return NotificationMessage(
        notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id=source_id or first_event.event_id,
        channel=NotificationChannel.TELEGRAM,
        title=f"⛔ {context.symbol} · 数据源中断",
        body="\n".join(lines),
        created_at=created_at or first_event.created_at,
    )


def _data_recovery_message(
    *,
    run_id: str,
    monitor: MonitorDefinition,
    observations: tuple[MonitorRunObservation, ...],
    recovered: tuple[MonitorRunObservation, ...],
    data_sources: tuple[str, ...],
    id_generator: IdGenerator,
    created_at: datetime,
) -> NotificationMessage:
    context = _notification_price_context(monitor, observations, {})
    lines = [
        f"监控：{monitor.name}",
        f"标的：{context.symbol}",
        "数据状态：已恢复",
        f"结果：{len(recovered)} 条规则已重新计算，当前没有新的价格告警变化",
        f"当前价格：{context.price}",
        f"价格时间：{context.price_time}",
    ]
    if data_sources:
        lines.append(f"数据来源：{', '.join(data_sources)}")
    lines.append("说明：这里只表示数据源恢复，不代表价格上涨或行情转好。")
    return NotificationMessage(
        notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
        source_type=NotificationSourceType.MONITOR_RUN,
        source_id=run_id,
        channel=NotificationChannel.TELEGRAM,
        title=f"🔵 {context.symbol} · 数据恢复",
        body="\n".join(lines),
        created_at=created_at,
    )


def _append_judgment_notification(
    base: NotificationMessage,
    judgment: NotificationMessage,
) -> NotificationMessage:
    prefix = f"{base.body}\n\nJUDGMENT\n"
    available = max(0, 4096 - len(prefix))
    judgment_body = judgment.body
    if len(judgment_body) > available:
        judgment_body = judgment_body[: max(0, available - 1)].rstrip() + "…"
    return NotificationMessage(
        notification_id=base.notification_id,
        source_type=base.source_type,
        source_id=base.source_id,
        channel=base.channel,
        title=base.title,
        body=prefix + judgment_body,
        created_at=base.created_at,
    )


def _append_model_analysis(
    base: NotificationMessage,
    analysis: str,
    *,
    max_chars: int = MONITOR_EVENT_ANALYSIS_MAX_CHARS,
) -> NotificationMessage:
    """Append one bounded model interpretation after deterministic content."""

    normalized = " ".join(analysis.split()).strip()
    limit = max(40, min(max_chars, 300))
    if len(normalized) > limit:
        normalized = normalized[: max(1, limit - 1)].rstrip("，。；、 ") + "…"
    suffix = f"\n\nMODEL_ANALYSIS\n{normalized}"
    available = max(0, 4096 - len(suffix))
    base_body = base.body
    if len(base_body) > available:
        base_body = base_body[: max(0, available - 1)].rstrip() + "…"
    return NotificationMessage(
        notification_id=base.notification_id,
        source_type=base.source_type,
        source_id=base.source_id,
        channel=base.channel,
        title=base.title,
        body=base_body + suffix,
        created_at=base.created_at,
    )


def _notification_event_symbol(
    events: tuple[MonitorEvent, ...],
    rules_by_code: dict[str, MonitorRule],
    default: str,
) -> str:
    symbols = tuple(
        dict.fromkeys(
            rule.instrument_id.rsplit(":", 1)[-1]
            for event in events
            if (rule := rules_by_code.get(event.rule_code)) is not None
            and rule.instrument_id is not None
        )
    )
    return symbols[0] if len(symbols) == 1 else default


def _weekend_proxy_price_basis(warning_codes: tuple[str, ...]) -> str | None:
    if "PAXG_USDC_WEEKEND_PROXY" in warning_codes:
        return (
            "Binance PAXG/USDC 代币化黄金现货周末代理；不是 XAUUSD 或 LBMA "
            "基准价，存在代币、场所、流动性及 USDC 基差，时间为抓取时间"
        )
    if "IG_WEEKEND_GOLD_CFD_FALLBACK" in warning_codes:
        return (
            "IG Weekend Gold CFD 周末代理；不是 XAUUSD 现货黄金或 LBMA 基准价，"
            "价格与工作日市场分开形成，时间为网页抓取时间"
        )
    if "CL_USDC_WEEKEND_PROXY" in warning_codes:
        return (
            "Hyperliquid XYZ CL/USDC 永续合约周末代理；不是 LIGHT.CMD-USD/USOIL、"
            "WTI 现货或 NYMEX CL，存在永续、场所及 USDC 基差，时间为抓取时间"
        )
    return None


def _notification_price_basis(
    context: _NotificationPriceContext,
    warning_codes: tuple[str, ...],
    *,
    provenance_basis: str | None,
) -> str | None:
    parts: list[str] = []
    if not context.current_available and context.previous_price is not None:
        parts.append("上一有效价格（当前不可用）")
    proxy_basis = _weekend_proxy_price_basis(warning_codes)
    if proxy_basis is not None:
        parts.append(proxy_basis)
    if provenance_basis is not None and proxy_basis is None:
        parts.append(provenance_basis)
    return "；".join(parts) or None


def _notification_unavailable_cause(observation: MonitorRunObservation) -> str:
    if "TECHNICAL_DATA_NOT_FRESH" in observation.warning_codes:
        return "技术指标数据超过规则允许的新鲜度"
    if observation.message == "Required fact exceeded the rule freshness limit.":
        return "所需事实超过规则允许的新鲜度"
    return observation.message


def _signed_decimal(value: Decimal, *, decimal_places: int | None = None) -> str:
    if decimal_places is not None:
        quantum = Decimal(1).scaleb(-decimal_places)
        value = value.quantize(quantum, rounding=ROUND_HALF_UP)
        # Decimal preserves a negative sign on a rounded zero (for example,
        # -0.004 quantizes to -0.00). A zero change has no direction.
        if value == 0:
            value = abs(value)
        rendered = format(value, f".{decimal_places}f")
    else:
        rendered = format(value, "f")
        if rendered == "-0":
            rendered = "0"
    if "." in rendered and decimal_places is None:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"+{rendered}" if value > 0 else rendered


def _notification_price_change_lines(
    context: _NotificationPriceContext,
) -> tuple[str, ...]:
    """Render a current-vs-previous price delta for a notification block.

    The raw price delta keeps its natural Decimal precision. Percentages are
    deliberately quantized half-up to two decimal places because they are the
    compact, human-facing value shown in Telegram.
    """
    if not context.current_available or context.previous_price is None:
        return ()
    current_price = Decimal(context.price)
    price_change = current_price - context.previous_price
    change_percent = (
        price_change / context.previous_price * Decimal("100")
        if context.previous_price != 0
        else None
    )
    rendered_change = _signed_decimal(price_change)
    if change_percent is not None:
        rendered_change = (
            f"{rendered_change} ({_signed_decimal(change_percent, decimal_places=2)}%)"
        )
    return (
        f"上次价格：{context.previous_price}",
        f"价格变化：{rendered_change}",
    )


def _notification_event_label(
    events: tuple[MonitorEvent, ...],
    rules_by_code: dict[str, MonitorRule] | None = None,
) -> str:
    if len(events) != 1:
        return f"{len(events)}项变化"
    event_label = {
        MonitorEventType.TRIGGERED: "新触发",
        MonitorEventType.RECOVERED: "告警解除",
        MonitorEventType.NOT_EVALUATED: "数据不可用",
    }[events[0].event_type]
    rule = (rules_by_code or {}).get(events[0].rule_code)
    if rule is None:
        return event_label
    return f"{_notification_text(_rule_condition(rule), 48)} {event_label}"


def _notification_title(emoji: str, symbol: str, event_label: str) -> str:
    suffix = f" · {event_label}"
    available_symbol_chars = max(1, 200 - len(emoji) - 1 - len(suffix))
    return f"{emoji} {_notification_text(symbol, available_symbol_chars)}{suffix}"


def _notification_text(value: str, maximum: int) -> str:
    """Keep human-authored/provider text single-line and bounded in notices."""
    compact = " ".join(value.split())
    compact = compact.replace("·", "/").replace("｜", "/")
    if len(compact) <= maximum:
        return compact
    return compact[: maximum - 1].rstrip() + "…"


def _notification_warning_lines(
    warning_codes: tuple[str, ...],
) -> tuple[str | None, tuple[str, ...]]:
    provenance = _DUKASCOPY_PROVENANCE_WARNINGS.intersection(warning_codes)
    consumed = provenance | _WEEKEND_PROXY_WARNINGS.intersection(warning_codes)
    remaining = tuple(code for code in warning_codes if code not in consumed)
    return (
        "Dukascopy OTC，非 LBMA" if provenance else None,
        remaining,
    )


def _rule_meaning(description: str | None) -> str:
    """Use a readable first clause without the legacy 32-character clipping."""
    if description is None or not description.strip():
        return "未提供规则说明"
    compact = " ".join(description.split())
    first_clause = re.split(r"[。！？!?；;]", compact, maxsplit=1)[0].strip()
    # Monitor descriptions are already contract-bounded to 500 characters. Keep
    # a wider final guard for pathological no-punctuation text while allowing a
    # normal complete meaning to wrap naturally inside Telegram's native table.
    return _notification_text(first_clause or compact, 160)


def _format_notification_rule_card(
    rule: MonitorRule,
    observation: MonitorRunObservation,
) -> str:
    # A compact, delimiter-based line is intentionally not a Markdown/fixed-width
    # table. The Telegram adapter parses this shape while retaining the legacy
    # table parser for already persisted outbox bodies.
    parts = [
        f"• 状态：{observation.state.value}",
        f"条件：{_notification_text(_rule_condition(rule), 96)}",
        f"含义：{_rule_meaning(rule.description)}",
    ]
    if observation.severity.value != "INFO":
        parts.append(f"级别：{_short_severity(observation.severity.value)}")
    return " · ".join(parts)


def _short_severity(value: str) -> str:
    return {"MEDIUM": "M", "HIGH": "H"}.get(value, value)


def _format_notification_change(event: MonitorEvent, rule: MonitorRule) -> str:
    """Serialize one transition with the exact rule context needed by Telegram.

    Transition messages are persisted before delivery, so this line intentionally
    carries the bounded condition and human meaning instead of requiring the
    notification adapter to look up a Monitor definition later.
    """
    return (
        f"• [{event.severity.value}] {rule.rule_code} · "
        f"条件：{_notification_text(_rule_condition(rule), 96)} · "
        f"含义：{_rule_meaning(rule.description)} → {event.event_type.value}"
    )


def _notification_price_context(
    monitor: MonitorDefinition,
    observations: tuple[MonitorRunObservation, ...],
    previous_states: dict[str, MonitorRuleState] | None = None,
) -> _NotificationPriceContext:
    rules_by_code = {item.rule_code: item for item in monitor.rules}

    def is_price_rule(observation: MonitorRunObservation) -> bool:
        rule = rules_by_code.get(observation.rule_code)
        return bool(
            rule is not None
            and (
                rule.rule_type in {MonitorRuleType.PRICE_ABOVE, MonitorRuleType.PRICE_BELOW}
                or rule.fact_type is TradePlanFactType.PRICE
            )
        )

    instrument_id = monitor.primary_instrument_id or next(
        (item.instrument_id for item in observations if is_price_rule(item)),
        None,
    )
    if instrument_id is None:
        instrument_id = next(
            (item.instrument_id for item in observations if item.instrument_id is not None),
            None,
        )
    symbol = instrument_id.rsplit(":", 1)[-1] if instrument_id else monitor.name
    candidates = tuple(
        item for item in observations if is_price_rule(item) and item.instrument_id == instrument_id
    )
    if not candidates:
        fallback_instrument_id = next(
            (item.instrument_id for item in observations if is_price_rule(item)),
            None,
        )
        if fallback_instrument_id is not None:
            instrument_id = fallback_instrument_id
            symbol = fallback_instrument_id.rsplit(":", 1)[-1]
            candidates = tuple(
                item
                for item in observations
                if is_price_rule(item) and item.instrument_id == fallback_instrument_id
            )
    current = next((item for item in candidates if item.observed_value is not None), None)
    previous: MonitorRuleState | None = None
    if previous_states:
        previous = next(
            (
                previous_states.get(item.rule_code)
                for item in candidates
                if previous_states.get(item.rule_code) is not None
                and previous_states[item.rule_code].observed_value is not None
            ),
            None,
        )
        if previous is None:
            previous = next(
                (
                    state
                    for code, state in previous_states.items()
                    if state.observed_value is not None
                    and rules_by_code.get(code) is not None
                    and (
                        rules_by_code[code].rule_type
                        in {MonitorRuleType.PRICE_ABOVE, MonitorRuleType.PRICE_BELOW}
                        or rules_by_code[code].fact_type is TradePlanFactType.PRICE
                    )
                ),
                None,
            )
    if current is not None:
        return _NotificationPriceContext(
            instrument_id=instrument_id,
            symbol=symbol,
            price=str(current.observed_value),
            price_time=(
                current.fact_as_of.isoformat() if current.fact_as_of is not None else "不可用"
            ),
            current_available=True,
            previous_price=(previous.observed_value if previous is not None else None),
            previous_price_time=(previous.fact_as_of if previous is not None else None),
        )
    if previous is not None:
        return _NotificationPriceContext(
            instrument_id=instrument_id,
            symbol=symbol,
            price=str(previous.observed_value),
            price_time=(
                previous.fact_as_of.isoformat() if previous.fact_as_of is not None else "不可用"
            ),
            current_available=False,
            previous_price=previous.observed_value,
            previous_price_time=previous.fact_as_of,
        )
    return _NotificationPriceContext(
        instrument_id=instrument_id,
        symbol=symbol,
        price="不可用",
        price_time=(
            candidates[0].fact_as_of.isoformat()
            if candidates and candidates[0].fact_as_of is not None
            else "不可用"
        ),
        current_available=False,
    )


def _post_market_summary_message(
    run: MonitorRun,
    monitors: tuple[MonitorDefinition, ...],
    id_generator: IdGenerator,
    *,
    events: tuple[MonitorEvent, ...] = (),
    previous_states_by_monitor: dict[str, dict[str, MonitorRuleState]] | None = None,
    monitor_sources_by_monitor: dict[str, tuple[str, ...]] | None = None,
    judgment_notifications_by_monitor: dict[str, NotificationMessage] | None = None,
    event_analyses_by_monitor: dict[str, str] | None = None,
) -> NotificationMessage:
    if run.cadence is MonitorCadence.A_SHARE_POST_MARKET:
        market_label = "A股"
    elif run.cadence is MonitorCadence.US_POST_MARKET:
        market_label = "美股"
    elif run.cadence is MonitorCadence.KR_POST_MARKET:
        market_label = "韩股"
    else:
        market_label = "市场"
    lines = [
        "POST_MARKET_SUMMARY",
        f"运行时间：{run.completed_at.isoformat()}",
        f"本轮变化：{run.events_created}",
    ]
    observations_by_monitor = {
        monitor.monitor_id: tuple(
            item for item in run.observations if item.monitor_id == monitor.monitor_id
        )
        for monitor in monitors
    }
    events_by_monitor = {
        monitor.monitor_id: tuple(item for item in events if item.monitor_id == monitor.monitor_id)
        for monitor in monitors
    }
    for monitor in monitors:
        observations = observations_by_monitor[monitor.monitor_id]
        monitor_events = events_by_monitor[monitor.monitor_id]
        context = _notification_price_context(
            monitor,
            observations,
            (previous_states_by_monitor or {}).get(monitor.monitor_id),
        )
        monitor_warning_codes = tuple(
            dict.fromkeys(code for item in observations for code in item.warning_codes)
        )
        provenance_basis, _ = _notification_warning_lines(monitor_warning_codes)
        price_basis = _notification_price_basis(
            context,
            monitor_warning_codes,
            provenance_basis=provenance_basis,
        )
        lines.extend(
            (
                "MONITOR",
                monitor.name,
                f"标的：{context.symbol}",
                f"当前价格：{context.price}",
                *((f"价格口径：{price_basis}",) if price_basis is not None else ()),
                f"价格时间：{context.price_time}",
                *_notification_price_change_lines(context),
                *(
                    (
                        "数据来源："
                        + ", ".join((monitor_sources_by_monitor or {}).get(monitor.monitor_id, ())),
                    )
                    if (monitor_sources_by_monitor or {}).get(monitor.monitor_id)
                    else ()
                ),
                *(("CHANGES",) if monitor_events else ()),
                *(
                    tuple(
                        _format_notification_change(
                            event,
                            {item.rule_code: item for item in monitor.rules}[event.rule_code],
                        )
                        for event in monitor_events
                    )
                ),
                "RULES",
            )
        )
        lines.extend(
            _format_notification_rule_card(
                {item.rule_code: item for item in monitor.rules}[observation.rule_code],
                observation,
            )
            for observation in observations
        )
        judgment_notification = (judgment_notifications_by_monitor or {}).get(monitor.monitor_id)
        if judgment_notification is not None:
            lines.append("JUDGMENT")
            lines.extend(judgment_notification.body.splitlines())
        lines.append("END_MONITOR")
        if context.instrument_id is not None and context.instrument_id.startswith("future:"):
            lines.append("期货价格并非现货；连续合约存在换月风险。")
    all_not_evaluated = tuple(
        item for item in run.observations if item.state is MonitorRuleStateValue.NOT_EVALUATED
    )
    causes = tuple(
        dict.fromkeys(
            code
            for item in all_not_evaluated
            for code in (
                *item.error_codes,
                *tuple(
                    warning
                    for warning in item.warning_codes
                    if warning not in (_DUKASCOPY_PROVENANCE_WARNINGS | _WEEKEND_PROXY_WARNINGS)
                ),
            )
        )
    )
    if causes:
        lines.append(f"数据原因：{'、'.join(causes)}")
    elif all_not_evaluated:
        lines.append(
            "数据原因："
            + "; ".join(
                _notification_text(message, 160)
                for message in dict.fromkeys(item.message for item in all_not_evaluated)
            )
        )
    cause_codes = set(causes)
    _, remaining_warning_codes = _notification_warning_lines(run.warning_codes)
    warning_codes = tuple(code for code in remaining_warning_codes if code not in cause_codes)
    error_codes = tuple(code for code in run.error_codes if code not in cause_codes)
    if warning_codes:
        lines.append(f"数据提示：{'、'.join(warning_codes)}")
    if error_codes:
        lines.append(f"运行错误：{'、'.join(error_codes)}")
    event_analyses = event_analyses_by_monitor or {}
    if event_analyses:
        lines.append("MODEL_ANALYSIS")
        for monitor in monitors:
            if analysis := event_analyses.get(monitor.monitor_id):
                lines.append(f"{monitor.name}：{analysis}")
    return NotificationMessage(
        notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
        source_type=NotificationSourceType.MONITOR_RUN,
        source_id=run.run_id,
        channel=NotificationChannel.TELEGRAM,
        title=(f"📊 {market_label}盘后 Monitor · {len(monitors)} 标的 · {run.events_created} 变化"),
        body="\n".join(lines),
        created_at=run.completed_at,
    )


def _monitor_price_context(
    monitor: MonitorDefinition,
    observations: tuple[MonitorRunObservation, ...],
    previous_states: dict[str, MonitorRuleState] | None = None,
) -> tuple[str | None, str, str, str]:
    context = _notification_price_context(monitor, observations, previous_states)
    return context.instrument_id, context.symbol, context.price, context.price_time


def _notification_rule_rows(
    monitor: MonitorDefinition,
    observations: tuple[MonitorRunObservation, ...],
) -> tuple[tuple[str, ...], ...]:
    rules_by_code = {item.rule_code: item for item in monitor.rules}
    return tuple(
        (
            observation.rule_code,
            _rule_condition(rules_by_code[observation.rule_code]),
            (
                str(observation.observed_value)
                if observation.observed_value is not None
                else "不可用"
            ),
            (
                str(observation.distance_value)
                if observation.distance_value is not None
                else "不可用"
            ),
            observation.state.value,
            observation.severity.value,
        )
        for observation in observations
    )


def _rule_condition(rule: MonitorRule) -> str:
    if rule.rule_type is MonitorRuleType.PRICE_ABOVE:
        return f"> {rule.price_threshold}"
    if rule.rule_type is MonitorRuleType.PRICE_BELOW:
        return f"< {rule.price_threshold}"
    if rule.rule_type is MonitorRuleType.RISK_OVERALL_AT_LEAST:
        assert rule.risk_status_threshold is not None
        return f">= {rule.risk_status_threshold.value}"
    comparator = (
        {
            "GT": ">",
            "GTE": "≥",
            "LT": "<",
            "LTE": "≤",
            "EQ": "=",
            "OCCURRED": "已发生",
        }.get(rule.comparator.value, rule.comparator.value)
        if rule.comparator is not None
        else "未知条件"
    )
    threshold = (
        str(rule.numeric_threshold)
        if rule.numeric_threshold is not None
        else rule.event_after.isoformat()
        if rule.event_after is not None
        else "事件发生"
    )
    interval = ""
    metric_key = rule.metric_key
    if rule.fact_type is TradePlanFactType.TECHNICAL:
        interval = {"1d": "日线 ", "1w": "周线 "}.get(
            rule.technical_interval or "1d", f"{rule.technical_interval} "
        )
        metric_key = {
            "rsi_14": "RSI14",
            "mfi_14": "MFI14",
            "atr_14": "ATR14",
        }.get(rule.metric_key or "", rule.metric_key)
    recovery = (
        f"；恢复阈值 {rule.recovery_threshold}" if rule.recovery_threshold is not None else ""
    )
    metric = (
        ""
        if rule.fact_type is TradePlanFactType.PRICE and rule.metric_key == "last"
        else f"{metric_key} "
    )
    return f"{interval}{metric}{comparator} {threshold}{recovery}"


def _format_rule_table(rows: tuple[tuple[str, ...], ...]) -> str:
    headers = ("RULE", "COND", "VALUE", "DIST", "STATE", "LEVEL")
    widths = tuple(
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    )

    def render(row: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()

    separator = "  ".join("-" * width for width in widths)
    return "\n".join((render(headers), separator, *(render(row) for row in rows)))
