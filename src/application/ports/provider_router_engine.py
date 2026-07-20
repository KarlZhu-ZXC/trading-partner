"""Provider router engine port (Phase 1D D6b1).

D6b1 freezes only this Protocol. ``ProviderRouterEngine`` implementation and
resilience orchestration are D6b2 and must not live here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.ports.category_provider import CategoryProvider
from application.ports.provider_cache_codec import ProviderCacheCodec
from domain.common.enums import DataCategory, DataCriticality, Market, VendorId
from domain.instruments.models import Instrument


class ProviderRouterEnginePort(Protocol):
    """Vendor-chain execution engine (chain/criticality already resolved).

    ``ProviderRouter`` owns config/tool-policy resolution; the engine does not
    read YAML. When ``cache_codec is None``, the call must not read or write
    cache and must not invent serialization (no pickle / default=str / reflection).
    """

    async def execute[T](
        self,
        *,
        market: Market,
        category: DataCategory,
        chain: tuple[VendorId, ...],
        criticality: DataCriticality,
        call: Callable[[CategoryProvider], Awaitable[ProviderSuccess[T]]],
        operation_name: str,
        request_fingerprint: str,
        instrument: Instrument | None,
        as_of: datetime,
        bypass_cache: bool,
        cache_codec: ProviderCacheCodec[T] | None,
        result_validator: Callable[[ProviderSuccess[T]], None] | None,
    ) -> RouterExecutionResult[T]:
        """Execute the resolved vendor chain and return a typed router result."""
        ...
