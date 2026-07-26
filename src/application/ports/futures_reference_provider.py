"""Provider port for futures product/contract definitions and roll mappings."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesProductDefinition,
)


@runtime_checkable
class FuturesReferenceProvider(CategoryProvider, Protocol):
    async def get_product_definition(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[FuturesProductDefinition]: ...

    async def list_contract_definitions(
        self,
        product_key: str,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FuturesContractDefinition, ...]]: ...

    async def resolve_continuous_mapping(
        self,
        series: ContinuousSeriesDefinition,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[ContinuousContractMapping, ...]]: ...


FUTURES_REFERENCE_RUNTIME_PROTOCOLS: tuple[type, ...] = (FuturesReferenceProvider,)
