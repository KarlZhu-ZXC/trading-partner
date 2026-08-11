"""Deterministic, read-only Phase 2B portfolio risk evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from application.dto.risk import RiskCheckInput
from application.services.account_service import AccountService
from application.services.position_sizing_service import PositionSizingService
from application.services.risk_policy_service import RiskPolicyService
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.portfolio.enums import AccountOpenOrderSide, AccountOpenOrderStatus
from domain.portfolio.models import AccountSnapshot
from domain.risk.enums import (
    RiskCheckStatus,
    RiskOverallStatus,
    RiskSeverity,
)
from domain.risk.models import (
    PositionSizingResult,
    RiskCheck,
    RiskCheckResult,
    RiskHypotheticalAddition,
    RiskPolicy,
)

_HUNDRED = Decimal("100")


class RiskEngineService:
    def __init__(
        self,
        accounts: AccountService,
        policies: RiskPolicyService,
        sizing: PositionSizingService | None = None,
    ) -> None:
        self._accounts = accounts
        self._policies = policies
        self._sizing = sizing

    async def check(
        self, request: RiskCheckInput, *, effective_as_of: datetime
    ) -> tuple[RiskCheckResult, tuple[AccountSnapshot, ...]]:
        require_aware_datetime(effective_as_of, field_name="effective_as_of")
        if request.refresh_accounts:
            refreshed = await self._accounts.refresh(
                providers=request.providers,
                as_of=effective_as_of,
            )
            snapshots = refreshed.snapshots
        else:
            snapshots = self._accounts.get_snapshots(request.account_snapshot_ids)
        policy = self._policies.get_current()
        sizing: PositionSizingResult | None = None
        duplicate_intent_count: int | None = None
        if request.trade_plan_id is not None:
            if self._sizing is None:
                raise DataContractError("Position Sizing service is not configured")
            plan = self._sizing.get_plan(request.trade_plan_id)
            duplicate_intent_count = self._sizing.count_duplicate_intents(plan)
            sizing = self._sizing.calculate(
                plan, policy, snapshots, request, as_of=effective_as_of
            )
        hypothetical = self._hypothetical(request, sizing)
        result = self._evaluate(
            policy=policy,
            snapshots=snapshots,
            hypothetical=hypothetical,
            sizing=sizing,
            duplicate_intent_count=duplicate_intent_count,
            as_of=effective_as_of,
        )
        return result, snapshots

    @staticmethod
    def _hypothetical(
        request: RiskCheckInput, sizing: PositionSizingResult | None
    ) -> RiskHypotheticalAddition | None:
        if sizing is not None:
            quantity = sizing.recommended_max_additional_quantity
            if quantity is None or quantity <= 0:
                return None
            return RiskHypotheticalAddition(
                instrument_id=sizing.instrument_id,
                quantity=quantity,
                assumed_price=sizing.reference_price,
                currency=sizing.currency,
            )
        if request.hypothetical_instrument_id is None:
            return None
        assert request.hypothetical_quantity is not None
        assert request.hypothetical_assumed_price is not None
        assert request.hypothetical_currency is not None
        return RiskHypotheticalAddition(
            instrument_id=request.hypothetical_instrument_id,
            quantity=request.hypothetical_quantity,
            assumed_price=request.hypothetical_assumed_price,
            currency=request.hypothetical_currency.upper(),
        )

    def _evaluate(
        self,
        *,
        policy: RiskPolicy,
        snapshots: tuple[AccountSnapshot, ...],
        hypothetical: RiskHypotheticalAddition | None,
        sizing: PositionSizingResult | None,
        duplicate_intent_count: int | None,
        as_of: datetime,
    ) -> RiskCheckResult:
        if not snapshots:
            raise DataContractError("risk check requires at least one account snapshot")
        checks: list[RiskCheck] = []
        quality: list[str] = []

        def add_quality(code: str) -> None:
            if code not in quality:
                quality.append(code)

        for account in snapshots:
            if account.degraded:
                add_quality("ACCOUNT_SNAPSHOT_DEGRADED")
            for code in account.warning_codes:
                add_quality(code)
            self._account_checks(account, policy, as_of, checks, add_quality)

        by_currency: defaultdict[str, Decimal] = defaultdict(Decimal)
        by_instrument: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
        accounts_by_instrument: defaultdict[str, set[str]] = defaultdict(set)
        position_currencies: set[str] = set()
        open_buy_orders = 0
        open_sell_orders = 0
        valued_open_buy_orders = 0
        for account in snapshots:
            for position in account.positions:
                accounts_by_instrument[position.instrument_id].add(account.account_ref)
                position_currencies.add(position.currency.upper())
                if position.market_value is None:
                    add_quality("MISSING_MARKET_VALUE")
                    continue
                value = abs(position.market_value)
                currency = position.currency.upper()
                by_currency[currency] += value
                by_instrument[(position.instrument_id, currency)] += value
            for order in account.open_orders:
                remaining = order.quantity - order.filled_quantity
                if remaining <= 0:
                    continue
                if order.status is AccountOpenOrderStatus.UNKNOWN:
                    add_quality("OPEN_ORDER_STATUS_UNKNOWN")
                if order.side is AccountOpenOrderSide.SELL:
                    open_sell_orders += 1
                    continue
                open_buy_orders += 1
                order_currency = _instrument_native_currency(order.instrument_id)
                if order_currency is None or order_currency != account.base_currency.upper():
                    add_quality("OPEN_ORDER_CURRENCY_UNAVAILABLE")
                    continue
                if order.limit_price is None:
                    add_quality("OPEN_ORDER_PRICE_UNAVAILABLE")
                    continue
                value = remaining * order.limit_price
                valued_open_buy_orders += 1
                position_currencies.add(order_currency)
                by_currency[order_currency] += value
                by_instrument[(order.instrument_id, order_currency)] += value

        checks.extend(
            (
                RiskCheck(
                    rule_code="OPEN_BUY_ORDERS_PRESENT",
                    status=(
                        RiskCheckStatus.WARN if open_buy_orders else RiskCheckStatus.PASS
                    ),
                    severity=(
                        RiskSeverity.MEDIUM if open_buy_orders else RiskSeverity.INFO
                    ),
                    actual=open_buy_orders,
                    limit=0,
                    unit="active_orders",
                    scope="portfolio",
                    message=(
                        "Active BUY orders were included in prospective exposure where valuated."
                        if open_buy_orders
                        else "No active BUY orders were reported."
                    ),
                ),
                RiskCheck(
                    rule_code="OPEN_SELL_ORDERS_PRESENT",
                    status=(
                        RiskCheckStatus.WARN if open_sell_orders else RiskCheckStatus.PASS
                    ),
                    severity=(
                        RiskSeverity.MEDIUM if open_sell_orders else RiskSeverity.INFO
                    ),
                    actual=open_sell_orders,
                    limit=0,
                    unit="active_orders",
                    scope="portfolio",
                    message=(
                        "Active SELL orders may reduce the durable position snapshot."
                        if open_sell_orders
                        else "No active SELL orders were reported."
                    ),
                ),
                RiskCheck(
                    rule_code="OPEN_BUY_ORDER_VALUATION_COVERAGE",
                    status=(
                        RiskCheckStatus.NOT_EVALUATED
                        if valued_open_buy_orders != open_buy_orders
                        else RiskCheckStatus.PASS
                    ),
                    severity=(
                        RiskSeverity.HIGH
                        if valued_open_buy_orders != open_buy_orders
                        else RiskSeverity.INFO
                    ),
                    actual=valued_open_buy_orders,
                    limit=open_buy_orders,
                    unit="valued_active_buy_orders",
                    scope="portfolio",
                    message=(
                        "Some active BUY orders lacked a safe native-currency limit valuation."
                        if valued_open_buy_orders != open_buy_orders
                        else "Every active BUY order was safely valued for prospective exposure."
                    ),
                ),
            )
        )

        if hypothetical is not None:
            value = hypothetical.quantity * hypothetical.assumed_price
            by_currency[hypothetical.currency] += value
            by_instrument[(hypothetical.instrument_id, hypothetical.currency)] += value
            position_currencies.add(hypothetical.currency)

        self._concentration_checks(
            policy, by_currency, by_instrument, checks, add_quality
        )
        self._gross_nav_check(
            policy,
            snapshots,
            by_currency,
            position_currencies,
            checks,
            add_quality,
        )
        for instrument_id, account_refs in sorted(accounts_by_instrument.items()):
            if len(account_refs) > 1:
                checks.append(
                    RiskCheck(
                        rule_code="DUPLICATE_INSTRUMENT_ACCOUNTS",
                        status=RiskCheckStatus.WARN,
                        severity=RiskSeverity.MEDIUM,
                        actual=len(account_refs),
                        limit=1,
                        unit="accounts",
                        scope=instrument_id,
                        message="The instrument is held in more than one account.",
                    )
                )

        if sizing is not None:
            risk_constraint = next(
                (
                    item
                    for item in sizing.constraints
                    if item.constraint_code == "PLAN_RISK_BUDGET"
                ),
                None,
            )
            limit = risk_constraint.limiting_value if risk_constraint is not None else None
            checks.append(
                RiskCheck(
                    rule_code="TRADE_PLAN_RISK_BUDGET",
                    status=(
                        RiskCheckStatus.NOT_EVALUATED
                        if sizing.estimated_max_loss is None or limit is None
                        else (
                            RiskCheckStatus.BREACH
                            if sizing.estimated_max_loss > limit
                            else RiskCheckStatus.PASS
                        )
                    ),
                    severity=(
                        RiskSeverity.HIGH
                        if sizing.estimated_max_loss is not None
                        and limit is not None
                        and sizing.estimated_max_loss > limit
                        else RiskSeverity.INFO
                    ),
                    actual=sizing.estimated_max_loss,
                    limit=limit,
                    unit=sizing.currency,
                    scope=sizing.plan_id,
                    message="Trade Plan maximum loss was checked against its risk budget.",
                )
            )
            for code in sizing.data_quality_codes:
                add_quality(code)
            self._phase3d_plan_checks(
                policy=policy,
                snapshots=snapshots,
                sizing=sizing,
                duplicate_intent_count=duplicate_intent_count,
                checks=checks,
            )

        if policy.is_system_default:
            add_quality("RISK_POLICY_DEFAULT_UNCONFIRMED")
        overall = self._overall(checks, quality, policy)
        return RiskCheckResult(
            policy=policy,
            account_snapshot_ids=tuple(item.snapshot_id for item in snapshots),
            as_of=as_of,
            checks=tuple(checks),
            data_quality_codes=tuple(quality),
            hypothetical=hypothetical,
            overall_status=overall,
            position_sizing=sizing,
            execution_effect=False,
        )

    @staticmethod
    def _phase3d_plan_checks(
        *,
        policy: RiskPolicy,
        snapshots: tuple[AccountSnapshot, ...],
        sizing: PositionSizingResult,
        duplicate_intent_count: int | None,
        checks: list[RiskCheck],
    ) -> None:
        constraints = {item.constraint_code: item for item in sizing.constraints}
        for rule_code, constraint_code in (
            ("TRADE_PLAN_REFERENCE_PRICE_AGE", "REFERENCE_PRICE_FRESHNESS"),
            ("LIQUIDITY_PARTICIPATION", "LIQUIDITY_PARTICIPATION"),
        ):
            constraint = constraints.get(constraint_code)
            if constraint is None or constraint.status is RiskCheckStatus.NOT_EVALUATED:
                checks.append(
                    RiskCheck(
                        rule_code=rule_code,
                        status=RiskCheckStatus.NOT_EVALUATED,
                        severity=RiskSeverity.HIGH,
                        actual=None,
                        limit=None,
                        unit="unknown",
                        scope=sizing.plan_id,
                        message=f"{rule_code} lacked a required verified fact.",
                    )
                )
            else:
                checks.append(
                    RiskCheck(
                        rule_code=rule_code,
                        status=RiskCheckStatus.PASS,
                        severity=RiskSeverity.INFO,
                        actual=constraint.limiting_value,
                        limit=(
                            policy.liquidity_participation_max_percent
                            if rule_code == "LIQUIDITY_PARTICIPATION"
                            else policy.max_price_age_seconds
                        ),
                        unit=constraint.unit,
                        scope=sizing.plan_id,
                        message=f"{rule_code} was evaluated from verified inputs.",
                    )
                )

        matching_positions = tuple(
            position
            for snapshot in snapshots
            for position in snapshot.positions
            if position.instrument_id == sizing.instrument_id
        )
        eligible_drawdowns = tuple(
            ((position.average_cost - position.market_price) / position.average_cost)
            * _HUNDRED
            for position in matching_positions
            if position.average_cost is not None
            and position.average_cost > 0
            and position.market_price is not None
        )
        checks.append(
            RiskEngineService._maximum_check(
                "DRAWDOWN_FROM_REPORTED_COST",
                max(eligible_drawdowns, default=Decimal(0))
                if eligible_drawdowns
                else None,
                policy.drawdown_max_percent,
                "percent_from_reported_cost",
                sizing.instrument_id,
                "Loss from reported average cost is within policy.",
                "Loss from reported average cost exceeds policy.",
            )
        )

        duplicate_actual = duplicate_intent_count or 0
        duplicate_status = (
            RiskCheckStatus.WARN if duplicate_actual else RiskCheckStatus.PASS
        )
        checks.append(
            RiskCheck(
                rule_code="DUPLICATE_DURABLE_INTENT",
                status=duplicate_status,
                severity=(
                    RiskSeverity.MEDIUM
                    if duplicate_status is RiskCheckStatus.WARN
                    else RiskSeverity.INFO
                ),
                actual=duplicate_actual,
                limit=0,
                unit="durable_research_intents",
                scope=sizing.instrument_id,
                message=(
                    "An unsuperseded initiate/add Decision intent already exists."
                    if duplicate_status is RiskCheckStatus.WARN
                    else "No duplicate durable initiate/add Decision intent exists."
                ),
            )
        )

        _asset, market, _symbol = parse_instrument_id(sizing.instrument_id)
        if market.value == "A_SHARE":
            lot_ok = (
                sizing.recommended_max_additional_quantity is not None
                and sizing.recommended_max_additional_quantity % Decimal("100") == 0
            )
            checks.append(
                RiskCheck(
                    rule_code="A_SHARE_BOARD_LOT",
                    status=(
                        RiskCheckStatus.PASS
                        if lot_ok
                        else RiskCheckStatus.NOT_EVALUATED
                    ),
                    severity=RiskSeverity.INFO if lot_ok else RiskSeverity.HIGH,
                    actual=sizing.recommended_max_additional_quantity,
                    limit=100,
                    unit="shares_per_lot",
                    scope=sizing.instrument_id,
                    message="A-share planned addition was rounded to board lots.",
                )
            )
            restricted = tuple(
                position
                for position in matching_positions
                if position.sellable_quantity is not None
                and position.sellable_quantity < position.quantity
            )
            checks.append(
                RiskCheck(
                    rule_code="A_SHARE_T1_SELLABILITY",
                    status=(RiskCheckStatus.WARN if restricted else RiskCheckStatus.PASS),
                    severity=(RiskSeverity.MEDIUM if restricted else RiskSeverity.INFO),
                    actual=len(restricted),
                    limit=0,
                    unit="positions",
                    scope=sizing.instrument_id,
                    message=(
                        "Some current shares are not sellable; T+1 or provider constraints apply."
                        if restricted
                        else "No current sellability restriction was reported."
                    ),
                )
            )
            checks.append(
                RiskCheck(
                    rule_code="A_SHARE_LIMIT_SUSPENSION_STATE",
                    status=RiskCheckStatus.NOT_EVALUATED,
                    severity=RiskSeverity.HIGH,
                    actual=None,
                    limit=None,
                    unit="state",
                    scope=sizing.instrument_id,
                    message="Limit-lock and suspension facts were not supplied to this risk run.",
                )
            )

        for code, message in (
            (
                "THEME_CONCENTRATION",
                "Verified theme classification exposure is unavailable to this risk run.",
            ),
            (
                "PAIRWISE_CORRELATION",
                "Aligned return correlation facts are unavailable to this risk run.",
            ),
            (
                "EVENT_BLACKOUT_WINDOW",
                "A verified upcoming event calendar was not supplied to this risk run.",
            ),
        ):
            checks.append(
                RiskCheck(
                    rule_code=code,
                    status=RiskCheckStatus.NOT_EVALUATED,
                    severity=RiskSeverity.HIGH,
                    actual=None,
                    limit=(
                        policy.theme_exposure_max_percent
                        if code == "THEME_CONCENTRATION"
                        else (
                            policy.correlation_max_absolute
                            if code == "PAIRWISE_CORRELATION"
                            else policy.event_blackout_days
                        )
                    ),
                    unit=(
                        "percent"
                        if code == "THEME_CONCENTRATION"
                        else "absolute_correlation"
                        if code == "PAIRWISE_CORRELATION"
                        else "days"
                    ),
                    scope=sizing.instrument_id,
                    message=message,
                )
            )

    def _account_checks(
        self,
        account: AccountSnapshot,
        policy: RiskPolicy,
        as_of: datetime,
        checks: list[RiskCheck],
        add_quality: Callable[[str], None],
    ) -> None:
        if account.account_as_of > as_of:
            add_quality("ACCOUNT_AS_OF_AFTER_CHECK_TIME")
            account_age: int | None = None
        else:
            account_age = int((as_of - account.account_as_of).total_seconds())
        checks.append(
            self._maximum_check(
                "ACCOUNT_AGE",
                account_age,
                policy.max_account_age_seconds,
                "seconds",
                account.account_ref,
                "Account snapshot age is within policy.",
                "Account snapshot exceeds the maximum age.",
            )
        )

        for position in account.positions:
            if position.market_value is None and position.market_price is None:
                continue
            scope = f"{account.account_ref}:{position.instrument_id}"
            if position.market_price_at is None:
                add_quality("PRICE_TIME_UNAVAILABLE")
                age = None
            elif position.market_price_at > as_of:
                add_quality("PRICE_TIME_AFTER_CHECK_TIME")
                age = None
            else:
                age = int((as_of - position.market_price_at).total_seconds())
            checks.append(
                self._maximum_check(
                    "PRICE_AGE",
                    age,
                    policy.max_price_age_seconds,
                    "seconds",
                    scope,
                    "Position price age is within policy.",
                    "Position price exceeds the maximum age.",
                )
            )

        net_assets = account.net_assets
        if net_assets is None or net_assets <= 0 or account.cash is None:
            add_quality("ACCOUNT_NAV_OR_CASH_UNAVAILABLE")
            cash_percent = None
        else:
            cash_percent = (account.cash / net_assets) * _HUNDRED
        checks.append(
            self._minimum_check(
                "MINIMUM_CASH_PERCENT",
                cash_percent,
                policy.minimum_cash_percent,
                "percent",
                account.account_ref,
                "Account cash is at or above the policy minimum.",
                "Account cash is below the policy minimum.",
            )
        )

        if net_assets is None or net_assets <= 0 or account.margin_used is None:
            add_quality("ACCOUNT_NAV_OR_MARGIN_UNAVAILABLE")
            margin_percent = None
        else:
            margin_percent = (account.margin_used / net_assets) * _HUNDRED
        checks.append(
            self._maximum_check(
                "MARGIN_USAGE_PERCENT",
                margin_percent,
                policy.margin_usage_max_percent,
                "percent",
                account.account_ref,
                "Account margin usage is within policy.",
                "Account margin usage exceeds the policy maximum.",
            )
        )

    @staticmethod
    def _concentration_checks(
        policy: RiskPolicy,
        by_currency: dict[str, Decimal],
        by_instrument: dict[tuple[str, str], Decimal],
        checks: list[RiskCheck],
        add_quality: Callable[[str], None],
    ) -> None:
        if not by_instrument:
            add_quality("NO_VALUED_POSITIONS")
            checks.append(
                RiskCheck(
                    rule_code="SINGLE_POSITION_CONCENTRATION",
                    status=RiskCheckStatus.NOT_EVALUATED,
                    severity=RiskSeverity.HIGH,
                    actual=None,
                    limit=policy.single_position_max_percent,
                    unit="percent_within_currency",
                    scope="portfolio",
                    message="No valued positions are available for concentration checks.",
                )
            )
            return
        for (instrument_id, currency), value in sorted(by_instrument.items()):
            total = by_currency[currency]
            percent = (value / total) * _HUNDRED if total > 0 else None
            checks.append(
                RiskEngineService._maximum_check(
                    "SINGLE_POSITION_CONCENTRATION",
                    percent,
                    policy.single_position_max_percent,
                    "percent_within_currency",
                    f"{instrument_id}/{currency}",
                    "Position concentration is within policy.",
                    "Position concentration exceeds the policy maximum.",
                )
            )

    @staticmethod
    def _gross_nav_check(
        policy: RiskPolicy,
        snapshots: tuple[AccountSnapshot, ...],
        by_currency: dict[str, Decimal],
        position_currencies: set[str],
        checks: list[RiskCheck],
        add_quality: Callable[[str], None],
    ) -> None:
        account_currencies = {item.base_currency.upper() for item in snapshots}
        navs = [item.net_assets for item in snapshots]
        evaluable = (
            len(account_currencies) == 1
            and position_currencies <= account_currencies
            and all(value is not None for value in navs)
        )
        actual: Decimal | None = None
        if not evaluable:
            if len(account_currencies | position_currencies) > 1:
                add_quality("FX_CONVERSION_UNAVAILABLE")
            if any(value is None for value in navs):
                add_quality("ACCOUNT_NAV_UNAVAILABLE")
        else:
            total_nav = sum((value for value in navs if value is not None), Decimal(0))
            if total_nav > 0:
                actual = (sum(by_currency.values(), Decimal(0)) / total_nav) * _HUNDRED
            else:
                add_quality("ACCOUNT_NAV_UNAVAILABLE")
        checks.append(
            RiskEngineService._maximum_check(
                "GROSS_EXPOSURE_TO_NAV",
                actual,
                policy.gross_exposure_max_percent,
                "percent",
                "portfolio",
                "Gross exposure is within the policy maximum.",
                "Gross exposure exceeds the policy maximum.",
            )
        )

    @staticmethod
    def _maximum_check(
        rule_code: str,
        actual: Decimal | int | None,
        limit: Decimal | int,
        unit: str,
        scope: str,
        pass_message: str,
        breach_message: str,
    ) -> RiskCheck:
        if actual is None:
            return RiskCheck(
                rule_code=rule_code,
                status=RiskCheckStatus.NOT_EVALUATED,
                severity=RiskSeverity.HIGH,
                actual=None,
                limit=limit,
                unit=unit,
                scope=scope,
                message=f"{rule_code} could not be evaluated from available facts.",
            )
        breached = actual > limit
        return RiskCheck(
            rule_code=rule_code,
            status=(RiskCheckStatus.BREACH if breached else RiskCheckStatus.PASS),
            severity=(RiskSeverity.HIGH if breached else RiskSeverity.INFO),
            actual=actual,
            limit=limit,
            unit=unit,
            scope=scope,
            message=breach_message if breached else pass_message,
        )

    @staticmethod
    def _minimum_check(
        rule_code: str,
        actual: Decimal | None,
        limit: Decimal,
        unit: str,
        scope: str,
        pass_message: str,
        breach_message: str,
    ) -> RiskCheck:
        if actual is None:
            return RiskEngineService._maximum_check(
                rule_code, None, limit, unit, scope, pass_message, breach_message
            )
        breached = actual < limit
        return RiskCheck(
            rule_code=rule_code,
            status=(RiskCheckStatus.BREACH if breached else RiskCheckStatus.PASS),
            severity=(RiskSeverity.HIGH if breached else RiskSeverity.INFO),
            actual=actual,
            limit=limit,
            unit=unit,
            scope=scope,
            message=breach_message if breached else pass_message,
        )

    @staticmethod
    def _overall(
        checks: list[RiskCheck], quality: list[str], policy: RiskPolicy
    ) -> RiskOverallStatus:
        if any(item.status is RiskCheckStatus.BREACH for item in checks):
            return RiskOverallStatus.BREACH
        if any(item.status is RiskCheckStatus.NOT_EVALUATED for item in checks):
            return RiskOverallStatus.INCOMPLETE
        if quality or policy.is_system_default or any(
            item.status is RiskCheckStatus.WARN for item in checks
        ):
            return RiskOverallStatus.WARN
        return RiskOverallStatus.PASS


def _instrument_native_currency(instrument_id: str) -> str | None:
    _asset, market, _symbol = parse_instrument_id(instrument_id)
    return {
        "A_SHARE": "CNY",
        "US": "USD",
        "KR": "KRW",
    }.get(market.value)
