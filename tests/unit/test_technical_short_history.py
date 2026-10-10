"""Short legal daily lookbacks retain usable weekly facts and disclose gaps."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.market import MarketBarDTO
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.dto.technical import (
    TechnicalAnalysisInput,
    TechnicalChartInput,
    TechnicalTimeframeDTO,
)
from application.services.technical_tool_coordinator import TechnicalToolCoordinator, _weekly_bars
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries
from infrastructure.system.redactor import DefaultSecretRedactor
from infrastructure.technical.ta_lib_indicator_engine import TALibIndicatorEngine

AS_OF = datetime(2026, 7, 24, 21, tzinfo=UTC)


def _bars(count: int = 60) -> tuple[MarketBar, ...]:
    bars: list[MarketBar] = []
    day = AS_OF - timedelta(days=90)
    while len(bars) < count:
        if day.weekday() < 5:
            price = Decimal(100 + len(bars))
            bars.append(MarketBar(
                timestamp=day, open=price, high=price + 1, low=price - 1,
                close=price, volume=Decimal(1000),
            ))
        day += timedelta(days=1)
    return tuple(bars)


def _coordinator(instrument: Instrument) -> TechnicalToolCoordinator:
    bars = _bars()
    series = USBarSeries(
        instrument_id=instrument.instrument_id, interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=bars[0].timestamp.date(), end=bars[-1].timestamp.date(), bars=bars,
    )
    meta = ProviderResultMeta(
        vendor=VendorId.YFINANCE, category=DataCategory.MARKET_OHLCV,
        role=SourceRole.PRIMARY, as_of=AS_OF, fetched_at=AS_OF,
        freshness=Freshness.FRESH, session=TradingSession.REGULAR,
        latency_ms=1, cache_disposition=CacheDisposition.MISS,
        adjustment=series.adjustment, data_delay_seconds=0, warnings=(),
    )
    data = MagicMock()
    data.get_bars = AsyncMock(return_value=RouterExecutionResult(
        ok=True, value=series, meta=meta, criticality=DataCriticality.CORE,
        attempts=(), warnings=(), error=None,
    ))
    access = MagicMock()
    access.get = AsyncMock(return_value=instrument)
    renderer = MagicMock()
    renderer.render.return_value = b"fixture-png"
    return TechnicalToolCoordinator(
        instrument_access=access, clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(), secret_redactor=DefaultSecretRedactor(),
        us_data_service=data, a_share_data_service=MagicMock(),
        indicator_engine=TALibIndicatorEngine(), chart_renderer=renderer,
    )


@pytest.mark.asyncio
async def test_minimum_lookback_keeps_daily_and_partial_weekly_facts(
    us_instrument: Instrument,
) -> None:
    envelope = await _coordinator(us_instrument).get_snapshot(TechnicalAnalysisInput(
        instrument_id=us_instrument.instrument_id, as_of=AS_OF,
        lookback_sessions=60, include_bars=True,
    ))
    assert envelope.ok is True
    assert envelope.data is not None
    daily, weekly = envelope.data.timeframes
    assert daily.bar_count == 60
    assert weekly.bar_count == 12
    assert daily.trend_state == "uptrend"
    assert weekly.trend_state == "insufficient_data"
    metrics = {metric.name: metric.value for metric in weekly.metrics}
    assert metrics["ema_10"] is not None
    assert metrics["bollinger_mid"] is None
    assert metrics["relative_volume_20"] is None
    assert metrics["sma_50"] is None
    assert envelope.degraded is True
    warning = next(w for w in envelope.warnings if w.code == "TECHNICAL_HISTORY_INCOMPLETE")
    assert warning.details == {"interval": "1w", "bar_count": 12}
    assert envelope.data.bars is not None and len(envelope.data.bars) == 60


@pytest.mark.asyncio
async def test_daily_only_short_request_has_no_weekly_history_warning(
    us_instrument: Instrument,
) -> None:
    envelope = await _coordinator(us_instrument).get_snapshot(TechnicalAnalysisInput(
        instrument_id=us_instrument.instrument_id, as_of=AS_OF,
        lookback_sessions=60, intervals=("1d",),
    ))
    assert envelope.ok is True
    assert not any(w.code == "TECHNICAL_HISTORY_INCOMPLETE" for w in envelope.warnings)


@pytest.mark.asyncio
async def test_short_weekly_chart_retains_png_and_discloses_indicator_gaps(
    us_instrument: Instrument,
) -> None:
    artifact = await _coordinator(us_instrument).render_chart(TechnicalChartInput(
        instrument_id=us_instrument.instrument_id, as_of=AS_OF,
        lookback_sessions=60, interval="1w",
    ))
    assert artifact.envelope.ok is True
    assert artifact.png == b"fixture-png"
    assert artifact.envelope.data is not None
    assert artifact.envelope.data.timeframes[0].bar_count == 12
    assert artifact.envelope.degraded is True
    assert any(w.code == "TECHNICAL_HISTORY_INCOMPLETE" for w in artifact.envelope.warnings)


def test_single_week_preserves_observation_without_fabricating_indicators() -> None:
    frame = TALibIndicatorEngine().analyze(_bars(1), interval="1w")
    metrics = {metric.name: metric.value for metric in frame.metrics}
    assert frame.bar_count == 1
    assert metrics["volume"] == Decimal(1000)
    assert metrics["ema_10"] is None
    assert frame.trend_state == "insufficient_data"
    assert frame.momentum_state == "insufficient_data"
    assert frame.volume_state == "insufficient_data"


@pytest.mark.parametrize("interval", ["1d", "1w"])
def test_empty_history_remains_an_error(interval: str) -> None:
    with pytest.raises(DataContractError):
        TALibIndicatorEngine().analyze((), interval=interval)


def _weekly_boundary_coordinator(
    instrument: Instrument, *, start: date, cutoff: datetime,
) -> tuple[TechnicalToolCoordinator, tuple[MarketBar, ...]]:
    day = datetime.combine(start, datetime.min.time(), tzinfo=UTC).replace(hour=20)
    bars: list[MarketBar] = []
    while day <= cutoff:
        # Synthetic XNYS weekday facts exclude Labor Day. The initial low
        # is provisionally confirmed after five later calendar-week bars;
        # Friday's lower low removes that confirmation within the same week.
        if day.weekday() < 5 and day.date() != date(2026, 9, 7):
            low = Decimal(80 if not bars else 70 if day.date() == date(2026, 9, 11) else 99)
            bars.append(MarketBar(
                timestamp=day, open=Decimal(100), high=Decimal(101), low=low,
                close=Decimal(100), volume=Decimal(1000),
            ))
        day += timedelta(days=1)
    series = USBarSeries(
        instrument_id=instrument.instrument_id, interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=start, end=cutoff.date(), bars=tuple(bars),
    )
    meta = ProviderResultMeta(
        vendor=VendorId.YFINANCE, category=DataCategory.MARKET_OHLCV,
        role=SourceRole.PRIMARY, as_of=cutoff, fetched_at=cutoff,
        freshness=Freshness.FRESH, session=TradingSession.REGULAR,
        latency_ms=1, cache_disposition=CacheDisposition.MISS,
        adjustment=series.adjustment, data_delay_seconds=0, warnings=(),
    )
    coordinator = _coordinator(instrument)
    coordinator._clock = FixedClock(cutoff)
    coordinator._us_data_service.get_bars = AsyncMock(return_value=RouterExecutionResult(
        ok=True, value=series, meta=meta, criticality=DataCriticality.CORE,
        attempts=(), warnings=(), error=None,
    ))
    return coordinator, tuple(bars)


@pytest.mark.asyncio
@pytest.mark.parametrize("start, cutoff", [
    (date(2026, 8, 3), datetime(2026, 9, 8, 21, tzinfo=UTC)),
    (date(2026, 8, 3), datetime(2026, 9, 11, 21, tzinfo=UTC)),
    (date(2026, 8, 5), datetime(2026, 9, 11, 21, tzinfo=UTC)),
])
async def test_weekly_snapshot_and_chart_disclose_boundaries_without_changing_facts(
    us_instrument: Instrument, start: date, cutoff: datetime,
) -> None:
    coordinator, daily = _weekly_boundary_coordinator(us_instrument, start=start, cutoff=cutoff)
    weekly = _weekly_bars(daily)
    original = TechnicalTimeframeDTO.from_domain(
        TALibIndicatorEngine().analyze(weekly, interval="1w")
    )
    snapshot = await coordinator.get_snapshot(TechnicalAnalysisInput(
        instrument_id=us_instrument.instrument_id, as_of=cutoff, lookback_sessions=60,
        intervals=("1w",), include_bars=True,
    ))
    chart = await coordinator.render_chart(TechnicalChartInput(
        instrument_id=us_instrument.instrument_id, as_of=cutoff, lookback_sessions=60,
        interval="1w",
    ))
    assert snapshot.ok and snapshot.data is not None
    assert chart.envelope.ok and chart.envelope.data is not None
    assert chart.png == b"fixture-png"
    limitation = "SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL"
    for envelope in (snapshot, chart.envelope):
        warning = next(
            w for w in envelope.warnings if w.code == "TECHNICAL_WEEKLY_BOUNDARIES_MAY_BE_PARTIAL"
        )
        assert "first and last weeks may be partial" in warning.message
        assert "available bar prefix" in warning.message
        assert "remain provisional" in warning.message
        assert "historical validation" in warning.message
        assert warning.details == {"interval": "1w", "smc_limitation": limitation}
        assert envelope.degraded is True
        assert envelope.as_of == cutoff
        assert envelope.data is not None
        frame = envelope.data.timeframes[0]
        assert frame.smart_money is not None and original.smart_money is not None
        assert frame.smart_money.limitations == (*original.smart_money.limitations, limitation)
        assert frame.smart_money.historically_validated is False
        # Every calculation, occurrence/confirmation timestamp and existing
        # limitation is preserved, including complete historical weeks.
        unchanged = frame.model_copy(update={
            "smart_money": frame.smart_money.model_copy(update={
                "limitations": original.smart_money.limitations,
            }),
        })
        assert unchanged == original
        if cutoff.date() == date(2026, 9, 8):
            swing = next(s for s in frame.smart_money.swings if s.kind == "low")
            assert swing.confirmed_at == daily[-1].timestamp
        else:
            assert frame.smart_money.swings == ()
    assert snapshot.data.bars_interval == "1w"
    assert snapshot.data.bars == tuple(MarketBarDTO.from_domain(bar) for bar in weekly)
    assert coordinator._chart_renderer.render.call_args.kwargs["bars"] == weekly
    rendered_analysis = coordinator._chart_renderer.render.call_args.kwargs["analysis"]
    assert rendered_analysis.smart_money.limitations[-1] == limitation


@pytest.mark.asyncio
async def test_daily_only_has_no_calendar_week_warning_or_smc_limitation(
    us_instrument: Instrument,
) -> None:
    coordinator = _coordinator(us_instrument)
    snapshot = await coordinator.get_snapshot(TechnicalAnalysisInput(
        instrument_id=us_instrument.instrument_id, as_of=AS_OF, lookback_sessions=60,
        intervals=("1d",),
    ))
    chart = await coordinator.render_chart(TechnicalChartInput(
        instrument_id=us_instrument.instrument_id, as_of=AS_OF, lookback_sessions=60,
        interval="1d",
    ))
    for envelope in (snapshot, chart.envelope):
        assert envelope.ok and envelope.data is not None
        assert not any(
            w.code == "TECHNICAL_WEEKLY_BOUNDARIES_MAY_BE_PARTIAL" for w in envelope.warnings
        )
        frame = envelope.data.timeframes[0]
        assert frame.smart_money is not None
        assert "SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL" not in frame.smart_money.limitations
        assert envelope.degraded is False


@pytest.mark.asyncio
@pytest.mark.parametrize("intervals", [("1d", "1w"), ("1w", "1d")])
async def test_weekly_disclosure_preserves_daily_compatibility_for_interval_order(
    us_instrument: Instrument, intervals: tuple[str, ...],
) -> None:
    envelope = await _coordinator(us_instrument).get_snapshot(TechnicalAnalysisInput(
        instrument_id=us_instrument.instrument_id, as_of=AS_OF, lookback_sessions=60,
        intervals=intervals, include_bars=True,
    ))
    assert envelope.ok and envelope.data is not None
    assert tuple(frame.interval for frame in envelope.data.timeframes) == intervals
    daily = next(frame for frame in envelope.data.timeframes if frame.interval == "1d")
    weekly = next(frame for frame in envelope.data.timeframes if frame.interval == "1w")
    original_daily = TechnicalTimeframeDTO.from_domain(
        TALibIndicatorEngine().analyze(_bars(), interval="1d")
    )
    assert daily == original_daily
    assert envelope.data.indicators.ema_10 == next(
        metric.value for metric in daily.metrics if metric.name == "ema_10"
    )
    assert envelope.data.bar_as_of == daily.bar_as_of
    assert envelope.data.bars_interval == intervals[0]
    assert weekly.smart_money is not None
    assert weekly.smart_money.limitations[-1] == "SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL"
    assert sum(
        w.code == "TECHNICAL_WEEKLY_BOUNDARIES_MAY_BE_PARTIAL" for w in envelope.warnings
    ) == 1
