"""Phase 1A mock instrument whitelist resolver."""

from __future__ import annotations

from domain.common.enums import AssetType, Market
from domain.common.errors import InvalidInstrument
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument

_A_SHARE_600519 = Instrument(
    instrument_id=build_instrument_id(AssetType.EQUITY, Market.A_SHARE, "600519.SH"),
    symbol="600519.SH",
    name="贵州茅台",
    market=Market.A_SHARE,
    exchange="SSE",
    currency="CNY",
    timezone="Asia/Shanghai",
    asset_type=AssetType.EQUITY,
)

_US_NVDA = Instrument(
    instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "NVDA"),
    symbol="NVDA",
    name="NVIDIA Corporation",
    market=Market.US,
    exchange="NASDAQ",
    currency="USD",
    timezone="America/New_York",
    asset_type=AssetType.EQUITY,
)

_WHITELIST: dict[tuple[Market, str], Instrument] = {
    (Market.A_SHARE, "600519.SH"): _A_SHARE_600519,
    (Market.US, "NVDA"): _US_NVDA,
}


class MockInstrumentResolver:
    """Map mock market/symbol pairs to predefined Instrument objects."""

    def resolve(self, market: Market, symbol: str) -> Instrument:
        key = (market, symbol.strip())
        instrument = _WHITELIST.get(key)
        if instrument is None:
            raise InvalidInstrument(
                f"Unsupported mock instrument for market={market.value} symbol={symbol!r}",
                details={"market": market.value, "symbol": symbol},
            )
        return instrument
