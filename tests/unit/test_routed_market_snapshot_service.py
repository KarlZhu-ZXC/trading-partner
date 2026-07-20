"""Phase 1D D8a: RoutedMarketSnapshotService meta/wire mapping."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from application.dto.provider_routing import (
    ProviderAttemptRecord,
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.category_provider import CategoryProvider
from application.services.criticality_policy import CriticalityPolicy
from application.services.provider_router import ProviderRouter
from application.services.routed_market_snapshot_service import (
    MOCK_DATA_WARNING,
    RoutedMarketSnapshotService,
    _request_fingerprint,
)
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    ProviderAttemptOutcome,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    ProviderNotConfigured,
    TradingPartnerError,
)
from domain.instruments.models import Instrument
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)
from infrastructure.providers.a_share.mock_market import (
    MockAShareMarketSnapshotProvider,
)
from infrastructure.providers.common.market_snapshot_category_adapter import (
    MarketSnapshotCategoryAdapter,
)
from infrastructure.providers.common.verified_snapshot_cache_codec import (
    VerifiedMarketSnapshotCacheCodec,
)
from infrastructure.providers.us.mock_market import MockUSMarketSnapshotProvider
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
SECRET = "test-secret-malicious-value"


class _StaticChainConfig:
    def __init__(self, chains: Mapping[tuple[Market, DataCategory], tuple[VendorId, ...]]) -> None:
        self._chains = dict(chains)

    def chain_for(self, market: Market, category: DataCategory) -> tuple[VendorId, ...]:
        return self._chains.get((market, category), ())

    def all_categories(self, market: Market) -> Mapping[DataCategory, tuple[VendorId, ...]]:
        out: dict[DataCategory, tuple[VendorId, ...]] = {}
        for (m, c), chain in self._chains.items():
            if m is market:
                out[c] = chain
        return MappingProxyType(out)


class _CategoryOnlyAdapter:
    """CategoryProvider without get_snapshot — must fail Protocol check."""

    def __init__(self, vendor_id: VendorId = VendorId.MOCK_US) -> None:
        self._vendor_id = vendor_id

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self._vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return True

    def is_configured(self) -> bool:
        return True


class _RecordingCodec:
    """Minimal codec spy; encode records success and type-gates the value."""

    def __init__(self) -> None:
        self.encode_calls: list[ProviderSuccess[VerifiedMarketSnapshot]] = []

    @property
    def codec_id(self) -> str:
        return "test.recording.v1"

    def encode(self, success: ProviderSuccess[VerifiedMarketSnapshot]) -> str:
        if not isinstance(success.value, VerifiedMarketSnapshot):
            raise DataContractError(
                "success.value must be a VerifiedMarketSnapshot",
                details={"field": "value", "rule": "type"},
            )
        self.encode_calls.append(success)
        return "{}"

    def decode(self, entry: Any) -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise NotImplementedError


def _meta(**overrides: object) -> ProviderResultMeta:
    base: dict[str, object] = {
        "vendor": VendorId.MOCK_US,
        "category": DataCategory.MARKET_SNAPSHOT,
        "role": SourceRole.PRIMARY,
        "as_of": AS_OF,
        "fetched_at": AS_OF,
        "freshness": Freshness.FRESH,
        "session": TradingSession.REGULAR,
        "latency_ms": 1,
        "cache_disposition": CacheDisposition.MISS,
        "adjustment": AdjustmentMethod.NONE,
        "data_delay_seconds": 0,
        "warnings": ("MOCK_DATA",),
    }
    base.update(overrides)
    return ProviderResultMeta(**base)  # type: ignore[arg-type]


def _make_router_with_engine(
    engine_execute: Callable[..., Awaitable[Any]],
    *,
    chains: Mapping[tuple[Market, DataCategory], tuple[VendorId, ...]] | None = None,
) -> ProviderRouter:
    mock_engine = AsyncMock()
    mock_engine.execute = engine_execute
    default_chains = {
        (Market.US, DataCategory.MARKET_SNAPSHOT): (VendorId.MOCK_US,),
        (Market.A_SHARE, DataCategory.MARKET_SNAPSHOT): (VendorId.MOCK_A_SHARE,),
    }
    return ProviderRouter(
        engine=mock_engine,
        chain_config=_StaticChainConfig(chains if chains is not None else default_chains),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )


def _service(
    router: ProviderRouter,
    *,
    clock: FixedClock | None = None,
    id_generator: SequentialIdGenerator | None = None,
    cache_codec: Any | None = None,
) -> RoutedMarketSnapshotService:
    return RoutedMarketSnapshotService(
        router=router,
        clock=clock or FixedClock(AS_OF),
        id_generator=id_generator or SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        cache_codec=cache_codec if cache_codec is not None else _RecordingCodec(),
    )


def test_request_fingerprint_is_canonical(us_instrument: Instrument) -> None:
    fp = _request_fingerprint(us_instrument, AS_OF)
    assert fp == f"v1|{us_instrument.instrument_id}|{AS_OF.isoformat()}"


@pytest.mark.asyncio
async def test_success_envelope_mock_data_first_and_wire_fields(
    us_instrument: Instrument,
) -> None:
    adapter = MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_US,
        provider=MockUSMarketSnapshotProvider(),
    )
    codec = _RecordingCodec()
    captured: dict[str, Any] = {}

    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        captured.update(kwargs)
        call = kwargs["call"]
        success = await call(adapter)
        # Simulate engine meta rewrite.
        meta = ProviderResultMeta(
            vendor=success.meta.vendor,
            category=success.meta.category,
            role=SourceRole.PRIMARY,
            as_of=success.meta.as_of,
            fetched_at=success.meta.fetched_at,
            freshness=success.meta.freshness,
            session=success.meta.session,
            latency_ms=success.meta.latency_ms,
            cache_disposition=CacheDisposition.MISS,
            adjustment=success.meta.adjustment,
            data_delay_seconds=success.meta.data_delay_seconds,
            warnings=success.meta.warnings,
        )
        return RouterExecutionResult(
            value=success.value,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=meta,
            attempts=(
                ProviderAttemptRecord(
                    vendor=VendorId.MOCK_US,
                    outcome=ProviderAttemptOutcome.SUCCESS,
                    error_code=None,
                    duration_ms=1,
                    message=None,
                ),
            ),
            warnings=(
                WarningInfo(
                    code="CACHE_SERVED",
                    message="Result served from provider cache",
                    details={},
                ),
                WarningInfo(
                    code="MOCK_DATA",
                    message="duplicate from router must be deduped",
                    details={},
                ),
                WarningInfo(
                    code="CACHE_SERVED",
                    message="duplicate code must be stable-deduped",
                    details={},
                ),
            ),
            error=None,
        )

    service = _service(
        _make_router_with_engine(_engine_execute),
        cache_codec=codec,
    )
    envelope = await service.get_snapshot(us_instrument, AS_OF)

    assert envelope.ok is True
    assert envelope.degraded is True
    assert envelope.warnings[0].code == "MOCK_DATA"
    assert envelope.warnings[0] == MOCK_DATA_WARNING
    codes = [w.code for w in envelope.warnings]
    assert codes == ["MOCK_DATA", "CACHE_SERVED"]
    assert envelope.freshness == Freshness.FRESH or envelope.freshness == "fresh"
    assert envelope.sources[0].name == VendorId.MOCK_US.value
    assert envelope.sources[0].role == SourceRole.PRIMARY or (envelope.sources[0].role == "primary")
    assert envelope.sources[0].retrieved_at == AS_OF
    assert envelope.data is not None
    assert envelope.data.instrument.symbol == "NVDA"
    assert envelope.data.latest_market_row.close == Decimal("173.00")
    assert envelope.as_of == AS_OF
    assert envelope.fetched_at == AS_OF

    # Router call contract
    assert captured["category"] is DataCategory.MARKET_SNAPSHOT
    assert captured["operation_name"] == "market_get_mock_snapshot"
    assert captured["request_fingerprint"] == _request_fingerprint(us_instrument, AS_OF)
    # tool_policy is resolved in the facade (not forwarded to the engine).
    assert captured["criticality"] is DataCriticality.CORE
    assert captured["chain"] == (VendorId.MOCK_US,)
    assert captured["cache_codec"] is codec
    assert captured["result_validator"] is not None
    assert captured["instrument"] is us_instrument
    # Live-path validator is the service method (full domain contract checks).
    assert captured["result_validator"] == service._result_validator


@pytest.mark.asyncio
async def test_call_rejects_non_market_snapshot_category_provider(
    us_instrument: Instrument,
) -> None:
    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        call = kwargs["call"]
        with pytest.raises(DataContractError) as ei:
            await call(_CategoryOnlyAdapter())
        err = ei.value
        assert err.details.get("rule") == "market_snapshot_category_provider"
        return RouterExecutionResult(
            value=None,
            ok=False,
            criticality=DataCriticality.CORE,
            meta=None,
            attempts=(),
            warnings=(),
            error=err,
        )

    service = _service(_make_router_with_engine(_engine_execute))
    envelope = await service.get_snapshot(us_instrument, AS_OF)
    assert envelope.ok is False
    assert envelope.errors[0].code == "DATA_CONTRACT_ERROR"


@pytest.mark.asyncio
async def test_call_does_not_use_getattr_for_get_snapshot(
    us_instrument: Instrument,
) -> None:
    """Protocol path must call get_snapshot directly (no reflection)."""
    adapter = MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_US,
        provider=MockUSMarketSnapshotProvider(),
    )
    # If service used getattr(adapter, "get_snapshot"), a custom __getattribute__
    # would observe it. We assert the Protocol isinstance path succeeds instead.
    assert isinstance(adapter, CategoryProvider)

    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        call = kwargs["call"]
        success = await call(adapter)
        assert isinstance(success, ProviderSuccess)
        assert success.meta.warnings == ("MOCK_DATA",)
        assert success.meta.role is SourceRole.SUPPLEMENTAL  # placeholder
        assert success.meta.cache_disposition is CacheDisposition.HIT  # placeholder
        assert success.meta.vendor is VendorId.MOCK_US
        assert success.meta.category is DataCategory.MARKET_SNAPSHOT
        assert success.meta.data_delay_seconds == 0
        assert success.meta.freshness is Freshness.FRESH
        assert success.meta.session is success.value.session
        assert success.meta.adjustment is success.value.adjustment
        return RouterExecutionResult(
            value=success.value,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=_meta(
                role=SourceRole.PRIMARY,
                cache_disposition=CacheDisposition.MISS,
                session=success.value.session,
                adjustment=success.value.adjustment,
            ),
            attempts=(),
            warnings=(),
            error=None,
        )

    envelope = await _service(_make_router_with_engine(_engine_execute)).get_snapshot(
        us_instrument, AS_OF
    )
    assert envelope.ok is True


@pytest.mark.asyncio
async def test_router_failure_preserves_warnings_and_redacts_secret_details(
    us_instrument: Instrument,
) -> None:
    err = ProviderNotConfigured(
        "vendor is not configured",
        details={"api_key": SECRET, "vendor": VendorId.MOCK_US.value},
    )
    router_warning = WarningInfo(
        code="PARTIAL_VENDOR_CHAIN",
        message="Vendor missing from registry",
        details={"vendor": "mock_us"},
    )

    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        return RouterExecutionResult(
            value=None,
            ok=False,
            criticality=DataCriticality.CORE,
            meta=None,
            attempts=(),
            warnings=(router_warning,),
            error=err,
        )

    envelope = await _service(_make_router_with_engine(_engine_execute)).get_snapshot(
        us_instrument, AS_OF
    )
    assert envelope.ok is False
    assert envelope.degraded is True
    assert len(envelope.warnings) == 1
    assert envelope.warnings[0].code == "PARTIAL_VENDOR_CHAIN"
    # Failure path keeps router warnings; does not force MOCK_DATA first.
    assert all(w.code != "MOCK_DATA" for w in envelope.warnings) or (
        envelope.warnings[0].code == "PARTIAL_VENDOR_CHAIN"
    )
    assert envelope.errors[0].details.get("api_key") == "***REDACTED***"
    assert SECRET not in str(envelope.errors[0].details)


@pytest.mark.asyncio
async def test_router_raise_is_mapped_to_failure_envelope(
    us_instrument: Instrument,
) -> None:
    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        raise RuntimeError("engine boom")

    envelope = await _service(_make_router_with_engine(_engine_execute)).get_snapshot(
        us_instrument, AS_OF
    )
    assert envelope.ok is False
    assert envelope.errors[0].code == "UNEXPECTED_ERROR"


@pytest.mark.asyncio
async def test_typed_router_raise_maps_to_error_info(
    us_instrument: Instrument,
) -> None:
    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        raise DataContractError(
            "bad contract",
            details={"field": "meta", "api_token": SECRET},
        )

    envelope = await _service(_make_router_with_engine(_engine_execute)).get_snapshot(
        us_instrument, AS_OF
    )
    assert envelope.ok is False
    assert envelope.errors[0].code == "DATA_CONTRACT_ERROR"
    assert envelope.errors[0].details.get("api_token") == "***REDACTED***"
    assert SECRET not in str(envelope.errors[0].details)


@pytest.mark.asyncio
async def test_a_share_success_preserves_mock_numeric_fixture(
    a_share_instrument: Instrument,
) -> None:
    adapter = MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_A_SHARE,
        provider=MockAShareMarketSnapshotProvider(),
    )
    as_of = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)

    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        success = await kwargs["call"](adapter)
        # Live result_validator runs full domain contract checks before success
        # (independent of cache disposition / codec encode).
        kwargs["result_validator"](success)
        # Codec still validates on encode after disposition rewrite to MISS.
        miss_meta = ProviderResultMeta(
            vendor=success.meta.vendor,
            category=success.meta.category,
            role=SourceRole.PRIMARY,
            as_of=success.meta.as_of,
            fetched_at=success.meta.fetched_at,
            freshness=success.meta.freshness,
            session=success.meta.session,
            latency_ms=success.meta.latency_ms,
            cache_disposition=CacheDisposition.MISS,
            adjustment=success.meta.adjustment,
            data_delay_seconds=success.meta.data_delay_seconds,
            warnings=success.meta.warnings,
        )
        VerifiedMarketSnapshotCacheCodec().encode(
            ProviderSuccess(value=success.value, meta=miss_meta)
        )
        return RouterExecutionResult(
            value=success.value,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=_meta(
                vendor=VendorId.MOCK_A_SHARE,
                as_of=as_of,
                fetched_at=as_of,
                session=success.value.session,
                adjustment=success.value.adjustment,
            ),
            attempts=(),
            warnings=(),
            error=None,
        )

    service = _service(
        _make_router_with_engine(_engine_execute),
        clock=FixedClock(as_of),
        cache_codec=VerifiedMarketSnapshotCacheCodec(),
    )
    envelope = await service.get_snapshot(a_share_instrument, as_of)
    assert envelope.ok is True
    assert envelope.data is not None
    assert envelope.data.latest_market_row.close == Decimal("1505.00")
    assert envelope.data.algorithm_version == "mock-1.0.0"
    assert envelope.warnings[0].code == "MOCK_DATA"
    assert envelope.sources[0].name == "mock_a_share"


@pytest.mark.asyncio
async def test_result_validator_rejects_non_snapshot(
    us_instrument: Instrument,
) -> None:
    service = _service(
        _make_router_with_engine(AsyncMock()),
        cache_codec=VerifiedMarketSnapshotCacheCodec(),
    )
    bad = ProviderSuccess(
        value="not-a-snapshot",  # type: ignore[arg-type]
        meta=_meta(),
    )
    with pytest.raises(DataContractError) as ei:
        service._result_validator(bad)  # type: ignore[arg-type]
    assert ei.value.details.get("rule") == "type"


class _NoOpCodec:
    """Codec that never validates — proves live path does not rely on encode."""

    @property
    def codec_id(self) -> str:
        return "test.noop.v1"

    def encode(self, success: ProviderSuccess[VerifiedMarketSnapshot]) -> str:
        return "{}"

    def decode(self, entry: Any) -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise NotImplementedError


def _malformed_snapshot(us_instrument: Instrument) -> VerifiedMarketSnapshot:
    """Construct a domain snapshot that fails full contract validation."""
    bar = MarketBar(
        timestamp=AS_OF,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1000"),
    )
    return VerifiedMarketSnapshot(
        instrument=us_instrument,
        requested_as_of=AS_OF,
        latest_market_row=bar,
        indicators=TechnicalIndicators.empty(),
        recent_closes=(),  # empty → non_empty contract rule
        adjustment=AdjustmentMethod.NONE,
        session=TradingSession.REGULAR,
        algorithm_version="mock-1.0.0",
    )


def test_result_validator_rejects_malformed_even_with_noop_codec(
    us_instrument: Instrument,
) -> None:
    """Malformed live snapshot is rejected without cache encode / full codec."""
    service = _service(
        _make_router_with_engine(AsyncMock()),
        cache_codec=_NoOpCodec(),
    )
    success = ProviderSuccess(
        value=_malformed_snapshot(us_instrument),
        meta=_meta(),
    )
    with pytest.raises(DataContractError) as ei:
        service._result_validator(success)
    assert ei.value.details.get("rule") == "non_empty"
    assert ei.value.details.get("field") == "recent_closes"


@pytest.mark.asyncio
async def test_malformed_live_snapshot_fails_envelope_with_cache_disabled(
    us_instrument: Instrument,
) -> None:
    """Engine path with cache_codec=None still rejects via result_validator."""
    malformed = _malformed_snapshot(us_instrument)

    class _BadAdapter:
        @property
        def vendor_id(self) -> VendorId:
            return VendorId.MOCK_US

        @property
        def provider_name(self) -> str:
            return VendorId.MOCK_US.value

        def supports(self, market: Market, category: DataCategory) -> bool:
            return True

        def is_configured(self) -> bool:
            return True

        async def get_snapshot(
            self, instrument: Instrument, as_of: datetime
        ) -> VerifiedMarketSnapshot:
            return malformed

    async def _engine_execute(**kwargs: Any) -> RouterExecutionResult[Any]:
        # Simulate engine: call → result_validator before health/cache success.
        # NoOp codec is present but never encodes; validation is not via codec.
        assert kwargs["cache_codec"] is service._cache_codec
        call = kwargs["call"]
        success = await call(_BadAdapter())
        try:
            kwargs["result_validator"](success)
        except DataContractError as exc:
            return RouterExecutionResult(
                value=None,
                ok=False,
                criticality=DataCriticality.CORE,
                meta=None,
                attempts=(),
                warnings=(),
                error=exc,
            )
        return RouterExecutionResult(
            value=success.value,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=_meta(),
            attempts=(),
            warnings=(),
            error=None,
        )

    service = _service(
        _make_router_with_engine(_engine_execute),
        cache_codec=_NoOpCodec(),
    )
    envelope = await service.get_snapshot(us_instrument, AS_OF)
    assert envelope.ok is False
    assert envelope.errors[0].code == "DATA_CONTRACT_ERROR"
    assert envelope.errors[0].details.get("rule") == "non_empty"


@pytest.mark.asyncio
async def test_naive_as_of_rejected(us_instrument: Instrument) -> None:
    service = _service(_make_router_with_engine(AsyncMock()))
    with pytest.raises(TradingPartnerError):
        await service.get_snapshot(
            us_instrument,
            datetime(2026, 7, 16, 16, 0),  # naive
        )


def test_export_from_services_package() -> None:
    from application.services import RoutedMarketSnapshotService as exported

    assert exported is RoutedMarketSnapshotService
