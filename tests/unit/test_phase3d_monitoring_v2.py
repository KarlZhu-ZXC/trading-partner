"""Focused Monitoring v2 fact-category and transition acceptance."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

from application.dto.monitoring import MonitorEvaluateInput
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_fact_resolver import MonitorFact, MonitorFactResolver
from conftest import FixedClock, SequentialIdGenerator
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition, MonitorRule
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType
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
            None if comparator is TradePlanComparator.OCCURRED else Decimal("1")
        ),
    )


class _FactResolver:
    unavailable = False

    async def resolve(self, _rule: MonitorRule, _as_of: datetime) -> MonitorFact:
        if self.unavailable:
            return MonitorFact(None, None, error_codes=("FACT_SOURCE_UNAVAILABLE",))
        return MonitorFact(Decimal("1"), NOW)


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
            case_id=None,
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
