"""Phase 1F F2c: US market data/context application services (focused)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.services.us_market_context_service import USMarketContextService
from application.services.us_market_data_service import (
    OP_BARS,
    OP_QUOTE,
    USMarketDataService,
    build_us_fingerprint,
)
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
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from domain.us_market.models import (
    USBarSeries,
    USBreadthSnapshot,
    USQuote,
    USSectorRotation,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 21, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 17, 20, 30, tzinfo=UTC)  # after NY close (20:00 UTC)
QUOTE_AT = datetime(2026, 7, 17, 15, 30, tzinfo=NY)
BAR_TS = datetime(2026, 7, 17, 16, 0, tzinfo=NY)
D = Decimal
_DEFAULT_LAST = D("120.50")
_DEFAULT_PREV_CLOSE = D("119.00")


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


def _us_etf(symbol: str) -> Instrument:
    return Instrument(
        instrument_id=f"etf:US:{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.US,
        exchange="ARCA",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.ETF,
    )


def _meta(
    category: DataCategory = DataCategory.MARKET_QUOTE,
    *,
    adjustment: AdjustmentMethod | None = None,
    as_of: datetime = AS_OF,
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.YFINANCE,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=AS_OF,
        freshness=Freshness.FRESH,
        session=TradingSession.REGULAR,
        latency_ms=1,
        cache_disposition=CacheDisposition.MISS,
        adjustment=adjustment,
        data_delay_seconds=0,
        warnings=(),
    )


def _quote(
    instrument_id: str = "equity:US:NVDA",
    *,
    last: Decimal = _DEFAULT_LAST,
    previous_close: Decimal | None = _DEFAULT_PREV_CLOSE,
) -> USQuote:
    # Keep OHLC consistent with last for arbitrary last/previous_close fixtures.
    high = max(last, D("1"))
    low = min(last, D("1")) if last > 0 else last
    open_ = last
    return USQuote(
        instrument_id=instrument_id,
        quote_at=QUOTE_AT,
        session=TradingSession.REGULAR,
        last=last,
        open=open_,
        high=high,
        low=low if low <= high else high,
        previous_close=previous_close,
        volume=D("1000000"),
        average_volume=D("900000"),
        market_cap=None,
        beta=None,
        week_52_low=None,
        week_52_high=None,
    )


def _series(
    instrument_id: str = "equity:US:NVDA",
    *,
    start: date = date(2026, 7, 17),
    end: date = date(2026, 7, 17),
    adjustment: AdjustmentMethod = AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
) -> USBarSeries:
    bar = MarketBar(
        timestamp=BAR_TS,
        open=D("118.00"),
        high=D("121.00"),
        low=D("117.50"),
        close=D("120.50"),
        volume=D("1000000"),
    )
    return USBarSeries(
        instrument_id=instrument_id,
        interval=USBarInterval.ONE_DAY,
        adjustment=adjustment,
        start=start,
        end=end,
        bars=(bar,),
    )


def _ok_result[T](value: T, meta: ProviderResultMeta) -> RouterExecutionResult[T]:
    return RouterExecutionResult(
        value=value,
        ok=True,
        criticality=DataCriticality.CORE,
        meta=meta,
        attempts=(),
        warnings=(),
        error=None,
    )


def _fail_result() -> RouterExecutionResult[Any]:
    return RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.CORE,
        meta=None,
        attempts=(),
        warnings=(),
        error=DataContractError("provider failed"),
    )


class _StubCodec:
    def __init__(self, codec_id: str) -> None:
        self._codec_id = codec_id

    @property
    def codec_id(self) -> str:
        return self._codec_id

    def encode(self, success: object) -> str:
        return "{}"

    def decode(self, entry: object) -> object:
        raise NotImplementedError


class _RecordingRouter:
    """Captures execute kwargs; optionally runs call + validator then returns canned ok."""

    def __init__(self, *, success_value: object, meta: ProviderResultMeta) -> None:
        self.success_value = success_value
        self.meta = meta
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs: object) -> RouterExecutionResult[Any]:
        self.calls.append(kwargs)
        call = kwargs["call"]
        validator = kwargs.get("result_validator")
        # Minimal adapter that satisfies isinstance narrowing when call runs.
        adapter = _USAdapter(self.success_value, self.meta)
        success = await call(adapter)  # type: ignore[misc,operator]
        if validator is not None:
            validator(success)  # type: ignore[operator]
        return _ok_result(success.value, success.meta)


class _USAdapter:
    def __init__(self, value: object, meta: ProviderResultMeta) -> None:
        self._value = value
        self._meta = meta

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.YFINANCE

    @property
    def provider_name(self) -> str:
        return "yfinance"

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US

    def is_configured(self) -> bool:
        return True

    async def get_quote(self, instrument: Instrument, as_of: datetime) -> ProviderSuccess[USQuote]:
        assert isinstance(self._value, USQuote)
        return ProviderSuccess(value=self._value, meta=self._meta)

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> ProviderSuccess[USBarSeries]:
        assert isinstance(self._value, USBarSeries)
        return ProviderSuccess(value=self._value, meta=self._meta)


class _FakeDataService:
    def __init__(self, results: dict[str, RouterExecutionResult[USQuote]]) -> None:
        self.results = results
        self.calls: list[str] = []

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> RouterExecutionResult[USQuote]:
        self.calls.append(instrument.instrument_id)
        return self.results[instrument.instrument_id]


class _FakeMaster:
    """Stand-in for InstrumentMasterService.get (raises when missing)."""

    def __init__(self, by_id: dict[str, Instrument | None]) -> None:
        self._by_id = by_id

    def get(self, instrument_id: str) -> Instrument:
        from domain.common.errors import InvalidInstrument

        instrument = self._by_id.get(instrument_id)
        if instrument is None:
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            )
        return instrument


class _FakeBreadthService:
    def __init__(self, snapshot: USBreadthSnapshot) -> None:
        self.snapshot = snapshot

    async def get_current(self, as_of: datetime) -> RouterExecutionResult[USBreadthSnapshot]:
        return RouterExecutionResult(
            value=self.snapshot,
            ok=True,
            criticality=DataCriticality.OPTIONAL,
            meta=_meta(DataCategory.MARKET_BREADTH, as_of=as_of),
            attempts=(),
            warnings=(),
            error=None,
        )


def _data_service(
    router: object,
    clock: FixedClock | None = None,
) -> USMarketDataService:
    return USMarketDataService(
        router,  # type: ignore[arg-type]
        clock or FixedClock(NOW),
        _StubCodec("us.quote.v1"),
        _StubCodec("us.bars.v1"),
    )


@pytest.mark.asyncio
async def test_quote_and_bars_router_delegation_and_validator() -> None:
    instrument = _us_equity()
    quote = _quote()
    series = _series()
    quote_meta = _meta(DataCategory.MARKET_QUOTE)
    bars_meta = _meta(
        DataCategory.MARKET_OHLCV,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
    )

    quote_router = _RecordingRouter(success_value=quote, meta=quote_meta)
    svc = _data_service(quote_router)
    q_result = await svc.get_quote(instrument, AS_OF)
    assert q_result.ok is True
    assert q_result.value == quote
    q_call = quote_router.calls[0]
    assert q_call["market"] is Market.US
    assert q_call["category"] is DataCategory.MARKET_QUOTE
    assert q_call["operation_name"] == OP_QUOTE
    assert q_call["request_fingerprint"] == build_us_fingerprint(
        OP_QUOTE, instrument.instrument_id, {}, AS_OF
    )
    assert q_call["cache_codec"] is svc._quote_codec
    assert q_call["result_validator"] is not None

    bars_router = _RecordingRouter(success_value=series, meta=bars_meta)
    svc_b = _data_service(bars_router)
    start, end = date(2026, 7, 17), date(2026, 7, 17)
    b_result = await svc_b.get_bars(
        instrument,
        start=start,
        end=end,
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        as_of=AS_OF,
    )
    assert b_result.ok is True
    assert b_result.value == series
    b_call = bars_router.calls[0]
    assert b_call["category"] is DataCategory.MARKET_OHLCV
    assert b_call["operation_name"] == OP_BARS
    assert b_call["request_fingerprint"] == build_us_fingerprint(
        OP_BARS,
        instrument.instrument_id,
        {
            "adjustment": AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED.value,
            "end": end.isoformat(),
            "interval": USBarInterval.ONE_DAY.value,
            "start": start.isoformat(),
        },
        AS_OF,
    )

    # Validator rejects category/instrument mismatch without needing engine.
    with pytest.raises(DataContractError, match="MARKET_QUOTE"):
        svc._validate_quote(
            ProviderSuccess(value=quote, meta=_meta(DataCategory.MARKET_OHLCV)),
            instrument=instrument,
            as_of=AS_OF,
        )
    with pytest.raises(DataContractError, match="instrument_id"):
        svc._validate_quote(
            ProviderSuccess(value=_quote("equity:US:AAPL"), meta=quote_meta),
            instrument=instrument,
            as_of=AS_OF,
        )
    with pytest.raises(DataContractError, match="end"):
        svc_b._validate_bars(
            ProviderSuccess(
                value=_series(end=date(2026, 7, 16)),
                meta=bars_meta,
            ),
            instrument=instrument,
            start=start,
            end=end,
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_invalid_input_rejected_before_router() -> None:
    router = _RecordingRouter(success_value=_quote(), meta=_meta())
    svc = _data_service(router)
    a_share = Instrument(
        instrument_id="equity:A_SHARE:600519",
        symbol="600519",
        name="Moutai",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )
    with pytest.raises(DataContractError, match="US"):
        await svc.get_quote(a_share, AS_OF)
    assert router.calls == []

    with pytest.raises(DataContractError, match="date"):
        await svc.get_bars(
            _us_equity(),
            start=datetime(2026, 7, 1, tzinfo=UTC),  # type: ignore[arg-type]
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
            as_of=AS_OF,
        )
    assert router.calls == []

    with pytest.raises(DataContractError, match="end must be"):
        await svc.get_bars(
            _us_equity(),
            start=date(2026, 7, 17),
            end=date(2026, 7, 1),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
            as_of=AS_OF,
        )
    assert router.calls == []


@pytest.mark.asyncio
async def test_context_all_proxies_success_change_percent() -> None:
    spy, qqq, iwm = _us_etf("SPY"), _us_etf("QQQ"), _us_etf("IWM")
    results = {
        "etf:US:SPY": _ok_result(
            _quote("etf:US:SPY", last=D("500"), previous_close=D("400")),
            _meta(),
        ),
        "etf:US:QQQ": _ok_result(
            _quote("etf:US:QQQ", last=D("440"), previous_close=D("400")),
            _meta(),
        ),
        "etf:US:IWM": _ok_result(
            _quote("etf:US:IWM", last=D("200"), previous_close=D("200")),
            _meta(),
        ),
    }
    data = _FakeDataService(results)
    ctx_svc = USMarketContextService(
        data,  # type: ignore[arg-type]
        _FakeMaster({"etf:US:SPY": spy, "etf:US:QQQ": qqq, "etf:US:IWM": iwm}),  # type: ignore[arg-type]
        FixedClock(NOW),
    )
    ctx = await ctx_svc.get_context(AS_OF)
    assert ctx.as_of is AS_OF
    assert ctx.spy.latest == D("500")
    assert ctx.spy.change_percent == D("25")
    assert ctx.qqq.change_percent == D("10")
    assert ctx.iwm.change_percent == D("0")
    assert ctx.advancing_count is None and ctx.declining_count is None
    assert ctx.warning_codes == (
        "US_BREADTH_UNAVAILABLE",
        "US_SECTOR_ROTATION_UNAVAILABLE",
    )
    assert set(data.calls) == {"etf:US:SPY", "etf:US:QQQ", "etf:US:IWM"}


@pytest.mark.asyncio
async def test_context_includes_provider_breadth_and_sector_rotation() -> None:
    instruments = {f"etf:US:{symbol}": _us_etf(symbol) for symbol in ("SPY", "QQQ", "IWM")}
    results = {
        instrument_id: _ok_result(
            _quote(instrument_id, last=D("100"), previous_close=D("99")), _meta()
        )
        for instrument_id in instruments
    }
    sector = USSectorRotation(
        sector="technology",
        index_symbol="^YH311",
        return_1d=D("1.2"),
        return_5d=D("2.3"),
        return_20d=D("4.5"),
        relative_spy_20d=D("1.1"),
    )
    breadth = USBreadthSnapshot(
        observed_at=AS_OF,
        advancing_count=1400,
        declining_count=1100,
        unchanged_count=80,
        basis="YAHOO_SCREENER_AND_SECTOR_INDEXES",
        universe="Yahoo US listed securities; may include ETFs and ADRs",
        sector_rotation=(sector,),
    )
    service = USMarketContextService(
        _FakeDataService(results),  # type: ignore[arg-type]
        _FakeMaster(instruments),  # type: ignore[arg-type]
        FixedClock(NOW),
        breadth_service=_FakeBreadthService(breadth),  # type: ignore[arg-type]
    )

    result = await service.get_context_result(AS_OF)

    assert result.context.advancing_count == 1400
    assert result.context.declining_count == 1100
    assert result.context.unchanged_count == 80
    assert result.context.breadth_basis == "YAHOO_SCREENER_AND_SECTOR_INDEXES"
    assert result.context.sector_rotation == (sector,)
    assert "US_BREADTH_UNAVAILABLE" not in result.context.warning_codes
    assert any(meta.category is DataCategory.MARKET_BREADTH for meta in result.metas)


@pytest.mark.asyncio
async def test_context_one_proxy_failure_degrades() -> None:
    spy, qqq, iwm = _us_etf("SPY"), _us_etf("QQQ"), _us_etf("IWM")
    results = {
        "etf:US:SPY": _ok_result(
            _quote("etf:US:SPY", last=D("500"), previous_close=D("400")),
            _meta(),
        ),
        "etf:US:QQQ": _fail_result(),
        "etf:US:IWM": _ok_result(
            _quote("etf:US:IWM", last=D("200"), previous_close=None),
            _meta(),
        ),
    }
    ctx_svc = USMarketContextService(
        _FakeDataService(results),  # type: ignore[arg-type]
        _FakeMaster({"etf:US:SPY": spy, "etf:US:QQQ": qqq, "etf:US:IWM": iwm}),  # type: ignore[arg-type]
        FixedClock(NOW),
    )
    ctx = await ctx_svc.get_context(AS_OF)
    assert ctx.spy.latest == D("500")
    assert ctx.qqq.latest is None and ctx.qqq.change_percent is None
    assert ctx.iwm.latest == D("200") and ctx.iwm.change_percent is None
    assert "PROXY_QQQ_UNAVAILABLE" in ctx.warning_codes
    assert "PROXY_IWM_CHANGE_UNAVAILABLE" in ctx.warning_codes
    assert "US_BREADTH_UNAVAILABLE" in ctx.warning_codes
    assert len(ctx.warning_codes) == len(set(ctx.warning_codes))


@pytest.mark.asyncio
async def test_context_missing_seed_degrades_without_quote_call() -> None:
    spy, iwm = _us_etf("SPY"), _us_etf("IWM")
    data = _FakeDataService(
        {
            "etf:US:SPY": _ok_result(
                _quote("etf:US:SPY", last=D("500"), previous_close=D("500")),
                _meta(),
            ),
            "etf:US:IWM": _ok_result(
                _quote("etf:US:IWM", last=D("200"), previous_close=D("200")),
                _meta(),
            ),
        }
    )
    ctx_svc = USMarketContextService(
        data,  # type: ignore[arg-type]
        _FakeMaster({"etf:US:SPY": spy, "etf:US:QQQ": None, "etf:US:IWM": iwm}),  # type: ignore[arg-type]
        FixedClock(NOW),
    )
    ctx = await ctx_svc.get_context(AS_OF)
    assert ctx.qqq.latest is None
    assert "PROXY_QQQ_UNAVAILABLE" in ctx.warning_codes
    assert "etf:US:QQQ" not in data.calls
    assert set(data.calls) == {"etf:US:SPY", "etf:US:IWM"}


@pytest.mark.asyncio
async def test_no_future_as_of() -> None:
    router = _RecordingRouter(success_value=_quote(), meta=_meta())
    svc = _data_service(router)
    future = NOW + timedelta(minutes=1)
    with pytest.raises(DataContractError, match="future"):
        await svc.get_quote(_us_equity(), future)
    with pytest.raises(DataContractError, match="future"):
        await svc.get_bars(
            _us_equity(),
            start=date(2026, 7, 1),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
            as_of=future,
        )
    assert router.calls == []

    ctx_svc = USMarketContextService(
        _FakeDataService({}),  # type: ignore[arg-type]
        _FakeMaster({}),  # type: ignore[arg-type]
        FixedClock(NOW),
    )
    with pytest.raises(DataContractError, match="future"):
        await ctx_svc.get_context(future)
