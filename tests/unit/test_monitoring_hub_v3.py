"""Focused Monitoring Hub interval, history, and local scheduler coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from application.dto.monitor_dispatch import MonitorDispatchDisposition
from application.dto.monitoring import (
    MonitorCreateInput,
    MonitorEvaluateInput,
    MonitorRuleInput,
)
from application.dto.tool_envelope import ToolEnvelope
from application.dto.us_market import USQuoteDTO
from application.ports.market_session_calendar import MarketSession
from application.services.monitor_dispatch_service import MonitorDispatchService
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_notification_service import MonitorNotificationService
from application.services.monitor_schedule_service import MonitorScheduleService
from domain.common.enums import Freshness, TradingSession
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from interfaces.cli.monitor_scheduler import LABEL, _launchd_payload

NOW = datetime(2026, 7, 29, 4, 5, tzinfo=UTC)
POST_CLOSE_NOW = datetime(2026, 7, 29, 20, 15, tzinfo=UTC)
MONITOR_ID = "monitor_00000000-0000-7000-8000-000000000099"
US_MONITOR_ID = "monitor_00000000-0000-7000-8000-000000000100"


def _quote() -> ToolEnvelope[USQuoteDTO]:
    return ToolEnvelope.success(
        request_id="req_interval_quote",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=USQuoteDTO(
            instrument_id="future:US:GC=F",
            quote_at=NOW,
            session=TradingSession.REGULAR,
            last=Decimal("4055.7"),
            open=None,
            high=None,
            low=None,
            previous_close=None,
            volume=None,
            average_volume=None,
            market_cap=None,
            beta=None,
            week_52_low=None,
            week_52_high=None,
        ),
    )


def _interval_monitor() -> MonitorDefinition:
    return MonitorDefinition(
        monitor_id=MONITOR_ID,
        version=1,
        name="Gold four-hour conditions",
        case_id=None,
        primary_instrument_id="future:US:GC=F",
        cadence=MonitorCadence.INTERVAL,
        interval_minutes=240,
        status=MonitorStatus.ACTIVE,
        rules=(
            MonitorRuleInput(
                rule_code="gold_floor",
                description="Gold fell below its configured floor.",
                rule_type=MonitorRuleType.PRICE_BELOW,
                severity=MonitorSeverity.HIGH,
                instrument_id="future:US:GC=F",
                price_threshold=Decimal("4080"),
                max_fact_age_seconds=86400,
            ).to_domain(),
        ),
        confirmed_by="user",
        idempotency_key="gold-four-hour-monitor",
        created_at=NOW - timedelta(hours=4),
    )


def _us_post_market_monitor() -> MonitorDefinition:
    return MonitorDefinition(
        monitor_id=US_MONITOR_ID,
        version=1,
        name="US post-market condition",
        case_id=None,
        primary_instrument_id="equity:US:TTWO",
        cadence=MonitorCadence.US_POST_MARKET,
        interval_minutes=None,
        status=MonitorStatus.ACTIVE,
        rules=(
            MonitorRuleInput(
                rule_code="ttwo_floor",
                description="TTWO fell below its configured floor.",
                rule_type=MonitorRuleType.PRICE_BELOW,
                severity=MonitorSeverity.MEDIUM,
                instrument_id="equity:US:TTWO",
                price_threshold=Decimal("250"),
                max_fact_age_seconds=86400,
            ).to_domain(),
        ),
        confirmed_by="user",
        idempotency_key="ttwo-post-market-monitor",
        created_at=NOW,
    )


class _USCalendar:
    def session_at(self, moment: datetime) -> MarketSession | None:
        if moment.date() == date(2026, 7, 29):
            return MarketSession(
                session_date=date(2026, 7, 29),
                close_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
            )
        return None

    def session_on_or_before(self, _moment: datetime) -> MarketSession | None:
        return MarketSession(
            session_date=date(2026, 7, 29),
            close_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
        )

    def previous_session(self, _session_date: date) -> MarketSession | None:
        return None

    def next_session(self, _session_date: date) -> MarketSession | None:
        return MarketSession(
            session_date=date(2026, 7, 30),
            close_at=datetime(2026, 7, 30, 20, 0, tzinfo=UTC),
        )


def test_interval_configuration_requires_whole_hours() -> None:
    rule = MonitorRuleInput(
        rule_code="gold_floor",
        description="Gold fell below its configured floor.",
        rule_type="PRICE_BELOW",
        instrument_id="future:US:GC=F",
        price_threshold=Decimal("4080"),
    )

    with pytest.raises(ValidationError, match="whole-hour"):
        MonitorCreateInput(
            name="invalid",
            cadence="INTERVAL",
            interval_minutes=90,
            rules=(rule,),
            confirmed_by="user",
            idempotency_key="invalid-interval",
        )


@pytest.mark.asyncio
async def test_hourly_dispatch_skips_until_due_and_keeps_full_observations(
    tmp_path, fixed_clock, id_generator
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor-hub.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    repository.create(_interval_monitor())
    fixed_clock.set(NOW)
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=_quote())
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )
    notifications = MonitorNotificationService(
        repository,
        None,
        fixed_clock,
        enabled=False,
        configured=False,
    )
    dispatcher = MonitorDispatchService(
        repository,
        evaluator,
        notifications,
        MonitorScheduleService(),
        fixed_clock,
    )

    first = await dispatcher.run_due_intervals()
    fixed_clock.set(NOW + timedelta(hours=1))
    skipped = await dispatcher.run_due_intervals()

    assert first.disposition is MonitorDispatchDisposition.EXECUTED
    assert first.due_monitor_ids == (MONITOR_ID,)
    assert first.run is not None
    assert first.run.observation_history_complete is True
    assert first.run.observations[0].state is MonitorRuleStateValue.TRIGGERED
    assert first.run.observations[0].observed_value == Decimal("4055.7")
    assert first.run.observations[0].threshold_value == Decimal("4080")
    assert first.run.observations[0].distance_value == Decimal("-24.3")
    persisted = repository.get_run(first.run.run_id)
    assert persisted is not None
    assert persisted.observations[0].rule_code == "gold_floor"
    assert skipped.disposition is MonitorDispatchDisposition.NO_DUE_MONITORS
    assert skipped.next_due_at == NOW + timedelta(hours=4)
    assert market.get_market_snapshot.await_count == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_unified_hourly_dispatch_runs_us_post_market_once_per_session(
    tmp_path, fixed_clock, id_generator
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor-market-schedule.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    repository.create(_us_post_market_monitor())
    fixed_clock.set(POST_CLOSE_NOW)
    quote = _quote().model_copy(
        update={
            "as_of": POST_CLOSE_NOW,
            "fetched_at": POST_CLOSE_NOW,
            "data": _quote().data.model_copy(
                update={
                    "instrument_id": "equity:US:TTWO",
                    "quote_at": POST_CLOSE_NOW,
                    "last": Decimal("246"),
                }
            ),
        }
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=quote)
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )
    notifications = MonitorNotificationService(
        repository,
        None,
        fixed_clock,
        enabled=False,
        configured=False,
    )
    schedule = MonitorScheduleService(us_calendar=_USCalendar())
    dispatcher = MonitorDispatchService(
        repository,
        evaluator,
        notifications,
        schedule,
        fixed_clock,
    )

    first = await dispatcher.run_due()
    fixed_clock.set(POST_CLOSE_NOW + timedelta(hours=1))
    second = await dispatcher.run_due()

    assert first.disposition is MonitorDispatchDisposition.EXECUTED
    assert first.due_monitor_ids == (US_MONITOR_ID,)
    assert len(first.runs) == 1
    assert first.runs[0].cadence is MonitorCadence.US_POST_MARKET
    assert second.disposition is MonitorDispatchDisposition.NO_DUE_MONITORS
    assert second.next_due_at == datetime(2026, 7, 30, 20, 10, tzinfo=UTC)
    assert market.get_market_snapshot.await_count == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_monitor_scoped_run_history_never_returns_sibling_observations(
    tmp_path, fixed_clock, id_generator
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor-scoped-runs.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    first = _us_post_market_monitor()
    second_id = "monitor_00000000-0000-7000-8000-000000000101"
    second_rule = replace(
        first.rules[0],
        rule_code="tsla_floor",
        instrument_id="equity:US:TSLA",
    )
    second = replace(
        first,
        monitor_id=second_id,
        name="TSLA post-market condition",
        primary_instrument_id="equity:US:TSLA",
        rules=(second_rule,),
        idempotency_key="tsla-post-market-monitor",
    )
    repository.create(first)
    repository.create(second)
    fixed_clock.set(POST_CLOSE_NOW)
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=_quote())
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    run = await evaluator.evaluate(
        MonitorEvaluateInput(
            monitor_ids=(first.monitor_id, second.monitor_id),
            as_of=POST_CLOSE_NOW,
        )
    )
    scoped = repository.list_runs(first.monitor_id, 1)
    full = repository.get_run(run.run_id)

    assert len(scoped) == 1
    assert scoped[0].selected_monitor_ids == (first.monitor_id,)
    assert scoped[0].rules_evaluated == 1
    assert {item.monitor_id for item in scoped[0].observations} == {first.monitor_id}
    assert full is not None
    assert {item.monitor_id for item in full.observations} == {
        first.monitor_id,
        second.monitor_id,
    }
    engine.dispose()


def test_launchd_job_wakes_hourly_and_runs_due_dispatcher(tmp_path: Path) -> None:
    payload = _launchd_payload(tmp_path, Path("/opt/homebrew/bin/uv"))

    assert payload["Label"] == LABEL
    assert payload["StartCalendarInterval"] == {"Minute": 5}
    assert payload["ProgramArguments"][-2:] == [
        "trading-partner-monitor-run",
        "due",
    ]
    assert payload["StandardOutPath"] == "/dev/null"
    assert "Codex" not in " ".join(payload["ProgramArguments"])
