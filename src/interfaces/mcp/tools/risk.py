"""Compact portfolio-risk operation adapters."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from application.dto.risk import RiskCheckInput, RiskPolicyUpdateInput
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_risk_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact risk operation adapters."""

    # ----------------------------------------------- Phase 2B Portfolio Risk Engine
    def risk_policy_get() -> dict[str, Any]:
        """Return the current versioned portfolio risk policy."""
        try:
            return container.services.risk.get_policy().model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def risk_policy_update(
        single_position_max_percent: Decimal,
        gross_exposure_max_percent: Decimal,
        minimum_cash_percent: Decimal,
        margin_usage_max_percent: Decimal,
        max_account_age_seconds: int,
        max_price_age_seconds: int,
        expected_version: int,
        confirmed_by: str,
        idempotency_key: str,
        risk_budget_max_percent: Decimal = Decimal("2"),
        theme_exposure_max_percent: Decimal = Decimal("40"),
        drawdown_max_percent: Decimal = Decimal("20"),
        liquidity_participation_max_percent: Decimal = Decimal("10"),
        correlation_max_absolute: Decimal = Decimal("0.85"),
        event_blackout_days: int = 3,
    ) -> dict[str, Any]:
        """Append a confirmed risk-policy version; this never executes an order."""
        try:
            inp = RiskPolicyUpdateInput.model_validate(
                {
                    "single_position_max_percent": single_position_max_percent,
                    "gross_exposure_max_percent": gross_exposure_max_percent,
                    "minimum_cash_percent": minimum_cash_percent,
                    "margin_usage_max_percent": margin_usage_max_percent,
                    "max_account_age_seconds": max_account_age_seconds,
                    "max_price_age_seconds": max_price_age_seconds,
                    "expected_version": expected_version,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                    "risk_budget_max_percent": risk_budget_max_percent,
                    "theme_exposure_max_percent": theme_exposure_max_percent,
                    "drawdown_max_percent": drawdown_max_percent,
                    "liquidity_participation_max_percent": (liquidity_participation_max_percent),
                    "correlation_max_absolute": correlation_max_absolute,
                    "event_blackout_days": event_blackout_days,
                }
            )
            return container.services.risk.update_policy(inp).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def risk_check(
        account_snapshot_ids: tuple[str, ...] = (),
        refresh_accounts: bool = False,
        providers: tuple[str, ...] = (),
        hypothetical_instrument_id: str | None = None,
        hypothetical_quantity: Decimal | None = None,
        hypothetical_assumed_price: Decimal | None = None,
        hypothetical_currency: str | None = None,
        trade_plan_id: str | None = None,
        average_daily_value: Decimal | None = None,
        max_liquidity_participation_percent: Decimal | None = None,
        atr: Decimal | None = None,
        target_volatility_percent: Decimal | None = None,
        annualized_volatility_percent: Decimal | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Check durable risk; refresh only on explicit user request.

        A confirmed Trade Plan may also be sized without execution.
        """
        try:
            inp = RiskCheckInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "refresh_accounts": refresh_accounts,
                    "providers": providers,
                    "hypothetical_instrument_id": hypothetical_instrument_id,
                    "hypothetical_quantity": hypothetical_quantity,
                    "hypothetical_assumed_price": hypothetical_assumed_price,
                    "hypothetical_currency": hypothetical_currency,
                    "trade_plan_id": trade_plan_id,
                    "average_daily_value": average_daily_value,
                    "max_liquidity_participation_percent": (max_liquidity_participation_percent),
                    "atr": atr,
                    "target_volatility_percent": target_volatility_percent,
                    "annualized_volatility_percent": annualized_volatility_percent,
                    "as_of": as_of,
                }
            )
            return (await container.services.risk.check(inp)).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        risk_policy_get=risk_policy_get,
        risk_policy_update=risk_policy_update,
        risk_check=risk_check,
    )
