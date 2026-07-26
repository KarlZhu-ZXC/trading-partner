"""Persistence port for durable futures product/contract/continuous definitions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesProductDefinition,
)


class FuturesDefinitionBatch:
    """Immutable write batch for append-only definition persistence."""

    __slots__ = (
        "products",
        "contracts",
        "continuous_series",
        "mappings",
    )

    def __init__(
        self,
        *,
        products: tuple[FuturesProductDefinition, ...] = (),
        contracts: tuple[FuturesContractDefinition, ...] = (),
        continuous_series: tuple[ContinuousSeriesDefinition, ...] = (),
        mappings: tuple[ContinuousContractMapping, ...] = (),
    ) -> None:
        self.products = products
        self.contracts = contracts
        self.continuous_series = continuous_series
        self.mappings = mappings


class FuturesDefinitionRepository(Protocol):
    def get_product(
        self,
        product_key: str,
        as_of: datetime,
    ) -> FuturesProductDefinition | None: ...

    def list_contracts(
        self,
        product_id: str,
        as_of: datetime,
    ) -> tuple[FuturesContractDefinition, ...]: ...

    def get_continuous_series(
        self,
        instrument_id: str,
        as_of: datetime,
    ) -> ContinuousSeriesDefinition | None: ...

    def list_continuous_mappings(
        self,
        continuous_instrument_id: str,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[ContinuousContractMapping, ...]: ...

    def save_definition_batch(self, batch: FuturesDefinitionBatch) -> None: ...

    def save_statistics(
        self,
        statistics: tuple[FuturesContractStatistics, ...],
    ) -> int: ...
