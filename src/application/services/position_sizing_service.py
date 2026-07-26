"""Deterministic, non-executing Phase 3D position sizing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from application.dto.risk import RiskCheckInput
from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.enums import AssetType, DecisionType, Market
from domain.common.errors import DataContractError, TradePlanNotFound
from domain.common.values import parse_instrument_id
from domain.portfolio.models import AccountSnapshot
from domain.risk.enums import RiskCheckStatus
from domain.risk.models import (
    PositionSizingConstraint,
    PositionSizingResult,
    RiskPolicy,
)
from domain.trade_plan.enums import TradePlanStatus
from domain.trade_plan.models import TradePlan

UowFactory = Callable[[], ResearchUnitOfWork]
_HUNDRED = Decimal("100")


class PositionSizingService:
    def __init__(self, uow_factory: UowFactory) -> None:
        self._uow_factory = uow_factory

    def get_plan(self, plan_id: str) -> TradePlan:
        with self._uow_factory() as uow:
            plan = uow.trade_plans.get_current(plan_id)
            if plan is None:
                raise TradePlanNotFound(
                    "Trade Plan was not found", details={"trade_plan_id": plan_id}
                )
            return plan

    def count_duplicate_intents(self, plan: TradePlan) -> int:
        """Count current durable initiate/add intents for the plan instrument.

        Decision records are research intent, never broker orders. A later record
        that explicitly supersedes an earlier one removes the earlier record from
        the current-intent set.
        """
        with self._uow_factory() as uow:
            decisions = uow.decisions.list_by_case(plan.case_id)
        superseded_ids = {
            item.supersedes_decision_id
            for item in decisions
            if item.supersedes_decision_id is not None
        }
        return sum(
            1
            for item in decisions
            if item.decision_id not in superseded_ids
            and item.primary_instrument_id == plan.instrument_id
            and item.decision_type
            in {DecisionType.INITIATE_INTENT, DecisionType.ADD_INTENT}
        )

    def calculate(
        self,
        plan: TradePlan,
        policy: RiskPolicy,
        snapshots: tuple[AccountSnapshot, ...],
        request: RiskCheckInput,
        *,
        as_of: datetime,
    ) -> PositionSizingResult:
        if plan.status is not TradePlanStatus.ACTIVE:
            raise DataContractError("Position sizing requires an ACTIVE Trade Plan")
        if plan.valid_from > as_of:
            raise DataContractError("Trade Plan is not active at the requested as_of")
        if plan.valid_until is not None and as_of > plan.valid_until:
            raise DataContractError("Trade Plan expired before the requested as_of")

        quality: list[str] = []
        constraints: list[PositionSizingConstraint] = []
        currency = plan.currency.upper()
        matching = tuple(item for item in snapshots if item.base_currency.upper() == currency)
        if len(matching) != len(snapshots):
            quality.append("FX_CONVERSION_UNAVAILABLE")
        nav = self._sum_complete(matching, "net_assets")
        cash = self._sum_complete(matching, "cash")
        current_quantity = sum(
            (
                position.quantity
                for snapshot in snapshots
                for position in snapshot.positions
                if position.instrument_id == plan.instrument_id
                and position.currency.upper() == currency
            ),
            Decimal(0),
        )

        _asset, market, _symbol = parse_instrument_id(plan.instrument_id)
        lot_size = self._lot_size(_asset, market)
        price_age = (as_of - plan.reference_price_at).total_seconds()
        price_usable = 0 <= price_age <= policy.max_price_age_seconds
        if not price_usable:
            quality.append("TRADE_PLAN_REFERENCE_PRICE_STALE")
            constraints.append(
                self._missing(
                    "REFERENCE_PRICE_FRESHNESS",
                    "Trade Plan reference price is in the future or exceeds policy age.",
                )
            )
        else:
            constraints.append(
                PositionSizingConstraint(
                    constraint_code="REFERENCE_PRICE_FRESHNESS",
                    status=RiskCheckStatus.PASS,
                    max_quantity=None,
                    limiting_value=Decimal(int(price_age)),
                    unit="seconds",
                    message="Trade Plan reference price is within policy age.",
                )
            )

        plan_max = self._percent_quantity(
            nav, plan.max_position_percent, plan.reference_price
        )
        constraints.append(
            self._quantity_constraint(
                "PLAN_MAX_POSITION",
                plan_max,
                plan.max_position_percent,
                "percent_nav",
                "Plan maximum position requires same-currency account NAV.",
            )
        )
        policy_max = self._percent_quantity(
            nav, policy.single_position_max_percent, plan.reference_price
        )
        constraints.append(
            self._quantity_constraint(
                "POLICY_SINGLE_POSITION",
                policy_max,
                policy.single_position_max_percent,
                "percent_nav",
                "Policy position cap requires same-currency account NAV.",
            )
        )
        cash_max = cash / plan.reference_price if cash is not None and cash >= 0 else None
        constraints.append(
            self._quantity_constraint(
                "AVAILABLE_CASH",
                cash_max,
                cash,
                currency,
                "Cash cap requires same-currency account cash.",
            )
        )

        per_unit_loss = (
            abs(plan.reference_price - plan.stop_price)
            if plan.stop_price is not None
            else None
        )
        if per_unit_loss is None or per_unit_loss == 0 or nav is None:
            quality.append("TRADE_PLAN_STOP_OR_NAV_UNAVAILABLE")
            risk_max = None
        else:
            effective_risk_percent = min(
                plan.risk_budget_percent, policy.risk_budget_max_percent
            )
            risk_budget_amount = nav * effective_risk_percent / _HUNDRED
            risk_max = risk_budget_amount / per_unit_loss
        constraints.append(
            self._quantity_constraint(
                "PLAN_RISK_BUDGET",
                risk_max,
                (
                    nav
                    * min(plan.risk_budget_percent, policy.risk_budget_max_percent)
                    / _HUNDRED
                    if nav is not None
                    else None
                ),
                currency,
                "Risk-budget sizing requires NAV and a non-zero stop distance.",
            )
        )

        if request.average_daily_value is None:
            constraints.append(
                self._missing(
                    "LIQUIDITY_PARTICIPATION",
                    "Average daily traded value was not supplied; liquidity cap is unavailable.",
                )
            )
            quality.append("LIQUIDITY_FACT_UNAVAILABLE")
        else:
            assert request.max_liquidity_participation_percent is not None
            effective_participation = min(
                request.max_liquidity_participation_percent,
                policy.liquidity_participation_max_percent,
            )
            liquid_value = (
                request.average_daily_value
                * effective_participation
                / _HUNDRED
            )
            constraints.append(
                self._quantity_constraint(
                    "LIQUIDITY_PARTICIPATION",
                    liquid_value / plan.reference_price,
                    effective_participation,
                    "percent_adv",
                    "",
                )
            )

        if request.atr is None or nav is None:
            constraints.append(
                self._missing(
                    "ATR_RISK_BUDGET",
                    "ATR sizing requires same-currency NAV and a verified ATR fact.",
                )
            )
        else:
            atr_risk_amount = (
                nav
                * min(plan.risk_budget_percent, policy.risk_budget_max_percent)
                / _HUNDRED
            )
            constraints.append(
                self._quantity_constraint(
                    "ATR_RISK_BUDGET",
                    atr_risk_amount / request.atr,
                    request.atr,
                    currency,
                    "",
                )
            )

        if request.annualized_volatility_percent is None or nav is None:
            constraints.append(
                self._missing(
                    "VOLATILITY_TARGET",
                    "Volatility targeting requires NAV and verified target/observed volatility.",
                )
            )
        else:
            assert request.target_volatility_percent is not None
            volatility_value = (
                nav
                * request.target_volatility_percent
                / request.annualized_volatility_percent
            )
            constraints.append(
                self._quantity_constraint(
                    "VOLATILITY_TARGET",
                    volatility_value / plan.reference_price,
                    request.target_volatility_percent,
                    "percent_annualized",
                    "",
                )
            )

        mandatory = (plan_max, policy_max, cash_max, risk_max)
        if nav is None:
            quality.append("ACCOUNT_NAV_UNAVAILABLE")
        if cash is None:
            quality.append("ACCOUNT_CASH_UNAVAILABLE")
        if not price_usable or any(value is None for value in mandatory):
            target_total = None
            max_total = None
            min_additional = None
            max_additional = None
            estimated_loss = None
        else:
            optional_limits = tuple(
                item.max_quantity
                for item in constraints
                if item.constraint_code in {
                    "LIQUIDITY_PARTICIPATION",
                    "ATR_RISK_BUDGET",
                    "VOLATILITY_TARGET",
                }
                and item.max_quantity is not None
            )
            raw_max = min(*(value for value in mandatory if value is not None), *optional_limits)
            assert nav is not None
            raw_target = nav * plan.target_position_percent / _HUNDRED / plan.reference_price
            max_total = self._round_down(raw_max, lot_size)
            target_total = self._round_down(min(raw_target, max_total), lot_size)
            max_additional = self._round_down(
                max(Decimal(0), max_total - current_quantity), lot_size
            )
            min_additional = self._round_down(
                max(Decimal(0), target_total - current_quantity), lot_size
            )
            min_additional = min(min_additional, max_additional)
            estimated_loss = (
                max_total * per_unit_loss if per_unit_loss is not None else None
            )

        return PositionSizingResult(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            instrument_id=plan.instrument_id,
            currency=currency,
            reference_price=plan.reference_price,
            reference_price_at=plan.reference_price_at,
            current_quantity=current_quantity,
            lot_size=lot_size,
            target_total_quantity=target_total,
            max_total_quantity=max_total,
            recommended_min_additional_quantity=min_additional,
            recommended_max_additional_quantity=max_additional,
            estimated_max_loss=estimated_loss,
            constraints=tuple(constraints),
            data_quality_codes=tuple(dict.fromkeys(quality)),
            historically_validated=False,
            execution_effect=False,
        )

    @staticmethod
    def _sum_complete(
        snapshots: tuple[AccountSnapshot, ...], field: str
    ) -> Decimal | None:
        if not snapshots:
            return None
        values = tuple(getattr(snapshot, field) for snapshot in snapshots)
        if any(value is None for value in values):
            return None
        return sum((value for value in values if value is not None), Decimal(0))

    @staticmethod
    def _percent_quantity(
        nav: Decimal | None, percent: Decimal, price: Decimal
    ) -> Decimal | None:
        return None if nav is None else nav * percent / _HUNDRED / price

    @staticmethod
    def _lot_size(asset: AssetType, market: Market) -> Decimal:
        if market is Market.A_SHARE and asset in {AssetType.EQUITY, AssetType.ETF}:
            return Decimal("100")
        if market is Market.US and asset in {AssetType.EQUITY, AssetType.ETF}:
            return Decimal("0.0001")
        return Decimal("1")

    @staticmethod
    def _round_down(value: Decimal, lot_size: Decimal) -> Decimal:
        lots = (value / lot_size).to_integral_value(rounding=ROUND_DOWN)
        return lots * lot_size

    @staticmethod
    def _missing(code: str, message: str) -> PositionSizingConstraint:
        return PositionSizingConstraint(
            constraint_code=code,
            status=RiskCheckStatus.NOT_EVALUATED,
            max_quantity=None,
            limiting_value=None,
            unit="unknown",
            message=message,
        )

    @staticmethod
    def _quantity_constraint(
        code: str,
        max_quantity: Decimal | None,
        limiting_value: Decimal | None,
        unit: str,
        missing_message: str,
    ) -> PositionSizingConstraint:
        if max_quantity is None:
            return PositionSizingService._missing(code, missing_message)
        return PositionSizingConstraint(
            constraint_code=code,
            status=RiskCheckStatus.PASS,
            max_quantity=max_quantity,
            limiting_value=limiting_value,
            unit=unit,
            message="Constraint produced a deterministic upper quantity bound.",
        )
