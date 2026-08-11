"""Focused Monitoring v2 fact-category and transition acceptance."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

from application.dto.monitoring import MonitorEvaluateInput
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_fact_resolver import MonitorFact, MonitorFactResolver
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import Market
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition, MonitorRule, MonitorRuleState
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


def _rule(
    code: str,
    fact_type: TradePlanFactType,
    metric: str,
    *,
    instrument_id: str | None = None,
    comparator: TradePlanComparator = TradePlanComparator.GTE,
    threshold: Decimal = Decimal("1"),
    recovery_threshold: Decimal | None = None,
    technical_interval: Literal["1d", "1w"] | None = None,
) -> MonitorRule:
    return MonitorRule(
        rule_code=code,
        rule_type=MonitorRuleType.FACT_COMPARISON,
        severity=MonitorSeverity.MEDIUM,
        instrument_id=instrument_id,
        price_threshold=None,
        risk_status_threshold=None,
        max_fact_age_seconds=3600,
        fact_type=fact_type,
        metric_key=metric,
        comparator=comparator,
        numeric_threshold=(
            None if comparator is TradePlanComparator.OCCURRED else threshold
        ),
        recovery_threshold=recovery_threshold,
        technical_interval=technical_interval,
    )


class _FactResolver:
    unavailable = False

    async def resolve(self, _rule: MonitorRule, _as_of: datetime) -> MonitorFact:
        if self.unavailable:
            return MonitorFact(None, None, error_codes=("FACT_SOURCE_UNAVAILABLE",))
        return MonitorFact(Decimal("1"), NOW)


class _DailyTechnicalFactResolver:
    def __init__(self, fact_at: datetime) -> None:
        self.fact_at = fact_at

    async def resolve(self, _rule: MonitorRule, _as_of: datetime) -> MonitorFact:
        return MonitorFact(
            Decimal("68.38"),
            self.fact_at,
            warning_codes=("TECHNICAL_DATA_NOT_FRESH",),
        )


@pytest.mark.parametrize(
    ("fact_at", "evaluated_at", "expected_state", "warning_expected"),
    (
        (
            datetime(2026, 8, 7, 20, tzinfo=UTC),
            datetime(2026, 8, 10, 16, 5, tzinfo=UTC),
            MonitorRuleStateValue.QUIET,
            False,
        ),
        (
            datetime(2026, 9, 4, 20, tzinfo=UTC),
            datetime(2026, 9, 8, 16, 5, tzinfo=UTC),
            MonitorRuleStateValue.QUIET,
            False,
        ),
        (
            datetime(2026, 8, 7, 20, tzinfo=UTC),
            datetime(2026, 8, 10, 21, 5, tzinfo=UTC),
            MonitorRuleStateValue.NOT_EVALUATED,
            True,
        ),
    ),
)
@pytest.mark.asyncio
async def test_daily_technical_freshness_uses_latest_completed_exchange_session(
    tmp_path,
    fact_at: datetime,
    evaluated_at: datetime,
    expected_state: MonitorRuleStateValue,
    warning_expected: bool,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'session-freshness.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    rule = _rule(
        "GDXU_RSI_OVERHEAT_80",
        TradePlanFactType.TECHNICAL,
        "rsi_14",
        instrument_id="etf:US:GDXU",
        threshold=Decimal("80"),
        technical_interval="1d",
    )
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000099",
        version=1,
        name="Session-aware daily technical",
        subject_id=None,
        primary_instrument_id="etf:US:GDXU",
        cadence=MonitorCadence.ON_DEMAND,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key=f"session-aware-{evaluated_at.isoformat()}",
        created_at=fact_at,
    )
    repository.create(monitor)
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        FixedClock(evaluated_at),
        SequentialIdGenerator(),
        _DailyTechnicalFactResolver(fact_at),  # type: ignore[arg-type]
        session_calendars={Market.US: XnysMarketSessionCalendar()},
    )

    run = await evaluator.evaluate(
        MonitorEvaluateInput(monitor_ids=(monitor.monitor_id,), as_of=evaluated_at)
    )

    assert run.observations[0].state is expected_state
    assert run.observations[0].fact_age_seconds is not None
    assert run.observations[0].fact_age_seconds > rule.max_fact_age_seconds
    assert (
        "TECHNICAL_DATA_NOT_FRESH" in run.observations[0].warning_codes
    ) is warning_expected
    engine.dispose()


@pytest.mark.asyncio
async def test_all_monitor_fact_categories_transition_and_deduplicate(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor-v2.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    rules = (
        _rule(
            "PRICE",
            TradePlanFactType.PRICE,
            "last",
            instrument_id="equity:US:NVDA",
            comparator=TradePlanComparator.GTE,
        ),
        _rule(
            "VOLUME",
            TradePlanFactType.VOLUME,
            "volume",
            instrument_id="equity:US:NVDA",
        ),
        _rule(
            "TECHNICAL",
            TradePlanFactType.TECHNICAL,
            "rsi_14",
            instrument_id="equity:US:NVDA",
        ),
        _rule(
            "FUNDAMENTAL",
            TradePlanFactType.FUNDAMENTAL,
            "revenue",
            instrument_id="equity:US:NVDA",
        ),
        _rule(
            "COMPANY_EVENT",
            TradePlanFactType.COMPANY_EVENT,
            "ANY",
            instrument_id="equity:US:NVDA",
            comparator=TradePlanComparator.OCCURRED,
        ),
        _rule("MACRO", TradePlanFactType.MACRO, "CPIAUCSL"),
        _rule(
            "SENTIMENT",
            TradePlanFactType.SENTIMENT,
            "sample_count",
            instrument_id="equity:US:NVDA",
        ),
        _rule(
            "THESIS_STATE",
            TradePlanFactType.THESIS_STATE,
            "status:thesis_00000000-0000-7000-8000-000000000001:active",
        ),
        _rule(
            "PORTFOLIO_RISK",
            TradePlanFactType.PORTFOLIO_RISK,
            "overall_status",
        ),
    )
    repository.create(
        MonitorDefinition(
            monitor_id="monitor_00000000-0000-7000-8000-000000000001",
            version=1,
            name="Phase 3D category acceptance",
            subject_id=None,
            primary_instrument_id="equity:US:NVDA",
            cadence=MonitorCadence.ON_DEMAND,
            status=MonitorStatus.ACTIVE,
            rules=rules,
            confirmed_by="user",
            idempotency_key="phase3d-category-monitor",
            created_at=NOW,
        )
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            ok=True,
            data=SimpleNamespace(last=Decimal("10"), quote_at=NOW),
            sources=(),
            warnings=(),
            errors=(),
        )
    )
    risk = MagicMock()
    risk.check = AsyncMock(
        return_value=SimpleNamespace(
            ok=True,
            data=SimpleNamespace(overall_status=RiskOverallStatus.WARN, as_of=NOW),
            warnings=(),
            errors=(),
        )
    )
    resolver = _FactResolver()
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        risk,
        FixedClock(NOW),
        SequentialIdGenerator(),
        resolver,  # type: ignore[arg-type]
    )

    first = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))
    unchanged = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))
    resolver.unavailable = True
    unavailable = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))

    assert first.status is MonitorRunStatus.SUCCEEDED
    assert first.rules_evaluated == 9
    assert first.events_created == 9
    assert unchanged.events_created == 0
    assert unavailable.status is MonitorRunStatus.PARTIAL
    assert unavailable.events_created == 7
    assert unavailable.error_codes == ("FACT_SOURCE_UNAVAILABLE",)
    states = repository.get_rule_states(
        "monitor_00000000-0000-7000-8000-000000000001"
    )
    state_by_code = {item.rule_code: item.state for item in states}
    assert state_by_code["PRICE"] is MonitorRuleStateValue.TRIGGERED
    assert state_by_code["PORTFOLIO_RISK"] is MonitorRuleStateValue.TRIGGERED
    assert all(
        state_by_code[code] is MonitorRuleStateValue.NOT_EVALUATED
        for code in {
            "VOLUME",
            "TECHNICAL",
            "FUNDAMENTAL",
            "COMPANY_EVENT",
            "MACRO",
            "SENTIMENT",
            "THESIS_STATE",
        }
    )
    events = repository.list_events(None, 100)
    assert sum(item.event_type is MonitorEventType.TRIGGERED for item in events) == 9
    assert sum(item.event_type is MonitorEventType.NOT_EVALUATED for item in events) == 7
    engine.dispose()


@pytest.mark.asyncio
async def test_a_share_fundamental_without_publication_time_is_not_evaluated() -> None:
    a_share = MagicMock()
    a_share.get_financial_statements = AsyncMock(
        return_value=SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                periods=(
                    SimpleNamespace(
                        metrics=(
                            SimpleNamespace(
                                metric_code="revenue",
                                value=Decimal("10"),
                                published_at=None,
                            ),
                        ),
                    ),
                ),
            ),
            warnings=(),
            errors=(),
        )
    )
    resolver = MonitorFactResolver(
        technical=MagicMock(),
        a_share=a_share,
        us_research=MagicMock(),
        us_context=MagicMock(),
        research_uow_factory=MagicMock(),
    )
    rule = _rule(
        "FUNDAMENTAL",
        TradePlanFactType.FUNDAMENTAL,
        "revenue",
        instrument_id="equity:A_SHARE:600519.SH",
    )

    fact = await resolver.resolve(rule, NOW)

    assert fact.value is None
    assert fact.as_of is None
    assert fact.error_codes == ("MONITOR_PUBLICATION_TIME_UNAVAILABLE",)


@pytest.mark.asyncio
async def test_technical_fact_uses_requested_weekly_interval() -> None:
    technical = MagicMock()
    technical.get_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                timeframes=(
                    SimpleNamespace(
                        interval="1w",
                        bar_as_of=NOW,
                        metrics=(SimpleNamespace(name="rsi_14", value=Decimal("28.5")),),
                    ),
                )
            ),
            warnings=(),
            errors=(),
        )
    )
    resolver = MonitorFactResolver(
        technical=technical,
        a_share=MagicMock(),
        us_research=MagicMock(),
        us_context=MagicMock(),
        research_uow_factory=MagicMock(),
    )
    rule = _rule(
        "WEEKLY_RSI",
        TradePlanFactType.TECHNICAL,
        "rsi_14",
        instrument_id="equity:US:NVDA",
        comparator=TradePlanComparator.LT,
        threshold=Decimal("30"),
        recovery_threshold=Decimal("35"),
        technical_interval="1w",
    )

    fact = await resolver.resolve(rule, NOW)

    request = technical.get_snapshot.await_args.args[0]
    assert request.intervals == ("1w",)
    assert fact.value == Decimal("28.5")
    assert fact.as_of == NOW


def test_fact_rule_hysteresis_waits_for_recovery_threshold() -> None:
    rule = _rule(
        "RSI_OVERSOLD",
        TradePlanFactType.TECHNICAL,
        "rsi_14",
        instrument_id="equity:US:NVDA",
        comparator=TradePlanComparator.LT,
        threshold=Decimal("30"),
        recovery_threshold=Decimal("35"),
        technical_interval="1d",
    )
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000099",
        version=1,
        name="RSI hysteresis",
        subject_id=None,
        primary_instrument_id="equity:US:NVDA",
        cadence=MonitorCadence.US_POST_MARKET,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="rsi-hysteresis",
        created_at=NOW,
    )
    previous = MonitorRuleState(
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        state=MonitorRuleStateValue.TRIGGERED,
        observed_value=Decimal("29"),
        fact_as_of=NOW,
        message="Rule condition triggered.",
        updated_at=NOW,
    )

    still_triggered = MonitorEvaluationService._evaluate_rule(
        monitor,
        rule,
        SimpleNamespace(value=Decimal("32"), as_of=NOW, closed_session_last_known=False),
        NOW,
        previous,
    )
    recovered = MonitorEvaluationService._evaluate_rule(
        monitor,
        rule,
        SimpleNamespace(value=Decimal("35"), as_of=NOW, closed_session_last_known=False),
        NOW,
        previous,
    )

    assert still_triggered.state is MonitorRuleStateValue.TRIGGERED
    assert recovered.state is MonitorRuleStateValue.QUIET


def test_monitor_repository_round_trips_technical_interval_and_hysteresis(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor-technical-rule.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    rule = _rule(
        "RSI_OVERSOLD",
        TradePlanFactType.TECHNICAL,
        "rsi_14",
        instrument_id="equity:US:NVDA",
        comparator=TradePlanComparator.LT,
        threshold=Decimal("30"),
        recovery_threshold=Decimal("35"),
        technical_interval="1w",
    )
    repository.create(
        MonitorDefinition(
            monitor_id="monitor_00000000-0000-7000-8000-000000000098",
            version=1,
            name="Weekly RSI",
            subject_id=None,
            primary_instrument_id="equity:US:NVDA",
            cadence=MonitorCadence.US_POST_MARKET,
            status=MonitorStatus.ACTIVE,
            rules=(rule,),
            confirmed_by="user",
            idempotency_key="weekly-rsi-roundtrip",
            created_at=NOW,
        )
    )

    restored = repository.get_current("monitor_00000000-0000-7000-8000-000000000098")

    assert restored is not None
    assert restored.rules[0].technical_interval == "1w"
    assert restored.rules[0].recovery_threshold == Decimal("35")
    engine.dispose()
