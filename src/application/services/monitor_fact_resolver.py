"""Deterministic Monitoring v2 fact resolution over existing product services."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from application.dto.a_share import (
    AShareGetFinancialStatementsInput,
    AShareGetSentimentSnapshotInput,
    AShareGetSnapshotInput,
)
from application.dto.technical import TechnicalAnalysisInput
from application.dto.us_context import (
    USGetMacroContextInput,
    USGetSentimentSnapshotInput,
)
from application.dto.us_research import (
    FundamentalGetSnapshotInput,
    ResearchGetCompanyUpdatesInput,
)
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.technical_tool_coordinator import TechnicalToolCoordinator
from application.services.us_context_tool_coordinator import USContextToolCoordinator
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from domain.a_share.enums import AShareSnapshotDetail
from domain.common.enums import Market
from domain.common.values import parse_instrument_id
from domain.monitoring.models import MonitorRule
from domain.trade_plan.enums import TradePlanFactType

UowFactory = Callable[[], ResearchUnitOfWork]


@dataclass(frozen=True, slots=True)
class MonitorFact:
    value: Decimal | None
    as_of: datetime | None
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    closed_session_last_known: bool = False


def _codes(items: Iterable[Any]) -> tuple[str, ...]:
    return tuple(item.code for item in items)


class MonitorFactResolver:
    def __init__(
        self,
        *,
        technical: TechnicalToolCoordinator,
        a_share: AShareToolCoordinator,
        us_research: USResearchToolCoordinator,
        us_context: USContextToolCoordinator,
        research_uow_factory: UowFactory,
    ) -> None:
        self._technical = technical
        self._a_share = a_share
        self._us_research = us_research
        self._us_context = us_context
        self._research_uow_factory = research_uow_factory

    async def resolve(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        fact_type = rule.fact_type
        if fact_type in {TradePlanFactType.TECHNICAL, TradePlanFactType.VOLUME}:
            return await self._technical_fact(rule, as_of)
        if fact_type is TradePlanFactType.FUNDAMENTAL:
            return await self._fundamental_fact(rule, as_of)
        if fact_type is TradePlanFactType.COMPANY_EVENT:
            return await self._company_event_fact(rule, as_of)
        if fact_type is TradePlanFactType.MACRO:
            return await self._macro_fact(rule, as_of)
        if fact_type is TradePlanFactType.SENTIMENT:
            return await self._sentiment_fact(rule, as_of)
        if fact_type is TradePlanFactType.THESIS_STATE:
            return self._thesis_fact(rule, as_of)
        return MonitorFact(
            None, None, error_codes=("MONITOR_FACT_TYPE_UNSUPPORTED",)
        )

    async def _technical_fact(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        assert rule.instrument_id is not None and rule.metric_key is not None
        metric_key = "volume" if rule.fact_type is TradePlanFactType.VOLUME else rule.metric_key
        envelope = await self._technical.get_snapshot(
            TechnicalAnalysisInput(
                instrument_id=rule.instrument_id,
                as_of=as_of,
                intervals=(rule.technical_interval or "1d",),
            )
        )
        if not envelope.ok or envelope.data is None:
            return MonitorFact(
                None,
                None,
                _codes(envelope.warnings),
                _codes(envelope.errors) or ("MONITOR_TECHNICAL_UNAVAILABLE",),
            )
        timeframe = envelope.data.timeframes[0]
        metric = next((item for item in timeframe.metrics if item.name == metric_key), None)
        if metric is None or metric.value is None:
            return MonitorFact(
                None,
                timeframe.bar_as_of,
                _codes(envelope.warnings),
                ("MONITOR_METRIC_UNAVAILABLE",),
            )
        return MonitorFact(
            metric.value,
            timeframe.bar_as_of,
            _codes(envelope.warnings),
            (),
            "CLOSED_SESSION_LAST_KNOWN" in _codes(envelope.warnings),
        )

    async def _fundamental_fact(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        assert rule.instrument_id is not None and rule.metric_key is not None
        _asset, market, _symbol = parse_instrument_id(rule.instrument_id)
        if market is Market.US:
            us_envelope = await self._us_research.get_fundamental_snapshot(
                FundamentalGetSnapshotInput(instrument_id=rule.instrument_id, as_of=as_of)
            )
            if not us_envelope.ok or us_envelope.data is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(us_envelope.warnings),
                    _codes(us_envelope.errors) or ("MONITOR_FUNDAMENTAL_UNAVAILABLE",),
                )
            reported = rule.metric_key.startswith("reported:")
            key = rule.metric_key.removeprefix("reported:")
            metrics = (
                us_envelope.data.reported_metrics if reported else us_envelope.data.metrics
            )
            value = getattr(metrics, key, None) if metrics is not None else None
            if not isinstance(value, Decimal):
                return MonitorFact(
                    None,
                    metrics.filed_at if metrics is not None else us_envelope.data.as_of,
                    _codes(us_envelope.warnings),
                    ("MONITOR_METRIC_UNAVAILABLE",),
                )
            assert metrics is not None
            fact_at = metrics.filed_at or us_envelope.data.as_of
            return MonitorFact(value, fact_at, _codes(us_envelope.warnings))
        if market is Market.A_SHARE:
            a_envelope = await self._a_share.get_financial_statements(
                AShareGetFinancialStatementsInput(
                    instrument_id=rule.instrument_id,
                    periods=1,
                    metric_codes=(rule.metric_key,),
                    as_of=as_of,
                )
            )
            if not a_envelope.ok or a_envelope.data is None or not a_envelope.data.periods:
                return MonitorFact(
                    None,
                    None,
                    _codes(a_envelope.warnings),
                    _codes(a_envelope.errors) or ("MONITOR_FUNDAMENTAL_UNAVAILABLE",),
                )
            a_metric = next(
                (
                    item
                    for item in a_envelope.data.periods[0].metrics
                    if item.metric_code == rule.metric_key
                ),
                None,
            )
            if a_metric is None or a_metric.value is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(a_envelope.warnings),
                    ("MONITOR_METRIC_UNAVAILABLE",),
                )
            a_fact_at = a_metric.published_at
            if a_fact_at is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(a_envelope.warnings),
                    ("MONITOR_PUBLICATION_TIME_UNAVAILABLE",),
                )
            return MonitorFact(a_metric.value, a_fact_at, _codes(a_envelope.warnings))
        return MonitorFact(None, None, error_codes=("MONITOR_UNSUPPORTED_MARKET",))

    async def _company_event_fact(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        assert rule.instrument_id is not None and rule.metric_key is not None
        _asset, market, _symbol = parse_instrument_id(rule.instrument_id)
        since = rule.event_after or (as_of - timedelta(days=30))
        wanted = rule.metric_key.upper()
        if market is Market.US:
            us_envelope = await self._us_research.get_company_updates(
                ResearchGetCompanyUpdatesInput(
                    instrument_id=rule.instrument_id,
                    since=since,
                    as_of=as_of,
                    limit=100,
                )
            )
            if not us_envelope.ok or us_envelope.data is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(us_envelope.warnings),
                    _codes(us_envelope.errors) or ("MONITOR_COMPANY_EVENT_UNAVAILABLE",),
                )
            matches = tuple(
                event
                for event in us_envelope.data.events
                if event.visible_time >= since
                and (wanted == "ANY" or event.event_type.value == wanted)
            )
            fact_at = max((item.visible_time for item in matches), default=as_of)
            return MonitorFact(
                Decimal(1 if matches else 0), fact_at, _codes(us_envelope.warnings)
            )
        if market is Market.A_SHARE:
            a_envelope = await self._a_share.get_snapshot(
                AShareGetSnapshotInput(
                    instrument_id=rule.instrument_id,
                    as_of=as_of,
                    detail=AShareSnapshotDetail.FULL,
                )
            )
            if not a_envelope.ok or a_envelope.data is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(a_envelope.warnings),
                    _codes(a_envelope.errors) or ("MONITOR_COMPANY_EVENT_UNAVAILABLE",),
                )
            a_matches = tuple(
                item
                for item in a_envelope.data.announcements
                if item.published_at >= since
                and (wanted == "ANY" or (item.category or "").upper() == wanted)
            )
            a_fact_at = max(
                (item.published_at for item in a_matches), default=as_of
            )
            return MonitorFact(
                Decimal(1 if a_matches else 0),
                a_fact_at,
                _codes(a_envelope.warnings),
            )
        return MonitorFact(None, None, error_codes=("MONITOR_UNSUPPORTED_MARKET",))

    async def _macro_fact(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        assert rule.metric_key is not None
        envelope = await self._us_context.get_macro_context(
            USGetMacroContextInput(
                series_ids=(rule.metric_key,), lookback_days=3650, as_of=as_of
            )
        )
        if not envelope.ok or envelope.data is None or not envelope.data.series:
            return MonitorFact(
                None,
                None,
                _codes(envelope.warnings),
                _codes(envelope.errors) or ("MONITOR_MACRO_UNAVAILABLE",),
            )
        series = envelope.data.series[0]
        if series.latest_value is None:
            return MonitorFact(
                None,
                series.last_updated,
                _codes(envelope.warnings),
                ("MONITOR_METRIC_UNAVAILABLE",),
            )
        fact_at = series.last_updated
        if fact_at is None and series.observations:
            fact_at = datetime.combine(
                series.observations[-1].observation_date, time.max, tzinfo=UTC
            )
        return MonitorFact(series.latest_value, fact_at, _codes(envelope.warnings))

    async def _sentiment_fact(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        assert rule.instrument_id is not None and rule.metric_key is not None
        _asset, market, _symbol = parse_instrument_id(rule.instrument_id)
        if market is Market.US:
            us_envelope = await self._us_context.get_sentiment_snapshot(
                USGetSentimentSnapshotInput(
                    instrument_id=rule.instrument_id,
                    start=(as_of - timedelta(days=7)).date(),
                    end=as_of.date(),
                    as_of=as_of,
                    limit_per_source=20,
                )
            )
            if not us_envelope.ok or us_envelope.data is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(us_envelope.warnings),
                    _codes(us_envelope.errors) or ("MONITOR_SENTIMENT_UNAVAILABLE",),
                )
            key, _, source = rule.metric_key.partition(":")
            if key == "disagreement":
                value = us_envelope.data.disagreement
            else:
                summaries = us_envelope.data.summaries
                if source:
                    summaries = tuple(
                        item for item in summaries if item.source.value == source.upper()
                    )
                if key == "sample_count":
                    value = Decimal(sum(item.sample_count for item in summaries))
                elif key == "weighted_score":
                    weighted = tuple(
                        item.weighted_score
                        for item in summaries
                        if item.weighted_score is not None
                    )
                    value = (
                        sum(weighted, Decimal(0)) / Decimal(len(weighted))
                        if weighted
                        else None
                    )
                else:
                    value = None
            fact_at = max(
                (item.published_at for item in us_envelope.data.samples),
                default=us_envelope.data.as_of,
            )
            return MonitorFact(
                value,
                fact_at,
                _codes(us_envelope.warnings),
                () if value is not None else ("MONITOR_METRIC_UNAVAILABLE",),
            )
        if market is Market.A_SHARE:
            a_envelope = await self._a_share.get_sentiment_snapshot(
                AShareGetSentimentSnapshotInput(
                    instrument_id=rule.instrument_id,
                    trade_date=as_of.date(),
                    as_of=as_of,
                )
            )
            if not a_envelope.ok or a_envelope.data is None:
                return MonitorFact(
                    None,
                    None,
                    _codes(a_envelope.warnings),
                    _codes(a_envelope.errors) or ("MONITOR_SENTIMENT_UNAVAILABLE",),
                )
            signals = tuple(
                item
                for item in a_envelope.data.signals
                if item.instrument_id in {None, rule.instrument_id}
            )
            values = tuple(
                value
                for value in (
                    getattr(item, rule.metric_key, None) for item in signals
                )
                if isinstance(value, (Decimal, int))
            )
            value = Decimal(str(values[0])) if values else None
            fact_at = max(
                (item.observed_at for item in signals if item.observed_at is not None),
                default=a_envelope.data.as_of,
            )
            return MonitorFact(
                value,
                fact_at,
                _codes(a_envelope.warnings),
                () if value is not None else ("MONITOR_METRIC_UNAVAILABLE",),
            )
        return MonitorFact(None, None, error_codes=("MONITOR_UNSUPPORTED_MARKET",))

    def _thesis_fact(self, rule: MonitorRule, as_of: datetime) -> MonitorFact:
        assert rule.metric_key is not None
        parts = rule.metric_key.split(":")
        target = parts[0]
        with self._research_uow_factory() as uow:
            if target == "status" and len(parts) == 3:
                thesis_id, expected = parts[1], parts[2]
                thesis = uow.theses.get(thesis_id)
                return MonitorFact(
                    Decimal(1 if thesis.status.value == expected.lower() else 0),
                    thesis.updated_at,
                )
            if target == "hard_invalidation_triggered" and len(parts) == 2:
                thesis_id = parts[1]
                thesis = uow.theses.get(thesis_id)
                rows = uow.invalidations.list_by_revision(
                    thesis_id, thesis.current_revision_no
                )
                triggered = any(
                    item.severity.value == "hard" and item.status.value == "triggered"
                    for item in rows
                )
                return MonitorFact(Decimal(1 if triggered else 0), thesis.updated_at)
        return MonitorFact(None, as_of, error_codes=("MONITOR_METRIC_UNAVAILABLE",))
