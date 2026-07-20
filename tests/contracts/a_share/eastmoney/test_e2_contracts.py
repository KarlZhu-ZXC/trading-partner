"""Eastmoney E2 fixture contracts for all six operations."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from a_share_fixture_transport import (
    FixtureHttpTransport,
    ScriptedHttpTransport,
    market_board_contract_drift_scripted,
    market_board_no_data_scripted,
    market_board_success_scripted,
)
from application.ports.http_transport import HttpResponse
from conftest import FixedClock
from domain.a_share.enums import BarInterval
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    DataCategory,
    Freshness,
    Market,
    VendorId,
)
from domain.common.errors import (
    CalendarOutOfRange,
    DataContractError,
    NoMarketData,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.eastmoney import (
    EASTMONEY_A_SHARE_EQUITY_FS,
    EASTMONEY_INDUSTRY_BOARD_FS,
    EastmoneyAShareAdapter,
)
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)  # 15:00 Shanghai
TRADE_DATE = date(2024, 1, 16)
_CALENDAR_PATH = Path(__file__).resolve().parents[4] / "config" / "a_share_trading_calendar.v1.json"
_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
    / "eastmoney"
)
_JSON_HEADERS = {"content-type": "application/json; charset=utf-8"}


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="贵州茅台",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )


def _gate():
    async def no_sleep(_dt: float) -> None:
        return None

    return create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.001,
        jitter_seconds=0.0,
        clock=lambda: 0.0,
        sleep=no_sleep,
        random_func=lambda: 0.0,
    )


def _calendar() -> JsonAShareTradingCalendar:
    return JsonAShareTradingCalendar.load(_CALENDAR_PATH)


def _adapter(
    operation: str, case: str, *, clock: FixedClock | None = None
) -> tuple[EastmoneyAShareAdapter, FixtureHttpTransport]:
    transport = FixtureHttpTransport(vendor="eastmoney", operation=operation, case=case)
    return (
        EastmoneyAShareAdapter(
            transport,
            _gate(),
            calendar=_calendar(),
            clock=clock or FixedClock(AS_OF),
            max_fresh_seconds=60,
            max_delayed_seconds=3600,
        ),
        transport,
    )


@pytest.mark.parametrize("value", [-1, True, 1.0])
def test_current_window_seconds_requires_nonnegative_exact_int(value: object) -> None:
    with pytest.raises(DataContractError) as exc:
        EastmoneyAShareAdapter(
            FixtureHttpTransport(vendor="eastmoney", operation="quote", case="success"),
            _gate(),
            calendar=_calendar(),
            clock=FixedClock(AS_OF),
            current_window_seconds=value,  # type: ignore[arg-type]
        )
    assert exc.value.details == {
        "field": "current_window_seconds",
        "rule": "nonnegative",
    }


def test_a_share_equity_fs_inventory() -> None:
    """Frozen complete A-share filter (exact string + segment inventory).

    Live probing proved standalone ``m:0+t:81`` / ``m:0+t:7`` return a mixed
    ~12,445-row bag that exceeds the hard ceiling. BSE is restricted via
    ``m:0+t:81+s:2048``; SZSE main segment covers GEM membership under the
    selected Eastmoney board layout.
    """
    assert EASTMONEY_A_SHARE_EQUITY_FS == ("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048")
    parts = EASTMONEY_A_SHARE_EQUITY_FS.split(",")
    assert parts == [
        "m:0+t:6",  # SZSE main (GEM coverage via selected Eastmoney segment)
        "m:0+t:80",  # SZSE SME segment
        "m:1+t:2",  # SSE main
        "m:1+t:23",  # STAR
        "m:0+t:81+s:2048",  # BSE restricted by s:2048
    ]
    # Explicitly forbid the broken standalone segments.
    assert "m:0+t:81" not in parts
    assert "m:0+t:7" not in parts
    assert EASTMONEY_INDUSTRY_BOARD_FS == "m:90+t:2"
    # Must not be the incomplete Shenzhen-only filter.
    assert EASTMONEY_A_SHARE_EQUITY_FS != "m:0+t:6,m:0+t:80"


@pytest.mark.asyncio
async def test_quote_success() -> None:
    clock = FixedClock(AS_OF, step_seconds=1)
    adapter, _ = _adapter("quote", "success", clock=clock)
    result = await adapter.get_quote(_instrument(), AS_OF)
    assert result.value.last == Decimal("1680.50")
    assert result.value.volume_shares == 12_345_600  # lots 123456 * 100
    assert result.meta.vendor is VendorId.EASTMONEY
    # fetched_at sampled after transport (clock advances on each now()).
    assert result.meta.fetched_at > AS_OF
    assert result.meta.data_delay_seconds is not None
    assert result.meta.data_delay_seconds > 0
    assert type(result.value.last) is Decimal


@pytest.mark.asyncio
async def test_quote_no_data() -> None:
    adapter, _ = _adapter("quote", "no_data")
    with pytest.raises(NoMarketData):
        await adapter.get_quote(_instrument(), AS_OF)


@pytest.mark.asyncio
async def test_quote_identity_mismatch() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="quote",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"f43":"10.00","f47":"1","f57":"000001",'
            b'"f86":"1705386605","f46":"10","f44":"11","f45":"9","f60":"10"}}'
        ),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    assert exc.value.details.get("rule") == "identity_mismatch"


@pytest.mark.asyncio
async def test_empty_object_is_contract_drift_not_no_data() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="quote",
        case="success",
        body_override=b"{}",
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    assert exc.value.details.get("rule") == "contract_drift"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adjustment", "expected_fqt"),
    [
        (AdjustmentMethod.NONE, "0"),
        (AdjustmentMethod.FORWARD_ADJUSTED, "1"),
        (AdjustmentMethod.BACKWARD_ADJUSTED, "2"),
    ],
)
async def test_bars_adjustment_mapping(adjustment: AdjustmentMethod, expected_fqt: str) -> None:
    adapter, transport = _adapter("bars", "success")
    result = await adapter.get_bars(
        _instrument(),
        start=date(2024, 1, 15),
        end=date(2024, 1, 16),
        interval=BarInterval.ONE_DAY,
        adjustment=adjustment,
        as_of=AS_OF,
    )
    assert result.value
    assert result.value[0].adjustment is adjustment
    assert result.value[0].volume_shares == 10_000_000  # 100000 lots * 100
    assert result.value[1].volume_shares == 12_345_600
    assert transport.requests[0].params["fqt"] == expected_fqt
    for bar in result.value:
        assert bar.end_at <= AS_OF
        assert bar.interval is BarInterval.ONE_DAY
        assert type(bar.close) is Decimal


@pytest.mark.asyncio
async def test_bars_daily_session_bounds() -> None:
    adapter, _ = _adapter("bars", "success")
    result = await adapter.get_bars(
        _instrument(),
        start=date(2024, 1, 16),
        end=date(2024, 1, 16),
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert len(result.value) == 1
    bar = result.value[0]
    # Calendar regular sessions: 09:30–11:30, 13:00–15:00 Shanghai
    assert bar.start_at.hour == 9 and bar.start_at.minute == 30
    assert bar.end_at.hour == 15 and bar.end_at.minute == 0


@pytest.mark.asyncio
async def test_bars_weekly_monthly_bounds_not_fixed_days() -> None:
    # Weekly stamp 2024-01-19 (Friday open day); week start Mon 2024-01-15.
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2024-01-19,10,11,12,9,100,1000.00"]}}'
        ),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    # as_of after 2024-01-19 close
    as_of = datetime(2024, 1, 19, 8, 0, tzinfo=UTC)
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(as_of),
    )
    weekly = await adapter.get_bars(
        _instrument(),
        start=date(2024, 1, 15),
        end=date(2024, 1, 19),
        interval=BarInterval.ONE_WEEK,
        adjustment=AdjustmentMethod.NONE,
        as_of=as_of,
    )
    assert len(weekly.value) == 1
    w = weekly.value[0]
    assert w.start_at.date() == date(2024, 1, 15)
    assert w.end_at.date() == date(2024, 1, 19)
    assert (w.end_at - w.start_at).days != 7 or w.end_at.hour == 15

    monthly_body = (
        b'{"rc":0,"rt":0,"data":{"code":"600519","klines":["2024-01-31,10,11,12,9,100,1000.00"]}}'
    )
    transport2 = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=monthly_body,
    )
    as_of_m = datetime(2024, 1, 31, 8, 0, tzinfo=UTC)
    adapter2 = EastmoneyAShareAdapter(
        transport2, _gate(), calendar=_calendar(), clock=FixedClock(as_of_m)
    )
    monthly = await adapter2.get_bars(
        _instrument(),
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        interval=BarInterval.ONE_MONTH,
        adjustment=AdjustmentMethod.NONE,
        as_of=as_of_m,
    )
    assert len(monthly.value) == 1
    m = monthly.value[0]
    assert m.start_at.date() == date(2024, 1, 2)  # first open day in Jan
    assert m.end_at.date() == date(2024, 1, 31)
    # Must not be naive +30 days approximation from stamp.
    assert m.end_at.month == 1


@pytest.mark.asyncio
async def test_bars_unfinished_period_cutoff() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2024-01-16,10,11,12,9,100,1000.00"]}}'
        ),
    )
    # as_of before session close → bar filtered out → NoMarketData
    early = datetime(2024, 1, 16, 2, 0, tzinfo=UTC)  # 10:00 Shanghai
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(NoMarketData):
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 16),
            end=date(2024, 1, 16),
            interval=BarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=early,
        )


@pytest.mark.asyncio
async def test_bars_no_data() -> None:
    adapter, _ = _adapter("bars", "no_data")
    with pytest.raises(NoMarketData):
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 15),
            end=date(2024, 1, 16),
            interval=BarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_order_book_success_and_cases() -> None:
    adapter, _ = _adapter("order_book", "success")
    result = await adapter.get_order_book(_instrument(), AS_OF)
    assert len(result.value) == 5
    assert result.value[0].bid_price == Decimal("1680.00")
    assert result.value[0].bid_volume_shares == 100  # 1 lot * 100

    adapter_nd, _ = _adapter("order_book", "no_data")
    with pytest.raises(NoMarketData):
        await adapter_nd.get_order_book(_instrument(), AS_OF)

    adapter_cd, _ = _adapter("order_book", "contract_drift")
    with pytest.raises(DataContractError):
        await adapter_cd.get_order_book(_instrument(), AS_OF)

    adapter_rl, _ = _adapter("order_book", "rate_limit")
    with pytest.raises(ProviderRateLimitError):
        await adapter_rl.get_order_book(_instrument(), AS_OF)


@pytest.mark.asyncio
async def test_ticks_success_empty_and_cases() -> None:
    adapter, _ = _adapter("ticks", "success")
    result = await adapter.get_ticks(_instrument(), limit=10, as_of=AS_OF)
    assert len(result.value) == 3
    assert result.value[0].volume_shares == 200  # 2 lots * 100
    assert type(result.value[0].price) is Decimal

    adapter_empty, _ = _adapter("ticks", "no_data")
    empty = await adapter_empty.get_ticks(_instrument(), limit=10, as_of=AS_OF)
    assert empty.value == ()

    adapter_cd, _ = _adapter("ticks", "contract_drift")
    with pytest.raises(DataContractError):
        await adapter_cd.get_ticks(_instrument(), limit=10, as_of=AS_OF)

    adapter_rl, _ = _adapter("ticks", "rate_limit")
    with pytest.raises(ProviderRateLimitError):
        await adapter_rl.get_ticks(_instrument(), limit=10, as_of=AS_OF)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["order_book", "ticks"])
async def test_market_structure_current_window_is_not_max_delayed(
    operation: str,
) -> None:
    transport = FixtureHttpTransport(vendor="eastmoney", operation=operation, case="success")
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        current_window_seconds=60,
        max_fresh_seconds=15,
        max_delayed_seconds=3600,
    )
    stale_as_of = AS_OF - timedelta(seconds=61)
    with pytest.raises(StaleMarketData) as exc:
        if operation == "order_book":
            await adapter.get_order_book(_instrument(), stale_as_of)
        else:
            await adapter.get_ticks(_instrument(), limit=10, as_of=stale_as_of)
    assert exc.value.details.get("rule") == "current_window"
    assert exc.value.details.get("window_seconds") == 60
    assert transport.requests == []


@pytest.mark.asyncio
async def test_quote_freshness_still_uses_max_delayed_not_current_window() -> None:
    transport = FixtureHttpTransport(vendor="eastmoney", operation="quote", case="success")
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF + timedelta(seconds=60)),
        current_window_seconds=0,
        max_fresh_seconds=0,
        max_delayed_seconds=3600,
    )
    result = await adapter.get_quote(_instrument(), AS_OF)
    assert result.meta.freshness is Freshness.DELAYED


@pytest.mark.asyncio
async def test_industry_performance_cases() -> None:
    adapter, _ = _adapter("industry_performance", "success")
    result = await adapter.get_industry_performance(trade_date=TRADE_DATE, limit=20, as_of=AS_OF)
    assert len(result.value) == 2
    assert result.value[0].industry_code == "BK0477"

    adapter_nd, _ = _adapter("industry_performance", "no_data")
    with pytest.raises(NoMarketData):
        await adapter_nd.get_industry_performance(trade_date=TRADE_DATE, limit=20, as_of=AS_OF)

    adapter_cd, _ = _adapter("industry_performance", "contract_drift")
    with pytest.raises(DataContractError):
        await adapter_cd.get_industry_performance(trade_date=TRADE_DATE, limit=20, as_of=AS_OF)

    adapter_rl, _ = _adapter("industry_performance", "rate_limit")
    with pytest.raises(ProviderUnavailableError):
        await adapter_rl.get_industry_performance(trade_date=TRADE_DATE, limit=20, as_of=AS_OF)


@pytest.mark.asyncio
async def test_market_board_success_multi_endpoint() -> None:
    transport = market_board_success_scripted()
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    result = await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
    # equity rows: +1.25, -0.50, 0, +2.10, +0.80, -1.20 → adv 3, dec 2, unch 1
    assert result.value.advancing_count == 3
    assert result.value.declining_count == 2
    assert result.value.unchanged_count == 1
    assert result.value.limit_up_count == 45
    assert result.value.limit_down_count == 12
    assert result.value.broken_limit_count == 8
    assert result.value.industries[0].industry_name == "白酒"
    assert len(transport.requests) == 5
    paths = [r.url for r in transport.requests]
    assert any("clist" in p for p in paths)
    assert any("getTopicZTPool" in p for p in paths)
    assert any("getTopicDTPool" in p for p in paths)
    assert any("getTopicZBPool" in p for p in paths)
    equity_req = transport.requests[0]
    assert equity_req.params["fs"] == EASTMONEY_A_SHARE_EQUITY_FS
    assert int(equity_req.params["pz"]) >= 5000
    # Pool contracts: public routing params (ut is static routing, not a credential).
    for req in transport.requests[1:4]:
        assert "date" in req.params
        assert req.params["date"] == "20240116"
        assert req.params["ut"] == "7eea3edcaed734bea9cbfc24409ed989"
        assert req.params["dpt"] == "wz.ztzt"
        assert req.params["Pageindex"] == "0"
        assert req.params["pagesize"] == "10000"
        assert "sort" in req.params
    assert result.meta.freshness is Freshness.UNKNOWN


@pytest.mark.asyncio
async def test_market_board_no_data() -> None:
    transport = market_board_no_data_scripted()
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(NoMarketData):
        await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)


@pytest.mark.asyncio
async def test_market_board_contract_drift() -> None:
    transport = market_board_contract_drift_scripted()
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError):
        await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)


def test_supports_and_configured() -> None:
    adapter, _ = _adapter("quote", "success")
    assert adapter.supports(Market.A_SHARE, DataCategory.MARKET_QUOTE)
    assert adapter.supports(Market.A_SHARE, DataCategory.MARKET_OHLCV)
    assert adapter.supports(Market.A_SHARE, DataCategory.MARKET_STRUCTURE)
    assert not adapter.supports(Market.US, DataCategory.MARKET_QUOTE)
    assert adapter.is_configured() is True


@pytest.mark.asyncio
async def test_every_eastmoney_request_uses_gate() -> None:
    gate = _gate()
    transport = FixtureHttpTransport(vendor="eastmoney", operation="quote", case="success")
    adapter = EastmoneyAShareAdapter(transport, gate, calendar=_calendar(), clock=FixedClock(AS_OF))
    await adapter.get_quote(_instrument(), AS_OF)
    assert gate.max_observed_in_flight == 1
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_secrecy_no_raw_body_in_errors() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="quote",
        case="success",
        body_override=b'{"rc":0,"data":{"f43":"not-a-number-SECRETTOKEN","f86":1,"f57":"600519"}}',
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    blob = f"{exc.value.message}{exc.value.details}"
    assert "SECRETTOKEN" not in blob


# --- calendar fail-closed bars ------------------------------------------------


@pytest.mark.asyncio
async def test_bars_holiday_period_end_rejected() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2024-01-01,10,11,12,9,100,1000.00"]}}'
        ),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 1),
            end=date(2024, 1, 2),
            interval=BarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "non_trading_day"


@pytest.mark.asyncio
async def test_bars_weekend_period_end_rejected() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2024-01-06,10,11,12,9,100,1000.00"]}}'
        ),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 5),
            end=date(2024, 1, 8),
            interval=BarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "non_trading_day"


@pytest.mark.asyncio
async def test_bars_out_of_range_propagates_calendar_error() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2027-01-04,10,11,12,9,100,1000.00"]}}'
        ),
    )
    as_of = datetime(2026, 12, 31, 8, 0, tzinfo=UTC)
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(as_of)
    )
    with pytest.raises(CalendarOutOfRange):
        await adapter.get_bars(
            _instrument(),
            start=date(2026, 12, 1),
            end=date(2026, 12, 31),
            interval=BarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=as_of,
        )


@pytest.mark.asyncio
async def test_bars_weekly_malformed_period_end_rejected() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2024-01-06,10,11,12,9,100,1000.00"]}}'  # Saturday
        ),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 1),
            end=date(2024, 1, 8),
            interval=BarInterval.ONE_WEEK,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "non_trading_period_end"


@pytest.mark.asyncio
async def test_bars_intraday_non_trading_day_rejected() -> None:
    transport = FixtureHttpTransport(
        vendor="eastmoney",
        operation="bars",
        case="success",
        body_override=(
            b'{"rc":0,"rt":0,"data":{"code":"600519","klines":['
            b'"2024-01-01 10:30:00,10,11,12,9,100,1000.00"]}}'
        ),
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 1),
            end=date(2024, 1, 2),
            interval=BarInterval.ONE_MINUTE,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "non_trading_day"


# --- current-only clist policy ------------------------------------------------


@pytest.mark.asyncio
async def test_industry_after_close_today_allowed() -> None:
    adapter, transport = _adapter("industry_performance", "success")
    result = await adapter.get_industry_performance(trade_date=TRADE_DATE, limit=20, as_of=AS_OF)
    assert len(result.value) == 2
    assert result.meta.freshness is Freshness.UNKNOWN
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_industry_weekend_previous_day_allowed() -> None:
    # Saturday 2024-01-20 Shanghai after Friday close → supportable = 2024-01-19
    as_of = datetime(2024, 1, 20, 4, 0, tzinfo=UTC)  # 12:00 Shanghai Saturday
    transport = FixtureHttpTransport(
        vendor="eastmoney", operation="industry_performance", case="success"
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(as_of)
    )
    result = await adapter.get_industry_performance(
        trade_date=date(2024, 1, 19), limit=20, as_of=as_of
    )
    assert len(result.value) == 2
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_industry_during_session_rejected_zero_transport() -> None:
    # 2024-01-16 10:00 Shanghai = 02:00 UTC
    as_of = datetime(2024, 1, 16, 2, 0, tzinfo=UTC)
    transport = FixtureHttpTransport(
        vendor="eastmoney", operation="industry_performance", case="success"
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(as_of)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_industry_performance(trade_date=date(2024, 1, 15), limit=20, as_of=as_of)
    assert exc.value.details.get("rule") == "current_cross_section_in_session"
    assert transport.requests == []
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_market_board_arbitrary_historical_rejected_zero_transport() -> None:
    transport = market_board_success_scripted()
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_market_board(trade_date=date(2024, 1, 2), as_of=AS_OF)
    assert exc.value.details.get("rule") == "current_only_trade_date"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_market_board_future_trade_date_rejected() -> None:
    """trade_date after Shanghai local date of as_of is rejected before network.

    With as_of on the closed session day, a later calendar trade_date hits the
    as_of-local rule first (defense in depth before current-only matching).
    """
    transport = market_board_success_scripted()
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_market_board(trade_date=date(2024, 1, 17), as_of=AS_OF)
    assert exc.value.details.get("rule") == "trade_date_not_after_as_of_local"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_market_board_wrong_supportable_day_rejected_zero_transport() -> None:
    """Same as_of local day but not the supportable closed session → current_only."""
    # Saturday 2024-01-20: supportable = Fri 2024-01-19; requesting Sat is wrong
    # but not later than as_of's local date, so current_only rule fires.
    as_of = datetime(2024, 1, 20, 8, 0, tzinfo=UTC)  # 16:00 Shanghai Saturday
    transport = market_board_success_scripted()
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(as_of)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_market_board(trade_date=date(2024, 1, 20), as_of=as_of)
    assert exc.value.details.get("rule") == "current_only_trade_date"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_industry_stale_as_of_rejected_zero_transport() -> None:
    """Stale as_of relative to current_window_seconds fails before network."""
    clock = FixedClock(AS_OF)
    transport = FixtureHttpTransport(
        vendor="eastmoney", operation="industry_performance", case="success"
    )
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=clock,
        current_window_seconds=120,
        max_fresh_seconds=15,
        max_delayed_seconds=120,
    )
    # Clock is AS_OF; as_of is 3 minutes older → exceeds 120s current window.
    stale_as_of = AS_OF - timedelta(minutes=3)
    with pytest.raises(StaleMarketData) as exc:
        await adapter.get_industry_performance(trade_date=TRADE_DATE, limit=20, as_of=stale_as_of)
    assert exc.value.details.get("rule") == "current_window"
    assert "window_seconds" in exc.value.details
    assert transport.requests == []
    # Secret-free details.
    blob = str(exc.value.details)
    assert "token" not in blob.lower()
    assert "authorization" not in blob.lower()


@pytest.mark.asyncio
async def test_market_board_stale_as_of_rejected_zero_transport() -> None:
    clock = FixedClock(AS_OF)
    transport = market_board_success_scripted()
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=clock,
        current_window_seconds=120,
        max_fresh_seconds=15,
        max_delayed_seconds=120,
    )
    stale_as_of = AS_OF - timedelta(minutes=5)
    with pytest.raises(StaleMarketData) as exc:
        await adapter.get_market_board(trade_date=TRADE_DATE, as_of=stale_as_of)
    assert exc.value.details.get("rule") == "current_window"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_industry_trade_date_later_than_as_of_local_zero_transport() -> None:
    """trade_date after Shanghai local date of as_of fails before network.

    Keep as_of within max_delayed_seconds of clock so the trade_date rule is
    the one that fires (not the stale window).
    """
    # Clock slightly after as_of so as_of is not in the future; both map to
    # Shanghai 2024-01-15 (as_of 15:30 SH / clock 15:31 SH).
    as_of = datetime(2024, 1, 15, 7, 30, tzinfo=UTC)  # 15:30 Shanghai 01-15
    clock_now = as_of + timedelta(seconds=60)
    transport = FixtureHttpTransport(
        vendor="eastmoney", operation="industry_performance", case="success"
    )
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(clock_now),
        max_fresh_seconds=15,
        max_delayed_seconds=3600,
    )
    # Requested trade_date is the next calendar day after as_of's local day.
    with pytest.raises(DataContractError) as exc:
        await adapter.get_industry_performance(trade_date=date(2024, 1, 16), limit=20, as_of=as_of)
    assert exc.value.details.get("rule") == "trade_date_not_after_as_of_local"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_market_board_trade_date_later_than_as_of_local_zero_transport() -> None:
    as_of = datetime(2024, 1, 15, 7, 30, tzinfo=UTC)  # 15:30 Shanghai 01-15
    clock_now = as_of + timedelta(seconds=60)
    transport = market_board_success_scripted()
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(clock_now),
        max_fresh_seconds=15,
        max_delayed_seconds=3600,
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_market_board(trade_date=date(2024, 1, 16), as_of=as_of)
    assert exc.value.details.get("rule") == "trade_date_not_after_as_of_local"
    assert transport.requests == []


# --- pagination adversarial ---------------------------------------------------


def _clist_body(total: int, codes: list[str]) -> bytes:
    diff = [
        {
            "f12": code,
            "f14": f"N{code}",
            "f2": "10.00",
            "f3": "1.00",
            "f6": "1000.00",
        }
        for code in codes
    ]
    return json.dumps(
        {"rc": 0, "rt": 0, "data": {"total": total, "diff": diff}},
        separators=(",", ":"),
    ).encode()


def _pool_body(tc: int = 0) -> bytes:
    return json.dumps(
        {"rc": 0, "rt": 0, "data": {"tc": tc, "pool": []}},
        separators=(",", ":"),
    ).encode()


def _industry_body() -> bytes:
    return (_FIXTURE_ROOT / "market_board" / "success_industry.json").read_bytes()


def _hr(body: bytes) -> HttpResponse:
    return HttpResponse(status_code=200, headers=dict(_JSON_HEADERS), body=body)


@pytest.mark.asyncio
async def test_market_board_multi_page_aggregation() -> None:
    # Page size 5000; declare total 3 across two small pages by using custom page
    # sizes via monkeypatch of constants is heavy — instead return total=3 with
    # page1 2 rows then page2 1 row by temporarily using small pz via...
    # We can't change page size easily; simulate with total matching first page
    # and a second industry page only. Multi-page equity: use Scripted with
    # total larger than first page by patching adapter constants.
    from infrastructure.providers.a_share import eastmoney as em_mod

    original = em_mod._CLIST_EQUITY_PAGE_SIZE
    em_mod._CLIST_EQUITY_PAGE_SIZE = 2
    try:
        transport = ScriptedHttpTransport(
            responses=[
                _hr(_clist_body(3, ["600519", "000001"])),
                _hr(_clist_body(3, ["600036"])),
                _hr(_pool_body(1)),
                _hr(_pool_body(0)),
                _hr(_pool_body(0)),
                _hr(_industry_body()),
            ]
        )
        adapter = EastmoneyAShareAdapter(
            transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
        )
        result = await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
        assert result.value.advancing_count == 3
        # equity pages 2 + pools 3 + industry 1
        assert len(transport.requests) == 6
        assert transport.requests[0].params["pn"] == "1"
        assert transport.requests[1].params["pn"] == "2"
    finally:
        em_mod._CLIST_EQUITY_PAGE_SIZE = original


@pytest.mark.asyncio
async def test_market_board_truncated_page_fail_closed() -> None:
    from infrastructure.providers.a_share import eastmoney as em_mod

    original = em_mod._CLIST_EQUITY_PAGE_SIZE
    em_mod._CLIST_EQUITY_PAGE_SIZE = 2
    try:
        transport = ScriptedHttpTransport(
            responses=[
                # total 4 but only 1 row and short page → truncated
                _hr(_clist_body(4, ["600519"])),
            ]
        )
        adapter = EastmoneyAShareAdapter(
            transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
        )
        with pytest.raises(DataContractError) as exc:
            await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
        assert exc.value.details.get("rule") == "truncated_page"
    finally:
        em_mod._CLIST_EQUITY_PAGE_SIZE = original


@pytest.mark.asyncio
async def test_market_board_duplicate_code_across_pages() -> None:
    from infrastructure.providers.a_share import eastmoney as em_mod

    original = em_mod._CLIST_EQUITY_PAGE_SIZE
    em_mod._CLIST_EQUITY_PAGE_SIZE = 2
    try:
        transport = ScriptedHttpTransport(
            responses=[
                _hr(_clist_body(3, ["600519", "000001"])),
                _hr(_clist_body(3, ["600519"])),  # duplicate
            ]
        )
        adapter = EastmoneyAShareAdapter(
            transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
        )
        with pytest.raises(DataContractError) as exc:
            await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
        assert exc.value.details.get("rule") == "duplicate_code"
    finally:
        em_mod._CLIST_EQUITY_PAGE_SIZE = original


@pytest.mark.asyncio
async def test_market_board_changing_total_across_pages() -> None:
    from infrastructure.providers.a_share import eastmoney as em_mod

    original = em_mod._CLIST_EQUITY_PAGE_SIZE
    em_mod._CLIST_EQUITY_PAGE_SIZE = 2
    try:
        transport = ScriptedHttpTransport(
            responses=[
                _hr(_clist_body(3, ["600519", "000001"])),
                _hr(_clist_body(4, ["600036"])),
            ]
        )
        adapter = EastmoneyAShareAdapter(
            transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
        )
        with pytest.raises(DataContractError) as exc:
            await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
        assert exc.value.details.get("rule") == "total_changed"
    finally:
        em_mod._CLIST_EQUITY_PAGE_SIZE = original


@pytest.mark.asyncio
async def test_market_board_empty_final_page_fail_closed() -> None:
    from infrastructure.providers.a_share import eastmoney as em_mod

    original = em_mod._CLIST_EQUITY_PAGE_SIZE
    em_mod._CLIST_EQUITY_PAGE_SIZE = 2
    try:
        transport = ScriptedHttpTransport(
            responses=[
                _hr(_clist_body(3, ["600519", "000001"])),
                _hr(_clist_body(3, [])),
            ]
        )
        adapter = EastmoneyAShareAdapter(
            transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
        )
        with pytest.raises(DataContractError) as exc:
            await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
        assert exc.value.details.get("rule") == "empty_page"
    finally:
        em_mod._CLIST_EQUITY_PAGE_SIZE = original


@pytest.mark.asyncio
async def test_market_board_max_total_fail_closed() -> None:
    from infrastructure.providers.a_share import eastmoney as em_mod

    original_max = em_mod._CLIST_EQUITY_MAX_TOTAL
    em_mod._CLIST_EQUITY_MAX_TOTAL = 5
    try:
        transport = ScriptedHttpTransport(responses=[_hr(_clist_body(6, ["600519", "000001"]))])
        adapter = EastmoneyAShareAdapter(
            transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
        )
        with pytest.raises(DataContractError) as exc:
            await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
        assert exc.value.details.get("rule") == "max_total_exceeded"
    finally:
        em_mod._CLIST_EQUITY_MAX_TOTAL = original_max


@pytest.mark.asyncio
async def test_market_board_total_zero_with_nonempty_diff_is_contract_error() -> None:
    """total=0 with non-empty diff must not silently return empty rows."""
    transport = ScriptedHttpTransport(responses=[_hr(_clist_body(0, ["600519"]))])
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
    assert exc.value.details.get("rule") == "total_zero_nonempty_diff"
    assert transport.requests  # one clist page attempted; then fail closed
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_pool_missing_rc_is_contract_drift() -> None:
    transport = ScriptedHttpTransport(
        responses=[
            _hr((_FIXTURE_ROOT / "market_board" / "success_equity.json").read_bytes()),
            _hr(b'{"data":{"tc":1,"pool":[]}}'),  # missing rc
            _hr(_pool_body(0)),
            _hr(_pool_body(0)),
            _hr(_industry_body()),
        ]
    )
    adapter = EastmoneyAShareAdapter(
        transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_market_board(trade_date=TRADE_DATE, as_of=AS_OF)
    assert exc.value.details.get("rule") == "contract_drift"


@pytest.mark.asyncio
async def test_calendar_required_at_construction() -> None:
    transport = FixtureHttpTransport(vendor="eastmoney", operation="quote", case="success")
    with pytest.raises(DataContractError):
        EastmoneyAShareAdapter(
            transport,
            _gate(),
            calendar=None,
            clock=FixedClock(AS_OF),  # type: ignore[arg-type]
        )
