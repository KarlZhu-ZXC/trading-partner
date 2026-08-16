"""Deterministic on-demand and scheduled Monitoring evaluation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
from application.ports.market_session_calendar import MarketSessionCalendar
from application.ports.monitor_repository import MonitorRepository
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.market_tool_coordinator import MarketToolCoordinator
from application.services.monitor_fact_resolver import MonitorFactResolver
from application.services.monitor_judgment_service import MonitorJudgmentService
from application.services.monitor_notification_rendering import (
    _POST_MARKET_CADENCES,
    _RISK_RANK,
    _append_judgment_notification,
    _data_recovery_message,
    _latest_completed_session,
    _notification_messages,
    _post_market_summary_message,
)
from application.services.risk_tool_coordinator import RiskToolCoordinator
from domain.common.diagnostics import ProviderFailureDiagnostic
from domain.common.enums import AssetType, Market
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
    MonitorJudgment,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
    MonitorRunObservation,
)
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
    diagnostics: tuple[ProviderFailureDiagnostic, ...] = ()


def _diagnostics_from_envelope(envelope: Any) -> tuple[ProviderFailureDiagnostic, ...]:
    values: list[ProviderFailureDiagnostic] = []
    for item in (*getattr(envelope, "warnings", ()), *getattr(envelope, "errors", ())):
        details = getattr(item, "details", {})
        if not isinstance(details, dict):
            continue
        candidates = details.get("provider_diagnostics")
        raw_values = candidates if isinstance(candidates, list) else [details]
        for raw in raw_values:
            if not isinstance(raw, dict):
                continue
            provider = raw.get("provider")
            stage = raw.get("stage")
            error_code = raw.get("error_code") or getattr(item, "code", None)
            attempt_count = raw.get("attempt_count")
            if not (
                isinstance(provider, str)
                and isinstance(stage, str)
                and isinstance(error_code, str)
                and isinstance(attempt_count, int)
            ):
                continue
            values.append(
                ProviderFailureDiagnostic(
                    provider=provider,
                    stage=stage,
                    error_code=error_code,
                    retryable=bool(raw.get("retryable", getattr(item, "retryable", False))),
                    attempt_count=attempt_count,
                    error_type=(
                        raw.get("error_type") if isinstance(raw.get("error_type"), str) else None
                    ),
                    status_class=(
                        raw.get("status_class")
                        if isinstance(raw.get("status_class"), str)
                        else None
                    ),
                    status_code=(
                        raw.get("status_code") if isinstance(raw.get("status_code"), int) else None
                    ),
                )
            )
    return tuple(dict.fromkeys(values))




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
        judgment_service: MonitorJudgmentService | None = None,
        session_calendars: Mapping[Market, MarketSessionCalendar] | None = None,
        provider_retry_attempts: int = 1,
        provider_retry_delay_seconds: float = 0.5,
    ) -> None:
        self._repository = repository
        self._a_share = a_share
        self._market = market
        self._risk = risk
        self._clock = clock
        self._ids = id_generator
        self._fact_resolver = fact_resolver
        self._judgment_service = judgment_service
        self._session_calendars = dict(session_calendars or {})
        self._provider_retry_attempts = max(1, min(provider_retry_attempts, 3))
        self._provider_retry_delay_seconds = max(0.0, provider_retry_delay_seconds)

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
        judgments: list[MonitorJudgment] = []
        warnings = list(selection_warnings)
        errors: list[str] = []
        rules_evaluated = 0
        previous_states_by_monitor: dict[str, dict[str, MonitorRuleState]] = {}
        monitor_sources_by_monitor: dict[str, tuple[str, ...]] = {}
        judgment_degradation_by_monitor: dict[str, NotificationMessage] = {}

        for monitor in monitors:
            monitor_events: list[MonitorEvent] = []
            judgment_notification: NotificationMessage | None = None
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
                        rule.technical_interval,
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
                # A live Provider request can finish after the run's request
                # timestamp. Judge freshness at the actual evaluation moment;
                # explicit historical ``as_of`` requests remain strict cutoffs.
                evaluated_at = as_of if request.as_of is not None else self._clock.now()
                fact = self._apply_session_aware_freshness(rule, fact, evaluated_at)
                warnings.extend(fact.warning_codes)
                errors.extend(fact.error_codes)
                monitor_sources.extend(fact.source_names)
                state = self._evaluate_rule(
                    monitor,
                    rule,
                    fact,
                    evaluated_at,
                    previous.get(rule.rule_code),
                )
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
            if self._judgment_service is not None and monitor.judgment_policy is not None:
                judgment_result = await self._judgment_service.evaluate(
                    run_id=run_id,
                    monitor=monitor,
                    observations=tuple(monitor_observations),
                    hard_transition=bool(monitor_events),
                )
                if judgment_result is not None:
                    judgments.append(judgment_result.judgment)
                    warnings.extend(judgment_result.judgment.warning_codes)
                    errors.extend(judgment_result.judgment.error_codes)
                    if judgment_result.event is not None:
                        events.append(judgment_result.event)
                        monitor_events.append(judgment_result.event)
                    if judgment_result.notification is not None:
                        judgment_notification = judgment_result.notification
                        if judgment_result.judgment.status == "FAILED":
                            judgment_degradation_by_monitor[monitor.monitor_id] = (
                                judgment_result.notification
                            )
            # Post-market runs persist each transition event for the durable
            # event history, but their Telegram delivery is consolidated into
            # one run-linked digest below. Other cadences batch all transitions
            # for the Monitor into one event-linked notification.
            deterministic_events = tuple(
                item for item in monitor_events if item.rule_code != "COMPOSITE_JUDGMENT"
            )
            data_recovered = tuple(
                item
                for item in monitor_observations
                if item.state is not MonitorRuleStateValue.NOT_EVALUATED
                and (prior := previous.get(item.rule_code)) is not None
                and prior.state is MonitorRuleStateValue.NOT_EVALUATED
            )
            if deterministic_events and request.cadence not in _POST_MARKET_CADENCES:
                monitor_sources_by_monitor[monitor.monitor_id] = tuple(
                    dict.fromkeys(monitor_sources)
                )
                deterministic_notifications = _notification_messages(
                    monitor,
                    deterministic_events,
                    tuple(monitor_observations),
                    previous,
                    tuple(dict.fromkeys(monitor_sources)),
                    self._ids,
                )
                if judgment_notification is not None:
                    notifications.append(
                        _append_judgment_notification(
                            deterministic_notifications[0], judgment_notification
                        )
                    )
                else:
                    notifications.extend(deterministic_notifications)
            else:
                monitor_sources_by_monitor[monitor.monitor_id] = tuple(
                    dict.fromkeys(monitor_sources)
                )
                if judgment_notification is not None:
                    is_degradation = (
                        judgment_degradation_by_monitor.get(monitor.monitor_id)
                        is judgment_notification
                    )
                    if is_degradation and request.cadence in _POST_MARKET_CADENCES:
                        # Post-market delivery is consolidated into the one
                        # run-linked digest below; do not enqueue a second card.
                        pass
                    elif is_degradation:
                        snapshot = _notification_messages(
                            monitor,
                            (),
                            tuple(monitor_observations),
                            previous,
                            tuple(dict.fromkeys(monitor_sources)),
                            self._ids,
                            source_id=judgment_notification.source_id,
                            created_at=judgment_notification.created_at,
                            event_label_override="复合判断不可用",
                            emoji_override="⚠️",
                        )[0]
                        notifications.append(
                            _append_judgment_notification(snapshot, judgment_notification)
                        )
                    else:
                        notifications.append(judgment_notification)
                if data_recovered and request.cadence not in _POST_MARKET_CADENCES:
                    notifications.append(
                        _data_recovery_message(
                            run_id=run_id,
                            monitor=monitor,
                            observations=tuple(monitor_observations),
                            recovered=data_recovered,
                            data_sources=tuple(dict.fromkeys(monitor_sources)),
                            id_generator=self._ids,
                            created_at=self._clock.now(),
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
            notifications.append(
                _post_market_summary_message(
                    run,
                    monitors,
                    self._ids,
                    events=tuple(item for item in events if item.rule_code != "COMPOSITE_JUDGMENT"),
                    previous_states_by_monitor=previous_states_by_monitor,
                    monitor_sources_by_monitor=monitor_sources_by_monitor,
                    judgment_notifications_by_monitor=judgment_degradation_by_monitor,
                )
            )
        return self._repository.record_evaluation(
            run,
            tuple(states),
            tuple(events),
            tuple(notifications),
            tuple(judgments),
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
            attempt = 0
            while True:
                attempt += 1
                market_envelope = await self._market.get_market_snapshot(
                    MarketGetSnapshotInput(instrument_id=instrument_id, as_of=as_of)
                )
                diagnostics = _diagnostics_from_envelope(market_envelope)
                if (
                    market_envelope.ok
                    or attempt >= self._provider_retry_attempts
                    or not any(item.retryable for item in diagnostics)
                ):
                    break
                await asyncio.sleep(self._provider_retry_delay_seconds * attempt)
            if market_envelope.ok and market_envelope.data is not None:
                value, fact_as_of = _extract_price_fact(market_envelope.data)
                if value is not None and fact_as_of is not None:
                    warning_codes = [item.code for item in market_envelope.warnings]
                    if attempt > 1:
                        warning_codes.append("MONITOR_PROVIDER_READ_RETRIED")
                    return _Fact(
                        value=value,
                        as_of=fact_as_of,
                        warning_codes=tuple(dict.fromkeys(warning_codes)),
                        error_codes=(),
                        closed_session_last_known=(
                            "CLOSED_SESSION_LAST_KNOWN"
                            in {item.code for item in market_envelope.warnings}
                        ),
                        source_names=tuple(item.name for item in market_envelope.sources),
                        diagnostics=diagnostics,
                    )
            warning_codes = [item.code for item in market_envelope.warnings]
            if attempt > 1:
                warning_codes.append("MONITOR_PROVIDER_READ_RETRIED")
            return _Fact(
                None,
                None,
                tuple(dict.fromkeys(warning_codes)),
                tuple(item.code for item in market_envelope.errors)
                or ("MONITOR_PRICE_UNAVAILABLE",),
                diagnostics=diagnostics,
            )
        return _Fact(None, None, (), ("MONITOR_UNSUPPORTED_MARKET",))

    def _apply_session_aware_freshness(
        self,
        rule: MonitorRule,
        fact: _Fact,
        evaluated_at: datetime,
    ) -> _Fact:
        """Treat the latest completed daily session as current across closures."""

        if not self._is_current_daily_technical_fact(rule, fact, evaluated_at):
            return fact
        return _Fact(
            value=fact.value,
            as_of=fact.as_of,
            warning_codes=tuple(
                code for code in fact.warning_codes if code != "TECHNICAL_DATA_NOT_FRESH"
            ),
            error_codes=fact.error_codes,
            closed_session_last_known=True,
            source_names=fact.source_names,
            diagnostics=fact.diagnostics,
        )

    def _is_current_daily_technical_fact(
        self,
        rule: MonitorRule,
        fact: _Fact,
        evaluated_at: datetime,
    ) -> bool:
        if (
            rule.fact_type is not TradePlanFactType.TECHNICAL
            or (rule.technical_interval or "1d") != "1d"
            or rule.instrument_id is None
            or fact.as_of is None
            or fact.as_of > evaluated_at
        ):
            return False
        asset_type, market, _symbol = parse_instrument_id(rule.instrument_id)
        if asset_type not in {AssetType.EQUITY, AssetType.ETF, AssetType.INDEX}:
            return False
        calendar = self._session_calendars.get(market)
        if calendar is None:
            return False
        expected = _latest_completed_session(calendar, evaluated_at)
        observed = calendar.session_on_or_before(fact.as_of)
        return (
            expected is not None
            and observed is not None
            and observed.session_date >= expected.session_date
        )

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
        previous: MonitorRuleState | None = None,
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
                if (
                    rule.recovery_threshold is not None
                    and previous is not None
                    and previous.monitor_version == monitor.version
                    and previous.state is MonitorRuleStateValue.TRIGGERED
                ):
                    triggered = not _recovered(
                        fact.value,
                        rule.recovery_threshold,
                        rule.comparator,
                    )
                else:
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


def _recovered(
    observed: Decimal,
    recovery_threshold: Decimal,
    comparator: TradePlanComparator,
) -> bool:
    if comparator in {TradePlanComparator.GT, TradePlanComparator.GTE}:
        return observed <= recovery_threshold
    if comparator in {TradePlanComparator.LT, TradePlanComparator.LTE}:
        return observed >= recovery_threshold
    raise DataContractError("recovery_threshold requires an ordered comparator")


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
        diagnostics=fact.diagnostics,
    )
