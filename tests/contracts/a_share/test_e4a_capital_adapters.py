"""Phase 1E E4a capital adapter fixture contracts (offline, deterministic)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from a_share_fixture_transport import FixtureHttpTransport, ScriptedHttpTransport
from application.ports.http_transport import HttpResponse
from conftest import FixedClock
from domain.a_share.enums import BarInterval
from domain.common.enums import AssetType, DataCategory, Market, ReliabilityLevel, VendorId
from domain.common.errors import (
    CalendarOutOfRange,
    DataContractError,
    ProviderRateLimitError,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.exchanges import (
    SseAShareDisclosureAdapter,
    SzseAShareDisclosureAdapter,
)
from infrastructure.providers.a_share.hkex import HkexNorthboundAdapter
from infrastructure.providers.a_share.sina import SinaAShareAdapter
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
_CALENDAR_PATH = Path(__file__).resolve().parents[3] / "config" / "a_share_trading_calendar.v1.json"
_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
)
_JSON = {"content-type": "application/json; charset=utf-8"}


def _equity(symbol: str = "600519.SH", exchange: str = "SSE") -> Instrument:
    return Instrument(
        instrument_id=f"equity:A_SHARE:{symbol}",
        symbol=symbol,
        name="test",
        market=Market.A_SHARE,
        exchange=exchange,
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


def _em(operation: str, case: str) -> EastmoneyAShareAdapter:
    transport = FixtureHttpTransport(vendor="eastmoney", operation=operation, case=case)
    return EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


def _fixture_body(vendor: str, operation: str, case: str) -> bytes:
    return (_FIXTURE_ROOT / vendor / operation / f"{case}.json").read_bytes()


# --- Eastmoney fund flow ------------------------------------------------------


@pytest.mark.asyncio
async def test_em_intraday_flow_success() -> None:
    adapter = _em("intraday_flow", "success")
    result = await adapter.get_intraday_flow(_equity(), AS_OF)
    assert result.meta.vendor is VendorId.EASTMONEY
    assert result.meta.category is DataCategory.CAPITAL
    assert result.value
    assert all(p.interval is BarInterval.ONE_MINUTE for p in result.value)
    assert all(type(p.main_net_cny) is Decimal or p.main_net_cny is None for p in result.value)
    assert all(p.occurred_at <= AS_OF for p in result.value)
    # Sorted unique ascending
    times = [p.occurred_at for p in result.value]
    assert times == sorted(set(times))


@pytest.mark.asyncio
async def test_em_intraday_flow_no_data_empty_success() -> None:
    adapter = _em("intraday_flow", "no_data")
    result = await adapter.get_intraday_flow(_equity(), AS_OF)
    assert result.value == ()


@pytest.mark.asyncio
async def test_em_intraday_flow_contract_drift() -> None:
    adapter = _em("intraday_flow", "contract_drift")
    with pytest.raises(DataContractError):
        await adapter.get_intraday_flow(_equity(), AS_OF)


@pytest.mark.asyncio
async def test_em_intraday_flow_rate_limit() -> None:
    adapter = _em("intraday_flow", "rate_limit")
    with pytest.raises(ProviderRateLimitError):
        await adapter.get_intraday_flow(_equity(), AS_OF)


@pytest.mark.asyncio
async def test_em_daily_flow_success() -> None:
    adapter = _em("daily_flow", "success")
    result = await adapter.get_daily_flow(
        _equity(), start=date(2024, 1, 1), end=date(2024, 1, 16), as_of=AS_OF
    )
    assert result.value
    assert all(p.interval is BarInterval.ONE_DAY for p in result.value)
    assert all(not p.is_authoritative for p in result.value)


# --- Northbound / margin / block / shareholder / chip -------------------------


@pytest.mark.asyncio
async def test_em_northbound_success() -> None:
    adapter = _em("northbound", "success")
    result = await adapter.get_northbound(
        start=date(2024, 1, 15), end=date(2024, 1, 15), as_of=AS_OF
    )
    assert result.value
    channels = {p.channel for p in result.value}
    assert channels <= {"sh", "sz", "total", "connect"}
    assert "total" in channels
    assert all(p.is_authoritative is False for p in result.value)
    assert all(p.reliability is ReliabilityLevel.LOW for p in result.value)
    assert "NORTHBOUND_DISCLOSURE_INCOMPLETE" in result.meta.warnings


@pytest.mark.asyncio
async def test_em_dragon_tiger_success_scripted() -> None:
    transport = ScriptedHttpTransport(
        responses=[
            HttpResponse(
                status_code=200,
                headers=dict(_JSON),
                body=_fixture_body("eastmoney", "dragon_tiger", "success_summary"),
            ),
            HttpResponse(
                status_code=200,
                headers=dict(_JSON),
                body=_fixture_body("eastmoney", "dragon_tiger", "success_seats"),
            ),
        ]
    )
    adapter = EastmoneyAShareAdapter(
        transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
    )
    inst = _equity("002140.SZ", exchange="SZSE")
    result = await adapter.get_dragon_tiger(inst, trade_date=date(2024, 1, 15), as_of=AS_OF)
    assert len(result.value) == 1
    rec = result.value[0]
    assert rec.instrument_id == inst.instrument_id
    assert rec.net_buy_cny == rec.buy_total_cny - rec.sell_total_cny
    assert rec.seats
    assert all(s.side in {"buy", "sell"} for s in rec.seats)


@pytest.mark.asyncio
async def test_em_margin_success() -> None:
    adapter = _em("margin", "success")
    result = await adapter.get_margin(_equity(), limit=10, as_of=AS_OF)
    assert result.value
    assert all(type(r.financing_balance_cny) is Decimal for r in result.value)
    dates = [r.trade_date for r in result.value]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_em_block_trades_success() -> None:
    adapter = _em("block_trades", "success")
    result = await adapter.get_block_trades(_equity(), limit=10, as_of=AS_OF)
    assert result.value
    assert result.value[0].volume_shares > 0


@pytest.mark.asyncio
async def test_em_shareholder_success() -> None:
    adapter = _em("shareholder_counts", "success")
    result = await adapter.get_shareholder_counts(_equity(), limit=10, as_of=AS_OF)
    assert result.value
    assert all(r.published_at is None or r.published_at <= AS_OF for r in result.value)


@pytest.mark.asyncio
async def test_em_chip_calendar_coverage_fails_before_network() -> None:
    """Derived chip requires all 120 calendar sessions before network I/O."""

    class _CountingTransport:
        def __init__(self) -> None:
            self.requests: list[object] = []

        async def send(self, request: object) -> object:
            self.requests.append(request)
            raise AssertionError("chip distribution must not perform network I/O")

    transport = _CountingTransport()
    adapter = EastmoneyAShareAdapter(
        transport,  # type: ignore[arg-type]
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
    )
    with pytest.raises(CalendarOutOfRange):
        await adapter.get_chip_distribution(_equity(), AS_OF)
    assert transport.requests == []


# --- Sina daily flow fallback -------------------------------------------------

_SINA_DAILY_FLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"
)
_SINA_GBK_HEADERS = {"content-type": "application/json; charset=gbk"}


@pytest.mark.asyncio
async def test_sina_daily_flow_success() -> None:
    transport = FixtureHttpTransport(vendor="sina", operation="daily_flow", case="success")
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_daily_flow(
        _equity(), start=date(2024, 1, 1), end=date(2024, 1, 16), as_of=AS_OF
    )
    assert result.meta.vendor is VendorId.SINA
    assert len(result.value) == 2
    assert all(p.interval is BarInterval.ONE_DAY for p in result.value)
    # Live-shaped fixture omits r1/r2/r3 nets → None, never zero.
    for point in result.value:
        assert point.main_net_cny is not None
        assert point.super_large_net_cny is not None
        assert point.large_net_cny is None
        assert point.medium_net_cny is None
        assert point.small_net_cny is None
    assert transport.requests[0].url == _SINA_DAILY_FLOW_URL
    assert transport.requests[0].params.get("daima") == "sh600519"


@pytest.mark.asyncio
async def test_sina_daily_flow_gbk_live_shaped_bytes() -> None:
    """Declared charset=gbk body (live Content-Type) decodes before JSON parse."""
    body = (_FIXTURE_ROOT / "sina" / "daily_flow" / "success.json").read_bytes()
    # Re-encode as GBK to exercise the declared-charset boundary path.
    gbk_body = body.decode("utf-8").encode("gbk")
    transport = FixtureHttpTransport(
        vendor="sina",
        operation="daily_flow",
        case="success",
        body_override=gbk_body,
        headers=dict(_SINA_GBK_HEADERS),
    )
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_daily_flow(
        _equity(), start=date(2024, 1, 1), end=date(2024, 1, 16), as_of=AS_OF
    )
    assert len(result.value) == 2
    # Sorted ascending by occurred_at: 2024-01-15 then 2024-01-16.
    assert result.value[0].main_net_cny == Decimal("1954209782.6700")
    assert result.value[1].main_net_cny == Decimal("676276059.3300")
    assert result.value[1].super_large_net_cny == Decimal("481207077.7000")
    assert all(p.large_net_cny is None for p in result.value)
    assert all(p.medium_net_cny is None for p in result.value)
    assert all(p.small_net_cny is None for p in result.value)


@pytest.mark.asyncio
async def test_sina_daily_flow_invalid_old_error_envelope() -> None:
    """HTTP 200 error envelope (code=11 Service not valid) is contract drift."""
    secret = "Service not valid-leak-token-should-not-escape"
    body = b'{"code":11,"message":"Service not valid","detail":"' + secret.encode("utf-8") + b'"}'
    transport = FixtureHttpTransport(
        vendor="sina",
        operation="daily_flow",
        case="contract_drift",
        body_override=body,
        headers=dict(_SINA_GBK_HEADERS),
    )
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as ei:
        await adapter.get_daily_flow(_equity(), start=None, end=None, as_of=AS_OF)
    assert ei.value.details.get("rule") == "contract_drift"
    blob = ei.value.message + repr(ei.value.details)
    assert secret not in blob
    assert "Service not valid" not in blob
    assert "leak-token" not in blob


@pytest.mark.asyncio
async def test_sina_daily_flow_no_data() -> None:
    transport = FixtureHttpTransport(vendor="sina", operation="daily_flow", case="no_data")
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_daily_flow(_equity(), start=None, end=None, as_of=AS_OF)
    assert result.value == ()


@pytest.mark.asyncio
async def test_sina_daily_flow_wrong_content_type() -> None:
    transport = FixtureHttpTransport(
        vendor="sina",
        operation="daily_flow",
        case="success",
        headers={"content-type": "text/html; charset=utf-8"},
    )
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as ei:
        await adapter.get_daily_flow(_equity(), start=None, end=None, as_of=AS_OF)
    assert ei.value.details.get("rule") == "content_type"


@pytest.mark.asyncio
async def test_sina_daily_flow_rate_limit() -> None:
    transport = FixtureHttpTransport(vendor="sina", operation="daily_flow", case="rate_limit")
    adapter = SinaAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(ProviderRateLimitError):
        await adapter.get_daily_flow(_equity(), start=None, end=None, as_of=AS_OF)


# --- HKEX northbound primary --------------------------------------------------


def _hkex(case: str, *, headers: dict[str, str] | None = None) -> HkexNorthboundAdapter:
    transport = FixtureHttpTransport(
        vendor="hkex",
        operation="northbound",
        case=case,
        headers=headers or {"content-type": "application/javascript; charset=utf-8"},
    )
    return HkexNorthboundAdapter(transport, clock=FixedClock(AS_OF))


@pytest.mark.asyncio
async def test_hkex_northbound_success_tabdata_aggregate_only() -> None:
    adapter = _hkex("success")
    result = await adapter.get_northbound(
        start=date(2024, 1, 15), end=date(2024, 1, 15), as_of=AS_OF
    )
    assert result.meta.vendor is VendorId.HKEX
    assert "NORTHBOUND_DISCLOSURE_INCOMPLETE" in result.meta.warnings
    assert len(result.value) == 2
    channels = [p.channel for p in result.value]
    assert channels == ["sh", "sz"]  # sorted; no fabricated total
    for p in result.value:
        assert p.is_authoritative is True
        assert p.reliability is ReliabilityLevel.HIGH
        assert p.buy_cny is None
        assert p.sell_cny is None
        assert p.net_buy_cny is None
        assert p.disclosure_note is not None and p.disclosure_note.strip()
        # Turnover figures must not be mapped into flow fields.
        assert p.buy_cny != Decimal("132054.80")
        assert p.net_buy_cny is None


@pytest.mark.asyncio
async def test_hkex_northbound_ignores_southbound_and_no_total() -> None:
    adapter = _hkex("success")
    result = await adapter.get_northbound(
        start=date(2024, 1, 15), end=date(2024, 1, 15), as_of=AS_OF
    )
    channels = {p.channel for p in result.value}
    assert channels == {"sh", "sz"}
    assert "total" not in channels
    assert "connect" not in channels


@pytest.mark.asyncio
async def test_hkex_northbound_html_is_no_data_never_parsed() -> None:
    adapter = _hkex("html_page", headers={"content-type": "text/html; charset=utf-8"})
    result = await adapter.get_northbound(
        start=date(2024, 1, 15), end=date(2024, 1, 15), as_of=AS_OF
    )
    assert result.value == ()


@pytest.mark.asyncio
async def test_hkex_northbound_contract_drift() -> None:
    adapter = _hkex("contract_drift")
    with pytest.raises(DataContractError):
        await adapter.get_northbound(start=date(2024, 1, 15), end=date(2024, 1, 15), as_of=AS_OF)


# --- SSE / SZSE dragon tiger fallback ----------------------------------------


@pytest.mark.asyncio
async def test_sse_dragon_tiger_success() -> None:
    transport = FixtureHttpTransport(vendor="sse", operation="dragon_tiger", case="success")
    adapter = SseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_dragon_tiger(
        _equity("600519.SH", exchange="SSE"),
        trade_date=date(2024, 1, 15),
        as_of=AS_OF,
    )
    assert result.meta.vendor is VendorId.SSE
    assert result.value
    assert result.value[0].instrument_id == "equity:A_SHARE:600519.SH"


@pytest.mark.asyncio
async def test_sse_dragon_tiger_no_data() -> None:
    transport = FixtureHttpTransport(vendor="sse", operation="dragon_tiger", case="no_data")
    adapter = SseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    result = await adapter.get_dragon_tiger(_equity(), trade_date=date(2024, 1, 15), as_of=AS_OF)
    assert result.value == ()


@pytest.mark.asyncio
async def test_szse_dragon_tiger_success() -> None:
    transport = FixtureHttpTransport(vendor="szse", operation="dragon_tiger", case="success")
    adapter = SzseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    inst = _equity("002140.SZ", exchange="SZSE")
    result = await adapter.get_dragon_tiger(inst, trade_date=date(2024, 1, 15), as_of=AS_OF)
    assert result.meta.vendor is VendorId.SZSE
    assert result.value
    assert result.value[0].instrument_id == inst.instrument_id


def test_sina_does_not_claim_intraday() -> None:
    adapter = SinaAShareAdapter(
        FixtureHttpTransport(vendor="sina", operation="daily_flow", case="success")
    )
    assert not hasattr(adapter, "get_intraday_flow") or not callable(
        getattr(type(adapter), "get_intraday_flow", None)
    )


def test_hkex_supports_capital_only() -> None:
    adapter = HkexNorthboundAdapter(
        FixtureHttpTransport(
            vendor="hkex",
            operation="northbound",
            case="success",
            headers={"content-type": "application/javascript"},
        )
    )
    assert adapter.supports(Market.A_SHARE, DataCategory.CAPITAL)
    assert not adapter.supports(Market.A_SHARE, DataCategory.MARKET_QUOTE)
