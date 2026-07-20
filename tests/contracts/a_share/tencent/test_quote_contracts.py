"""Tencent quote fixture contracts: success / no-data / drift / rate-limit."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from a_share_fixture_transport import FixtureHttpTransport, ScriptedHttpTransport
from application.ports.http_transport import HttpResponse
from conftest import FixedClock
from domain.a_share.enums import BarInterval
from domain.common.enums import AdjustmentMethod, AssetType, DataCategory, Market, VendorId
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderRateLimitError,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.tencent import TencentAShareAdapter
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

# quote_at in fixture: 2024-01-16 14:30:05 Asia/Shanghai = 06:30:05 UTC
AS_OF = datetime(2024, 1, 16, 6, 30, 10, tzinfo=UTC)
SH = ZoneInfo("Asia/Shanghai")
_CALENDAR_PATH = Path(__file__).resolve().parents[4] / "config" / "a_share_trading_calendar.v1.json"


def _instrument(*, asset: AssetType = AssetType.EQUITY, symbol: str = "600519.SH") -> Instrument:
    return Instrument(
        instrument_id=f"{asset.value}:A_SHARE:{symbol}",
        symbol=symbol,
        name="测试标的",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=asset,
    )


def _adapter(
    case: str, *, clock: FixedClock | None = None
) -> tuple[TencentAShareAdapter, FixtureHttpTransport]:
    transport = FixtureHttpTransport(vendor="tencent", operation="quote", case=case)
    adapter = TencentAShareAdapter(
        transport,
        clock=clock or FixedClock(AS_OF),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    return adapter, transport


@pytest.mark.asyncio
async def test_tencent_quote_success() -> None:
    adapter, _ = _adapter("success")
    result = await adapter.get_quote(_instrument(), AS_OF)
    assert result.value.last == Decimal("1680.50")
    assert result.value.instrument_id == "equity:A_SHARE:600519.SH"
    assert result.meta.vendor is VendorId.TENCENT
    assert result.value.quote_at == datetime(2024, 1, 16, 14, 30, 5, tzinfo=SH)
    assert result.value.volume_shares == 12_345_600  # 123456 lots * 100
    assert result.value.turnover_amount_cny == Decimal("2074567800.00")
    assert result.value.float_market_cap_cny == Decimal("2100000000000.00")
    assert result.value.total_market_cap_cny == Decimal("2200000000000.00")
    assert result.meta.fetched_at == AS_OF
    assert result.meta.data_delay_seconds is not None
    assert result.meta.data_delay_seconds > 0
    assert type(result.value.last) is Decimal
    assert not isinstance(result.value.last, float)


@pytest.mark.asyncio
async def test_tencent_quote_no_data() -> None:
    adapter, _ = _adapter("no_data")
    with pytest.raises(NoMarketData):
        await adapter.get_quote(_instrument(), AS_OF)


@pytest.mark.asyncio
async def test_tencent_quote_contract_drift() -> None:
    adapter, _ = _adapter("contract_drift")
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    assert exc.value.details.get("rule") == "contract_drift"


@pytest.mark.asyncio
async def test_tencent_quote_rate_limit() -> None:
    adapter, _ = _adapter("rate_limit")
    with pytest.raises(ProviderRateLimitError):
        await adapter.get_quote(_instrument(), AS_OF)


@pytest.mark.asyncio
async def test_tencent_rejects_wrong_code_identity() -> None:
    transport = FixtureHttpTransport(
        vendor="tencent",
        operation="quote",
        case="success",
        body_override=(
            b'v_sh000001="1~PINGAN~000001~10.50~10.00~10.20~100~0~0~'
            b"~~~~~~~~~~"
            b'~20240116143005~0.50~5.00~11.00~10.00~10.50/100/1000.00~~~0.1~5~~~~1.00~2.00~~11.00~9.00~";'
        ),
    )
    adapter = TencentAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    assert exc.value.details.get("rule") == "identity_mismatch"


@pytest.mark.asyncio
async def test_tencent_quote_at_after_as_of_fails_cutoff() -> None:
    # as_of before quote timestamp → cutoff failure
    early = datetime(2024, 1, 16, 6, 0, 0, tzinfo=UTC)
    adapter, _ = _adapter("success", clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), early)
    assert exc.value.details.get("rule") in {"as_of_cutoff", "not_future"}


@pytest.mark.asyncio
async def test_tencent_errors_do_not_embed_raw_body() -> None:
    transport = FixtureHttpTransport(
        vendor="tencent",
        operation="quote",
        case="success",
        body_override=b'v_sh600519="broken-secret-payload-token";',
    )
    adapter = TencentAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    blob = f"{exc.value.message}{exc.value.details}"
    assert "broken-secret-payload-token" not in blob
    assert "v_sh600519" not in blob


def test_tencent_supports_quote_and_ohlcv() -> None:
    transport = FixtureHttpTransport(vendor="tencent", operation="quote", case="success")
    adapter = TencentAShareAdapter(transport)
    assert adapter.supports(Market.A_SHARE, DataCategory.MARKET_QUOTE)
    assert adapter.supports(Market.A_SHARE, DataCategory.MARKET_OHLCV)
    assert not adapter.supports(Market.A_SHARE, DataCategory.MARKET_STRUCTURE)
    assert adapter.is_configured() is True
    disabled = TencentAShareAdapter(transport, enabled=False)
    assert disabled.is_configured() is False


@pytest.mark.asyncio
async def test_tencent_index_does_not_invent_share_volume() -> None:
    adapter, _ = _adapter("success")
    result = await adapter.get_quote(_instrument(asset=AssetType.INDEX), AS_OF)
    assert result.value.volume_shares is None


@pytest.mark.asyncio
async def test_tencent_equity_qfq_daily_bars_contract() -> None:
    payload = {
        "code": 0,
        "data": {
            "sh600519": {
                "qfqday": [
                    ["2024-01-15", "1600", "1610", "1620", "1590", "123"],
                    ["2024-01-16", "1610", "1630", "1640", "1605", "456"],
                ]
            }
        },
    }
    transport = ScriptedHttpTransport(
        [
            HttpResponse(
                200,
                {"content-type": "application/json; charset=utf-8"},
                json.dumps(payload).encode(),
            )
        ]
    )
    adapter = TencentAShareAdapter(
        transport,
        calendar=JsonAShareTradingCalendar.load(_CALENDAR_PATH),
        clock=FixedClock(datetime(2024, 1, 16, 8, 0, tzinfo=UTC)),
    )
    result = await adapter.get_bars(
        _instrument(),
        start=date(2024, 1, 15),
        end=date(2024, 1, 16),
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        as_of=datetime(2024, 1, 16, 8, 0, tzinfo=UTC),
    )
    assert [bar.close for bar in result.value] == [Decimal("1610"), Decimal("1630")]
    assert result.value[-1].volume_shares == 45_600
    assert result.meta.category is DataCategory.MARKET_OHLCV
    assert result.meta.adjustment is AdjustmentMethod.FORWARD_ADJUSTED
    assert transport.requests[0].params == {"param": "sh600519,day,2024-01-15,2024-01-16,2000,qfq"}


@pytest.mark.asyncio
async def test_tencent_etf_accepts_asset_scoped_day_key() -> None:
    payload = {
        "code": 0,
        "data": {"sh516010": {"day": [["2024-01-16", "1.00", "1.05", "1.06", "0.99", "10"]]}},
    }
    transport = ScriptedHttpTransport(
        [HttpResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode())]
    )
    adapter = TencentAShareAdapter(
        transport,
        calendar=JsonAShareTradingCalendar.load(_CALENDAR_PATH),
        clock=FixedClock(datetime(2024, 1, 16, 8, 0, tzinfo=UTC)),
    )
    result = await adapter.get_bars(
        _instrument(asset=AssetType.ETF, symbol="516010.SH"),
        start=date(2024, 1, 16),
        end=date(2024, 1, 16),
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        as_of=datetime(2024, 1, 16, 8, 0, tzinfo=UTC),
    )
    assert result.value[0].close == Decimal("1.05")
    assert result.value[0].volume_shares == 1_000


@pytest.mark.asyncio
async def test_tencent_bars_reject_unsupported_interval_before_network() -> None:
    transport = ScriptedHttpTransport([])
    adapter = TencentAShareAdapter(
        transport,
        calendar=JsonAShareTradingCalendar.load(_CALENDAR_PATH),
        clock=FixedClock(datetime(2024, 1, 16, 8, 0, tzinfo=UTC)),
    )
    with pytest.raises(DataContractError) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2024, 1, 16),
            end=date(2024, 1, 16),
            interval=BarInterval.ONE_WEEK,
            adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
            as_of=datetime(2024, 1, 16, 8, 0, tzinfo=UTC),
        )
    assert exc.value.details.get("rule") == "unsupported_interval"
    assert transport.requests == []
