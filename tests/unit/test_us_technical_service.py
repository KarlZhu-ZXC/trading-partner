"""Phase 1F F3a: deterministic US technical service (focused)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from application.dto.provider_routing import (
    ProviderResultMeta,
    RouterExecutionResult,
)
from application.services.us_technical_service import USTechnicalService
from conftest import FixedClock
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
from domain.common.errors import DataContractError, NoMarketData, StaleMarketData
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 21, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 17, 20, 30, tzinfo=UTC)
D = Decimal

# Frozen golden literals from independent one-shot over the same 260-bar fixture
# (linear uptrend close=100+i*0.25, vol=1e6+i*1000; localcontext prec=50).
_GOLDEN = {
    "ema_10": D("163.625"),
    "sma_50": D("158.625"),
    "sma_200": D("139.875"),
    "rsi_14": D("100"),
    "macd": D("1.75"),
    "macd_signal": D("1.7499999999999999999999999999999999999999999999950"),
    "macd_histogram": D("0.0000000000000000000000000000000000000000000000050"),
    "atr_14": D("0.90"),
    "bollinger_mid": D("162.375"),
    "bollinger_upper": D("165.25814064866769897233854580970446812514576721281"),
    "bollinger_lower": D("159.49185935133230102766145419029553187485423278719"),
    "vwma": D("162.38165266106442577030812324929971988795518207283"),
    "mfi": D("100"),
    "support": D("159.60"),
    "resistance": D("165.25"),
}


def _us_equity(symbol: str = "NVDA") -> Instrument:
    return Instrument(
        instrument_id=f"equity:US:{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _bars_meta(*, as_of: datetime = AS_OF) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.YFINANCE,
        category=DataCategory.MARKET_OHLCV,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=AS_OF,
        freshness=Freshness.FRESH,
        session=TradingSession.REGULAR,
        latency_ms=1,
        cache_disposition=CacheDisposition.MISS,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        data_delay_seconds=0,
        warnings=(),
    )


def _ok(series: USBarSeries) -> RouterExecutionResult[USBarSeries]:
    return RouterExecutionResult(
        value=series,
        ok=True,
        criticality=DataCriticality.CORE,
        meta=_bars_meta(),
        attempts=(),
        warnings=(),
        error=None,
    )


def _fail(error: Exception) -> RouterExecutionResult[Any]:
    return RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.CORE,
        meta=None,
        attempts=(),
        warnings=(),
        error=error,  # type: ignore[arg-type]
    )


def _trend_bars(n: int, *, end: date = date(2026, 7, 17)) -> tuple[MarketBar, ...]:
    start = end - timedelta(days=n - 1)
    bars: list[MarketBar] = []
    for i in range(n):
        d = start + timedelta(days=i)
        close = D("100") + D(i) * D("0.25")
        open_ = close - D("0.10")
        high = close + D("0.50")
        low = close - D("0.40")
        vol = D("1000000") + D(i) * D("1000")
        ts = datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY)
        bars.append(
            MarketBar(
                timestamp=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=vol,
            )
        )
    return tuple(bars)


class _FakeDataService:
    """Tiny fake: records get_bars kwargs and returns a canned result."""

    def __init__(self, result: RouterExecutionResult[Any]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> RouterExecutionResult[Any]:
        self.calls.append(
            {
                "instrument": instrument,
                "start": start,
                "end": end,
                "interval": interval,
                "adjustment": adjustment,
                "as_of": as_of,
            }
        )
        return self.result


def _service(
    result: RouterExecutionResult[Any],
    *,
    clock: FixedClock | None = None,
) -> tuple[USTechnicalService, _FakeDataService]:
    fake = _FakeDataService(result)
    svc = USTechnicalService(fake, clock or FixedClock(NOW))  # type: ignore[arg-type]
    return svc, fake


@pytest.mark.asyncio
async def test_golden_260_bar_trend_all_indicators() -> None:
    instrument = _us_equity()
    bars = _trend_bars(260)
    series = USBarSeries(
        instrument_id=instrument.instrument_id,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=bars[0].timestamp.astimezone(NY).date(),
        end=bars[-1].timestamp.astimezone(NY).date(),
        bars=bars,
    )
    svc, fake = _service(_ok(series))
    snap = await svc.get_snapshot(instrument, as_of=AS_OF, lookback_sessions=260)

    assert snap.instrument_id == instrument.instrument_id
    assert snap.as_of == AS_OF
    assert snap.bar_as_of == bars[-1].timestamp
    assert snap.algorithm_version == "tp_technical_v1"
    assert snap.historically_validated is False
    assert snap.support_resistance_method == "rolling_extrema_20_v1"
    assert snap.support == _GOLDEN["support"]
    assert snap.resistance == _GOLDEN["resistance"]

    ind = snap.indicators
    assert ind.ema_10 == _GOLDEN["ema_10"]
    assert ind.sma_50 == _GOLDEN["sma_50"]
    assert ind.sma_200 == _GOLDEN["sma_200"]
    assert ind.rsi_14 == _GOLDEN["rsi_14"]
    assert ind.macd == _GOLDEN["macd"]
    assert ind.macd_signal == _GOLDEN["macd_signal"]
    assert ind.macd_histogram == _GOLDEN["macd_histogram"]
    assert ind.atr_14 == _GOLDEN["atr_14"]
    assert ind.bollinger_mid == _GOLDEN["bollinger_mid"]
    assert ind.bollinger_upper == _GOLDEN["bollinger_upper"]
    assert ind.bollinger_lower == _GOLDEN["bollinger_lower"]
    assert ind.vwma == _GOLDEN["vwma"]
    assert ind.mfi == _GOLDEN["mfi"]

    # Request range: NY as_of date ± lookback*2 natural days.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    ny_end = AS_OF.astimezone(NY).date()
    assert call["start"] == ny_end - timedelta(days=520)
    assert call["end"] == ny_end
    assert call["interval"] is USBarInterval.ONE_DAY
    assert call["adjustment"] is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
    assert call["as_of"] == AS_OF


@pytest.mark.asyncio
async def test_nine_bar_boundary_all_none_and_routed_params() -> None:
    # 9 bars: below every indicator window (EMA10 needs 10; S/R needs 20).
    instrument = _us_equity()
    bars = _trend_bars(9)
    series = USBarSeries(
        instrument_id=instrument.instrument_id,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=bars[0].timestamp.astimezone(NY).date(),
        end=bars[-1].timestamp.astimezone(NY).date(),
        bars=bars,
    )
    svc, fake = _service(_ok(series))
    snap = await svc.get_snapshot(instrument, as_of=AS_OF, lookback_sessions=260)

    ind = snap.indicators
    assert ind.ema_10 is None
    assert ind.sma_50 is None
    assert ind.sma_200 is None
    assert ind.rsi_14 is None
    assert ind.macd is None
    assert ind.macd_signal is None
    assert ind.macd_histogram is None
    assert ind.atr_14 is None
    assert ind.bollinger_mid is None
    assert ind.bollinger_upper is None
    assert ind.bollinger_lower is None
    assert ind.vwma is None
    assert ind.mfi is None
    assert snap.support is None
    assert snap.resistance is None
    assert snap.bar_as_of == bars[-1].timestamp
    assert snap.algorithm_version == "tp_technical_v1"
    assert snap.historically_validated is False
    assert snap.support_resistance_method == "rolling_extrema_20_v1"

    call = fake.calls[0]
    ny_end = AS_OF.astimezone(NY).date()
    assert call["start"] == ny_end - timedelta(days=520)
    assert call["end"] == ny_end
    assert call["interval"] is USBarInterval.ONE_DAY
    assert call["adjustment"] is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED


@pytest.mark.asyncio
async def test_router_failure_stale_future_invalid_lookback() -> None:
    instrument = _us_equity()
    clock = FixedClock(NOW)

    # Router typed failure is re-raised (no invented data).
    fail_err = NoMarketData("upstream empty", details={"field": "bars"})
    svc, _ = _service(_fail(fail_err), clock=clock)
    with pytest.raises(NoMarketData, match="upstream empty"):
        await svc.get_snapshot(instrument, as_of=AS_OF)

    # Stale: latest bar more than 4 NY calendar days before as_of.
    stale_day = date(2026, 7, 10)  # 7 days before 2026-07-17
    stale_bar = MarketBar(
        timestamp=datetime(2026, 7, 10, 16, 0, tzinfo=NY),
        open=D("10"),
        high=D("11"),
        low=D("9"),
        close=D("10.5"),
        volume=D("1000"),
    )
    stale_series = USBarSeries(
        instrument_id=instrument.instrument_id,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=stale_day,
        end=stale_day,
        bars=(stale_bar,),
    )
    svc, _ = _service(_ok(stale_series), clock=clock)
    with pytest.raises(StaleMarketData):
        await svc.get_snapshot(instrument, as_of=AS_OF)

    # Future as_of relative to clock.
    future = NOW + timedelta(hours=1)
    bars = _trend_bars(30)
    series = USBarSeries(
        instrument_id=instrument.instrument_id,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=bars[0].timestamp.astimezone(NY).date(),
        end=bars[-1].timestamp.astimezone(NY).date(),
        bars=bars,
    )
    svc, _ = _service(_ok(series), clock=clock)
    with pytest.raises(DataContractError, match="future"):
        await svc.get_snapshot(instrument, as_of=future)

    # Invalid lookback (strict int range 20..1000).
    svc, _ = _service(_ok(series), clock=clock)
    with pytest.raises(DataContractError, match="lookback"):
        await svc.get_snapshot(instrument, as_of=AS_OF, lookback_sessions=19)
    with pytest.raises(DataContractError, match="lookback"):
        await svc.get_snapshot(instrument, as_of=AS_OF, lookback_sessions=1001)
    with pytest.raises(DataContractError, match="strict int"):
        await svc.get_snapshot(
            instrument,
            as_of=AS_OF,
            lookback_sessions=True,  # type: ignore[arg-type]
        )
