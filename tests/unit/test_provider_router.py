"""Phase 1D D6b2: ProviderRouter facade — chain/criticality only."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.ports.category_provider import CategoryProvider
from application.services.criticality_policy import CriticalityPolicy
from application.services.provider_router import ProviderRouter
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
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
        "warnings": (),
    }
    base.update(overrides)
    return ProviderResultMeta(**base)  # type: ignore[arg-type]


def _ok_result(
    *,
    criticality: DataCriticality = DataCriticality.CORE,
) -> RouterExecutionResult[str]:
    return RouterExecutionResult(
        value="ok",
        ok=True,
        criticality=criticality,
        meta=_meta(),
        attempts=(),
        warnings=(),
        error=None,
    )


def _make_router(
    *,
    engine: Any | None = None,
    chains: Mapping[tuple[Market, DataCategory], tuple[VendorId, ...]] | None = None,
) -> tuple[ProviderRouter, AsyncMock]:
    mock_engine = engine if engine is not None else AsyncMock()
    if engine is None:
        mock_engine.execute = AsyncMock(return_value=_ok_result())
    chain_config = _StaticChainConfig(
        chains
        if chains is not None
        else {
            (Market.US, DataCategory.MARKET_SNAPSHOT): (
                VendorId.MOCK_US,
                VendorId.NULL,
            ),
            (Market.US, DataCategory.NEWS): (VendorId.NULL,),
        }
    )
    router = ProviderRouter(
        engine=mock_engine,
        chain_config=chain_config,
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )
    return router, mock_engine


@pytest.mark.asyncio
async def test_execute_resolves_default_chain_and_criticality() -> None:
    router, engine = _make_router()

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("facade must not invoke call")

    result = await router.execute(
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        call=_call,
        operation_name="get_snapshot",
        request_fingerprint="fp-1",
        as_of=AS_OF,
    )
    assert result.ok is True
    engine.execute.assert_awaited_once()
    kwargs = engine.execute.await_args.kwargs
    assert kwargs["chain"] == (VendorId.MOCK_US, VendorId.NULL)
    assert kwargs["criticality"] is DataCriticality.CORE
    assert kwargs["market"] is Market.US
    assert kwargs["category"] is DataCategory.MARKET_SNAPSHOT
    assert kwargs["bypass_cache"] is False


@pytest.mark.asyncio
async def test_execute_tool_override_chain_wins() -> None:
    router, engine = _make_router()
    policy = ToolDataPolicy(
        tool_name="market_snapshot",
        required_categories=(DataCategory.MARKET_SNAPSHOT,),
        optional_categories=(),
        category_chain_overrides={
            DataCategory.MARKET_SNAPSHOT: (VendorId.ALPHA_VANTAGE,),
        },
    )

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    await router.execute(
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        call=_call,
        operation_name="get_snapshot",
        request_fingerprint="fp",
        as_of=AS_OF,
        tool_policy=policy,
    )
    assert engine.execute.await_args.kwargs["chain"] == (VendorId.ALPHA_VANTAGE,)
    assert engine.execute.await_args.kwargs["criticality"] is DataCriticality.CORE


@pytest.mark.asyncio
async def test_execute_optional_tool_category_is_optional_criticality() -> None:
    router, engine = _make_router()
    policy = ToolDataPolicy(
        tool_name="research",
        required_categories=(DataCategory.MARKET_SNAPSHOT,),
        optional_categories=(DataCategory.NEWS,),
        category_chain_overrides={},
    )

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    await router.execute(
        market=Market.US,
        category=DataCategory.NEWS,
        call=_call,
        operation_name="get_news",
        request_fingerprint="fp",
        as_of=AS_OF,
        tool_policy=policy,
    )
    assert engine.execute.await_args.kwargs["criticality"] is DataCriticality.OPTIONAL
    assert engine.execute.await_args.kwargs["chain"] == (VendorId.NULL,)


@pytest.mark.asyncio
async def test_execute_empty_chain_still_delegates_to_engine() -> None:
    router, engine = _make_router(chains={})

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    await router.execute(
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        call=_call,
        operation_name="get_snapshot",
        request_fingerprint="fp",
        as_of=AS_OF,
    )
    assert engine.execute.await_args.kwargs["chain"] == ()


@pytest.mark.asyncio
async def test_facade_does_not_call_engine_helpers_for_vendor_execution() -> None:
    """Facade must not perform cache/retry/breaker — only engine.execute."""
    engine = MagicMock()
    engine.execute = AsyncMock(return_value=_ok_result())
    router, _ = _make_router(engine=engine)

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    await router.execute(
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        call=_call,
        operation_name="get_snapshot",
        request_fingerprint="fp",
        as_of=AS_OF,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )
    engine.execute.assert_awaited_once()
    # No other orchestration methods on a plain MagicMock should be required.
    assert engine.execute.await_args.kwargs["bypass_cache"] is True


def test_peek_chain_returns_config_chain() -> None:
    router, _ = _make_router()
    assert router.peek_chain(Market.US, DataCategory.MARKET_SNAPSHOT) == (
        VendorId.MOCK_US,
        VendorId.NULL,
    )


def test_peek_chain_rejects_bad_types() -> None:
    router, _ = _make_router()
    with pytest.raises(DataContractError) as exc_info:
        router.peek_chain("US", DataCategory.MARKET_SNAPSHOT)  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "market"
    with pytest.raises(DataContractError) as exc_info:
        router.peek_chain(Market.US, "market_snapshot")  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "category"


@pytest.mark.asyncio
async def test_execute_rejects_naive_as_of() -> None:
    router, engine = _make_router()

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    with pytest.raises(DataContractError):
        await router.execute(
            market=Market.US,
            category=DataCategory.MARKET_SNAPSHOT,
            call=_call,
            operation_name="get_snapshot",
            request_fingerprint="fp",
            as_of=datetime(2026, 7, 16, 15, 0),  # naive
        )
    engine.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_rejects_non_bool_bypass_cache() -> None:
    router, engine = _make_router()

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    with pytest.raises(DataContractError) as exc_info:
        await router.execute(
            market=Market.US,
            category=DataCategory.MARKET_SNAPSHOT,
            call=_call,
            operation_name="get_snapshot",
            request_fingerprint="fp",
            as_of=AS_OF,
            bypass_cache=1,  # type: ignore[arg-type]
        )
    assert exc_info.value.details.get("field") == "bypass_cache"
    engine.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_rejects_bad_instrument_type() -> None:
    router, engine = _make_router()

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    with pytest.raises(DataContractError) as exc_info:
        await router.execute(
            market=Market.US,
            category=DataCategory.MARKET_SNAPSHOT,
            call=_call,
            operation_name="get_snapshot",
            request_fingerprint="fp",
            as_of=AS_OF,
            instrument="equity:US:NVDA",  # type: ignore[arg-type]
        )
    assert exc_info.value.details.get("field") == "instrument"
    engine.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_passes_instrument_and_codec_through() -> None:
    router, engine = _make_router()
    instrument = Instrument(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    codec = object()

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    def _validator(_s: ProviderSuccess[str]) -> None:
        return None

    await router.execute(
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        call=_call,
        operation_name="get_snapshot",
        request_fingerprint="fp",
        instrument=instrument,
        as_of=AS_OF,
        cache_codec=codec,  # type: ignore[arg-type]
        result_validator=_validator,
    )
    kwargs = engine.execute.await_args.kwargs
    assert kwargs["instrument"] is instrument
    assert kwargs["cache_codec"] is codec
    assert kwargs["result_validator"] is _validator


@pytest.mark.asyncio
async def test_facade_does_not_echo_secrets_in_validation_errors() -> None:
    router, _ = _make_router()

    async def _call(_adapter: CategoryProvider) -> ProviderSuccess[str]:
        raise AssertionError("unused")

    with pytest.raises(DataContractError) as exc_info:
        await router.execute(
            market=Market.US,
            category=DataCategory.MARKET_SNAPSHOT,
            call=_call,
            operation_name="get_snapshot",
            request_fingerprint="fp",
            as_of=AS_OF,
            tool_policy=SECRET,  # type: ignore[arg-type]
        )
    blob = f"{exc_info.value!s}{exc_info.value!r}{exc_info.value.details!r}"
    assert SECRET not in blob
    assert "sk-live" not in blob
