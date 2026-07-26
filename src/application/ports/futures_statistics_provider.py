"""Provider port for futures settlement / volume / open-interest statistics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.cross_asset.futures_models import FuturesContractStatistics


@runtime_checkable
class FuturesStatisticsProvider(CategoryProvider, Protocol):
    async def get_contract_statistics(
        self,
        instrument_ids: tuple[str, ...],
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FuturesContractStatistics, ...]]: ...


FUTURES_STATISTICS_RUNTIME_PROTOCOLS: tuple[type, ...] = (FuturesStatisticsProvider,)
