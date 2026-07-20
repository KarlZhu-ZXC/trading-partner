"""Deterministic portfolio exposure analysis and non-executing simulation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.services.account_service import AccountService
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.portfolio.models import (
    AccountSnapshot,
    PortfolioExposure,
    PortfolioSimulation,
    PortfolioSnapshot,
)

_GROSS_WARNING = "GROSS_POSITION_VALUE"
_MISSING_WARNING = "MISSING_MARKET_VALUE"
_FX_WARNING = "FX_CONVERSION_UNAVAILABLE"


class PortfolioService:
    def __init__(
        self,
        accounts: AccountService,
        repository: AccountSnapshotRepository,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._accounts = accounts
        self._repository = repository
        self._clock = clock
        self._ids = id_generator

    def analyze(
        self,
        *,
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> PortfolioSnapshot:
        accounts = self._accounts.get_snapshots(account_snapshot_ids)
        snapshot = self._build(accounts, base_currency=base_currency, additions=())
        return self._repository.append_portfolio(snapshot)

    def simulate_addition(
        self,
        *,
        account_snapshot_ids: tuple[str, ...] = (),
        instrument_id: str,
        quantity: Decimal,
        assumed_price: Decimal,
        currency: str,
        base_currency: str = "USD",
    ) -> PortfolioSimulation:
        parse_instrument_id(instrument_id)
        if quantity <= 0 or assumed_price <= 0:
            raise DataContractError("quantity and assumed_price must be positive")
        accounts = self._accounts.get_snapshots(account_snapshot_ids)
        before = self._build(accounts, base_currency=base_currency, additions=())
        after = self._build(
            accounts,
            base_currency=base_currency,
            additions=((instrument_id, currency.upper(), quantity * assumed_price),),
        )
        return PortfolioSimulation(
            before=before,
            after=after,
            added_instrument_id=instrument_id,
            added_quantity=quantity,
            assumed_price=assumed_price,
            currency=currency.upper(),
            execution_effect=False,
        )

    def _build(
        self,
        accounts: tuple[AccountSnapshot, ...],
        *,
        base_currency: str,
        additions: Iterable[tuple[str, str, Decimal]],
    ) -> PortfolioSnapshot:
        base = base_currency.strip().upper()
        if not base:
            raise DataContractError("base_currency must be nonblank")
        by_currency: defaultdict[str, Decimal] = defaultdict(Decimal)
        by_market: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
        by_instrument: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
        missing: set[str] = set()

        for account in accounts:
            for position in account.positions:
                if position.market_value is None:
                    missing.add(position.instrument_id)
                    continue
                value = abs(position.market_value)
                currency = position.currency.upper()
                _asset, market, _symbol = parse_instrument_id(position.instrument_id)
                by_currency[currency] += value
                by_market[(market.value, currency)] += value
                by_instrument[(position.instrument_id, currency)] += value
        for instrument_id, currency, value in additions:
            _asset, market, _symbol = parse_instrument_id(instrument_id)
            by_currency[currency] += value
            by_market[(market.value, currency)] += value
            by_instrument[(instrument_id, currency)] += value

        warnings = [_GROSS_WARNING]
        if missing:
            warnings.append(_MISSING_WARNING)
        currencies = {currency for currency, value in by_currency.items() if value != 0}
        if currencies - {base}:
            warnings.append(_FX_WARNING)
        complete = not missing and currencies <= {base}
        total = sum(by_currency.values(), Decimal(0)) if complete else None
        exposures: list[PortfolioExposure] = []
        exposures.extend(
            self._exposure("currency", currency, value, total)
            for currency, value in sorted(by_currency.items())
        )
        exposures.extend(
            self._exposure("market", f"{market}/{currency}", value, total)
            for (market, currency), value in sorted(by_market.items())
        )
        exposures.extend(
            self._exposure("instrument", f"{instrument_id}/{currency}", value, total)
            for (instrument_id, currency), value in sorted(by_instrument.items())
        )
        now = self._clock.now()
        return PortfolioSnapshot(
            portfolio_snapshot_id=self._ids.new(EntityIdPrefix.SNAPSHOT),
            account_snapshot_ids=tuple(item.snapshot_id for item in accounts),
            as_of=max((item.account_as_of for item in accounts), default=now),
            base_currency=base,
            total_value=total,
            exposures=tuple(exposures),
            missing_instrument_ids=tuple(sorted(missing)),
            degraded=len(warnings) > 1,
            warning_codes=tuple(warnings),
        )

    @staticmethod
    def _exposure(
        dimension: str, key: str, value: Decimal, total: Decimal | None
    ) -> PortfolioExposure:
        weight = value / total if total is not None and total > 0 else None
        return PortfolioExposure(dimension=dimension, key=key, value=value, weight=weight)
