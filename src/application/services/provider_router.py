"""ProviderRouter application facade (Phase 1D D6b2).

Resolves criticality and vendor chain only. Never executes vendors, cache,
retry, breaker, rate-limit, or health — those belong to
``ProviderRouterEnginePort``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.provider_router_engine import ProviderRouterEnginePort
from application.ports.secret_redactor import SecretRedactor
from application.ports.vendor_chain_config import VendorChainConfig
from application.services.criticality_policy import CriticalityPolicy
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument


class ProviderRouter:
    """Application facade over a resolved vendor chain + criticality."""

    def __init__(
        self,
        engine: ProviderRouterEnginePort,
        chain_config: VendorChainConfig,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        criticality_policy: CriticalityPolicy,
    ) -> None:
        self._engine = engine
        self._chain_config = chain_config
        # Retained for public API freeze / future envelope correlation; D6b2
        # orchestration lives entirely in the engine.
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._criticality_policy = criticality_policy

    def peek_chain(
        self, market: Market, category: DataCategory
    ) -> tuple[VendorId, ...]:
        """Read-only chain resolution for tests/diagnostics (not Codex source pick)."""
        if not isinstance(market, Market):
            raise DataContractError(
                "market must be a Market",
                details={"field": "market", "type": type(market).__name__},
            )
        if not isinstance(category, DataCategory):
            raise DataContractError(
                "category must be a DataCategory",
                details={"field": "category", "type": type(category).__name__},
            )
        return self._chain_config.chain_for(market, category)

    async def execute[T](
        self,
        *,
        market: Market,
        category: DataCategory,
        call: Callable[[CategoryProvider], Awaitable[ProviderSuccess[T]]],
        operation_name: str,
        request_fingerprint: str,
        instrument: Instrument | None = None,
        as_of: datetime,
        tool_policy: ToolDataPolicy | None = None,
        bypass_cache: bool = False,
        cache_codec: ProviderCacheCodec[T] | None = None,
        result_validator: Callable[[ProviderSuccess[T]], None] | None = None,
    ) -> RouterExecutionResult[T]:
        """Resolve chain/criticality then delegate to the engine."""
        if not isinstance(market, Market):
            raise DataContractError(
                "market must be a Market",
                details={"field": "market", "type": type(market).__name__},
            )
        if not isinstance(category, DataCategory):
            raise DataContractError(
                "category must be a DataCategory",
                details={"field": "category", "type": type(category).__name__},
            )
        require_aware_datetime(as_of, field_name="as_of")
        if instrument is not None and not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument or None",
                details={"field": "instrument", "type": type(instrument).__name__},
            )
        if tool_policy is not None and not isinstance(tool_policy, ToolDataPolicy):
            raise DataContractError(
                "tool_policy must be ToolDataPolicy or None",
                details={
                    "field": "tool_policy",
                    "type": type(tool_policy).__name__,
                },
            )
        if not callable(call):
            raise DataContractError(
                "call must be callable",
                details={"field": "call", "type": type(call).__name__},
            )
        if type(bypass_cache) is not bool:
            raise DataContractError(
                "bypass_cache must be a bool",
                details={
                    "field": "bypass_cache",
                    "type": type(bypass_cache).__name__,
                },
            )

        criticality = self._criticality_policy.for_category(category, tool_policy)
        chain = self._resolve_chain(market, category, tool_policy)

        # Touch retained deps so wiring remains intentional without orchestration.
        _ = self._clock
        _ = self._id_generator
        _ = self._secret_redactor

        return await self._engine.execute(
            market=market,
            category=category,
            chain=chain,
            criticality=criticality,
            call=call,
            operation_name=operation_name,
            request_fingerprint=request_fingerprint,
            instrument=instrument,
            as_of=as_of,
            bypass_cache=bypass_cache,
            cache_codec=cache_codec,
            result_validator=result_validator,
        )

    def _resolve_chain(
        self,
        market: Market,
        category: DataCategory,
        tool_policy: ToolDataPolicy | None,
    ) -> tuple[VendorId, ...]:
        if tool_policy is not None and category in tool_policy.category_chain_overrides:
            return tool_policy.category_chain_overrides[category]
        return self._chain_config.chain_for(market, category)
