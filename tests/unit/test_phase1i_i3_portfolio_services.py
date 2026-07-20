from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.services.account_service import AccountService
from application.services.portfolio_service import PortfolioService
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot, PortfolioSnapshot

_NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: object) -> str:
        self.value += 1
        return f"snapshot_service-{self.value}"


class _Repository:
    def __init__(self, accounts: tuple[AccountSnapshot, ...] = ()) -> None:
        self.accounts = {item.snapshot_id: item for item in accounts}
        self.portfolios: list[PortfolioSnapshot] = []

    def append_account(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        self.accounts[snapshot.snapshot_id] = snapshot
        return snapshot

    def get_account(self, snapshot_id: str) -> AccountSnapshot | None:
        return self.accounts.get(snapshot_id)

    def latest_accounts(self) -> tuple[AccountSnapshot, ...]:
        return tuple(self.accounts.values())

    def append_portfolio(self, snapshot: PortfolioSnapshot) -> PortfolioSnapshot:
        self.portfolios.append(snapshot)
        return snapshot

    def get_portfolio(self, snapshot_id: str) -> PortfolioSnapshot | None:
        return next(
            (item for item in self.portfolios if item.portfolio_snapshot_id == snapshot_id), None
        )


def _position(instrument_id: str, value: str | None, currency: str = "USD") -> AccountPosition:
    return AccountPosition(
        instrument_id=instrument_id,
        side=AccountPositionSide.LONG,
        quantity=Decimal(1),
        sellable_quantity=Decimal(1),
        average_cost=None,
        diluted_cost=None,
        market_price=None,
        market_price_at=None,
        market_value=None if value is None else Decimal(value),
        unrealized_pnl=None,
        realized_pnl=None,
        currency=currency,
    )


def _account(snapshot_id: str, positions: tuple[AccountPosition, ...]) -> AccountSnapshot:
    return AccountSnapshot(
        snapshot_id=snapshot_id,
        account_ref=snapshot_id,
        provider=VendorId.MANUAL_CSV,
        environment=AccountEnvironment.MANUAL,
        base_currency="USD",
        account_as_of=_NOW,
        fetched_at=_NOW,
        cash=None,
        buying_power=None,
        net_assets=None,
        margin_used=None,
        positions=positions,
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )


class _Provider:
    vendor_id = VendorId.MANUAL_CSV
    provider_name = "manual_csv"

    def __init__(self, snapshot: AccountSnapshot) -> None:
        self.snapshot = snapshot

    def supports(self, market: object, category: object) -> bool:
        return category is DataCategory.ACCOUNT

    def is_configured(self) -> bool:
        return True

    async def get_account_snapshots(
        self, *, as_of: datetime
    ) -> ProviderSuccess[tuple[AccountSnapshot, ...]]:
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.ACCOUNT,
            SourceRole.SUPPLEMENTAL,
            as_of,
            as_of,
            Freshness.UNKNOWN,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            None,
            (),
        )
        return ProviderSuccess((self.snapshot,), meta)


@pytest.mark.asyncio
async def test_account_refresh_persists_configured_provider_and_reports_skipped_primary() -> None:
    snapshot = _account("snapshot_manual", (_position("equity:US:NVDA", "100"),))
    repository = _Repository()
    service = AccountService(
        {VendorId.MANUAL_CSV: _Provider(snapshot)},
        repository,
        _Clock(),
    )

    result = await service.refresh()

    assert result.snapshots == (snapshot,)
    assert result.warning_codes == (
        "SCHWAB_NOT_CONFIGURED",
        "MOOMOO_NOT_CONFIGURED",
    )
    assert repository.get_account(snapshot.snapshot_id) is snapshot


def test_portfolio_analysis_refuses_to_sum_currencies_or_missing_values() -> None:
    account = _account(
        "snapshot_mixed",
        (
            _position("equity:US:NVDA", "100", "USD"),
            _position("equity:A_SHARE:600519.SH", "200", "CNH"),
            _position("equity:US:MSFT", None, "USD"),
        ),
    )
    repository = _Repository((account,))
    accounts = AccountService({}, repository, _Clock())
    service = PortfolioService(accounts, repository, _Clock(), _Ids())

    result = service.analyze()

    assert result.total_value is None
    assert result.missing_instrument_ids == ("equity:US:MSFT",)
    assert result.warning_codes == (
        "GROSS_POSITION_VALUE",
        "MISSING_MARKET_VALUE",
        "FX_CONVERSION_UNAVAILABLE",
    )


def test_portfolio_simulation_is_pure_and_updates_usd_gross_exposure() -> None:
    account = _account("snapshot_usd", (_position("equity:US:NVDA", "100"),))
    repository = _Repository((account,))
    accounts = AccountService({}, repository, _Clock())
    service = PortfolioService(accounts, repository, _Clock(), _Ids())

    result = service.simulate_addition(
        instrument_id="equity:US:MSFT",
        quantity=Decimal(2),
        assumed_price=Decimal(50),
        currency="USD",
    )

    assert result.execution_effect is False
    assert result.before.total_value == Decimal(100)
    assert result.after.total_value == Decimal(200)
    assert repository.portfolios == []
