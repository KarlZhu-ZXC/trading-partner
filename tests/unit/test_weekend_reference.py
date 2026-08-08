"""Weekend PAXG/USDC and CL/USDC reference coverage."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.http_transport import HttpRequest, HttpResponse
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import ProviderTimeoutError
from domain.cross_asset.enums import SpotVenueBasis
from domain.cross_asset.spot_models import SpotObservation
from domain.instruments.models import Instrument
from infrastructure.providers.cross_asset.weekend_reference import (
    BinancePaxgUsdcWeekendAdapter,
    HyperliquidClUsdcWeekendAdapter,
    WeekendReferenceFallbackSpotAdapter,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
XAU = Instrument(
    instrument_id="commodity_spot:OTC:XAUUSD",
    symbol="XAUUSD",
    name="OTC Gold",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)
OIL = Instrument(
    instrument_id="cfd:OTC:LIGHT_CMD_USD",
    symbol="LIGHT_CMD_USD",
    name="Dukascopy Light Oil Rolling CFD",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.CFD,
)


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


class _Transport:
    def __init__(self, payloads: list[object]) -> None:
        self.responses = deque(
            HttpResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode(),
            )
            for payload in payloads
        )
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.popleft()


class _TransientTransport(_Transport):
    def __init__(self, failures: int, payload: object) -> None:
        super().__init__([payload])
        self.failures = failures

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.failures:
            self.failures -= 1
            raise ProviderTimeoutError(
                "HTTP request timed out",
                details={"error_type": "timeout", "status_class": "none"},
            )
        return self.responses.popleft()


class _PrimaryOil:
    vendor_id = VendorId.DUKASCOPY
    provider_name = "dukascopy"

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.OTC and category is DataCategory.MARKET_QUOTE

    def is_configured(self) -> bool:
        return True

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[SpotObservation]:
        return ProviderSuccess(
            value=SpotObservation(
                instrument_id=instrument.instrument_id,
                currency="USD",
                unit="USD per barrel-equivalent",
                quote_at=NOW,
                venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
                source="dukascopy",
                mid=Decimal("76.50"),
            ),
            meta=ProviderResultMeta(
                vendor=VendorId.DUKASCOPY,
                category=DataCategory.MARKET_QUOTE,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=NOW,
                freshness=Freshness.STALE,
                session=TradingSession.CLOSED,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=None,
                data_delay_seconds=3600,
                warnings=("ROLLING_CFD_NOT_SPOT",),
            ),
        )


class _FailingPrimaryOil(_PrimaryOil):
    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[SpotObservation]:
        raise ProviderTimeoutError(
            "Dukascopy request timed out",
            details={"error_type": "timeout", "status_class": "none"},
        )


@pytest.mark.asyncio
async def test_binance_paxg_usdc_is_labelled_xau_weekend_proxy_and_cached() -> None:
    clock = _Clock()
    transport = _Transport([{"symbol": "PAXGUSDC", "bidPrice": "4338.31", "askPrice": "4338.55"}])
    adapter = BinancePaxgUsdcWeekendAdapter(transport, clock=clock, enabled=True)

    first = await adapter.get_quote(XAU, NOW)
    clock.value = datetime(2026, 8, 8, 12, 0, 30, tzinfo=UTC)
    cached = await adapter.get_quote(XAU, clock.value)

    assert first.value.mid == Decimal("4338.43")
    assert first.value.currency == "USDC"
    assert first.value.venue_basis is SpotVenueBasis.PAXG_USDC_SPOT_PROXY
    assert first.meta.vendor is VendorId.BINANCE
    assert "WEEKEND_PROXY_NOT_XAUUSD_SPOT" in first.meta.warnings
    assert cached.meta.cache_disposition is CacheDisposition.HIT
    assert cached.meta.data_delay_seconds == 30
    assert len(transport.requests) == 1
    assert transport.requests[0].params == {"symbol": "PAXGUSDC"}


@pytest.mark.asyncio
async def test_hyperliquid_cl_usdc_is_labelled_oil_perpetual_proxy() -> None:
    transport = _Transport([{"xyz:CL": "76.718", "xyz:GOLD": "4345.25"}])
    adapter = HyperliquidClUsdcWeekendAdapter(
        transport,
        clock=_Clock(),
        enabled=True,
    )

    result = await adapter.get_quote(OIL, NOW)

    assert result.value.mid == Decimal("76.718")
    assert result.value.currency == "USDC"
    assert result.value.venue_basis is SpotVenueBasis.CL_USDC_PERPETUAL_PROXY
    assert result.meta.vendor is VendorId.HYPERLIQUID
    assert "WEEKEND_PROXY_NOT_WTI_SPOT" in result.meta.warnings
    assert json.loads(transport.requests[0].body or b"{}") == {
        "type": "allMids",
        "dex": "xyz",
    }


@pytest.mark.asyncio
async def test_hyperliquid_retries_bounded_transient_transport_failures() -> None:
    transport = _TransientTransport(2, {"xyz:CL": "76.718"})
    adapter = HyperliquidClUsdcWeekendAdapter(
        transport,
        clock=_Clock(),
        enabled=True,
        retry_attempts=3,
        retry_backoff_seconds=0,
    )

    result = await adapter.get_quote(OIL, NOW)

    assert result.value.mid == Decimal("76.718")
    assert len(transport.requests) == 3


@pytest.mark.asyncio
async def test_oil_weekend_fallback_preserves_typed_hyperliquid_failure_code() -> None:
    proxy = HyperliquidClUsdcWeekendAdapter(
        _Transport([{"xyz:GOLD": "4345.25"}]),
        clock=_Clock(),
        enabled=True,
    )
    adapter = WeekendReferenceFallbackSpotAdapter(
        _PrimaryOil(),
        gold_proxy=proxy,
        oil_proxy=proxy,
        legacy_gold_fallback=None,
        clock=_Clock(),
    )

    result = await adapter.get_quote(OIL, NOW)

    assert result.value.mid == Decimal("76.50")
    assert "OIL_WEEKEND_REFERENCE_UNAVAILABLE" in result.meta.warnings
    assert "DATA_CONTRACT_ERROR" in result.meta.warnings
    assert len(result.meta.diagnostics) == 1
    diagnostic = result.meta.diagnostics[0]
    assert diagnostic.provider == "hyperliquid"
    assert diagnostic.stage == "weekend_quote"
    assert diagnostic.error_code == "DATA_CONTRACT_ERROR"
    assert diagnostic.attempt_count == 1
    assert not diagnostic.retryable


@pytest.mark.asyncio
async def test_oil_weekend_fallback_records_exhausted_retry_diagnostic() -> None:
    proxy = HyperliquidClUsdcWeekendAdapter(
        _TransientTransport(3, {"xyz:CL": "76.718"}),
        clock=_Clock(),
        enabled=True,
        retry_attempts=3,
        retry_backoff_seconds=0,
    )
    adapter = WeekendReferenceFallbackSpotAdapter(
        _PrimaryOil(),
        gold_proxy=proxy,
        oil_proxy=proxy,
        legacy_gold_fallback=None,
        clock=_Clock(),
    )

    result = await adapter.get_quote(OIL, NOW)

    diagnostic = result.meta.diagnostics[0]
    assert diagnostic.error_code == "PROVIDER_TIMEOUT_ERROR"
    assert diagnostic.error_type == "timeout"
    assert diagnostic.status_class == "none"
    assert diagnostic.attempt_count == 3
    assert diagnostic.retryable


@pytest.mark.asyncio
async def test_weekend_fallback_failure_preserves_complete_provider_chain() -> None:
    proxy = HyperliquidClUsdcWeekendAdapter(
        _TransientTransport(3, {"xyz:CL": "76.718"}),
        clock=_Clock(),
        enabled=True,
        retry_attempts=3,
        retry_backoff_seconds=0,
    )
    adapter = WeekendReferenceFallbackSpotAdapter(
        _FailingPrimaryOil(),
        gold_proxy=proxy,
        oil_proxy=proxy,
        legacy_gold_fallback=None,
        clock=_Clock(),
    )

    with pytest.raises(ProviderTimeoutError) as caught:
        await adapter.get_quote(OIL, NOW)

    attempts = caught.value.details["provider_diagnostics"]
    assert isinstance(attempts, list)
    assert [item["provider"] for item in attempts] == ["hyperliquid", "dukascopy"]
    assert attempts[0]["attempt_count"] == 3
    assert attempts[1]["stage"] == "primary_quote"
