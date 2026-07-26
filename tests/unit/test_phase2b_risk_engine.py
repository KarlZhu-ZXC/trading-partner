"""Focused Phase 2B risk policy and deterministic evaluation coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.risk import RiskCheckInput, RiskPolicyUpdateInput
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.services.risk_engine_service import RiskEngineService
from application.services.risk_policy_service import RiskPolicyService
from domain.common.enums import Freshness, VendorId
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot
from domain.risk.enums import RiskCheckStatus, RiskConfirmer, RiskOverallStatus
from domain.risk.models import RiskPolicy
from interfaces.mcp.server import PUBLIC_TOOL_NAMES, create_mcp_server

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _policy(*, system_default: bool = True) -> RiskPolicy:
    return RiskPolicy(
        policy_id="risk_policy_00000000-0000-7000-8000-000000000001",
        version=1,
        single_position_max_percent=Decimal("20"),
        gross_exposure_max_percent=Decimal("120"),
        minimum_cash_percent=Decimal("5"),
        margin_usage_max_percent=Decimal("25"),
        max_account_age_seconds=3600,
        max_price_age_seconds=900,
        is_system_default=system_default,
        confirmed_by=(RiskConfirmer.SYSTEM_DEFAULT if system_default else RiskConfirmer.USER),
        created_at=NOW,
        idempotency_key="default" if system_default else "confirmed",
    )


def _position(symbol: str, value: str, *, price_time: datetime | None = NOW) -> AccountPosition:
    return AccountPosition(
        instrument_id=f"equity:US:{symbol}",
        side=AccountPositionSide.LONG,
        quantity=Decimal("1"),
        sellable_quantity=Decimal("1"),
        average_cost=None,
        diluted_cost=None,
        market_price=Decimal(value) if price_time is not None else None,
        market_price_at=price_time,
        market_value=Decimal(value),
        unrealized_pnl=None,
        realized_pnl=None,
        currency="USD",
    )


def _account(*positions: AccountPosition, currency: str = "USD") -> AccountSnapshot:
    return AccountSnapshot(
        snapshot_id="snapshot_00000000-0000-7000-8000-000000000001",
        account_ref="acct_test",
        provider=VendorId.MANUAL_CSV,
        environment=AccountEnvironment.REAL,
        base_currency=currency,
        account_as_of=NOW - timedelta(minutes=5),
        fetched_at=NOW,
        cash=Decimal("100"),
        buying_power=Decimal("100"),
        net_assets=Decimal("1000"),
        margin_used=Decimal("0"),
        positions=tuple(positions),
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )


class _Accounts:
    def __init__(self, snapshots: tuple[AccountSnapshot, ...]) -> None:
        self.snapshots = snapshots

    def get_snapshots(self, _ids: tuple[str, ...]) -> tuple[AccountSnapshot, ...]:
        return self.snapshots

    async def refresh(self, **_kwargs: object) -> None:
        raise AssertionError("durable risk checks must not refresh account providers")


class _Policies:
    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy

    def get_current(self) -> RiskPolicy:
        return self.policy


@pytest.mark.asyncio
async def test_risk_check_reports_breach_and_unconfirmed_default() -> None:
    account = _account(_position("NVDA", "250"), _position("MSFT", "250"))
    service = RiskEngineService(_Accounts((account,)), _Policies(_policy()))  # type: ignore[arg-type]

    result, _ = await service.check(RiskCheckInput(), effective_as_of=NOW)

    concentration = [
        item for item in result.checks if item.rule_code == "SINGLE_POSITION_CONCENTRATION"
    ]
    assert {item.status for item in concentration} == {RiskCheckStatus.BREACH}
    assert result.overall_status is RiskOverallStatus.BREACH
    assert "RISK_POLICY_DEFAULT_UNCONFIRMED" in result.data_quality_codes
    assert result.execution_effect is False


@pytest.mark.asyncio
async def test_missing_price_time_is_incomplete_not_pass() -> None:
    account = _account(
        _position("NVDA", "100", price_time=None),
        _position("MSFT", "100"),
        _position("AAPL", "100"),
        _position("AMZN", "100"),
        _position("GOOG", "100"),
    )
    service = RiskEngineService(
        _Accounts((account,)), _Policies(_policy(system_default=False))  # type: ignore[arg-type]
    )

    result, _ = await service.check(RiskCheckInput(), effective_as_of=NOW)

    price_check = next(item for item in result.checks if item.rule_code == "PRICE_AGE")
    assert price_check.status is RiskCheckStatus.NOT_EVALUATED
    assert result.overall_status is RiskOverallStatus.INCOMPLETE
    assert "PRICE_TIME_UNAVAILABLE" in result.data_quality_codes


def test_policy_update_is_versioned_and_idempotent(fixed_clock, id_generator) -> None:
    class Repository:
        current = _policy()

        def get_current(self) -> RiskPolicy:
            return self.current

        def get_by_idempotency_key(self, key: str) -> RiskPolicy | None:
            return self.current if self.current.idempotency_key == key else None

        def append(self, policy: RiskPolicy) -> RiskPolicy:
            self.current = policy
            return policy

    repository = Repository()
    service = RiskPolicyService(repository, fixed_clock, id_generator)
    request = RiskPolicyUpdateInput(
        single_position_max_percent=Decimal("15"),
        gross_exposure_max_percent=Decimal("100"),
        minimum_cash_percent=Decimal("10"),
        margin_usage_max_percent=Decimal("20"),
        max_account_age_seconds=1800,
        max_price_age_seconds=600,
        expected_version=1,
        confirmed_by="user",
        idempotency_key="confirm-risk-v2",
    )

    first = service.update(request)
    replay = service.update(request)

    assert first.version == 2
    assert first.is_system_default is False
    assert first.confirmed_by is RiskConfirmer.USER
    assert replay == first


@pytest.mark.asyncio
async def test_compact_risk_handlers_validate_and_delegate() -> None:
    envelope = ToolEnvelope.failure(
        request_id="req_risk",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        errors=(
            ErrorInfo(code="STUB", message="stub", retryable=False, details={}),
        ),
    )
    container = MagicMock()
    container.settings.mcp_server_name = "risk-test"
    coordinator = MagicMock()
    coordinator.get_policy.return_value = envelope
    coordinator.update_policy.return_value = envelope
    coordinator.check = AsyncMock(return_value=envelope)
    container.risk_tool_coordinator = coordinator
    manager = create_mcp_server(container)._tool_manager

    assert {tool.name for tool in manager.list_tools()} == set(PUBLIC_TOOL_NAMES)
    await manager.call_tool("portfolio_risk_get", {"request": {"operation": "policy"}})
    await manager.call_tool(
        "risk_policy_update",
        {
            "single_position_max_percent": "15",
            "gross_exposure_max_percent": "100",
            "minimum_cash_percent": "10",
            "margin_usage_max_percent": "20",
            "max_account_age_seconds": 1800,
            "max_price_age_seconds": 600,
            "expected_version": 1,
            "confirmed_by": "user",
            "idempotency_key": "risk-v2",
        },
    )
    await manager.call_tool(
        "portfolio_risk_get",
        {"request": {"operation": "check", "refresh_accounts": False}},
    )

    assert isinstance(coordinator.update_policy.call_args.args[0], RiskPolicyUpdateInput)
    assert isinstance(coordinator.check.await_args.args[0], RiskCheckInput)
