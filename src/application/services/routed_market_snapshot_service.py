"""Routed market-snapshot use-case over ProviderRouter (Phase 1D D8a).

Does not replace :class:`~application.services.market_snapshot_service.MarketSnapshotService`.
Bootstrap / Mock Coordinator wiring is deferred to D8b.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.market import VerifiedMarketSnapshotDTO
from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.tool_envelope import (
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.market_snapshot_category_provider import (
    MarketSnapshotCategoryProvider,
)
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.secret_redactor import SecretRedactor
from application.services.provider_router import ProviderRouter
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    SourceRole,
)
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.models import VerifiedMarketSnapshot
from domain.market.validation import validate_verified_market_snapshot

# Wire-compatible with Phase 1A MarketSnapshotService.
MOCK_DATA_WARNING = WarningInfo(
    code="MOCK_DATA",
    message="Response contains deterministic mock data.",
    details={},
)

_OPERATION_NAME = "market_get_mock_snapshot"


def _request_fingerprint(instrument: Instrument, as_of: datetime) -> str:
    """Canonical fingerprint body; Router hashes it for the cache key."""
    return f"v1|{instrument.instrument_id}|{as_of.isoformat()}"


def _merge_success_warnings(
    router_warnings: tuple[WarningInfo, ...],
) -> tuple[WarningInfo, ...]:
    """MOCK_DATA first; then router warnings stable-deduped by code."""
    merged: list[WarningInfo] = [MOCK_DATA_WARNING]
    seen: set[str] = {MOCK_DATA_WARNING.code}
    for warning in router_warnings:
        code = warning.code
        if code in seen:
            continue
        seen.add(code)
        merged.append(warning)
    return tuple(merged)


class RoutedMarketSnapshotService:
    """Market snapshot use-case that always routes via ProviderRouter."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        cache_codec: ProviderCacheCodec[VerifiedMarketSnapshot],
    ) -> None:
        self._router = router
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._cache_codec = cache_codec

    def _result_validator(
        self, success: ProviderSuccess[VerifiedMarketSnapshot]
    ) -> None:
        """Explicit non-None Router result_validator for MARKET_SNAPSHOT.

        Type-gates ProviderSuccess, then runs the full domain contract
        validator on every success value before Engine health/cache success.
        Independent of cache enablement and disposition — codec encode is not
        a substitute for this path. Application imports domain only.
        """
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if not isinstance(success.value, VerifiedMarketSnapshot):
            raise DataContractError(
                "success.value must be a VerifiedMarketSnapshot",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        validate_verified_market_snapshot(success.value)

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ToolEnvelope[VerifiedMarketSnapshotDTO]:
        require_aware_datetime(as_of, field_name="as_of")
        request_id = self._id_generator.new(EntityIdPrefix.REQ)

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[VerifiedMarketSnapshot]:
            # Runtime-checkable Protocol only — no getattr / reflection.
            if not isinstance(adapter, MarketSnapshotCategoryProvider):
                raise DataContractError(
                    "adapter must implement MarketSnapshotCategoryProvider",
                    details={
                        "field": "adapter",
                        "rule": "market_snapshot_category_provider",
                        "type": type(adapter).__name__,
                    },
                )
            snapshot = await adapter.get_snapshot(instrument, as_of)
            fetched_at = self._clock.now()
            # role / cache_disposition are placeholders; Engine rewrites them.
            meta = ProviderResultMeta(
                vendor=adapter.vendor_id,
                category=DataCategory.MARKET_SNAPSHOT,
                role=SourceRole.SUPPLEMENTAL,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.FRESH,
                session=snapshot.session,
                latency_ms=None,
                cache_disposition=CacheDisposition.HIT,
                adjustment=snapshot.adjustment,
                data_delay_seconds=0,
                warnings=("MOCK_DATA",),
            )
            return ProviderSuccess(value=snapshot, meta=meta)

        try:
            result = await self._router.execute(
                market=instrument.market,
                category=DataCategory.MARKET_SNAPSHOT,
                call=_call,
                operation_name=_OPERATION_NAME,
                request_fingerprint=_request_fingerprint(instrument, as_of),
                instrument=instrument,
                as_of=as_of,
                tool_policy=None,
                bypass_cache=False,
                cache_codec=self._cache_codec,
                result_validator=self._result_validator,
            )
        except TradingPartnerError as exc:
            fetched_at = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=fetched_at,
                errors=[to_error_info(exc, self._secret_redactor)],
            )
        except Exception as exc:  # noqa: BLE001 — convert to failure envelope
            fetched_at = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=fetched_at,
                errors=[
                    to_error_info_from_exception(exc, self._secret_redactor)
                ],
            )

        if not result.ok:
            fetched_at = self._clock.now()
            # result.error is non-null TradingPartnerError when ok=False.
            assert result.error is not None
            return ToolEnvelope.failure(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                errors=[to_error_info(result.error, self._secret_redactor)],
                warnings=result.warnings,
            )

        # Success: meta/value non-null by RouterExecutionResult invariant.
        assert result.meta is not None
        assert result.value is not None
        meta = result.meta
        source = SourceReference(
            name=meta.vendor.value,
            role=meta.role,
            url=None,
            retrieved_at=meta.fetched_at,
            data_delay_seconds=meta.data_delay_seconds,
        )
        data = VerifiedMarketSnapshotDTO.from_domain(result.value)
        return ToolEnvelope.success(
            request_id=request_id,
            market=instrument.market,
            as_of=as_of,
            fetched_at=meta.fetched_at,
            freshness=meta.freshness,
            sources=(source,),
            data=data,
            degraded=True,
            warnings=_merge_success_warnings(result.warnings),
        )
