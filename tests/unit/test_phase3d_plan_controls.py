"""Focused Phase 3D domain, sizing, and risk-control tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from application.dto.monitoring import MonitorCreateInput
from application.dto.research import TradePlanConditionPayload
from application.dto.risk import RiskCheckInput
from application.services.monitor_service import MonitorService
from application.services.position_sizing_service import PositionSizingService
from application.services.risk_engine_service import RiskEngineService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import DecisionType, ResearchSubjectStatus, VendorId
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
)
from domain.portfolio.models import AccountOpenOrder, AccountPosition, AccountSnapshot
from domain.risk.enums import RiskCheckStatus, RiskConfirmer
from domain.risk.models import RiskPolicy
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlan, TradePlanCondition

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


def _condition(instrument_id: str) -> TradePlanCondition:
    return TradePlanCondition(
        condition_code="ENTRY_PRICE",
        phase=TradePlanConditionPhase.ENTRY,
        mode=TradePlanConditionMode.MONITORABLE,
        description="Entry condition",
        severity="MEDIUM",
        fact_type=TradePlanFactType.PRICE,
        metric_key="last",
        comparator=TradePlanComparator.LTE,
        threshold=Decimal("100"),
        unit="native_currency",
        instrument_id=instrument_id,
        max_fact_age_seconds=900,
    )


def _plan(instrument_id: str, currency: str, price: str, stop: str) -> TradePlan:
    return TradePlan(
        plan_id="trade_plan_00000000-0000-7000-8000-000000000001",
        version=1,
        subject_id="case_00000000-0000-7000-8000-000000000002",
        thesis_id="thesis_00000000-0000-7000-8000-000000000003",
        instrument_id=instrument_id,
        status=TradePlanStatus.ACTIVE,
        valid_from=NOW - timedelta(days=1),
        valid_until=None,
        currency=currency,
        reference_price=Decimal(price),
        reference_price_at=NOW,
        target_position_percent=Decimal("10"),
        max_position_percent=Decimal("20"),
        risk_budget_percent=Decimal("1"),
        stop_price=Decimal(stop),
        conditions=(_condition(instrument_id),),
        notes="No execution authority",
        confirmed_by="user",
        created_at=NOW,
        idempotency_key="phase3d-plan",
    )


def _policy() -> RiskPolicy:
    return RiskPolicy(
        policy_id="risk_policy_00000000-0000-7000-8000-000000000004",
        version=1,
        single_position_max_percent=Decimal("25"),
        gross_exposure_max_percent=Decimal("120"),
        minimum_cash_percent=Decimal("5"),
        margin_usage_max_percent=Decimal("25"),
        max_account_age_seconds=3600,
        max_price_age_seconds=900,
        is_system_default=False,
        confirmed_by=RiskConfirmer.USER,
        created_at=NOW,
        idempotency_key="test",
        liquidity_participation_max_percent=Decimal("10"),
    )


def _account(
    instrument_id: str,
    currency: str,
    *,
    quantity: str = "0",
    average_cost: Decimal | None = None,
    open_orders: tuple[AccountOpenOrder, ...] = (),
) -> AccountSnapshot:
    positions = ()
    if Decimal(quantity) > 0:
        positions = (
            AccountPosition(
                instrument_id=instrument_id,
                side=AccountPositionSide.LONG,
                quantity=Decimal(quantity),
                sellable_quantity=Decimal(quantity),
                average_cost=average_cost,
                diluted_cost=None,
                market_price=Decimal("10"),
                market_price_at=NOW,
                market_value=Decimal(quantity) * Decimal("10"),
                unrealized_pnl=None,
                realized_pnl=None,
                currency=currency,
            ),
        )
    return AccountSnapshot(
        snapshot_id="snapshot_00000000-0000-7000-8000-000000000005",
        account_ref="acct_phase3d",
        provider=VendorId.MANUAL_CSV,
        environment=AccountEnvironment.REAL,
        base_currency=currency,
        account_as_of=NOW,
        fetched_at=NOW,
        cash=Decimal("50000"),
        buying_power=Decimal("50000"),
        net_assets=Decimal("100000"),
        margin_used=Decimal("0"),
        positions=positions,
        open_orders=open_orders,
        degraded=False,
        warning_codes=(),
    )


def test_candidate_rejects_manual_condition_with_machine_fields() -> None:
    with pytest.raises(ValueError, match="MANUAL condition"):
        TradePlanConditionPayload(
            condition_code="BAD_MANUAL",
            phase=TradePlanConditionPhase.REVIEW,
            mode=TradePlanConditionMode.MANUAL,
            description="Human-only review",
            fact_type=TradePlanFactType.PRICE,
        )


def test_position_sizing_covers_a_share_lots_us_fractional_and_stale_price() -> None:
    service = PositionSizingService(lambda: None)  # type: ignore[arg-type,return-value]
    policy = _policy()

    a_plan = _plan("equity:A_SHARE:600519.SH", "CNY", "10", "8")
    a_result = service.calculate(
        a_plan,
        policy,
        (_account(a_plan.instrument_id, "CNY", quantity="100"),),
        RiskCheckInput(
            average_daily_value=Decimal("1000000"),
            max_liquidity_participation_percent=Decimal("50"),
        ),
        as_of=NOW,
    )
    assert a_result.lot_size == Decimal("100")
    assert a_result.recommended_min_additional_quantity == Decimal("400")
    assert a_result.recommended_max_additional_quantity == Decimal("400")
    liquidity = next(
        item for item in a_result.constraints if item.constraint_code == "LIQUIDITY_PARTICIPATION"
    )
    assert liquidity.limiting_value == Decimal("10")
    assert a_result.execution_effect is False
    assert a_result.historically_validated is False

    us_plan = _plan("equity:US:NVDA", "USD", "93", "86")
    us_result = service.calculate(
        us_plan,
        policy,
        (_account(us_plan.instrument_id, "USD"),),
        RiskCheckInput(),
        as_of=NOW,
    )
    assert us_result.lot_size == Decimal("0.0001")
    assert us_result.recommended_max_additional_quantity is not None
    assert us_result.recommended_max_additional_quantity % Decimal("0.0001") == 0

    stale = service.calculate(
        replace(us_plan, reference_price_at=NOW - timedelta(hours=1)),
        policy,
        (_account(us_plan.instrument_id, "USD"),),
        RiskCheckInput(),
        as_of=NOW,
    )
    assert stale.recommended_max_additional_quantity is None
    assert "TRADE_PLAN_REFERENCE_PRICE_STALE" in stale.data_quality_codes


@pytest.mark.asyncio
async def test_missing_average_cost_keeps_drawdown_not_evaluated() -> None:
    plan = _plan("equity:US:NVDA", "USD", "10", "8")
    account = _account(plan.instrument_id, "USD", quantity="10", average_cost=None)

    class Accounts:
        def get_snapshots(self, _ids: tuple[str, ...]) -> tuple[AccountSnapshot, ...]:
            return (account,)

        async def refresh(self, **_kwargs: object) -> None:
            raise AssertionError("must not refresh")

    class Policies:
        def get_current(self) -> RiskPolicy:
            return _policy()

    class Sizing:
        def get_plan(self, _plan_id: str) -> TradePlan:
            return plan

        def count_duplicate_intents(self, _plan: TradePlan) -> int:
            return 0

        def calculate(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            return PositionSizingService(lambda: None).calculate(  # type: ignore[arg-type,return-value]
                plan, _policy(), (account,), RiskCheckInput(), as_of=NOW
            )

    result, _ = await RiskEngineService(
        Accounts(), Policies(), Sizing()  # type: ignore[arg-type]
    ).check(RiskCheckInput(trade_plan_id=plan.plan_id), effective_as_of=NOW)
    drawdown = next(
        item for item in result.checks if item.rule_code == "DRAWDOWN_FROM_REPORTED_COST"
    )
    assert drawdown.status is RiskCheckStatus.NOT_EVALUATED
    assert result.execution_effect is False


@pytest.mark.asyncio
async def test_risk_includes_valued_open_buy_orders_as_prospective_exposure() -> None:
    order = AccountOpenOrder(
        provider_order_id="order-open-buy",
        instrument_id="equity:US:NVDA",
        side=AccountOpenOrderSide.BUY,
        status=AccountOpenOrderStatus.PARTIAL,
        quantity=Decimal("10"),
        filled_quantity=Decimal("2"),
        limit_price=Decimal("100"),
        submitted_at=NOW,
    )
    account = _account("equity:US:AAPL", "USD", quantity="10", open_orders=(order,))

    class Accounts:
        def get_snapshots(self, _ids: tuple[str, ...]) -> tuple[AccountSnapshot, ...]:
            return (account,)

    class Policies:
        def get_current(self) -> RiskPolicy:
            return _policy()

    result, _ = await RiskEngineService(  # type: ignore[arg-type]
        Accounts(), Policies()
    ).check(RiskCheckInput(), effective_as_of=NOW)

    open_buy = next(item for item in result.checks if item.rule_code == "OPEN_BUY_ORDERS_PRESENT")
    coverage = next(
        item for item in result.checks if item.rule_code == "OPEN_BUY_ORDER_VALUATION_COVERAGE"
    )
    nvda = next(
        item
        for item in result.checks
        if item.rule_code == "SINGLE_POSITION_CONCENTRATION"
        and item.scope == "equity:US:NVDA/USD"
    )
    assert open_buy.status is RiskCheckStatus.WARN
    assert coverage.status is RiskCheckStatus.PASS
    assert nvda.actual == Decimal("88.88888888888888888888888889")


def test_monitor_compiles_only_machine_conditions_and_inherits_plan_expiry() -> None:
    manual = TradePlanCondition(
        condition_code="MANUAL_GUIDANCE",
        phase=TradePlanConditionPhase.REVIEW,
        mode=TradePlanConditionMode.MANUAL,
        description="Read management guidance.",
        severity="INFO",
    )
    plan = replace(
        _plan("equity:US:NVDA", "USD", "100", "90"),
        conditions=(_condition("equity:US:NVDA"), manual),
        valid_until=NOW + timedelta(days=7),
    )

    class Plans:
        def get_version(self, plan_id: str, version: int) -> TradePlan | None:
            return plan if (plan_id, version) == (plan.plan_id, plan.version) else None

    class Subjects:
        def get(self, subject_id: str) -> object:
            assert subject_id == plan.subject_id
            return SimpleNamespace(status=ResearchSubjectStatus.ACTIVE)

    class Uow:
        trade_plans = Plans()
        subjects = Subjects()

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    repository = MagicMock()
    repository.get_by_idempotency_key.return_value = None
    service = MonitorService(
        repository,
        lambda: Uow(),  # type: ignore[arg-type,return-value]
        FixedClock(NOW),
        SequentialIdGenerator(),
    )
    result = service.create(
        MonitorCreateInput(
            name="NVDA plan monitor",
            trade_plan_id=plan.plan_id,
            trade_plan_version=plan.version,
            compile_trade_plan_conditions=True,
            confirmed_by="user",
            idempotency_key="phase3d-monitor",
        )
    )

    assert result.monitor.trade_plan_id == plan.plan_id
    assert result.monitor.trade_plan_version == 1
    assert result.monitor.valid_until == plan.valid_until
    assert [rule.rule_code for rule in result.monitor.rules] == ["ENTRY_PRICE"]
    assert result.monitor.rules[0].fact_type is TradePlanFactType.PRICE
    repository.create.assert_called_once()


@pytest.mark.parametrize("explicit_reference", [False, True])
def test_uco_trade_plan_monitor_can_observe_usoil_reference(
    explicit_reference: bool,
) -> None:
    execution_instrument = "etf:US:UCO"
    reference_instrument = "cfd:OTC:LIGHT_CMD_USD"
    plan = replace(
        _plan(execution_instrument, "USD", "25", "20"),
        conditions=(_condition(reference_instrument),),
    )

    class Plans:
        def get_version(self, plan_id: str, version: int) -> TradePlan | None:
            return plan if (plan_id, version) == (plan.plan_id, plan.version) else None

    class Subjects:
        def get(self, subject_id: str) -> object:
            assert subject_id == plan.subject_id
            return SimpleNamespace(status=ResearchSubjectStatus.ACTIVE)

    class Uow:
        trade_plans = Plans()
        subjects = Subjects()

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    repository = MagicMock()
    repository.get_by_idempotency_key.return_value = None
    service = MonitorService(
        repository,
        lambda: Uow(),  # type: ignore[arg-type,return-value]
        FixedClock(NOW),
        SequentialIdGenerator(),
    )
    result = service.create(
        MonitorCreateInput(
            name="USOIL reference for UCO plan",
            primary_instrument_id=(reference_instrument if explicit_reference else None),
            trade_plan_id=plan.plan_id,
            trade_plan_version=plan.version,
            compile_trade_plan_conditions=True,
            confirmed_by="user",
            idempotency_key=f"uco-usoil-{explicit_reference}",
        )
    )

    assert plan.instrument_id == execution_instrument
    assert result.monitor.primary_instrument_id == reference_instrument
    assert result.monitor.rules[0].instrument_id == reference_instrument


def test_duplicate_intent_check_uses_only_unsuperseded_durable_decisions() -> None:
    plan = _plan("equity:US:NVDA", "USD", "100", "90")
    old = SimpleNamespace(
        decision_id="decision_old",
        supersedes_decision_id=None,
        primary_instrument_id=plan.instrument_id,
        decision_type=DecisionType.ADD_INTENT,
    )
    replacement = SimpleNamespace(
        decision_id="decision_new",
        supersedes_decision_id="decision_old",
        primary_instrument_id=plan.instrument_id,
        decision_type=DecisionType.HOLD,
    )

    class Decisions:
        def list_by_subject(self, subject_id: str):  # type: ignore[no-untyped-def]
            assert subject_id == plan.subject_id
            return (old, replacement)

    class Uow:
        decisions = Decisions()

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    service = PositionSizingService(lambda: Uow())  # type: ignore[arg-type,return-value]
    assert service.count_duplicate_intents(plan) == 0
