"""Deterministic on-demand and scheduled Monitoring evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from application.dto.a_share import AShareGetSnapshotInput
from application.dto.cross_asset import SpotObservationDTO
from application.dto.monitoring import MonitorEvaluateInput
from application.dto.risk import RiskCheckInput
from application.dto.us_market import MarketGetSnapshotInput, USQuoteDTO
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.monitor_repository import MonitorRepository
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.market_tool_coordinator import MarketToolCoordinator
from application.services.monitor_fact_resolver import MonitorFactResolver
from application.services.risk_tool_coordinator import RiskToolCoordinator
from domain.common.enums import Market
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorStatus,
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
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType


@dataclass(frozen=True, slots=True)
class _Fact:
    value: Decimal | None
    as_of: datetime | None
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    closed_session_last_known: bool = False
    source_names: tuple[str, ...] = ()


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

_POST_MARKET_CADENCES = frozenset(
    {
        MonitorCadence.A_SHARE_POST_MARKET,
        MonitorCadence.US_POST_MARKET,
        MonitorCadence.KR_POST_MARKET,
    }
)


class MonitorEvaluationService:
    def __init__(
        self,
        repository: MonitorRepository,
        a_share: AShareToolCoordinator,
        market: MarketToolCoordinator,
        risk: RiskToolCoordinator,
        clock: Clock,
        id_generator: IdGenerator,
        fact_resolver: MonitorFactResolver | None = None,
    ) -> None:
        self._repository = repository
        self._a_share = a_share
        self._market = market
        self._risk = risk
        self._clock = clock
        self._ids = id_generator
        self._fact_resolver = fact_resolver

    async def evaluate(self, request: MonitorEvaluateInput) -> MonitorRun:
        started_at = self._clock.now()
        as_of = request.as_of or started_at
        if as_of > started_at:
            raise DataContractError("monitor as_of must not be in the future")
        monitors, selection_warnings = self._select(request, as_of=as_of)
        run_id = self._ids.new(EntityIdPrefix.MONITOR_RUN)
        price_cache: dict[str, _Fact] = {}
        risk_fact: _Fact | None = None
        generic_cache: dict[tuple[object, ...], _Fact] = {}
        states: list[MonitorRuleState] = []
        events: list[MonitorEvent] = []
        notifications: list[NotificationMessage] = []
        observations: list[MonitorRunObservation] = []
        warnings = list(selection_warnings)
        errors: list[str] = []
        rules_evaluated = 0
        previous_states_by_monitor: dict[str, dict[str, MonitorRuleState]] = {}
        monitor_sources_by_monitor: dict[str, tuple[str, ...]] = {}

        for monitor in monitors:
            monitor_events: list[MonitorEvent] = []
            monitor_observations: list[MonitorRunObservation] = []
            monitor_sources: list[str] = []
            previous = {
                item.rule_code: item
                for item in self._repository.get_rule_states(monitor.monitor_id)
            }
            previous_states_by_monitor[monitor.monitor_id] = previous
            for rule in monitor.rules:
                rules_evaluated += 1
                if rule.rule_type in {
                    MonitorRuleType.PRICE_ABOVE,
                    MonitorRuleType.PRICE_BELOW,
                }:
                    assert rule.instrument_id is not None
                    fact = price_cache.get(rule.instrument_id)
                    if fact is None:
                        fact = await self._price_fact(rule.instrument_id, as_of)
                        price_cache[rule.instrument_id] = fact
                elif rule.rule_type is MonitorRuleType.RISK_OVERALL_AT_LEAST:
                    if risk_fact is None:
                        risk_fact = await self._risk_fact(as_of)
                    fact = risk_fact
                elif rule.fact_type is TradePlanFactType.PRICE:
                    assert rule.instrument_id is not None
                    fact = price_cache.get(rule.instrument_id)
                    if fact is None:
                        fact = await self._price_fact(rule.instrument_id, as_of)
                        price_cache[rule.instrument_id] = fact
                elif rule.fact_type is TradePlanFactType.PORTFOLIO_RISK:
                    if risk_fact is None:
                        risk_fact = await self._risk_fact(as_of)
                    fact = risk_fact
                else:
                    cache_key = (
                        rule.fact_type,
                        rule.instrument_id,
                        rule.metric_key,
                        rule.event_after,
                    )
                    fact = generic_cache.get(cache_key)
                    if fact is None:
                        if self._fact_resolver is None:
                            fact = _Fact(
                                None,
                                None,
                                (),
                                ("MONITOR_FACT_RESOLVER_NOT_CONFIGURED",),
                            )
                        else:
                            resolved = await self._fact_resolver.resolve(rule, as_of)
                            fact = _Fact(
                                resolved.value,
                                resolved.as_of,
                                resolved.warning_codes,
                                resolved.error_codes,
                                resolved.closed_session_last_known,
                            )
                        generic_cache[cache_key] = fact
                warnings.extend(fact.warning_codes)
                errors.extend(fact.error_codes)
                monitor_sources.extend(fact.source_names)
                # A live Provider request can finish after the run's request
                # timestamp. Judge freshness at the actual evaluation moment;
                # explicit historical ``as_of`` requests remain strict cutoffs.
                evaluated_at = as_of if request.as_of is not None else self._clock.now()
                state = self._evaluate_rule(monitor, rule, fact, evaluated_at)
                states.append(state)
                observation = _run_observation(
                    run_id=run_id,
                    monitor=monitor,
                    rule=rule,
                    fact=fact,
                    state=state,
                    as_of=evaluated_at,
                )
                observations.append(observation)
                monitor_observations.append(observation)
                event = self._transition_event(
                    monitor,
                    rule,
                    previous.get(rule.rule_code),
                    state,
                    evaluated_at,
                )
                if event is not None:
                    events.append(event)
                    monitor_events.append(event)
            # Post-market runs persist each transition event for the durable
            # event history, but their Telegram delivery is consolidated into
            # one run-linked digest below. Other cadences retain one
            # event-linked notification per transition event.
            if monitor_events and request.cadence not in _POST_MARKET_CADENCES:
                monitor_sources_by_monitor[monitor.monitor_id] = tuple(
                    dict.fromkeys(monitor_sources)
                )
                notifications.extend(
                    _notification_messages(
                        monitor,
                        tuple(monitor_events),
                        tuple(monitor_observations),
                        previous,
                        tuple(dict.fromkeys(monitor_sources)),
                        self._ids,
                    )
                )
            else:
                monitor_sources_by_monitor[monitor.monitor_id] = tuple(
                    dict.fromkeys(monitor_sources)
                )

        warning_codes = tuple(dict.fromkeys(warnings))
        error_codes = tuple(dict.fromkeys(errors))
        if not monitors and request.monitor_ids:
            error_codes = tuple(dict.fromkeys((*error_codes, "NO_ACTIVE_MONITORS")))
        if error_codes or any(item.state is MonitorRuleStateValue.NOT_EVALUATED for item in states):
            status = MonitorRunStatus.PARTIAL if monitors else MonitorRunStatus.FAILED
        else:
            # Warnings describe fact quality/provenance, not run completeness.
            # A run is complete when every selected rule produced an evaluated
            # state and no typed error occurred.
            status = MonitorRunStatus.SUCCEEDED
        run = MonitorRun(
            run_id=run_id,
            requested_monitor_ids=request.monitor_ids,
            selected_monitor_ids=tuple(item.monitor_id for item in monitors),
            cadence=request.cadence,
            as_of=as_of,
            started_at=started_at,
            completed_at=self._clock.now(),
            status=status,
            monitors_evaluated=len(monitors),
            rules_evaluated=rules_evaluated,
            events_created=len(events),
            warning_codes=warning_codes,
            error_codes=error_codes,
            observation_history_complete=True,
            observations=tuple(observations),
            execution_effect=False,
        )
        if (
            run.cadence
            in {
                MonitorCadence.A_SHARE_POST_MARKET,
                MonitorCadence.US_POST_MARKET,
                MonitorCadence.KR_POST_MARKET,
            }
            and monitors
        ):
            notifications.append(
                _post_market_summary_message(
                    run,
                    monitors,
                    self._ids,
                    events=tuple(events),
                    previous_states_by_monitor=previous_states_by_monitor,
                    monitor_sources_by_monitor=monitor_sources_by_monitor,
                )
            )
        return self._repository.record_evaluation(
            run,
            tuple(states),
            tuple(events),
            tuple(notifications),
        )

    def _select(
        self, request: MonitorEvaluateInput, *, as_of: datetime
    ) -> tuple[tuple[MonitorDefinition, ...], tuple[str, ...]]:
        warnings: list[str] = []
        if request.monitor_ids:
            selected: list[MonitorDefinition] = []
            for monitor_id in request.monitor_ids:
                value = self._repository.get_current(monitor_id)
                if value is None:
                    warnings.append("MONITOR_NOT_FOUND")
                elif value.status is not MonitorStatus.ACTIVE:
                    warnings.append("MONITOR_NOT_ACTIVE")
                elif value.valid_until is not None and as_of > value.valid_until:
                    warnings.append("MONITOR_EXPIRED")
                elif request.cadence is not None and value.cadence is not request.cadence:
                    warnings.append("MONITOR_CADENCE_MISMATCH")
                else:
                    selected.append(value)
            return tuple(selected), tuple(dict.fromkeys(warnings))
        values = self._repository.list_current(MonitorStatus.ACTIVE)
        if request.cadence is not None:
            values = tuple(item for item in values if item.cadence is request.cadence)
        expired = tuple(
            item for item in values if item.valid_until is not None and as_of > item.valid_until
        )
        if expired:
            warnings.append("MONITOR_EXPIRED")
        values = tuple(
            item for item in values if item.valid_until is None or as_of <= item.valid_until
        )
        return values, tuple(dict.fromkeys(warnings))

    async def _price_fact(self, instrument_id: str, as_of: datetime) -> _Fact:
        _asset, market, _symbol = parse_instrument_id(instrument_id)
        if market is Market.A_SHARE:
            a_share_envelope = await self._a_share.get_snapshot(
                AShareGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
            )
            if (
                a_share_envelope.ok
                and a_share_envelope.data is not None
                and a_share_envelope.data.quote is not None
            ):
                return _Fact(
                    value=a_share_envelope.data.quote.last,
                    as_of=a_share_envelope.data.quote.quote_at,
                    warning_codes=tuple(item.code for item in a_share_envelope.warnings),
                    error_codes=(),
                    closed_session_last_known=(
                        "CLOSED_SESSION_LAST_KNOWN"
                        in {item.code for item in a_share_envelope.warnings}
                    ),
                    source_names=tuple(item.name for item in a_share_envelope.sources),
                )
            return _Fact(
                None,
                None,
                tuple(item.code for item in a_share_envelope.warnings),
                tuple(item.code for item in a_share_envelope.errors)
                or ("MONITOR_PRICE_UNAVAILABLE",),
            )
        if market is Market.DCE:
            # No Phase 3A quote/OHLCV path; never invent settlement for evaluation.
            return _Fact(
                None,
                None,
                (),
                ("DCE_QUOTE_BARS_UNAVAILABLE", "MONITOR_PRICE_UNAVAILABLE"),
            )
        if market in {Market.US, Market.KR, Market.CME, Market.OTC}:
            market_envelope = await self._market.get_market_snapshot(
                MarketGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
            )
            if market_envelope.ok and market_envelope.data is not None:
                value, fact_as_of = _extract_price_fact(market_envelope.data)
                if value is not None and fact_as_of is not None:
                    return _Fact(
                        value=value,
                        as_of=fact_as_of,
                        warning_codes=tuple(item.code for item in market_envelope.warnings),
                        error_codes=(),
                        closed_session_last_known=(
                            "CLOSED_SESSION_LAST_KNOWN"
                            in {item.code for item in market_envelope.warnings}
                        ),
                        source_names=tuple(item.name for item in market_envelope.sources),
                    )
            return _Fact(
                None,
                None,
                tuple(item.code for item in market_envelope.warnings),
                tuple(item.code for item in market_envelope.errors)
                or ("MONITOR_PRICE_UNAVAILABLE",),
            )
        return _Fact(None, None, (), ("MONITOR_UNSUPPORTED_MARKET",))

    async def _risk_fact(self, as_of: datetime) -> _Fact:
        envelope = await self._risk.check(RiskCheckInput(as_of=as_of))
        warnings = tuple(item.code for item in envelope.warnings)
        if not envelope.ok or envelope.data is None:
            return _Fact(
                None,
                None,
                warnings,
                tuple(item.code for item in envelope.errors) or ("MONITOR_RISK_UNAVAILABLE",),
            )
        overall = RiskOverallStatus(envelope.data.overall_status)
        if overall is RiskOverallStatus.INCOMPLETE:
            return _Fact(None, envelope.data.as_of, warnings, ("RISK_CHECK_INCOMPLETE",))
        return _Fact(Decimal(_RISK_RANK[overall]), envelope.data.as_of, warnings, ())

    @staticmethod
    def _evaluate_rule(
        monitor: MonitorDefinition,
        rule: MonitorRule,
        fact: _Fact,
        as_of: datetime,
    ) -> MonitorRuleState:
        if fact.value is None or fact.as_of is None:
            return MonitorRuleState(
                monitor_id=monitor.monitor_id,
                monitor_version=monitor.version,
                rule_code=rule.rule_code,
                state=MonitorRuleStateValue.NOT_EVALUATED,
                observed_value=fact.value,
                fact_as_of=fact.as_of,
                message="Required fact was unavailable.",
                updated_at=as_of,
            )
        age = (as_of - fact.as_of).total_seconds()
        if age < 0 or (age > rule.max_fact_age_seconds and not fact.closed_session_last_known):
            return MonitorRuleState(
                monitor_id=monitor.monitor_id,
                monitor_version=monitor.version,
                rule_code=rule.rule_code,
                state=MonitorRuleStateValue.NOT_EVALUATED,
                observed_value=fact.value,
                fact_as_of=fact.as_of,
                message="Required fact exceeded the rule freshness limit.",
                updated_at=as_of,
            )
        if rule.rule_type is MonitorRuleType.PRICE_ABOVE:
            assert rule.price_threshold is not None
            triggered = fact.value >= rule.price_threshold
        elif rule.rule_type is MonitorRuleType.PRICE_BELOW:
            assert rule.price_threshold is not None
            triggered = fact.value <= rule.price_threshold
        elif rule.rule_type is MonitorRuleType.RISK_OVERALL_AT_LEAST:
            assert rule.risk_status_threshold is not None
            triggered = fact.value >= Decimal(_RISK_RANK[rule.risk_status_threshold])
        else:
            assert rule.comparator is not None
            if rule.comparator is TradePlanComparator.OCCURRED:
                triggered = fact.value >= 1
            else:
                assert rule.numeric_threshold is not None
                triggered = _compare(fact.value, rule.numeric_threshold, rule.comparator)
        return MonitorRuleState(
            monitor_id=monitor.monitor_id,
            monitor_version=monitor.version,
            rule_code=rule.rule_code,
            state=(MonitorRuleStateValue.TRIGGERED if triggered else MonitorRuleStateValue.QUIET),
            observed_value=fact.value,
            fact_as_of=fact.as_of,
            message="Rule condition triggered." if triggered else "Rule condition is quiet.",
            updated_at=as_of,
        )

    def _transition_event(
        self,
        monitor: MonitorDefinition,
        rule: MonitorRule,
        previous: MonitorRuleState | None,
        current: MonitorRuleState,
        created_at: datetime,
    ) -> MonitorEvent | None:
        previous_state = (
            previous.state
            if previous is not None and previous.monitor_version == monitor.version
            else None
        )
        if previous_state is current.state:
            return None
        if current.state is MonitorRuleStateValue.TRIGGERED:
            event_type = MonitorEventType.TRIGGERED
        elif (
            current.state is MonitorRuleStateValue.QUIET
            and previous_state is MonitorRuleStateValue.TRIGGERED
        ):
            event_type = MonitorEventType.RECOVERED
        elif current.state is MonitorRuleStateValue.NOT_EVALUATED:
            event_type = MonitorEventType.NOT_EVALUATED
        else:
            return None
        threshold = rule.price_threshold
        if threshold is None and rule.risk_status_threshold is not None:
            threshold = Decimal(_RISK_RANK[rule.risk_status_threshold])
        if threshold is None:
            threshold = rule.numeric_threshold
        return MonitorEvent(
            event_id=self._ids.new(EntityIdPrefix.MONITOR_EVENT),
            monitor_id=monitor.monitor_id,
            monitor_version=monitor.version,
            rule_code=rule.rule_code,
            event_type=event_type,
            severity=rule.severity,
            observed_value=current.observed_value,
            threshold_value=threshold,
            fact_as_of=current.fact_as_of,
            message=current.message,
            created_at=created_at,
        )


def _extract_price_fact(data: Any) -> tuple[Decimal | None, datetime | None]:
    """Session-aware price + observation time from market snapshot payloads.

    Uses the provider-returned ``quote_at`` (never wall-clock) so closed-session
    last-known values retain their real observation time for freshness checks.
    """
    if isinstance(data, USQuoteDTO):
        return data.last, data.quote_at
    if isinstance(data, SpotObservationDTO):
        price: Decimal | None = data.mid if data.mid is not None else data.last
        if price is None and data.bid is not None and data.ask is not None:
            price = (data.bid + data.ask) / Decimal("2")
        return price, data.quote_at
    # Duck-typed fallback for test doubles / alternate DTOs.
    last = getattr(data, "last", None)
    mid = getattr(data, "mid", None)
    quote_at = getattr(data, "quote_at", None)
    value = last if last is not None else mid
    if value is not None and quote_at is not None:
        return value, quote_at
    return None, None


def _compare(observed: Decimal, threshold: Decimal, comparator: TradePlanComparator) -> bool:
    if comparator is TradePlanComparator.GT:
        return observed > threshold
    if comparator is TradePlanComparator.GTE:
        return observed >= threshold
    if comparator is TradePlanComparator.LT:
        return observed < threshold
    if comparator is TradePlanComparator.LTE:
        return observed <= threshold
    if comparator is TradePlanComparator.EQ:
        return observed == threshold
    raise DataContractError("OCCURRED comparator must use event evaluation")


def _run_observation(
    *,
    run_id: str,
    monitor: MonitorDefinition,
    rule: MonitorRule,
    fact: _Fact,
    state: MonitorRuleState,
    as_of: datetime,
) -> MonitorRunObservation:
    threshold = rule.price_threshold
    if threshold is None and rule.risk_status_threshold is not None:
        threshold = Decimal(_RISK_RANK[rule.risk_status_threshold])
    if threshold is None:
        threshold = rule.numeric_threshold
    distance = fact.value - threshold if fact.value is not None and threshold is not None else None
    distance_percent = (
        distance / threshold * Decimal("100")
        if distance is not None and threshold not in {None, Decimal(0)}
        else None
    )
    fact_age_seconds = (
        max(0, int((as_of - fact.as_of).total_seconds())) if fact.as_of is not None else None
    )
    return MonitorRunObservation(
        run_id=run_id,
        monitor_id=monitor.monitor_id,
        monitor_version=monitor.version,
        rule_code=rule.rule_code,
        instrument_id=rule.instrument_id,
        severity=rule.severity,
        state=state.state,
        observed_value=state.observed_value,
        threshold_value=threshold,
        distance_value=distance,
        distance_percent=distance_percent,
        fact_as_of=state.fact_as_of,
        fact_age_seconds=fact_age_seconds,
        warning_codes=fact.warning_codes,
        error_codes=fact.error_codes,
        message=state.message,
    )


def _notification_messages(
    monitor: MonitorDefinition,
    events: tuple[MonitorEvent, ...],
    observations: tuple[MonitorRunObservation, ...],
    previous_states: dict[str, MonitorRuleState],
    data_sources: tuple[str, ...],
    id_generator: IdGenerator,
) -> tuple[NotificationMessage, ...]:
    event_types = {event.event_type for event in events}
    emoji = (
        "🚨"
        if MonitorEventType.TRIGGERED in event_types
        else "⚠️"
        if MonitorEventType.NOT_EVALUATED in event_types
        else "✅"
    )
    context = _notification_price_context(monitor, observations, previous_states)
    rules_by_code = {item.rule_code: item for item in monitor.rules}
    lines = [monitor.name, f"标的：{context.symbol}", f"当前价格：{context.price}"]
    if not context.current_available and context.previous_price is not None:
        lines.append("价格口径：上一有效价格（当前不可用）")
    lines.append(f"价格时间：{context.price_time}")
    lines.extend(_notification_price_change_lines(context))
    if data_sources:
        lines.append(f"数据来源：{', '.join(data_sources)}")
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
        rule = rules_by_code[observation.rule_code]
        lines.append(_format_notification_rule_card(rule, observation))
    warning_codes = tuple(
        dict.fromkeys(code for observation in observations for code in observation.warning_codes)
    )
    error_codes = tuple(
        dict.fromkeys(code for observation in observations for code in observation.error_codes)
    )
    not_evaluated = tuple(
        item for item in observations if item.state is MonitorRuleStateValue.NOT_EVALUATED
    )
    if error_codes:
        lines.append(f"数据原因：{', '.join(error_codes)}")
    elif not_evaluated:
        causes = tuple(dict.fromkeys(item.message for item in not_evaluated))
        lines.append(f"数据原因：{'; '.join(_notification_text(item, 160) for item in causes)}")
    provenance_line, remaining_warning_codes = _notification_warning_lines(warning_codes)
    if provenance_line:
        lines.append(provenance_line)
    if remaining_warning_codes:
        lines.append(f"数据提示：{', '.join(remaining_warning_codes)}")
    if "IG_WEEKEND_GOLD_CFD_FALLBACK" in warning_codes:
        lines.append(
            "周末口径：IG Weekend Gold CFD 仅作为 XAUUSD 周末波动代理；不是现货黄金或 LBMA 基准价。"
        )
    if context.instrument_id is not None and context.instrument_id.startswith("future:"):
        lines.append("期货价格并非现货；连续合约存在换月风险。")
    event_label = _notification_event_label(events, rules_by_code)
    title = _notification_title(emoji, context.symbol, event_label)
    body = "\n".join(lines)
    return tuple(
        NotificationMessage(
            notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
            source_type=NotificationSourceType.MONITOR_EVENT,
            source_id=event.event_id,
            channel=NotificationChannel.TELEGRAM,
            title=title,
            body=body,
            created_at=event.created_at,
        )
        for event in events
    )


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
        MonitorEventType.RECOVERED: "已恢复",
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
    remaining = tuple(code for code in warning_codes if code not in provenance)
    return (
        "口径：Dukascopy OTC，非 LBMA" if provenance else None,
        remaining,
    )


def _rule_meaning(description: str | None) -> str:
    """Use the first human-readable clause as the compact card meaning."""
    if description is None or not description.strip():
        return "未提供规则说明"
    compact = " ".join(description.split())
    first_clause = re.split(r"[。！？!?；;]", compact, maxsplit=1)[0].strip()
    return _notification_text(first_clause or compact, 32)


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
        lines.extend(
            (
                "MONITOR",
                monitor.name,
                f"标的：{context.symbol}",
                f"当前价格：{context.price}",
                *(() if context.current_available else ("价格口径：上一有效价格（当前不可用）",)),
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
                    if warning not in _DUKASCOPY_PROVENANCE_WARNINGS
                ),
            )
        )
    )
    if causes:
        lines.append(f"数据原因：{', '.join(causes)}")
    elif all_not_evaluated:
        lines.append(
            "数据原因："
            + "; ".join(
                _notification_text(message, 160)
                for message in dict.fromkeys(item.message for item in all_not_evaluated)
            )
        )
    cause_codes = set(causes)
    provenance_line, remaining_warning_codes = _notification_warning_lines(run.warning_codes)
    if provenance_line:
        lines.append(provenance_line)
    warning_codes = tuple(code for code in remaining_warning_codes if code not in cause_codes)
    error_codes = tuple(code for code in run.error_codes if code not in cause_codes)
    if warning_codes:
        lines.append(f"数据提示：{', '.join(warning_codes)}")
    if error_codes:
        lines.append(f"运行错误：{', '.join(error_codes)}")
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
    comparator = rule.comparator.value if rule.comparator is not None else "UNKNOWN"
    threshold = (
        str(rule.numeric_threshold)
        if rule.numeric_threshold is not None
        else rule.event_after.isoformat()
        if rule.event_after is not None
        else "事件发生"
    )
    return f"{rule.metric_key} {comparator} {threshold}"


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
