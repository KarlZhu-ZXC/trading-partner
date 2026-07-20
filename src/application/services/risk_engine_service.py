"""Deterministic, read-only Phase 2B portfolio risk evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from application.dto.risk import RiskCheckInput
from application.services.account_service import AccountService
from application.services.risk_policy_service import RiskPolicyService
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.portfolio.models import AccountSnapshot
from domain.risk.enums import (
    RiskCheckStatus,
    RiskOverallStatus,
    RiskSeverity,
)
from domain.risk.models import (
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
    ) -> None:
        self._accounts = accounts
        self._policies = policies

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
        hypothetical = self._hypothetical(request)
        result = self._evaluate(
            policy=self._policies.get_current(),
            snapshots=snapshots,
            hypothetical=hypothetical,
            as_of=effective_as_of,
        )
        return result, snapshots

    @staticmethod
    def _hypothetical(request: RiskCheckInput) -> RiskHypotheticalAddition | None:
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
            execution_effect=False,
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
