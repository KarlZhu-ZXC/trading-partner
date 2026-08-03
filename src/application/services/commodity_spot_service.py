"""Commodity spot / OTC quote and bar application service (Phase 3A-3).

Composes the CommoditySpotProvider port. Never fabricates prices. Dukascopy SWFX
feeds are broker/OTC observations, not LBMA benchmarks. Rolling copper CFD keeps
``cfd:OTC:COPPER_CMD_USD`` identity and always warns ``ROLLING_CFD_NOT_SPOT``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from application.dto.cross_asset import CommoditySpotBarSeriesDTO, SpotObservationDTO
from application.dto.provider_routing import ProviderResultMeta
from application.dto.tool_envelope import WarningInfo
from application.ports.clock import Clock
from application.ports.commodity_spot_provider import CommoditySpotProvider
from domain.common.enums import AdjustmentMethod, AssetType, Market
from domain.common.errors import DataContractError, NoMarketData, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.cross_asset.enums import OfferSide
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval

_BASE_WARNINGS = (
    WarningInfo(
        code="DUKASCOPY_SWFX_NOT_LBMA",
        message=(
            "Dukascopy SWFX/broker OTC feed is not an LBMA auction benchmark "
            "and must not be labelled as LBMA gold or silver."
        ),
    ),
    WarningInfo(
        code="OTC_BROKER_FEED",
        message="Observation uses Dukascopy SWFX venue basis, not exchange cash.",
    ),
    WarningInfo(
        code="VOLUME_BEST_BID_ASK_NOT_EXCHANGE",
        message=(
            "Bar volume is best bid/ask side volume from the broker feed, "
            "not exchange traded volume."
        ),
    ),
)
_CFD_WARNING = WarningInfo(
    code="ROLLING_CFD_NOT_SPOT",
    message=(
        "Rolling commodity CFD is not copper spot, LME Cash, or COMEX copper. "
        "Identity remains cfd:OTC:COPPER_CMD_USD."
    ),
)
_IG_WEEKEND_WARNINGS = (
    WarningInfo(
        code="IG_WEEKEND_GOLD_CFD_FALLBACK",
        message="Current quote uses IG Weekend Gold as the weekend fallback.",
    ),
    WarningInfo(
        code="WEEKEND_PROXY_NOT_SPOT",
        message="IG Weekend Gold is a separately formed CFD price, not XAUUSD spot.",
    ),
    WarningInfo(
        code="IG_BROWSER_SCRAPE",
        message="The quote was extracted deterministically from IG's public webpage.",
    ),
    WarningInfo(
        code="PRICE_TIME_IS_SCRAPE_TIME",
        message="The page exposes no authoritative quote timestamp; quote_at is scrape time.",
    ),
    WarningInfo(
        code="IG_WEEKEND_PRICE_SEPARATE_FROM_WEEKDAY_SPOT",
        message="IG forms weekend prices separately from its weekday gold market.",
    ),
)


@dataclass(frozen=True, slots=True)
class CommoditySpotQuoteResult:
    ok: bool
    data: SpotObservationDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    meta: ProviderResultMeta | None = None


@dataclass(frozen=True, slots=True)
class CommoditySpotBarsResult:
    ok: bool
    data: CommoditySpotBarSeriesDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    meta: ProviderResultMeta | None = None


def _warnings_for(instrument: Instrument) -> tuple[WarningInfo, ...]:
    if instrument.asset_type is AssetType.CFD:
        return _BASE_WARNINGS + (_CFD_WARNING,)
    return _BASE_WARNINGS


def _quote_warnings(
    instrument: Instrument,
    observation: SpotObservationDTO,
) -> tuple[WarningInfo, ...]:
    if observation.venue_basis.value == "ig_weekend_cfd":
        return _IG_WEEKEND_WARNINGS
    return _warnings_for(instrument)


def _require_otc_instrument(instrument: Instrument) -> None:
    if not isinstance(instrument, Instrument):
        raise DataContractError(
            "instrument must be Instrument",
            details={"field": "instrument"},
        )
    if instrument.market is not Market.OTC:
        raise DataContractError(
            "commodity spot service only accepts Market.OTC instruments",
            details={"field": "market", "rule": "otc_only"},
        )
    if instrument.asset_type not in {AssetType.COMMODITY_SPOT, AssetType.CFD}:
        raise DataContractError(
            "commodity spot service only accepts commodity_spot or cfd",
            details={"field": "asset_type"},
        )
    if (
        instrument.asset_type is AssetType.CFD
        and instrument.instrument_id == "cfd:OTC:COPPER_CMD_USD"
    ):
        # Explicit identity gate for copper rolling CFD.
        return
    if instrument.asset_type is AssetType.COMMODITY_SPOT and instrument.symbol in {
        "XAUUSD",
        "XAGUSD",
    }:
        return
    # Allow other OTC spot/CFD symbols only when the provider will accept them;
    # service does not invent unsupported identities.
    return


class CommoditySpotService:
    def __init__(
        self,
        *,
        provider: CommoditySpotProvider,
        clock: Clock,
    ) -> None:
        self._provider = provider
        self._clock = clock

    def _resolve_as_of(self, as_of: datetime | None) -> datetime:
        resolved = as_of if as_of is not None else self._clock.now()
        require_aware_datetime(resolved, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if resolved > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        return resolved

    async def get_quote(
        self,
        instrument: Instrument,
        *,
        as_of: datetime | None = None,
    ) -> CommoditySpotQuoteResult:
        try:
            _require_otc_instrument(instrument)
            resolved = self._resolve_as_of(as_of)
            result = await self._provider.get_quote(instrument, resolved)
            observation = SpotObservationDTO.from_domain(result.value)
            warnings = _quote_warnings(instrument, observation)
            # Merge provider meta warning codes as structured WarningInfo if present.
            extra = tuple(
                WarningInfo(code=code, message=code.replace("_", " ").lower())
                for code in result.meta.warnings
                if code not in {w.code for w in warnings}
            )
            return CommoditySpotQuoteResult(
                ok=True,
                data=observation,
                warnings=warnings + extra,
                error=None,
                meta=result.meta,
            )
        except TradingPartnerError as exc:
            return CommoditySpotQuoteResult(
                ok=False,
                data=None,
                warnings=_warnings_for(instrument)
                if isinstance(instrument, Instrument)
                else (),
                error=exc,
            )

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        as_of: datetime | None = None,
        offer_side: OfferSide = OfferSide.BID,
        adjustment: AdjustmentMethod = AdjustmentMethod.NONE,
    ) -> CommoditySpotBarsResult:
        try:
            _require_otc_instrument(instrument)
            resolved = self._resolve_as_of(as_of)
            if adjustment is not AdjustmentMethod.NONE:
                raise DataContractError(
                    "commodity spot/CFD bars require adjustment=none",
                    details={"field": "adjustment", "rule": "none_only"},
                )
            result = await self._provider.get_bars(
                instrument,
                start=start,
                end=end,
                interval=interval,
                adjustment=AdjustmentMethod.NONE,
                as_of=resolved,
                offer_side=offer_side,
            )
            if not result.value.bars:
                raise NoMarketData(
                    "commodity spot bar series is empty",
                    details={"field": "bars", "code": "NO_MARKET_DATA"},
                )
            warnings = _warnings_for(instrument)
            extra = tuple(
                WarningInfo(code=code, message=code.replace("_", " ").lower())
                for code in result.meta.warnings
                if code not in {w.code for w in warnings}
            )
            return CommoditySpotBarsResult(
                ok=True,
                data=CommoditySpotBarSeriesDTO.from_domain(result.value),
                warnings=warnings + extra,
                error=None,
                meta=result.meta,
            )
        except TradingPartnerError as exc:
            return CommoditySpotBarsResult(
                ok=False,
                data=None,
                warnings=_warnings_for(instrument)
                if isinstance(instrument, Instrument)
                else (),
                error=exc,
            )


__all__ = [
    "CommoditySpotBarsResult",
    "CommoditySpotQuoteResult",
    "CommoditySpotService",
]
