"""Router-backed optional US market breadth and sector-rotation service."""

from __future__ import annotations

from datetime import datetime

from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.us_market_providers import USMarketBreadthProvider
from application.services.provider_router import ProviderRouter
from domain.common.enums import DataCategory, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.us_market.models import USBreadthSnapshot

OP_MARKET_BREADTH = "us.market_breadth.v1"


class USMarketBreadthService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        codec: ProviderCacheCodec[USBreadthSnapshot],
    ) -> None:
        if router is None or clock is None or codec is None:
            raise DataContractError(
                "router, clock, and codec are required",
                details={"field": "dependencies", "rule": "required"},
            )
        self._router = router
        self._clock = clock
        self._codec = codec

    async def get_current(self, as_of: datetime) -> RouterExecutionResult[USBreadthSnapshot]:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future",
                details={"field": "as_of", "rule": "not_future"},
            )
        # Current aggregate facts are bucketed so separate chats share the same
        # durable provider-cache row instead of making a new Yahoo request.
        cache_as_of = as_of.replace(minute=(as_of.minute // 15) * 15, second=0, microsecond=0)

        async def _call(adapter: CategoryProvider) -> ProviderSuccess[USBreadthSnapshot]:
            if not isinstance(adapter, USMarketBreadthProvider):
                raise DataContractError(
                    "adapter does not implement US market breadth protocol",
                    details={"category": DataCategory.MARKET_BREADTH.value, "rule": "protocol"},
                )
            return await adapter.get_market_breadth(as_of=cache_as_of)

        def _validate(success: ProviderSuccess[USBreadthSnapshot]) -> None:
            if success.meta.category is not DataCategory.MARKET_BREADTH:
                raise DataContractError(
                    "breadth meta.category must be MARKET_BREADTH",
                    details={"field": "meta.category", "rule": "category"},
                )
            if not isinstance(success.value, USBreadthSnapshot):
                raise DataContractError(
                    "breadth value must be USBreadthSnapshot",
                    details={"field": "value", "rule": "type"},
                )
            if success.meta.as_of != cache_as_of:
                raise DataContractError(
                    "breadth meta.as_of must match request",
                    details={"field": "meta.as_of", "rule": "identity"},
                )

        fingerprint = f"v1|{OP_MARKET_BREADTH}|current|{cache_as_of.isoformat()}"
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.MARKET_BREADTH,
            call=_call,
            operation_name=OP_MARKET_BREADTH,
            request_fingerprint=fingerprint,
            instrument=None,
            as_of=cache_as_of,
            tool_policy=None,
            bypass_cache=False,
            cache_codec=self._codec,
            result_validator=_validate,
        )
