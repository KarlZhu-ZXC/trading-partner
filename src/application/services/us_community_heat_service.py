"""Router-backed Moomoo community-attention rankings for US market context."""

from __future__ import annotations

from datetime import datetime

from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.us_market_providers import USCommunityHeatProvider
from application.services.provider_router import ProviderRouter
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.us_market.models import USCommunityHeatSnapshot

OP_COMMUNITY_HEAT = "us.community_heat.moomoo.v1"

_POLICY = ToolDataPolicy(
    tool_name="market_get_context",
    required_categories=(),
    optional_categories=(DataCategory.COMMUNITY_HEAT,),
    category_chain_overrides={DataCategory.COMMUNITY_HEAT: (VendorId.MOOMOO,)},
)


class USCommunityHeatService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        codec: ProviderCacheCodec[USCommunityHeatSnapshot],
    ) -> None:
        self._router = router
        self._clock = clock
        self._codec = codec

    async def get_current(
        self, *, limit: int, as_of: datetime
    ) -> RouterExecutionResult[USCommunityHeatSnapshot]:
        require_aware_datetime(as_of, field_name="as_of")
        if as_of > self._clock.now():
            raise DataContractError(
                "as_of must not be in the future",
                details={"field": "as_of", "rule": "not_future"},
            )
        if not 1 <= limit <= 200:
            raise DataContractError(
                "community heat limit must be in [1,200]",
                details={"field": "limit", "rule": "range"},
            )
        cache_as_of = as_of.replace(
            minute=(as_of.minute // 15) * 15,
            second=0,
            microsecond=0,
        )

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USCommunityHeatSnapshot]:
            if not isinstance(adapter, USCommunityHeatProvider):
                raise DataContractError(
                    "adapter does not implement US community heat protocol",
                    details={"category": DataCategory.COMMUNITY_HEAT.value, "rule": "protocol"},
                )
            return await adapter.get_community_heat(limit=limit, as_of=cache_as_of)

        def validate(success: ProviderSuccess[USCommunityHeatSnapshot]) -> None:
            if success.meta.category is not DataCategory.COMMUNITY_HEAT:
                raise DataContractError(
                    "community heat meta category is invalid",
                    details={"field": "meta.category", "rule": "category"},
                )
            if success.meta.as_of != cache_as_of:
                raise DataContractError(
                    "community heat meta as_of must match request",
                    details={"field": "meta.as_of", "rule": "identity"},
                )
            if not isinstance(success.value, USCommunityHeatSnapshot):
                raise DataContractError(
                    "community heat value has invalid type",
                    details={"field": "value", "rule": "type"},
                )
            if len(success.value.items) > limit:
                raise DataContractError(
                    "community heat result exceeds requested limit",
                    details={"field": "value.items", "rule": "limit"},
                )

        fingerprint = f"v1|{OP_COMMUNITY_HEAT}|US|limit={limit}|{cache_as_of.isoformat()}"
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.COMMUNITY_HEAT,
            call=call,
            operation_name=OP_COMMUNITY_HEAT,
            request_fingerprint=fingerprint,
            as_of=cache_as_of,
            tool_policy=_POLICY,
            cache_codec=self._codec,
            result_validator=validate,
        )
