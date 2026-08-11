"""Contract tests for current-only Moomoo OpenD US overnight quotes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from conftest import FixedClock
from domain.common.enums import AssetType, DataCategory, Market, TradingSession, VendorId
from domain.common.errors import NoMarketData
from domain.instruments.models import Instrument
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDOperation
from infrastructure.providers.us.moomoo_overnight import MoomooOvernightQuoteAdapter

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 11, 1, 30, tzinfo=NY)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="etf:US:GDX",
        symbol="GDX",
        name="VanEck Gold Miners ETF",
        market=Market.US,
        exchange="ARCA",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.ETF,
    )


class _Context:
    def __init__(
        self,
        row: dict[str, object],
        *,
        ok: bool = True,
        market_state: str = "OVERNIGHT",
    ) -> None:
        self.row = row
        self.ok = ok
        self.market_state = market_state
        self.closed = False
        self.codes: list[str] = []

    def get_market_snapshot(self, codes: list[str]) -> tuple[bool, object]:
        self.codes = codes
        return self.ok, [self.row]

    def get_market_state(self, codes: list[str]) -> tuple[bool, object]:
        self.codes = codes
        return self.ok, [{"code": codes[0], "market_state": self.market_state}]

    def close(self) -> None:
        self.closed = True


class _Limiter:
    def __init__(self) -> None:
        self.operations: list[MoomooOpenDOperation] = []

    def wait(self, operation: MoomooOpenDOperation, *, scope: str | None = None) -> None:
        assert scope is None
        self.operations.append(operation)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "code": "US.GDX",
        "update_time": "2026-08-11 01:29:50.000",
        "overnight_price": 89.22,
        "overnight_high_price": 91.67,
        "overnight_low_price": 88.88,
        "overnight_volume": 75008,
        "prev_close_price": 90.49,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_overnight_quote_maps_dedicated_fields_and_regular_close_baseline() -> None:
    context = _Context(_row())
    limiter = _Limiter()
    adapter = MoomooOvernightQuoteAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=FixedClock(NOW),
        context_factory=lambda _host, _port: context,
        opend_rate_limiter=limiter,
    )

    success = await adapter.get_quote(_instrument(), NOW)

    assert adapter.vendor_id is VendorId.MOOMOO
    assert adapter.supports(Market.US, DataCategory.MARKET_QUOTE)
    assert success.value.last == Decimal("89.22")
    assert success.value.previous_close == Decimal("90.49")
    assert success.value.session is TradingSession.OVERNIGHT
    assert success.value.quote_at == datetime(2026, 8, 11, 1, 29, 50, tzinfo=NY)
    assert success.value.high == Decimal("91.67")
    assert success.value.low == Decimal("88.88")
    assert success.value.volume == Decimal("75008")
    assert success.meta.session is TradingSession.OVERNIGHT
    assert "MOOMOO_OVERNIGHT_PRICE" in success.meta.warnings
    assert context.codes == ["US.GDX"]
    assert context.closed is True
    assert limiter.operations == [
        MoomooOpenDOperation.MARKET_STATE,
        MoomooOpenDOperation.MARKET_SNAPSHOT,
    ]


@pytest.mark.asyncio
async def test_outside_overnight_window_fails_before_opend_access() -> None:
    called = False

    def factory(_host: str, _port: int) -> _Context:
        nonlocal called
        called = True
        return _Context(_row())

    daytime = datetime(2026, 8, 11, 10, 0, tzinfo=NY)
    adapter = MoomooOvernightQuoteAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=FixedClock(daytime),
        context_factory=factory,
    )

    with pytest.raises(NoMarketData, match="outside"):
        await adapter.get_quote(_instrument(), daytime)
    assert called is False


@pytest.mark.asyncio
async def test_missing_overnight_price_is_not_relabelled_from_regular_last() -> None:
    context = _Context(_row(overnight_price=float("nan"), last_price=92.0))
    adapter = MoomooOvernightQuoteAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=FixedClock(NOW),
        context_factory=lambda _host, _port: context,
    )

    with pytest.raises(NoMarketData, match="no verified overnight price"):
        await adapter.get_quote(_instrument(), NOW)


@pytest.mark.asyncio
async def test_time_window_alone_cannot_relabel_non_overnight_market_state() -> None:
    context = _Context(_row(), market_state="PRE_MARKET_BEGIN")
    limiter = _Limiter()
    adapter = MoomooOvernightQuoteAdapter(
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=FixedClock(NOW),
        context_factory=lambda _host, _port: context,
        opend_rate_limiter=limiter,
    )

    with pytest.raises(NoMarketData, match="does not report"):
        await adapter.get_quote(_instrument(), NOW)
    assert limiter.operations == [MoomooOpenDOperation.MARKET_STATE]
    assert context.closed is True
