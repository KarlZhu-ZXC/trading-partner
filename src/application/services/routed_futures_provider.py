"""Market-prefix router for futures reference/statistics providers (Phase 3A).

Application-layer composition only: selects CME vs DCE adapters by product key
or instrument identity. Never imports infrastructure.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from application.dto.provider_routing import ProviderSuccess
from application.ports.futures_reference_provider import FuturesReferenceProvider
from application.ports.futures_statistics_provider import FuturesStatisticsProvider
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import DataContractError, NoMarketData
from domain.common.values import parse_instrument_id
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesProductDefinition,
)

_FUTURES_MARKETS = frozenset({Market.CME, Market.DCE})


class RoutedFuturesProvider:
    """Delegates futures reference/statistics by ``MARKET:ROOT`` / instrument market."""

    def __init__(
        self,
        providers: Mapping[Market, FuturesReferenceProvider],
    ) -> None:
        if not providers:
            raise DataContractError(
                "at least one futures provider is required",
                details={"field": "providers"},
            )
        for market, provider in providers.items():
            if market not in _FUTURES_MARKETS:
                raise DataContractError(
                    "futures provider market must be CME or DCE",
                    details={"market": getattr(market, "value", market)},
                )
            if not isinstance(provider, FuturesReferenceProvider):
                raise DataContractError(
                    "provider must implement FuturesReferenceProvider",
                    details={"market": market.value},
                )
            if not isinstance(provider, FuturesStatisticsProvider):
                raise DataContractError(
                    "provider must implement FuturesStatisticsProvider",
                    details={"market": market.value},
                )
        self._providers: dict[Market, FuturesReferenceProvider] = dict(providers)

    @property
    def vendor_id(self) -> VendorId:
        # Composite label; actual provenance lives on ProviderSuccess.meta.
        first = next(iter(self._providers.values()))
        return first.vendor_id

    @property
    def provider_name(self) -> str:
        return "routed_futures"

    def supports(self, market: Market, category: DataCategory) -> bool:
        provider = self._providers.get(market)
        if provider is None:
            return False
        return provider.supports(market, category)

    def is_configured(self) -> bool:
        return any(p.is_configured() for p in self._providers.values())

    def _provider_for_market(self, market: Market) -> FuturesReferenceProvider:
        provider = self._providers.get(market)
        if provider is None:
            raise NoMarketData(
                "no futures provider registered for market",
                details={
                    "market": market.value,
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                },
            )
        return provider

    def _market_from_product_key(self, product_key: str) -> Market:
        if not isinstance(product_key, str) or ":" not in product_key:
            raise DataContractError(
                "product_key must match MARKET:ROOT",
                details={"field": "product_key", "rule": "format"},
            )
        market_raw = product_key.split(":", 1)[0].strip()
        try:
            market = Market(market_raw)
        except ValueError as exc:
            raise DataContractError(
                "product_key market is unknown",
                details={"field": "product_key", "market": market_raw},
            ) from exc
        if market not in _FUTURES_MARKETS:
            raise DataContractError(
                "product_key market must be CME or DCE",
                details={"market": market.value},
            )
        return market

    def _market_from_instruments(self, instrument_ids: tuple[str, ...]) -> Market:
        markets: set[Market] = set()
        for instrument_id in instrument_ids:
            _asset, market, _symbol = parse_instrument_id(instrument_id)
            markets.add(market)
        if len(markets) != 1:
            raise DataContractError(
                "instrument_ids must share a single futures market",
                details={
                    "markets": sorted(m.value for m in markets),
                    "rule": "single_market",
                },
            )
        market = next(iter(markets))
        if market not in _FUTURES_MARKETS:
            raise DataContractError(
                "statistics instruments must use Market.CME or Market.DCE",
                details={"market": market.value},
            )
        return market

    async def get_product_definition(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[FuturesProductDefinition]:
        market = self._market_from_product_key(product_key)
        return await self._provider_for_market(market).get_product_definition(
            product_key, as_of
        )

    async def list_contract_definitions(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FuturesContractDefinition, ...]]:
        market = self._market_from_product_key(product_key)
        return await self._provider_for_market(market).list_contract_definitions(
            product_key, as_of
        )

    async def resolve_continuous_mapping(
        self,
        series: ContinuousSeriesDefinition,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[ContinuousContractMapping, ...]]:
        _asset, market, _symbol = parse_instrument_id(series.instrument_id)
        if market not in _FUTURES_MARKETS:
            raise DataContractError(
                "continuous series market must be CME or DCE",
                details={"market": market.value},
            )
        return await self._provider_for_market(market).resolve_continuous_mapping(
            series, start, end, as_of
        )

    async def get_contract_statistics(
        self,
        instrument_ids: tuple[str, ...],
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FuturesContractStatistics, ...]]:
        market = self._market_from_instruments(instrument_ids)
        provider = self._provider_for_market(market)
        # Runtime-checked at construction; narrow for the type checker.
        assert isinstance(provider, FuturesStatisticsProvider)
        return await provider.get_contract_statistics(
            instrument_ids, trade_date, as_of
        )


__all__ = ["RoutedFuturesProvider"]
