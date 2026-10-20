"""Focused contracts for the Dukascopy Jetta adapter and legacy fallback."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AdjustmentMethod, AssetType, CacheDisposition, DataCategory, Market
from domain.common.errors import (
    DataContractError,
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
    result = await DukascopySpotAdapter(transport, clock=_FixedClock()).get_quote(_XAU, AS_OF)

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
    result = await DukascopySpotAdapter(transport, clock=_FixedClock()).get_quote(_LIGHT_OIL, AS_OF)

    assert result.value.instrument_id == "cfd:OTC:LIGHT_CMD_USD"
    assert result.value.unit == "USD/bbl"
    assert result.value.bid == Decimal("78.10")
    assert "ROLLING_CFD_NOT_SPOT" in result.meta.warnings
    assert "OTC_BROKER_FEED" in result.meta.warnings
    assert "VOLUME_BEST_BID_ASK_NOT_EXCHANGE" in result.meta.warnings
    assert "DUKASCOPY_SWFX_NOT_LBMA" not in result.meta.warnings


@pytest.mark.asyncio
async def test_completed_bucket_is_cached_but_active_bucket_is_not() -> None:
    payload = _candle_payload(timestamp=datetime(2026, 6, 23, 14, tzinfo=UTC), base="31.0")
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

    down = DukascopySpotAdapter(_TransportFailure(), clock=_FixedClock(), proxy_configured=True)
    with pytest.raises(ProviderUnavailableError) as exc:
        await down.get_quote(_XAU, AS_OF)
    assert exc.value.details["network_route"] == "proxy"
    assert "jetta.dukascopy.com" not in exc.value.message


class _WafTransport(_FixtureTransport):
    async def send(self, request: HttpRequest) -> HttpResponse:
        if urlsplit(request.url).hostname == "jetta.dukascopy.com":
            self.requests.append(request)
            return HttpResponse(
                status_code=202,
                headers={"Content-Type": "text/html", "X-Amzn-Waf-Action": "challenge"},
                body=b"private upstream challenge",
            )
        return await super().send(request)


@pytest.mark.asyncio
async def test_waf_challenge_is_nonretryable_and_cools_down_then_recovers() -> None:
    class Clock:
        current = AS_OF

        def now(self) -> datetime:
            return self.current

    clock = Clock()
    transport = _WafTransport({})
    adapter = DukascopySpotAdapter(transport, clock=clock)
    with pytest.raises(ProviderUnavailableError) as exc:
        await adapter.get_quote(_XAU, AS_OF)
    assert exc.value.details["error_type"] == "waf_challenge"
    assert exc.value.retryable is False
    assert "private upstream" not in str(exc.value)
    count = len(transport.requests)
    # Cooldown is shared across symbols and quote/history paths.
    with pytest.raises(ProviderUnavailableError):
        await adapter.get_bars(
            _XAG,
            start=AS_OF.date(),
            end=AS_OF.date(),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert len(transport.requests) == count
    assert not adapter._completed_bucket_cache
    clock.current += timedelta(seconds=301)
    # Recover without restarting the adapter once the source is serving JSON.
    working = _FixtureTransport(
        {
            "/v1/candles/minute/XAU-USD/BID": (
                200,
                _candle_payload(timestamp=clock.current - timedelta(minutes=1), base="2348.1"),
            ),
            "/v1/candles/minute/XAU-USD/ASK": (
                200,
                _candle_payload(timestamp=clock.current - timedelta(minutes=1), base="2348.4"),
            ),
        }
    )
    transport.send = working.send  # type: ignore[method-assign]
    result = await adapter.get_quote(_XAU, clock.current)
    assert result.value.mid == Decimal("2348.25")


@pytest.mark.asyncio
async def test_waf_challenge_can_use_configured_legacy_without_repeating_jetta() -> None:
    quote = [
        {
            "instrument": "XAU/USD",
            "bid": "2348.10",
            "ask": "2348.40",
            "timestamp": int((AS_OF - timedelta(minutes=1)).timestamp() * 1000),
        }
    ]
    transport = _WafTransport({"legacy:api/currentPrices": (200, quote)})
    adapter = DukascopySpotAdapter(transport, clock=_FixedClock(), api_key="fixture-key")
    first = await adapter.get_quote(_XAU, AS_OF)
    count = sum(urlsplit(r.url).hostname == "jetta.dukascopy.com" for r in transport.requests)
    second = await adapter.get_quote(_XAU, AS_OF)
    assert first.value.mid == second.value.mid == Decimal("2348.25")
    assert "DUKASCOPY_LEGACY_KEY_API_FALLBACK" in second.meta.warnings
    assert (
        sum(urlsplit(r.url).hostname == "jetta.dukascopy.com" for r in transport.requests) == count
    )


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


_COMPLETION_CASES = (
    (USBarInterval.ONE_MINUTE, "minute", timedelta(minutes=1), "/2026/7/23"),
    (USBarInterval.SIXTY_MINUTES, "hour", timedelta(hours=1), "/2026/7"),
    (USBarInterval.ONE_DAY, "day", timedelta(days=1), "/2026"),
)
_COMPLETION_OPEN = datetime(2026, 7, 23, 1, tzinfo=UTC)
_COMPLETION_FETCHED = datetime(2027, 2, 1, tzinfo=UTC)


def _completion_payload(opening: datetime, duration: timedelta) -> dict[str, object]:
    payload = _candle_payload(timestamp=opening - duration, base="32.10", second="32.25")
    payload["shift"] = int(duration.total_seconds() * 1000)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,source,duration,suffix", _COMPLETION_CASES)
@pytest.mark.parametrize(
    "offset", [-timedelta(microseconds=1), timedelta(0), timedelta(microseconds=1)]
)
@pytest.mark.parametrize("legacy", [False, True])
async def test_bars_respect_native_completion_boundary(
    interval: USBarInterval,
    source: str,
    duration: timedelta,
    suffix: str,
    offset: timedelta,
    legacy: bool,
) -> None:
    opening = (
        _COMPLETION_OPEN.replace(hour=0) if interval is USBarInterval.ONE_DAY else _COMPLETION_OPEN
    )
    cutoff = opening + duration + offset
    route = f"/v1/candles/{source}/XAG-USD/BID{suffix}"
    rows = [
        {
            "timestamp": int(timestamp.timestamp() * 1000),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": "1.25",
        }
        for timestamp, price in ((opening - duration, "32.10"), (opening, "32.25"))
    ]
    transport = _FixtureTransport(
        {
            route: (503, {}) if legacy else (200, _completion_payload(opening, duration)),
            "legacy:api/instrumentList": (200, [{"id": 2, "name": "XAG/USD"}]),
            "legacy:api/historicalPrices": (200, rows),
        }
    )
    adapter = DukascopySpotAdapter(
        transport, clock=FixedClock(_COMPLETION_FETCHED), api_key="fixture-only" if legacy else None
    )
    result = await adapter.get_bars(
        _XAG,
        start=date(2026, 7, 22),
        end=date(2026, 7, 23),
        interval=interval,
        adjustment=AdjustmentMethod.NONE,
        as_of=cutoff,
        offer_side=OfferSide.BID,
    )
    expected = [opening - duration] if offset < timedelta(0) else [opening - duration, opening]
    assert [bar.timestamp for bar in result.value.bars] == expected
    assert all(cutoff - bar.timestamp >= duration for bar in result.value.bars)
    assert [bar.close for bar in result.value.bars] == [Decimal("32.10")] + (
        [] if offset < timedelta(0) else [Decimal("32.25")]
    )
    assert all(bar.open == bar.high == bar.low == bar.close for bar in result.value.bars)
    assert result.meta.as_of == cutoff and result.meta.fetched_at == _COMPLETION_FETCHED
    assert result.meta.adjustment is AdjustmentMethod.NONE
    assert result.value.offer_side is OfferSide.BID
    assert result.value.volume_basis is SpotVolumeBasis.BEST_BID_ASK_VOLUME
    assert ("DUKASCOPY_LEGACY_KEY_API_FALLBACK" in result.meta.warnings) is legacy


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,source,duration,suffix", _COMPLETION_CASES)
async def test_cached_immutable_bucket_rechecks_completion_cutoff(
    interval: USBarInterval,
    source: str,
    duration: timedelta,
    suffix: str,
) -> None:
    opening = (
        _COMPLETION_OPEN.replace(hour=0) if interval is USBarInterval.ONE_DAY else _COMPLETION_OPEN
    )
    route = f"/v1/candles/{source}/XAG-USD/BID{suffix}"
    transport = _FixtureTransport({route: (200, _completion_payload(opening, duration))})
    adapter = DukascopySpotAdapter(transport, clock=FixedClock(_COMPLETION_FETCHED))
    for cached, cutoff in (
        (False, opening + duration / 2),
        (True, opening + duration / 2),
        (True, opening + duration),
    ):
        result = await adapter.get_bars(
            _XAG,
            start=date(2026, 7, 22) if interval is USBarInterval.ONE_DAY else date(2026, 7, 23),
            end=date(2026, 7, 23),
            interval=interval,
            adjustment=AdjustmentMethod.NONE,
            as_of=cutoff,
        )
        assert result.meta.cache_disposition is (
            CacheDisposition.HIT if cached else CacheDisposition.MISS
        )
        expected = (
            [opening - duration] if cutoff < opening + duration else [opening - duration, opening]
        )
        assert [bar.timestamp for bar in result.value.bars] == expected
        assert result.meta.as_of == cutoff
    successful_requests = [
        request for request in transport.requests if urlsplit(request.url).path == route
    ]
    assert len(successful_requests) == len(transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,source,duration,suffix", _COMPLETION_CASES)
@pytest.mark.parametrize("legacy", [False, True])
async def test_only_partial_native_bar_is_unavailable(
    interval: USBarInterval,
    source: str,
    duration: timedelta,
    suffix: str,
    legacy: bool,
) -> None:
    opening = (
        _COMPLETION_OPEN.replace(hour=0) if interval is USBarInterval.ONE_DAY else _COMPLETION_OPEN
    )
    payload = _candle_payload(timestamp=opening, base="32.25")
    payload["shift"] = int(duration.total_seconds() * 1000)
    route = f"/v1/candles/{source}/XAG-USD/BID{suffix}"
    transport = _FixtureTransport(
        {
            route: (200, payload),
            "legacy:api/instrumentList": (200, [{"id": 2, "name": "XAG/USD"}]),
            "legacy:api/historicalPrices": (
                200,
                [
                    {
                        "timestamp": int(opening.timestamp() * 1000),
                        "open": "32.25",
                        "high": "32.25",
                        "low": "32.25",
                        "close": "32.25",
                        "volume": "1.25",
                    }
                ],
            ),
        }
    )
    adapter = DukascopySpotAdapter(
        transport, clock=FixedClock(_COMPLETION_FETCHED), api_key="fixture-only" if legacy else None
    )
    with pytest.raises(NoMarketData, match="no completed bars"):
        await adapter.get_bars(
            _XAG,
            start=date(2026, 7, 23),
            end=date(2026, 7, 23),
            interval=interval,
            adjustment=AdjustmentMethod.NONE,
            as_of=opening + duration / 2,
        )


@pytest.mark.asyncio
async def test_quote_minute_close_proxy_does_not_gain_bar_completion_filter() -> None:
    cutoff = _COMPLETION_OPEN + timedelta(seconds=30)
    routes = {
        "/v1/candles/minute/XAG-USD/BID": (
            200,
            _candle_payload(timestamp=_COMPLETION_OPEN, base="32.10"),
        ),
        "/v1/candles/minute/XAG-USD/ASK": (
            200,
            _candle_payload(timestamp=_COMPLETION_OPEN, base="32.20"),
        ),
    }
    result = await DukascopySpotAdapter(
        _FixtureTransport(routes), clock=FixedClock(cutoff)
    ).get_quote(_XAG, cutoff)
    assert result.value.quote_at == _COMPLETION_OPEN
    assert result.value.bid == Decimal("32.10") and result.value.ask == Decimal("32.20")
    assert result.value.mid == Decimal("32.15") and result.value.last is None
    assert "DUKASCOPY_MINUTE_CLOSE_QUOTE_PROXY" in result.meta.warnings
    assert result.meta.as_of == cutoff


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,source,duration,suffix", _COMPLETION_CASES)
async def test_completed_filter_preserves_requested_day_boundaries(
    interval: USBarInterval,
    source: str,
    duration: timedelta,
    suffix: str,
) -> None:
    payload = _candle_payload(
        timestamp=datetime(2026, 7, 22, tzinfo=UTC), base="32.10", second="32.25"
    )
    payload["shift"] = int(duration.total_seconds() * 1000)
    payload["times"] = [0, int(timedelta(days=1) / duration), int(timedelta(days=1) / duration)]
    for field in ("opens", "highs", "lows", "closes"):
        payload[field] = [0, 15, 15]
    payload["volumes"] = [1.25, 2.5, 3.75]
    route = f"/v1/candles/{source}/XAG-USD/BID{suffix}"
    result = await DukascopySpotAdapter(
        _FixtureTransport({route: (200, payload)}), clock=FixedClock(_COMPLETION_FETCHED)
    ).get_bars(
        _XAG,
        start=date(2026, 7, 23),
        end=date(2026, 7, 23),
        interval=interval,
        adjustment=AdjustmentMethod.NONE,
        as_of=datetime(2026, 7, 26, tzinfo=UTC),
    )
    assert [bar.timestamp for bar in result.value.bars] == [datetime(2026, 7, 23, tzinfo=UTC)]
    bar = result.value.bars[0]
    assert bar.open == bar.high == bar.low == bar.close == Decimal("32.25")
    assert bar.volume == Decimal("2.5")


def _legacy_identity_transport(payload: object) -> _FixtureTransport:
    return _FixtureTransport(
        {
            "/v1/candles/minute/XAU-USD/BID": (503, {}),
            "/v1/candles/minute/XAU-USD/ASK": (503, {}),
            "legacy:api/currentPrices": (200, payload),
        }
    )


def _legacy_identity_row(code: str | None, *, field: str = "instrument") -> dict[str, object]:
    row: dict[str, object] = {
        "bid": "2348.10",
        "ask": "2348.40",
        "timestamp": int((AS_OF - timedelta(seconds=10)).timestamp() * 1000),
    }
    if code is not None:
        row[field] = code
    return row


@pytest.mark.asyncio
async def test_legacy_quote_rejects_sole_wrong_instrument() -> None:
    transport = _legacy_identity_transport([_legacy_identity_row("XAG/USD")])
    adapter = DukascopySpotAdapter(transport, clock=_FixedClock(), api_key="fixture-only")
    with pytest.raises(NoMarketData, match="matching quote"):
        await adapter.get_quote(_XAU, AS_OF)
    assert len(transport.requests) == 3  # One explicit chain read, no added retries.


@pytest.mark.asyncio
async def test_legacy_quote_missing_identity_fails_codec_contract() -> None:
    transport = _legacy_identity_transport([_legacy_identity_row(None)])
    with pytest.raises(DataContractError, match="missing instrument code"):
        await DukascopySpotAdapter(
            transport, clock=_FixedClock(), api_key="fixture-only"
        ).get_quote(_XAU, AS_OF)


@pytest.mark.asyncio
async def test_legacy_quote_rejects_ambiguous_matching_rows() -> None:
    transport = _legacy_identity_transport(
        [
            _legacy_identity_row("XAU/USD"),
            {**_legacy_identity_row("XAU/USD"), "bid": "2448.10", "ask": "2448.40"},
        ]
    )
    with pytest.raises(DataContractError, match="ambiguous matching quotes"):
        await DukascopySpotAdapter(
            transport, clock=_FixedClock(), api_key="fixture-only"
        ).get_quote(_XAU, AS_OF)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["instrument", "name", "symbol", "code"])
async def test_legacy_quote_exact_identity_field_aliases_remain_valid(field: str) -> None:
    transport = _legacy_identity_transport([_legacy_identity_row(" XAU/USD ", field=field)])
    result = await DukascopySpotAdapter(
        transport, clock=_FixedClock(), api_key="fixture-only"
    ).get_quote(_XAU, AS_OF)
    assert result.value.instrument_id == _XAU.instrument_id
    assert result.value.bid == Decimal("2348.10") and result.value.ask == Decimal("2348.40")
    assert result.value.mid == Decimal("2348.25") and result.value.last is None
    assert result.value.quote_at == AS_OF - timedelta(seconds=10)
    assert result.meta.as_of == AS_OF
    assert "DUKASCOPY_LEGACY_KEY_API_FALLBACK" in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["keyed_map", "mixed_rows"])
async def test_legacy_quote_selects_only_exact_requested_identity(shape: str) -> None:
    payload: object = (
        {"XAU/USD": _legacy_identity_row(None)}
        if shape == "keyed_map"
        else [_legacy_identity_row("XAG/USD"), _legacy_identity_row("XAU/USD")]
    )
    result = await DukascopySpotAdapter(
        _legacy_identity_transport(payload), clock=_FixedClock(), api_key="fixture-only"
    ).get_quote(_XAU, AS_OF)
    assert result.value.instrument_id == _XAU.instrument_id
    assert result.value.bid == Decimal("2348.10") and result.value.ask == Decimal("2348.40")
    assert result.value.mid == Decimal("2348.25")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformation", ["ohlc", "column_length", "duplicate_time", "negative_volume"]
)
async def test_invalid_immutable_bucket_recovers_on_next_read_then_valid_cache_hit(
    malformation: str,
) -> None:
    opening = datetime(2026, 7, 23, 1, tzinfo=UTC)
    route = "/v1/candles/hour/XAG-USD/BID/2026/7"
    malformed = _candle_payload(timestamp=opening, base="32.10")
    if malformation == "ohlc":
        malformed["highs"] = [-1]
    elif malformation == "column_length":
        malformed["opens"] = []
    elif malformation == "negative_volume":
        malformed["volumes"] = [-1.25]
    else:
        malformed["times"] = [0, 0]
        for column in ("opens", "highs", "lows", "closes"):
            malformed[column] = [0, 0]
        malformed["volumes"] = [1.25, 1.25]
    transport = _FixtureTransport({route: (200, malformed)})
    adapter = DukascopySpotAdapter(transport, clock=FixedClock(_COMPLETION_FETCHED))
    kwargs = {
        "start": date(2026, 7, 23),
        "end": date(2026, 7, 23),
        "interval": USBarInterval.SIXTY_MINUTES,
        "adjustment": AdjustmentMethod.NONE,
        "as_of": opening + timedelta(hours=2),
    }
    with pytest.raises(DataContractError):
        await adapter.get_bars(_XAG, **kwargs)
    assert len(transport.requests) == 1  # Failure never auto-retries.
    transport.routes[route] = (200, _candle_payload(timestamp=opening, base="32.25"))
    for disposition in (CacheDisposition.MISS, CacheDisposition.HIT):
        result = await adapter.get_bars(_XAG, **kwargs)
        assert result.meta.cache_disposition is disposition
        assert result.meta.as_of == kwargs["as_of"]
        assert result.value.bars[0].timestamp == opening
        assert result.value.bars[0].close == Decimal("32.25")
        assert len(transport.requests) == 2
