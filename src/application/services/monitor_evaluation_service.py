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
        price_cache: dict[str, _Fact] = {}
        risk_fact: _Fact | None = None
        generic_cache: dict[tuple[object, ...], _Fact] = {}
        states: list[MonitorRuleState] = []
        events: list[MonitorEvent] = []
        warnings = list(selection_warnings)
        errors: list[str] = []
        rules_evaluated = 0

        for monitor in monitors:
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
                event = self._transition_event(
                    monitor,
                    rule,
                    previous.get(rule.rule_code),
                    state,
                    as_of,
                )
                if event is not None:
                    events.append(event)

        warning_codes = tuple(dict.fromkeys(warnings))
        error_codes = tuple(dict.fromkeys(errors))
        if not monitors and request.monitor_ids:
            error_codes = tuple(dict.fromkeys((*error_codes, "NO_ACTIVE_MONITORS")))
        if error_codes or any(
            item.state is MonitorRuleStateValue.NOT_EVALUATED for item in states
        ):
            status = MonitorRunStatus.PARTIAL if monitors else MonitorRunStatus.FAILED
        elif warning_codes:
            status = MonitorRunStatus.PARTIAL
        else:
            status = MonitorRunStatus.SUCCEEDED
        run = MonitorRun(
            run_id=self._ids.new(EntityIdPrefix.MONITOR_RUN),
            requested_monitor_ids=request.monitor_ids,
            as_of=as_of,
            started_at=started_at,
            completed_at=self._clock.now(),
            status=status,
            monitors_evaluated=len(monitors),
            rules_evaluated=rules_evaluated,
            events_created=len(events),
            warning_codes=warning_codes,
            error_codes=error_codes,
            execution_effect=False,
        )
        return self._repository.record_evaluation(run, tuple(states), tuple(events))

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
        if market in {Market.US, Market.CME, Market.OTC}:
            market_envelope = await self._market.get_market_snapshot(
                MarketGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
            )
            if market_envelope.ok and market_envelope.data is not None:
                value, fact_as_of = _extract_price_fact(market_envelope.data)
                if value is not None and fact_as_of is not None:
                    return _Fact(
                        value=value,
                        as_of=fact_as_of,
                        warning_codes=tuple(
                            item.code for item in market_envelope.warnings
                        ),
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
        if age < 0 or (
            age > rule.max_fact_age_seconds and not fact.closed_session_last_known
        ):
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
                triggered = _compare(
                    fact.value, rule.numeric_threshold, rule.comparator
                )
        return MonitorRuleState(
            monitor_id=monitor.monitor_id,
            monitor_version=monitor.version,
            rule_code=rule.rule_code,
            state=(
                MonitorRuleStateValue.TRIGGERED
                if triggered
                else MonitorRuleStateValue.QUIET
            ),
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


def _compare(
    observed: Decimal, threshold: Decimal, comparator: TradePlanComparator
) -> bool:
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
