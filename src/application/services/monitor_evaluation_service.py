"""Deterministic on-demand and scheduled Monitoring evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
    MonitorNotificationChannel,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorNotificationMessage,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
    MonitorRunObservation,
)
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType


@dataclass(frozen=True, slots=True)
class _Fact:
    value: Decimal | None
    as_of: datetime | None
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    closed_session_last_known: bool = False


_RISK_RANK = {
    RiskOverallStatus.PASS: 0,
    RiskOverallStatus.WARN: 1,
    RiskOverallStatus.BREACH: 2,
}


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
        notifications: list[MonitorNotificationMessage] = []
        observations: list[MonitorRunObservation] = []
        warnings = list(selection_warnings)
        errors: list[str] = []
        rules_evaluated = 0

        for monitor in monitors:
            monitor_events: list[MonitorEvent] = []
            monitor_observations: list[MonitorRunObservation] = []
            previous = {
                item.rule_code: item
                for item in self._repository.get_rule_states(monitor.monitor_id)
            }
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
                state = self._evaluate_rule(monitor, rule, fact, as_of)
                states.append(state)
                observation = _run_observation(
                    run_id=run_id,
                    monitor=monitor,
                    rule=rule,
                    fact=fact,
                    state=state,
                    as_of=as_of,
                )
                observations.append(observation)
                monitor_observations.append(observation)
                event = self._transition_event(
                    monitor,
                    rule,
                    previous.get(rule.rule_code),
                    state,
                    as_of,
                )
                if event is not None:
                    events.append(event)
                    monitor_events.append(event)
            if monitor_events:
                notifications.extend(
                    _notification_messages(
                        monitor,
                        tuple(monitor_events),
                        tuple(monitor_observations),
                        self._ids,
                    )
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
            notifications.append(_post_market_summary_message(run, monitors, self._ids))
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
    id_generator: IdGenerator,
) -> tuple[MonitorNotificationMessage, ...]:
    event_types = {event.event_type for event in events}
    emoji = (
        "🚨"
        if MonitorEventType.TRIGGERED in event_types
        else "⚠️"
        if MonitorEventType.NOT_EVALUATED in event_types
        else "✅"
    )
    instrument_id = monitor.primary_instrument_id or next(
        (item.instrument_id for item in observations if item.instrument_id is not None),
        None,
    )
    symbol = instrument_id.rsplit(":", 1)[-1] if instrument_id is not None else monitor.name
    rules_by_code = {item.rule_code: item for item in monitor.rules}
    price_observation = next(
        (
            item
            for item in observations
            if item.instrument_id == instrument_id
            and item.observed_value is not None
            and (
                rules_by_code[item.rule_code].rule_type
                in {MonitorRuleType.PRICE_ABOVE, MonitorRuleType.PRICE_BELOW}
                or rules_by_code[item.rule_code].fact_type is TradePlanFactType.PRICE
            )
        ),
        None,
    )
    current_price = (
        str(price_observation.observed_value) if price_observation is not None else "不可用"
    )
    price_time = (
        price_observation.fact_as_of.isoformat()
        if price_observation is not None and price_observation.fact_as_of is not None
        else "不可用"
    )
    lines = [monitor.name]
    if price_observation is not None:
        lines.extend((f"当前价格：{current_price}", f"价格时间：{price_time}"))
    lines.append("CHANGES")
    lines.extend(
        f"• [{event.severity.value}] {event.rule_code} → {event.event_type.value}"
        for event in events
    )
    table_rows: list[tuple[str, ...]] = []
    for observation in observations:
        rule = rules_by_code[observation.rule_code]
        observed = (
            str(observation.observed_value) if observation.observed_value is not None else "不可用"
        )
        distance = (
            str(observation.distance_value) if observation.distance_value is not None else "不可用"
        )
        table_rows.append(
            (
                observation.rule_code,
                _rule_condition(rule),
                observed,
                distance,
                observation.state.value,
                observation.severity.value,
            )
        )
    lines.extend(("RULES", _format_rule_table(tuple(table_rows))))
    warning_codes = tuple(
        dict.fromkeys(code for observation in observations for code in observation.warning_codes)
    )
    if warning_codes:
        lines.append(f"数据提示：{', '.join(warning_codes)}")
    if instrument_id is not None and instrument_id.startswith("future:"):
        lines.append("期货价格并非现货；连续合约存在换月风险。")
    event_label = events[0].event_type.value if len(events) == 1 else f"{len(events)} 项状态变化"
    title = f"{emoji} {symbol} · {event_label}"
    body = "\n".join(lines)
    return tuple(
        MonitorNotificationMessage(
            notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
            source_event_id=event.event_id,
            source_run_id=None,
            channel=MonitorNotificationChannel.TELEGRAM,
            title=title,
            body=body,
            created_at=event.created_at,
        )
        for event in events
    )


def _post_market_summary_message(
    run: MonitorRun,
    monitors: tuple[MonitorDefinition, ...],
    id_generator: IdGenerator,
) -> MonitorNotificationMessage:
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
    for monitor in monitors:
        observations = observations_by_monitor[monitor.monitor_id]
        instrument_id, symbol, price, price_time = _monitor_price_context(
            monitor,
            observations,
        )
        lines.extend(
            (
                "MONITOR",
                monitor.name,
                f"标的：{symbol}",
                f"当前价格：{price}",
                f"价格时间：{price_time}",
                "RULES",
                _format_rule_table(_notification_rule_rows(monitor, observations)),
                "END_MONITOR",
            )
        )
        if instrument_id is not None and instrument_id.startswith("future:"):
            lines.append("期货价格并非现货；连续合约存在换月风险。")
    if run.warning_codes:
        lines.append(f"数据提示：{', '.join(run.warning_codes)}")
    if run.error_codes:
        lines.append(f"运行错误：{', '.join(run.error_codes)}")
    return MonitorNotificationMessage(
        notification_id=id_generator.new(EntityIdPrefix.MONITOR_NOTIFICATION),
        source_event_id=None,
        source_run_id=run.run_id,
        channel=MonitorNotificationChannel.TELEGRAM,
        title=(f"📊 {market_label}盘后 Monitor · {len(monitors)} 标的 · {run.events_created} 变化"),
        body="\n".join(lines),
        created_at=run.completed_at,
    )


def _monitor_price_context(
    monitor: MonitorDefinition,
    observations: tuple[MonitorRunObservation, ...],
) -> tuple[str | None, str, str, str]:
    instrument_id = monitor.primary_instrument_id or next(
        (item.instrument_id for item in observations if item.instrument_id is not None),
        None,
    )
    symbol = instrument_id.rsplit(":", 1)[-1] if instrument_id else monitor.name
    rules_by_code = {item.rule_code: item for item in monitor.rules}
    price_observation = next(
        (
            item
            for item in observations
            if item.instrument_id == instrument_id
            and item.observed_value is not None
            and (
                rules_by_code[item.rule_code].rule_type
                in {MonitorRuleType.PRICE_ABOVE, MonitorRuleType.PRICE_BELOW}
                or rules_by_code[item.rule_code].fact_type is TradePlanFactType.PRICE
            )
        ),
        None,
    )
    price = str(price_observation.observed_value) if price_observation is not None else "不可用"
    price_time = (
        price_observation.fact_as_of.isoformat()
        if price_observation is not None and price_observation.fact_as_of is not None
        else "不可用"
    )
    return instrument_id, symbol, price, price_time


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
