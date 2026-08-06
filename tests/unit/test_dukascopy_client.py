"""Focused contracts for the Dukascopy Jetta adapter and legacy fallback."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from domain.common.enums import AdjustmentMethod, AssetType, DataCategory, Market
from domain.common.errors import (
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.cross_asset.enums import OfferSide, SpotVenueBasis, SpotVolumeBasis
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.providers.cross_asset.dukascopy_client import DukascopySpotAdapter
from infrastructure.system.clock import SystemClock

AS_OF = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)

_XAU = Instrument(
    instrument_id="commodity_spot:OTC:XAUUSD",
    symbol="XAUUSD",
    name="OTC Gold",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)
_XAG = Instrument(
    instrument_id="commodity_spot:OTC:XAGUSD",
    symbol="XAGUSD",
    name="OTC Silver",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)
_LIGHT_OIL = Instrument(
    instrument_id="cfd:OTC:LIGHT_CMD_USD",
    symbol="LIGHT_CMD_USD",
    name="Dukascopy Light Oil Rolling CFD (not WTI spot, not a NYMEX future)",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.CFD,
)


def _candle_payload(
    *,
    timestamp: datetime,
    base: str,
    second: str | None = None,
) -> dict[str, object]:
    base_decimal = Decimal(base)
    values = [base_decimal] if second is None else [base_decimal, Decimal(second)]
    deltas = [0]
    if len(values) == 2:
        deltas.append(int((values[1] - values[0]) / Decimal("0.01")))
    return {
        "timestamp": int(timestamp.timestamp() * 1000),
        "multiplier": 0.01,
        "shift": 3_600_000,
        "open": float(base_decimal),
        "high": float(base_decimal),
        "low": float(base_decimal),
        "close": float(base_decimal),
        "times": [0] if second is None else [0, 1],
        "opens": deltas,
        "highs": deltas,
        "lows": deltas,
        "closes": deltas,
        "volumes": [1.25] if second is None else [1.25, 2.5],
    }


class _FixtureTransport:
    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes = routes
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        host = urlsplit(request.url).hostname or ""
        if host == "freeserv.dukascopy.com":
            key = f"legacy:{request.params.get('path', '')}"
        else:
            key = urlsplit(request.url).path
        status, payload = self.routes.get(key, (404, {}))
        return HttpResponse(
            status_code=status,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode(),
        )


class _FixedClock(SystemClock):
    def now(self) -> datetime:  # type: ignore[override]
        return AS_OF


class _TransportFailure:
    async def send(self, request: HttpRequest) -> HttpResponse:
        del request
        raise ProviderUnavailableError(
            "HTTP transport failure",
            details={"error_type": "transport_failure", "status_class": "none"},
        )


@pytest.mark.asyncio
async def test_keyless_jetta_quote_preserves_swfx_basis_and_delay() -> None:
    quote_at = AS_OF - timedelta(minutes=1)
    transport = _FixtureTransport(
        {
            "/v1/candles/minute/XAU-USD/BID": (
                200,
                _candle_payload(timestamp=quote_at, base="2348.10"),
            ),
            "/v1/candles/minute/XAU-USD/ASK": (
                200,
                _candle_payload(timestamp=quote_at, base="2348.40"),
            ),
        }
    )
    result = await DukascopySpotAdapter(transport, clock=_FixedClock()).get_quote(
        _XAU, AS_OF
    )

    assert result.value.bid == Decimal("2348.10")
    assert result.value.ask == Decimal("2348.40")
    assert result.value.mid == Decimal("2348.25")
    assert result.value.last is None
    assert result.value.venue_basis is SpotVenueBasis.DUKASCOPY_SWFX
    assert result.meta.data_delay_seconds == 60
    assert "DUKASCOPY_MINUTE_CLOSE_QUOTE_PROXY" in result.meta.warnings
    assert all(request.params.get("from") for request in transport.requests)


@pytest.mark.asyncio
async def test_jetta_hour_bars_decode_delta_columns_and_volume_basis() -> None:
    payload = _candle_payload(
        timestamp=datetime(2026, 7, 23, 14, tzinfo=UTC),
        base="32.10",
        second="32.25",
    )
    transport = _FixtureTransport({"/v1/candles/hour/XAG-USD/BID": (200, payload)})
    result = await DukascopySpotAdapter(transport, clock=_FixedClock()).get_bars(
        _XAG,
        start=date(2026, 7, 23),
        end=date(2026, 7, 23),
        interval=USBarInterval.SIXTY_MINUTES,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
        offer_side=OfferSide.BID,
    )

    assert [bar.close for bar in result.value.bars] == [
        Decimal("32.10"),
        Decimal("32.25"),
    ]
    assert result.value.volume_basis is SpotVolumeBasis.BEST_BID_ASK_VOLUME
    assert transport.requests[0].params["from"] == str(
        int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)
    )


@pytest.mark.asyncio
async def test_jetta_light_oil_quote_uses_rolling_cfd_unit_and_warnings() -> None:
    quote_at = AS_OF - timedelta(minutes=1)
    transport = _FixtureTransport(
        {
            "/v1/candles/minute/LIGHT.CMD-USD/BID": (
                200,
                _candle_payload(timestamp=quote_at, base="78.100"),
            ),
            "/v1/candles/minute/LIGHT.CMD-USD/ASK": (
                200,
                _candle_payload(timestamp=quote_at, base="78.120"),
            ),
        }
    )
    result = await DukascopySpotAdapter(transport, clock=_FixedClock()).get_quote(
        _LIGHT_OIL, AS_OF
    )

    assert result.value.instrument_id == "cfd:OTC:LIGHT_CMD_USD"
    assert result.value.unit == "USD/bbl"
    assert result.value.bid == Decimal("78.10")
    assert "ROLLING_CFD_NOT_SPOT" in result.meta.warnings
    assert "OTC_BROKER_FEED" in result.meta.warnings
    assert "VOLUME_BEST_BID_ASK_NOT_EXCHANGE" in result.meta.warnings
    assert "DUKASCOPY_SWFX_NOT_LBMA" not in result.meta.warnings


@pytest.mark.asyncio
async def test_completed_bucket_is_cached_but_active_bucket_is_not() -> None:
    payload = _candle_payload(
        timestamp=datetime(2026, 6, 23, 14, tzinfo=UTC), base="31.0"
    )
    path = "/v1/candles/hour/XAG-USD/BID/2026/6"
    transport = _FixtureTransport({path: (200, payload)})
    adapter = DukascopySpotAdapter(transport, clock=_FixedClock())
    kwargs = dict(
        start=date(2026, 6, 23),
        end=date(2026, 6, 23),
        interval=USBarInterval.SIXTY_MINUTES,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    first = await adapter.get_bars(_XAG, **kwargs)
    second = await adapter.get_bars(_XAG, **kwargs)

    assert len(transport.requests) == 1
    assert first.meta.cache_disposition.value == "miss"
    assert second.meta.cache_disposition.value == "hit"


@pytest.mark.asyncio
async def test_legacy_key_api_is_optional_fallback_only() -> None:
    legacy_quote = [
        {
            "instrument": "XAU/USD",
            "bid": "2348.10",
            "ask": "2348.40",
            "timestamp": int((AS_OF - timedelta(minutes=1)).timestamp() * 1000),
        }
    ]
    transport = _FixtureTransport(
        {
            "/v1/candles/minute/XAU-USD/BID": (503, {}),
            "/v1/candles/minute/XAU-USD/ASK": (503, {}),
            "legacy:api/currentPrices": (200, legacy_quote),
        }
    )
    result = await DukascopySpotAdapter(
        transport, clock=_FixedClock(), api_key=" legacy-key "
    ).get_quote(_XAU, AS_OF)

    legacy_request = next(
        request
        for request in transport.requests
        if urlsplit(request.url).hostname == "freeserv.dukascopy.com"
    )
    assert legacy_request.params["key"] == "legacy-key"
    assert "legacy-key" not in legacy_request.url
    assert result.meta.role.value == "fallback"
    assert "DUKASCOPY_LEGACY_KEY_API_FALLBACK" in result.meta.warnings


@pytest.mark.asyncio
async def test_rate_limit_and_transport_failure_remain_typed_and_redacted() -> None:
    limited = DukascopySpotAdapter(
        _FixtureTransport(
            {
                "/v1/candles/minute/XAU-USD/BID": (429, {}),
                "/v1/candles/minute/XAU-USD/ASK": (429, {}),
            }
        ),
        clock=_FixedClock(),
    )
    with pytest.raises(ProviderRateLimitError):
        await limited.get_quote(_XAU, AS_OF)

    down = DukascopySpotAdapter(
        _TransportFailure(), clock=_FixedClock(), proxy_configured=True
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await down.get_quote(_XAU, AS_OF)
    assert exc.value.details["network_route"] == "proxy"
    assert "jetta.dukascopy.com" not in exc.value.message


def test_configuration_and_supported_surface() -> None:
    adapter = DukascopySpotAdapter(_FixtureTransport({}))
    assert adapter.is_configured() is True
    assert adapter.supports(Market.OTC, DataCategory.MARKET_QUOTE)
    assert not adapter.supports(Market.US, DataCategory.MARKET_QUOTE)
    assert DukascopySpotAdapter(_FixtureTransport({}), enabled=False).is_configured() is False


@pytest.mark.asyncio
async def test_disabled_adapter_and_empty_history_are_typed() -> None:
    with pytest.raises(ProviderNotConfigured):
        await DukascopySpotAdapter(
            _FixtureTransport({}), enabled=False, clock=_FixedClock()
        ).get_quote(_XAU, AS_OF)

    adapter = DukascopySpotAdapter(
        _FixtureTransport(
            {
                "/v1/candles/hour/XAG-USD/BID": (
                    200,
                    {
                        "times": [],
                        "opens": [],
                        "highs": [],
                        "lows": [],
                        "closes": [],
                        "volumes": [],
                    },
                )
            }
        ),
        clock=_FixedClock(),
    )
    with pytest.raises(NoMarketData):
        await adapter.get_bars(
            _XAG,
            start=date(2026, 7, 23),
            end=date(2026, 7, 23),
            interval=USBarInterval.SIXTY_MINUTES,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
