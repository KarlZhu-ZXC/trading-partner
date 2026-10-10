"""Read-only synthetic actual adapter -> router -> service -> technical audit."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from test_metal_futures_fallbacks import _gate
from test_provider_router import _make_router
from test_provider_router_engine import _engine, _settings
from test_technical_cme_otc import _CME, _coordinator
from test_yahoo_finance import (
    NY,
    SEOUL,
    RecordingTransport,
    _chart_payload,
    _future_instrument,
    _kr_instrument,
)

from application.services.us_market_data_service import USMarketDataService
from conftest import FixedClock
from domain.common.enums import DataCategory, Market, VendorId
from infrastructure.providers.us.codecs import us_bars_codec, us_quote_codec
from infrastructure.providers.us.eastmoney_futures import EastmoneyMetalFuturesAdapter
from infrastructure.providers.us.yahoo_finance import YahooFinanceAdapter

DAY = date(2026, 7, 24)
NOW = datetime(2026, 7, 27, 12, tzinfo=NY)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["SI=F", "GC=F"])
async def test_native_date_labels_disclose_derived_confirmation_time_for_snapshot_and_chart(symbol):
    from dataclasses import replace

    from application.dto.technical import TechnicalAnalysisInput, TechnicalChartInput
    from infrastructure.technical.matplotlib_chart_renderer import _SMC_LIMITATION_MESSAGES
    from infrastructure.technical.ta_lib_indicator_engine import TALibIndicatorEngine

    instrument = replace(
        _future_instrument(), symbol=symbol, instrument_id=f"future:US:{symbol}"
    )
    days = []
    day = DAY
    while len(days) < 60:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    lows = [99.0] * 60
    lows[-6] = 90.0
    payload = _chart_payload(
        days=days, opens=[100.0] * 60, highs=[101.0] * 60, lows=lows,
        closes=[100.0] * 60, volumes=[10] * 60, symbol=symbol,
    )
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(NOW)
    )
    coordinator, _, _ = _chain(adapter, instrument)
    coordinator._indicator_engine = TALibIndicatorEngine()
    coordinator._chart_renderer.render.return_value = b"synthetic-png"
    after = datetime(2026, 7, 25, 0, tzinfo=NY)
    earlier = datetime(2026, 7, 24, 12, tzinfo=NY)
    limitation = "SMC_FUTURES_DATE_LABEL_CONFIRMATION_TIME_UNKNOWN"
    snapshot = await coordinator.get_snapshot(TechnicalAnalysisInput(
        instrument_id=instrument.instrument_id, as_of=after,
        intervals=("1d", "1w"), include_bars=True, lookback_sessions=60,
    ))
    assert snapshot.ok and snapshot.data is not None
    assert snapshot.data.bars is not None
    assert snapshot.data.bars[-1].timestamp.hour == (0 if symbol == "SI=F" else 17)
    for frame in snapshot.data.timeframes:
        assert frame.smart_money is not None
        assert (limitation in frame.smart_money.limitations) is (symbol == "SI=F")
    daily = snapshot.data.timeframes[0]
    assert daily.smart_money is not None
    assert len(daily.smart_money.swings) == 1
    assert daily.smart_money.swings[0].confirmed_at == snapshot.data.bars[-1].timestamp
    historical = await coordinator.get_snapshot(TechnicalAnalysisInput(
        instrument_id=instrument.instrument_id, as_of=earlier,
        intervals=("1d",), include_bars=True, lookback_sessions=60,
    ))
    assert historical.ok and historical.data is not None
    assert historical.data.timeframes[0].smart_money.swings == ()
    for interval in ("1d", "1w"):
        artifact = await coordinator.render_chart(TechnicalChartInput(
            instrument_id=instrument.instrument_id, as_of=after,
            interval=interval, lookback_sessions=60,
        ))
        assert artifact.envelope.ok and artifact.png == b"synthetic-png"
        rendered = coordinator._chart_renderer.render.call_args.kwargs["analysis"]
        assert (limitation in rendered.smart_money.limitations) is (symbol == "SI=F")
    assert "not verified availability times" in _SMC_LIMITATION_MESSAGES[limitation]


def _payload(symbol):
    value = _chart_payload(
        days=[DAY - timedelta(days=1), DAY],
        opens=[100.0, 100.0],
        highs=[110.0, 999.0],
        lows=[90.0, 90.0],
        closes=[100.0, 105.0],
        volumes=[10, 20],
        symbol=symbol,
    )
    meta = value["chart"]["result"][0]["meta"]
    meta["currentTradingPeriod"] = {
        "pre": {},
        "post": {},
        "regular": {
            "timezone": "EDT",
            "gmtoffset": -14400,
            "start": int(datetime(2026, 7, 23, 18, tzinfo=NY).timestamp()),
            "end": int(datetime(2026, 7, 24, 17, tzinfo=NY).timestamp()),
        },
    }
    return value


def _chain(adapter, instrument):
    clock = FixedClock(NOW)
    engine, cache, _, _, registry = _engine(
        clock=clock,
        settings=_settings(_env_file=None, provider_retry_max_attempts=1),
    )
    registry.register(adapter.vendor_id, adapter)
    router, _ = _make_router(
        engine=engine,
        chains={
            (instrument.market, DataCategory.MARKET_OHLCV): (adapter.vendor_id,),
            (instrument.market, DataCategory.MARKET_QUOTE): (adapter.vendor_id,),
        },
    )
    service = USMarketDataService(router, clock, us_quote_codec(), us_bars_codec())
    coordinator = _coordinator(instruments={instrument.instrument_id: instrument}, us=service)
    return coordinator, service, cache


async def _load(adapter, instrument, as_of):
    coordinator, _, cache = _chain(adapter, instrument)
    result, bars, basis = await coordinator._load_daily_bars(
        instrument,
        as_of=as_of,
        lookback=80,
    )
    assert result.ok, result.error
    return result, bars, basis, cache


@pytest.mark.asyncio
@pytest.mark.parametrize("instrument,symbol", [(_future_instrument(), "GC=F"), (_CME, "GCZ26.CMX")])
async def test_yahoo_futures_pre_completion_excludes_current_daily_high(instrument, symbol):
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(_payload(symbol)).encode()),
        clock=FixedClock(NOW),
    )
    result, bars, _, cache = await _load(
        adapter, instrument, datetime(2026, 7, 24, 16, 30, tzinfo=NY)
    )
    observed = [(bar.timestamp.isoformat(), str(bar.high)) for bar in bars]
    assert result.meta.vendor is VendorId.YFINANCE
    assert cache.set_calls == 1
    assert [bar.high for bar in bars] == [Decimal("110")], observed


@pytest.mark.asyncio
@pytest.mark.parametrize("hour,minute,count", [(16, 30, 1), (17, 0, 2), (17, 1, 2)])
async def test_eastmoney_futures_completion_cutoff(hour, minute, count):
    from test_metal_futures_fallbacks import RecordingTransport as MetalTransport

    payload = {
        "data": {
            "code": "GC00Y",
            "name": "COMEX黄金",
            "klines": [
                "2026-07-23,100,100,110,90,10,1000",
                "2026-07-24,100,105,999,90,20,2000",
            ],
        }
    }
    adapter = EastmoneyMetalFuturesAdapter(
        MetalTransport(json.dumps(payload).encode()),
        _gate(),
        clock=FixedClock(NOW),
    )
    result, bars, _, _ = await _load(
        adapter, _future_instrument(), datetime(2026, 7, 24, hour, minute, tzinfo=NY)
    )
    assert len(bars) == count
    assert "EASTMONEY_DAILY_DERIVED_BARS" in result.meta.warnings
    assert bars[-1].timestamp.hour == 17


@pytest.mark.asyncio
@pytest.mark.parametrize("hour,minute,count", [(15, 0, 1), (15, 30, 2), (15, 31, 2)])
async def test_existing_kr_daily_cutoff(hour, minute, count):
    payload = _payload("005930.KS")
    result = payload["chart"]["result"][0]
    result["timestamp"] = [
        int(datetime(2026, 7, day, 0, tzinfo=SEOUL).timestamp()) for day in (23, 24)
    ]
    result["meta"]["exchangeTimezoneName"] = "Asia/Seoul"
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(NOW)
    )
    _, bars, _, _ = await _load(
        adapter, _kr_instrument(), datetime(2026, 7, 24, hour, minute, tzinfo=SEOUL)
    )
    assert len(bars) == count
    assert bars[-1].timestamp.astimezone(SEOUL).hour == 15
    assert bars[-1].timestamp.astimezone(SEOUL).minute == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["tencent", "eastmoney"])
@pytest.mark.parametrize("hour,minute,count", [(14, 59, 1), (15, 0, 2), (15, 1, 2)])
async def test_a_share_calendar_completion_through_technical(vendor, hour, minute, count):
    from zoneinfo import ZoneInfo

    from test_e2_contracts import _calendar, _instrument
    from test_e2_contracts import _gate as share_gate

    from a_share_fixture_transport import FixtureHttpTransport, ScriptedHttpTransport
    from application.ports.http_transport import HttpResponse
    from application.services.a_share_market_structure_service import (
        AShareMarketStructureService,
    )
    from infrastructure.providers.a_share import codecs
    from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
    from infrastructure.providers.a_share.tencent import TencentAShareAdapter

    sh = ZoneInfo("Asia/Shanghai")
    clock = FixedClock(datetime(2024, 1, 17, 12, tzinfo=sh))
    as_of = datetime(2024, 1, 16, hour, minute, tzinfo=sh)
    calendar = _calendar()
    instrument = _instrument()
    if vendor == "tencent":
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "qfqday": [
                        ["2024-01-15", "100", "100", "110", "90", "10"],
                        ["2024-01-16", "100", "105", "999", "90", "20"],
                    ]
                }
            },
        }
        transport = ScriptedHttpTransport(
            [
                HttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps(payload).encode(),
                )
            ]
        )
        adapter = TencentAShareAdapter(transport, calendar=calendar, clock=clock)
    else:
        payload = {
            "rc": 0,
            "rt": 0,
            "data": {
                "code": "600519",
                "klines": [
                    "2024-01-15,100,100,110,90,10,1000",
                    "2024-01-16,100,105,999,90,20,2000",
                ],
            },
        }
        transport = FixtureHttpTransport(
            vendor="eastmoney",
            operation="bars",
            case="success",
            body_override=json.dumps(payload).encode(),
        )
        adapter = EastmoneyAShareAdapter(transport, share_gate(), calendar=calendar, clock=clock)
    engine, _, _, _, registry = _engine(
        clock=clock, settings=_settings(_env_file=None, provider_retry_max_attempts=1)
    )
    registry.register(adapter.vendor_id, adapter)
    router, _ = _make_router(
        engine=engine,
        chains={(Market.A_SHARE, DataCategory.MARKET_OHLCV): (adapter.vendor_id,)},
    )
    service = AShareMarketStructureService(
        router=router,
        clock=clock,
        calendar=calendar,
        quote_codec=codecs.quote_codec(),
        bars_codec=codecs.bars_codec(),
        order_book_codec=codecs.order_book_codec(),
        ticks_codec=codecs.ticks_codec(),
        industry_codec=codecs.industry_performance_codec(),
        market_board_codec=codecs.market_board_codec(),
    )
    coordinator = _coordinator(instruments={instrument.instrument_id: instrument})
    coordinator._a_share_data_service = service
    result, bars, basis = await coordinator._load_daily_bars(instrument, as_of=as_of, lookback=80)
    assert result.ok, result.error
    assert len(bars) == count
    assert bars[-1].timestamp.astimezone(sh).hour == 15
    assert basis == "forward_adjusted_daily_close"
    if count == 1:
        assert bars[0].high == Decimal("110")


@pytest.mark.asyncio
async def test_historical_future_quote_daily_fallback_excludes_unfinished_range():
    payload = _payload("GC=F")
    payload["chart"]["result"][0]["meta"]["regularMarketTime"] = int(NOW.timestamp())
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(NOW)
    )
    result = await adapter.get_quote(_future_instrument(), datetime(2026, 7, 24, 16, 30, tzinfo=NY))
    assert result.value.high == Decimal("110"), (
        result.value.quote_at,
        result.value.high,
    )
    assert result.value.last == Decimal("100")
    assert "FUTURES_DAILY_DERIVED_QUOTE_TIME" in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["GC", "MGC", "SI", "HG", "PL", "PA"])
@pytest.mark.parametrize("hour,minute,count", [(16, 59, 1), (17, 0, 2), (17, 1, 2)])
async def test_proven_specific_metals_standard_completion(root, hour, minute, count):
    from dataclasses import replace

    instrument = replace(
        _CME,
        symbol=f"{root}Z26",
        instrument_id=f"future:CME:{root}Z26",
        exchange="NYMEX" if root in {"PL", "PA"} else "COMEX",
    )
    symbol = f"{root}Z26.NYM" if root in {"PL", "PA"} else f"{root}Z26.CMX"
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(_payload(symbol)).encode()),
        clock=FixedClock(NOW),
    )
    result, bars, _, _ = await _load(
        adapter, instrument, datetime(2026, 7, 24, hour, minute, tzinfo=NY)
    )
    assert len(bars) == count
    assert bars[-1].timestamp.hour == 17
    assert "FUTURES_DAILY_CONSERVATIVE_COMPLETION_BOUND" in result.meta.warnings


@pytest.mark.asyncio
async def test_halfday_uses_disclosed_conservative_bound_without_equity_calendar():
    from test_yahoo_finance import _FailingSessionCalendar

    from domain.common.enums import AdjustmentMethod
    from domain.us_market.enums import USBarInterval

    days = [date(2026, 11, 25), date(2026, 11, 27)]
    payload = _chart_payload(
        days=days,
        opens=[100.0, 100.0],
        highs=[110.0, 999.0],
        lows=[90.0, 90.0],
        closes=[100.0, 105.0],
        volumes=[10, 20],
        symbol="GC=F",
    )
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()),
        clock=FixedClock(datetime(2026, 11, 30, 12, tzinfo=NY)),
        us_session_calendar=_FailingSessionCalendar(),
    )
    before = await adapter.get_bars(
        _future_instrument(),
        start=days[0],
        end=days[-1],
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=datetime(2026, 11, 27, 16, 30, tzinfo=NY),
    )
    exact = await adapter.get_bars(
        _future_instrument(),
        start=days[0],
        end=days[-1],
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=datetime(2026, 11, 27, 17, tzinfo=NY),
    )
    assert len(before.value.bars) == 1
    assert len(exact.value.bars) == 2
    assert "FUTURES_DAILY_CONSERVATIVE_COMPLETION_BOUND" in exact.meta.warnings
    assert "US_NON_SESSION_DAILY_BAR" not in exact.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["MGC", "SI", "HG", "PL", "PA"])
async def test_unresolved_continuous_coverage_keeps_native_label_and_completed_prior_dates(
    root,
):
    from dataclasses import replace

    instrument = replace(
        _future_instrument(),
        symbol=f"{root}=F",
        instrument_id=f"future:US:{root}=F",
        exchange="NYMEX" if root in {"PL", "PA"} else "COMEX",
    )
    transport = RecordingTransport(body=json.dumps(_payload(instrument.symbol)).encode())
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(NOW))
    before, bars, _, _ = await _load(adapter, instrument, datetime(2026, 7, 24, 23, 59, tzinfo=NY))
    after, completed, _, _ = await _load(adapter, instrument, datetime(2026, 7, 25, 0, tzinfo=NY))
    assert [bar.high for bar in bars] == [Decimal("110")]
    assert len(completed) == 2
    assert bars[-1].timestamp == datetime(2026, 7, 23, 0, tzinfo=NY)
    assert completed[-1].timestamp == datetime(2026, 7, 24, 0, tzinfo=NY)
    assert "FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN" in before.meta.warnings
    assert "FUTURES_DAILY_DATE_ONLY_COMPLETION_UNKNOWN" in after.meta.warnings
    assert "FUTURES_DAILY_CONSERVATIVE_COMPLETION_BOUND" not in after.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,exchange", [("ES=F", "CME"), ("CL=F", "NYMEX")])
async def test_unproven_other_future_daily_bars_fail_before_fetch(symbol, exchange):
    from dataclasses import replace

    from domain.common.enums import AdjustmentMethod
    from domain.common.errors import NoMarketData
    from domain.us_market.enums import USBarInterval

    instrument = replace(
        _future_instrument(),
        symbol=symbol,
        instrument_id=f"future:US:{symbol}",
        exchange=exchange,
    )
    transport = RecordingTransport(body=b"{}")
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(NOW))
    with pytest.raises(NoMarketData, match="completion"):
        await adapter.get_bars(
            instrument,
            start=DAY,
            end=DAY,
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=datetime(2026, 7, 24, 16, 30, tzinfo=NY),
        )
    assert transport.requests == []


@pytest.mark.asyncio
async def test_unresolved_proxy_keeps_actual_quote_time_and_unknown_previous_close():
    from dataclasses import replace

    instrument = replace(_future_instrument(), symbol="SI=F", instrument_id="future:US:SI=F")
    payload = _payload("SI=F")
    meta = payload["chart"]["result"][0]["meta"]
    meta["regularMarketTime"] = int(datetime(2026, 7, 24, 16, 20, tzinfo=NY).timestamp())
    meta["regularMarketPrice"] = 105.0
    meta["regularMarketDayHigh"] = 109.0
    meta["regularMarketDayLow"] = 95.0
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(NOW)
    )
    result = await adapter.get_quote(instrument, datetime(2026, 7, 24, 16, 30, tzinfo=NY))
    assert result.value.last == Decimal("105")
    assert result.value.quote_at == datetime(2026, 7, 24, 16, 20, tzinfo=NY)
    assert result.value.high == Decimal("109")
    assert result.value.previous_close is None
    assert "FUTURES_DAILY_QUOTE_TIME_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_unresolved_proxy_does_not_synthesize_dated_quote_time():
    from dataclasses import replace

    from domain.common.errors import NoMarketData

    instrument = replace(_future_instrument(), symbol="SI=F", instrument_id="future:US:SI=F")
    payload = _payload("SI=F")
    payload["chart"]["result"][0]["meta"]["regularMarketTime"] = int(NOW.timestamp())
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(NOW)
    )
    with pytest.raises(NoMarketData):
        await adapter.get_quote(instrument, datetime(2026, 7, 24, 16, 30, tzinfo=NY))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["bars", "quote"])
async def test_legacy_futures_cache_policy_cannot_hit_new_completion_semantics(
    operation,
):
    from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
    from application.dto.provider_state import CacheEntry
    from application.services.us_market_data_service import (
        OP_BARS,
        OP_QUOTE,
        build_us_fingerprint,
    )
    from domain.common.enums import (
        AdjustmentMethod,
        CacheDisposition,
        Freshness,
        SourceRole,
        TradingSession,
    )
    from domain.market.models import MarketBar
    from domain.providers.cache_key import build_cache_key
    from domain.us_market.enums import USBarInterval
    from domain.us_market.models import USBarSeries, USQuote
    from infrastructure.providers.router_engine import ProviderRouterEngine

    instrument = _future_instrument()
    as_of = datetime(2026, 7, 24, 16, 30, tzinfo=NY)
    payload = _payload("GC=F")
    payload["chart"]["result"][0]["meta"]["regularMarketTime"] = int(NOW.timestamp())
    transport = RecordingTransport(body=json.dumps(payload).encode())
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(NOW))
    coordinator, service, cache = _chain(adapter, instrument)
    category = DataCategory.MARKET_OHLCV if operation == "bars" else DataCategory.MARKET_QUOTE
    legacy_meta = ProviderResultMeta(
        vendor=VendorId.YFINANCE,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=NOW,
        freshness=Freshness.DELAYED,
        session=TradingSession.REGULAR,
        latency_ms=1,
        cache_disposition=CacheDisposition.MISS,
        adjustment=AdjustmentMethod.NONE if operation == "bars" else None,
        data_delay_seconds=1800,
        warnings=("FUTURES_CONTRACT_NOT_SPOT", "CONTINUOUS_FUTURES_ROLL_RISK"),
    )
    if operation == "bars":
        start = DAY - timedelta(days=160)
        params = {
            "start": start.isoformat(),
            "end": DAY.isoformat(),
            "interval": "1d",
            "adjustment": AdjustmentMethod.NONE.value,
        }
        legacy_value = USBarSeries(
            instrument_id=instrument.instrument_id,
            start=start,
            end=DAY,
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            bars=(
                MarketBar(
                    timestamp=datetime(2026, 7, 24, 16, tzinfo=NY),
                    open=Decimal("100"),
                    high=Decimal("999"),
                    low=Decimal("90"),
                    close=Decimal("105"),
                    volume=Decimal("20"),
                ),
            ),
        )
        codec = us_bars_codec()
        fingerprint = build_us_fingerprint(OP_BARS, instrument.instrument_id, params, as_of)
    else:
        legacy_value = USQuote(
            instrument_id=instrument.instrument_id,
            quote_at=datetime(2026, 7, 24, 16, tzinfo=NY),
            session=TradingSession.REGULAR,
            last=Decimal("105"),
            open=Decimal("100"),
            high=Decimal("999"),
            low=Decimal("90"),
            previous_close=Decimal("100"),
            volume=Decimal("20"),
            average_volume=None,
            market_cap=None,
            beta=None,
            week_52_low=None,
            week_52_high=None,
        )
        codec = us_quote_codec()
        fingerprint = build_us_fingerprint(OP_QUOTE, instrument.instrument_id, {}, as_of)
    op = OP_BARS if operation == "bars" else OP_QUOTE
    key = build_cache_key(
        instrument.market,
        category,
        instrument.instrument_id,
        as_of,
        ProviderRouterEngine._build_call_fingerprint(op, fingerprint),
    )
    cache.entries[key] = CacheEntry(
        key=key,
        category=category,
        market=instrument.market,
        instrument_id=instrument.instrument_id,
        vendor=VendorId.YFINANCE,
        payload_json=codec.encode(ProviderSuccess(value=legacy_value, meta=legacy_meta)),
        as_of=as_of,
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        freshness=legacy_meta.freshness,
    )

    async def call():
        if operation == "bars":
            result, _, _ = await coordinator._load_daily_bars(instrument, as_of=as_of, lookback=80)
            return result
        return await service.get_quote(instrument, as_of)

    fresh = await call()
    assert fresh.ok, fresh.error
    assert fresh.meta.cache_disposition is CacheDisposition.MISS
    hit = await call()
    assert hit.ok, hit.error
    assert hit.meta.cache_disposition is CacheDisposition.HIT
    assert hit.value == fresh.value
    assert len(transport.requests) == 1
    assert key in cache.entries  # Policy invalidation does not mutate old data.
    if operation == "bars":
        assert [bar.high for bar in fresh.value.bars] == [Decimal("110")]
    else:
        assert fresh.value.high == Decimal("110")
        assert fresh.value.quote_at == datetime(2026, 7, 23, 17, tzinfo=NY)


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["US", "KR"])
async def test_equity_and_kr_fingerprint_keys_remain_unchanged(market):
    from test_yahoo_finance import _instrument

    from application.services.us_market_data_service import (
        OP_BARS,
        OP_QUOTE,
        build_us_fingerprint,
    )
    from domain.common.enums import AdjustmentMethod
    from domain.providers.cache_key import build_cache_key
    from infrastructure.providers.router_engine import ProviderRouterEngine

    instrument = _instrument() if market == "US" else _kr_instrument()
    zone = NY if market == "US" else SEOUL
    as_of = datetime(2026, 7, 24, 16, 30, tzinfo=zone)
    payload = _payload("NVDA" if market == "US" else "005930.KS")
    payload["chart"]["result"][0]["timestamp"] = [
        int(datetime(2026, 7, day, 0, tzinfo=zone).timestamp()) for day in (23, 24)
    ]
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(NOW)
    )
    coordinator, service, cache = _chain(adapter, instrument)
    result, _, _ = await coordinator._load_daily_bars(instrument, as_of=as_of, lookback=80)
    assert result.ok, result.error
    quote = await service.get_quote(instrument, as_of)
    assert quote.ok, quote.error
    params = {
        "start": (DAY - timedelta(days=160)).isoformat(),
        "end": DAY.isoformat(),
        "interval": "1d",
        "adjustment": AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED.value,
    }
    for category, op, original_params in [
        (DataCategory.MARKET_OHLCV, OP_BARS, params),
        (DataCategory.MARKET_QUOTE, OP_QUOTE, {}),
    ]:
        old_fingerprint = build_us_fingerprint(op, instrument.instrument_id, original_params, as_of)
        key = build_cache_key(
            instrument.market,
            category,
            instrument.instrument_id,
            as_of,
            ProviderRouterEngine._build_call_fingerprint(op, old_fingerprint),
        )
        assert key in cache.entries


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,exchange", [("SI=F", "COMEX"), ("ES=F", "CME")])
@pytest.mark.parametrize("metadata", ["future_time", "absent_price"])
async def test_uncertain_daily_time_does_not_block_actual_current_minute(
    symbol, exchange, metadata
):
    from dataclasses import replace

    from test_yahoo_finance import SequenceTransport, _intraday_body

    from domain.common.enums import TradingSession

    instrument = replace(
        _future_instrument(), symbol=symbol, instrument_id=f"future:US:{symbol}", exchange=exchange
    )
    as_of = datetime(2026, 7, 24, 16, 30, tzinfo=NY)
    payload = _payload(symbol)
    meta = payload["chart"]["result"][0]["meta"]
    if metadata == "future_time":
        meta["regularMarketTime"] = int((as_of + timedelta(days=1)).timestamp())
    else:
        meta["regularMarketPrice"] = None
    minute = _intraday_body(
        timestamps=[as_of - timedelta(minutes=1)],
        closes=[107.0],
        regular_market_time=as_of - timedelta(minutes=2),
        regular_market_price=107.0,
        symbol=symbol,
        trading_period_at=as_of,
    )
    minute_payload = json.loads(minute)
    minute_payload["chart"]["result"][0]["meta"]["currentTradingPeriod"] = payload["chart"][
        "result"
    ][0]["meta"]["currentTradingPeriod"]
    transport = SequenceTransport(
        [json.dumps(payload).encode(), json.dumps(minute_payload).encode()]
    )
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(as_of))
    result = await adapter.get_quote(instrument, as_of)
    assert result.value.last == Decimal("107")
    assert result.value.quote_at == as_of - timedelta(minutes=1)
    assert result.value.session is TradingSession.REGULAR
    assert result.meta.session is TradingSession.REGULAR
    assert result.value.previous_close is None
    assert result.value.open is None and result.value.high is None and result.value.volume is None
    assert "FUTURES_DAILY_QUOTE_TIME_UNAVAILABLE" in result.meta.warnings
    assert "INTRADAY_QUOTE_RECOVERY" in result.meta.warnings
    assert "FUTURES_DAILY_DERIVED_QUOTE_TIME" not in result.meta.warnings
    assert len(transport.requests) == 2
