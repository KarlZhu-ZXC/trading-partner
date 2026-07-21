"""Phase 1F F3b: USToolCoordinator provenance-preserving composition (focused)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.dto.tool_envelope import WarningInfo
from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetContextInput,
    MarketGetSnapshotInput,
    TechnicalGetSnapshotInput,
    USGetSnapshotInput,
)
from application.services.us_market_context_service import USMarketContextResult
from application.services.us_tool_coordinator import USToolCoordinator
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
from domain.common.errors import InvalidInstrument, NoMarketData, ProviderUnavailableError
from domain.instruments.models import Instrument
from domain.market.models import MarketBar, TechnicalIndicators
from domain.us_market.enums import USBarInterval
from domain.us_market.models import (
    USBarSeries,
    USMarketContext,
    USMarketProxy,
    USQuote,
    USTechnicalSnapshot,
)
from infrastructure.system.redactor import DefaultSecretRedactor

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 21, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 17, 20, 30, tzinfo=UTC)
QUOTE_AT = datetime(2026, 7, 17, 15, 30, tzinfo=NY)
BAR_TS = datetime(2026, 7, 17, 16, 0, tzinfo=NY)
D = Decimal
_INSTRUMENT_ID = "equity:US:NVDA"
_INSTRUMENT = Instrument(
    instrument_id=_INSTRUMENT_ID,
    symbol="NVDA",
    name="NVDA",
    market=Market.US,
    exchange="NASDAQ",
    currency="USD",
    timezone="America/New_York",
    asset_type=AssetType.EQUITY,
)


def _meta(
    *,
    vendor: VendorId = VendorId.YFINANCE,
    role: SourceRole = SourceRole.PRIMARY,
    category: DataCategory = DataCategory.MARKET_QUOTE,
    fetched_at: datetime | None = None,
    freshness: Freshness = Freshness.FRESH,
    session: TradingSession = TradingSession.REGULAR,
    warnings: tuple[str, ...] = (),
    adjustment: AdjustmentMethod | None = None,
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=vendor,
        category=category,
        role=role,
        as_of=AS_OF,
        fetched_at=fetched_at if fetched_at is not None else AS_OF,
        freshness=freshness,
        session=session,
        latency_ms=1,
        cache_disposition=CacheDisposition.MISS,
        adjustment=adjustment,
        data_delay_seconds=0,
        warnings=warnings,
    )


def _quote() -> USQuote:
    return USQuote(
        instrument_id=_INSTRUMENT_ID,
        quote_at=QUOTE_AT,
        session=TradingSession.REGULAR,
        last=D("120.50"),
        open=D("118.00"),
        high=D("121.00"),
        low=D("117.50"),
        previous_close=D("119.00"),
        volume=D("1000000"),
        average_volume=D("900000"),
        market_cap=None,
        beta=None,
        week_52_low=None,
        week_52_high=None,
    )


def _series(*, n: int = 30) -> USBarSeries:
    end = date(2026, 7, 17)
    start = end - timedelta(days=n - 1)
    bars: list[MarketBar] = []
    for i in range(n):
        d = start + timedelta(days=i)
        close = D("100") + D(i) * D("0.25")
        bars.append(
            MarketBar(
                timestamp=datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY),
                open=close - D("0.10"),
                high=close + D("0.50"),
                low=close - D("0.40"),
                close=close,
                volume=D("1000000"),
            )
        )
    return USBarSeries(
        instrument_id=_INSTRUMENT_ID,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        start=bars[0].timestamp.astimezone(NY).date(),
        end=bars[-1].timestamp.astimezone(NY).date(),
        bars=tuple(bars),
    )


def _ok[T](value: T, meta: ProviderResultMeta) -> RouterExecutionResult[T]:
    return RouterExecutionResult(
        value=value,
        ok=True,
        criticality=DataCriticality.CORE,
        meta=meta,
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


def _context(
    *,
    codes: tuple[str, ...] = ("US_BREADTH_UNAVAILABLE",),
) -> USMarketContext:
    proxy = USMarketProxy(instrument_id="etf:US:SPY", latest=D("500"), change_percent=D("0.5"))
    return USMarketContext(
        as_of=AS_OF,
        spy=proxy,
        qqq=USMarketProxy(instrument_id="etf:US:QQQ", latest=D("400"), change_percent=D("0.4")),
        iwm=USMarketProxy(instrument_id="etf:US:IWM", latest=D("200"), change_percent=D("0.2")),
        advancing_count=None,
        declining_count=None,
        warning_codes=codes,
    )


def _technical() -> USTechnicalSnapshot:
    return USTechnicalSnapshot(
        instrument_id=_INSTRUMENT_ID,
        as_of=AS_OF,
        bar_as_of=BAR_TS,
        indicators=TechnicalIndicators(
            ema_10=None,
            sma_50=None,
            sma_200=None,
            rsi_14=None,
            macd=None,
            macd_signal=None,
            macd_histogram=None,
            atr_14=None,
            bollinger_mid=None,
            bollinger_upper=None,
            bollinger_lower=None,
            vwma=None,
            mfi=None,
        ),
        support=None,
        resistance=None,
        algorithm_version="tp_technical_v1",
        historically_validated=False,
        support_resistance_method="rolling_extrema_20_v1",
    )


def _coordinator(
    *,
    data: MagicMock | None = None,
    context: MagicMock | None = None,
    technical: MagicMock | None = None,
    master: MagicMock | None = None,
    clock: FixedClock | None = None,
    ids: SequentialIdGenerator | None = None,
) -> tuple[USToolCoordinator, MagicMock, MagicMock, MagicMock, MagicMock]:
    if master is None:
        master = MagicMock()
        master.get.return_value = _INSTRUMENT
    if data is None:
        data = MagicMock()
        data.get_quote = AsyncMock(return_value=_ok(_quote(), _meta()))
        data.get_bars = AsyncMock(
            return_value=_ok(
                _series(),
                _meta(
                    category=DataCategory.MARKET_OHLCV,
                    adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
                ),
            )
        )
    if context is None:
        context = MagicMock()
        context.get_context_result = AsyncMock(
            return_value=USMarketContextResult(
                context=_context(),
                metas=(_meta(vendor=VendorId.YFINANCE, category=DataCategory.MARKET_QUOTE),),
            )
        )
    if technical is None:
        technical = MagicMock()
        technical.build_snapshot.return_value = _technical()
    clock = clock or FixedClock(NOW)
    ids = ids or SequentialIdGenerator()
    coord = USToolCoordinator(
        instrument_master=master,
        clock=clock,
        id_generator=ids,
        secret_redactor=DefaultSecretRedactor(),
        data_service=data,
        context_service=context,
        technical_service=technical,
    )
    return coord, master, data, context, technical


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "request_factory"),
    [
        (
            "get_market_snapshot",
            lambda: MarketGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=AS_OF),
        ),
        (
            "get_market_bars",
            lambda: MarketGetBarsInput(
                instrument_id=_INSTRUMENT_ID,
                start=date(2026, 7, 1),
                end=date(2026, 7, 17),
                as_of=AS_OF,
            ),
        ),
        (
            "get_market_context",
            lambda: MarketGetContextInput(as_of=AS_OF),
        ),
        (
            "get_technical_snapshot",
            lambda: TechnicalGetSnapshotInput(
                instrument_id=_INSTRUMENT_ID, as_of=AS_OF, lookback_sessions=60
            ),
        ),
        (
            "get_us_snapshot",
            lambda: USGetSnapshotInput(
                instrument_id=_INSTRUMENT_ID, as_of=AS_OF, lookback_sessions=60
            ),
        ),
    ],
)
async def test_five_handlers_delegate_and_return_us_envelope(
    method: str, request_factory: Any
) -> None:
    coord, master, data, context, technical = _coordinator()
    envelope = await getattr(coord, method)(request_factory())

    assert envelope.ok is True
    assert envelope.market == Market.US
    assert envelope.as_of == AS_OF
    assert envelope.request_id.startswith("req_")
    assert envelope.data is not None
    if method != "get_market_context":
        master.get.assert_called_once_with(_INSTRUMENT_ID)
    if method == "get_market_snapshot":
        data.get_quote.assert_awaited_once()
    if method == "get_market_bars":
        data.get_bars.assert_awaited_once()
        assert data.get_bars.await_args.kwargs["adjustment"] is (
            AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
        )
    if method == "get_market_context":
        context.get_context_result.assert_awaited_once()
        assert envelope.degraded is True  # breadth always unavailable
        assert any(w.code == "US_BREADTH_UNAVAILABLE" for w in envelope.warnings)
    if method == "get_technical_snapshot":
        data.get_bars.assert_awaited_once()
        technical.build_snapshot.assert_called_once()
    if method == "get_us_snapshot":
        data.get_quote.assert_awaited_once()
        data.get_bars.assert_awaited_once()
        context.get_context_result.assert_awaited_once()
        technical.build_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_future_bars_default_to_unadjusted() -> None:
    future = Instrument(
        instrument_id="future:US:GC=F",
        symbol="GC=F",
        name="COMEX Gold Futures Continuous",
        market=Market.US,
        exchange="COMEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.FUTURE,
    )
    master = MagicMock()
    master.get.return_value = future
    data = MagicMock()
    future_series = USBarSeries(
        instrument_id=future.instrument_id,
        interval=USBarInterval.SIXTY_MINUTES,
        adjustment=AdjustmentMethod.NONE,
        start=date(2026, 7, 17),
        end=date(2026, 7, 17),
        bars=(
            MarketBar(
                timestamp=BAR_TS,
                open=D("4000"),
                high=D("4010"),
                low=D("3990"),
                close=D("4005"),
                volume=D("100"),
            ),
        ),
    )
    data.get_bars = AsyncMock(
        return_value=_ok(
            future_series,
            _meta(
                category=DataCategory.MARKET_OHLCV,
                adjustment=AdjustmentMethod.NONE,
                warnings=(
                    "FUTURES_CONTRACT_NOT_SPOT",
                    "CONTINUOUS_FUTURES_ROLL_RISK",
                ),
            ),
        )
    )
    coord, _, _, _, _ = _coordinator(master=master, data=data)
    envelope = await coord.get_market_bars(
        MarketGetBarsInput(
            instrument_id=future.instrument_id,
            start=date(2026, 7, 17),
            end=date(2026, 7, 17),
            interval=USBarInterval.SIXTY_MINUTES,
            as_of=AS_OF,
        )
    )
    assert envelope.ok is True
    assert envelope.data is not None
    assert envelope.data.adjustment is AdjustmentMethod.NONE
    assert {warning.code for warning in envelope.warnings}.issuperset(
        {"FUTURES_CONTRACT_NOT_SPOT", "CONTINUOUS_FUTURES_ROLL_RISK"}
    )
    assert data.get_bars.await_args.kwargs["adjustment"] is AdjustmentMethod.NONE


@pytest.mark.asyncio
async def test_core_failure_uses_typed_error_and_redacts_secrets() -> None:
    err = ProviderUnavailableError(
        "chain exhausted secret=test-secret-value",
        details={"token": "test-secret-value"},
        retryable=True,
    )
    data = MagicMock()
    data.get_quote = AsyncMock(return_value=_fail(err))
    data.get_bars = AsyncMock()
    coord, _master, _data, _ctx, _tech = _coordinator(data=data)

    envelope = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=AS_OF)
    )

    assert envelope.ok is False
    assert envelope.degraded is True
    assert envelope.errors[0].code == err.code
    assert envelope.errors[0].retryable is True
    assert "test-secret-value" not in envelope.errors[0].message
    assert "test-secret-value" not in str(envelope.errors[0].details)
    data.get_bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_composite_fetches_bars_once_and_reuses_for_technical() -> None:
    coord, _master, data, _ctx, technical = _coordinator()
    series = _series(n=40)
    meta = _meta(
        category=DataCategory.MARKET_OHLCV,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
    )
    data.get_bars = AsyncMock(return_value=_ok(series, meta))

    envelope = await coord.get_us_snapshot(
        USGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=AS_OF, lookback_sessions=60)
    )

    assert envelope.ok is True
    assert data.get_bars.await_count == 1
    technical.build_snapshot.assert_called_once()
    kwargs = technical.build_snapshot.call_args.kwargs
    assert kwargs["series"] is series
    assert kwargs["as_of"] == AS_OF
    assert kwargs["lookback_sessions"] == 60
    # Technical lookback range: NY date ± lookback*2 natural days.
    call = data.get_bars.await_args
    assert call is not None
    ny = AS_OF.astimezone(NY).date()
    assert call.kwargs["start"] == ny - timedelta(days=120)
    assert call.kwargs["end"] == ny
    assert call.kwargs["interval"] is USBarInterval.ONE_DAY


@pytest.mark.asyncio
async def test_composite_optional_technical_and_context_degradation() -> None:
    context = MagicMock()
    context.get_context_result = AsyncMock(side_effect=RuntimeError("context backend down"))
    technical = MagicMock()
    technical.build_snapshot.side_effect = NoMarketData(
        "no bars available at or before as_of",
        details={"field": "bars"},
    )
    coord, _master, _data, _ctx, _tech = _coordinator(context=context, technical=technical)

    envelope = await coord.get_us_snapshot(
        USGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=AS_OF, lookback_sessions=60)
    )

    assert envelope.ok is True
    assert envelope.degraded is True
    codes = [w.code for w in envelope.warnings]
    assert "US_CONTEXT_UNAVAILABLE" in codes
    assert "US_TECHNICAL_UNAVAILABLE" in codes
    assert envelope.data is not None
    assert envelope.data.quote is not None
    assert envelope.data.bars is not None
    assert envelope.data.technical is None
    assert envelope.data.context is None
    assert envelope.data.degraded is True


@pytest.mark.asyncio
async def test_source_freshness_warnings_dedupe_and_max_fetched_at() -> None:
    t0 = AS_OF
    t1 = AS_OF + timedelta(seconds=30)
    t2 = AS_OF + timedelta(seconds=60)
    quote_meta = _meta(
        vendor=VendorId.YFINANCE,
        role=SourceRole.PRIMARY,
        fetched_at=t0,
        freshness=Freshness.DELAYED,
        warnings=("PROVIDER_NOTE",),
    )
    quote_result = RouterExecutionResult(
        value=_quote(),
        ok=True,
        criticality=DataCriticality.CORE,
        meta=quote_meta,
        attempts=(),
        warnings=(WarningInfo(code="ROUTER_NOTE", message="router note", details={}),),
        error=None,
    )
    data = MagicMock()
    data.get_quote = AsyncMock(return_value=quote_result)
    data.get_bars = AsyncMock(
        return_value=_ok(
            _series(),
            _meta(
                vendor=VendorId.YFINANCE,
                role=SourceRole.PRIMARY,
                category=DataCategory.MARKET_OHLCV,
                adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
                fetched_at=t2,
                freshness=Freshness.FRESH,
            ),
        )
    )
    context = MagicMock()
    context.get_context_result = AsyncMock(
        return_value=USMarketContextResult(
            context=_context(codes=("US_BREADTH_UNAVAILABLE", "PROXY_IWM_UNAVAILABLE")),
            metas=(
                _meta(
                    vendor=VendorId.ALPHA_VANTAGE,
                    role=SourceRole.FALLBACK,
                    fetched_at=t1,
                    freshness=Freshness.STALE,
                ),
            ),
        )
    )

    coord, _m, _d, _c, _t = _coordinator(data=data, context=context)
    envelope = await coord.get_us_snapshot(
        USGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=AS_OF, lookback_sessions=60)
    )

    assert envelope.ok is True
    assert envelope.degraded is True
    assert envelope.freshness == Freshness.STALE
    assert envelope.fetched_at == t2
    names = [(s.name, s.role) for s in envelope.sources]
    assert names[0] == (VendorId.YFINANCE.value, SourceRole.PRIMARY)
    assert names[1] == (VendorId.ALPHA_VANTAGE.value, SourceRole.FALLBACK)
    yf = next(s for s in envelope.sources if s.name == VendorId.YFINANCE.value)
    assert yf.retrieved_at == t2  # max of yfinance/primary
    codes = [w.code for w in envelope.warnings]
    assert codes[0] == "ROUTER_NOTE"
    assert "US_BREADTH_UNAVAILABLE" in codes
    assert "PROXY_IWM_UNAVAILABLE" in codes
    assert "PROVIDER_NOTE" in codes
    assert "FALLBACK_US_SOURCE" in codes
    assert "DELAYED_US_DATA" in codes
    assert "STALE_US_DATA" in codes
    assert codes.count("FALLBACK_US_SOURCE") == 1
    assert codes.count("DELAYED_US_DATA") == 1


@pytest.mark.asyncio
async def test_closed_session_stale_quote_uses_latest_known_warning() -> None:
    data = MagicMock()
    data.get_quote = AsyncMock(
        return_value=_ok(
            _quote(),
            _meta(freshness=Freshness.STALE, session=TradingSession.CLOSED),
        )
    )
    coordinator, *_ = _coordinator(data=data)

    envelope = await coordinator.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=AS_OF)
    )

    codes = [warning.code for warning in envelope.warnings]
    assert envelope.ok is True
    assert envelope.freshness == Freshness.STALE.value
    assert "CLOSED_SESSION_LAST_KNOWN" in codes
    assert "STALE_US_DATA" not in codes


@pytest.mark.asyncio
async def test_request_id_and_effective_as_of_sampled_once() -> None:
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    master = MagicMock()
    master.get.side_effect = InvalidInstrument(
        "instrument not found",
        details={"instrument_id": _INSTRUMENT_ID},
    )
    coord, _m, data, _c, _t = _coordinator(master=master, clock=clock, ids=ids)

    envelope = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=None)
    )

    assert envelope.ok is False
    assert envelope.as_of == NOW
    assert envelope.request_id.startswith("req_")
    assert envelope.market == Market.US
    assert envelope.errors[0].code == "INVALID_INSTRUMENT"
    master.get.assert_called_once_with(_INSTRUMENT_ID)
    data.get_quote.assert_not_awaited()

    master.get.side_effect = None
    master.get.return_value = _INSTRUMENT
    explicit = NOW - timedelta(minutes=15)
    envelope2 = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_INSTRUMENT_ID, as_of=explicit)
    )
    assert envelope2.ok is True
    assert envelope2.as_of == explicit
    assert envelope2.request_id != envelope.request_id
    assert data.get_quote.await_args is not None
    assert data.get_quote.await_args.args[1] == explicit
