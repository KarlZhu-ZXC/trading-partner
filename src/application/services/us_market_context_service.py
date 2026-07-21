"""US market context composition service (Phase 1F F2c / F3c).

Resolves SPY/QQQ/IWM proxy instruments via ``InstrumentMasterService`` (short
UoW lifetime per lookup), fetches concurrent quotes via ``USMarketDataService``,
and assembles ``USMarketContext`` with optional provider-backed breadth and
per-proxy degradation (never fails the whole context for one proxy miss).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from application.dto.provider_routing import ProviderResultMeta
from application.ports.clock import Clock
from application.services.instrument_master_service import InstrumentMasterService
from application.services.us_community_heat_service import USCommunityHeatService
from application.services.us_market_breadth_service import USMarketBreadthService
from application.services.us_market_data_service import USMarketDataService
from domain.common.errors import DataContractError, InvalidInstrument, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_market.models import (
    USBreadthSnapshot,
    USCommunityHeatSnapshot,
    USMarketContext,
    USMarketProxy,
    USQuote,
)

_PROXY_SLOTS: tuple[tuple[str, str], ...] = (
    ("spy", "etf:US:SPY"),
    ("qqq", "etf:US:QQQ"),
    ("iwm", "etf:US:IWM"),
)
_BREADTH_WARNING = "US_BREADTH_UNAVAILABLE"
_ROTATION_WARNING = "US_SECTOR_ROTATION_UNAVAILABLE"
_COMMUNITY_HEAT_WARNING = "MOOMOO_COMMUNITY_HEAT_UNAVAILABLE"
_HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class USMarketContextResult:
    """Context product plus successful proxy quote provenance metas (SPY/QQQ/IWM)."""

    context: USMarketContext
    metas: tuple[ProviderResultMeta, ...]


class USMarketContextService:
    """Compose US market proxy context from quote surfaces only."""

    def __init__(
        self,
        data_service: USMarketDataService,
        instrument_master: InstrumentMasterService,
        clock: Clock,
        breadth_service: USMarketBreadthService | None = None,
        community_heat_service: USCommunityHeatService | None = None,
        community_heat_limit: int = 20,
    ) -> None:
        if data_service is None or instrument_master is None or clock is None:
            raise DataContractError(
                "data_service, instrument_master, and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        self._data_service = data_service
        self._instrument_master = instrument_master
        self._clock = clock
        self._breadth_service = breadth_service
        self._community_heat_service = community_heat_service
        if not 1 <= community_heat_limit <= 200:
            raise DataContractError(
                "community_heat_limit must be in [1,200]",
                details={"field": "community_heat_limit", "rule": "range"},
            )
        self._community_heat_limit = community_heat_limit

    def _require_as_of_not_future(self, as_of: datetime) -> None:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )

    @staticmethod
    def _unavailable_proxy(instrument_id: str) -> USMarketProxy:
        return USMarketProxy(
            instrument_id=instrument_id,
            latest=None,
            change_percent=None,
        )

    @staticmethod
    def _symbol_from_instrument_id(instrument_id: str) -> str:
        # Frozen IDs: etf:US:SPY → SPY
        return instrument_id.rsplit(":", 1)[-1]

    @staticmethod
    def _change_percent(quote: USQuote) -> tuple[Decimal | None, str | None]:
        prev = quote.previous_close
        if prev is None or prev == 0:
            symbol = quote.instrument_id.rsplit(":", 1)[-1]
            return None, f"PROXY_{symbol}_CHANGE_UNAVAILABLE"
        return ((quote.last - prev) / prev) * _HUNDRED, None

    def _resolve_proxy_instrument(self, instrument_id: str) -> Instrument | None:
        """Look up one proxy; missing/invalid degrades to unavailable (no raise)."""
        try:
            return self._instrument_master.get(instrument_id)
        except (InvalidInstrument, DataContractError):
            return None

    async def _proxy_from_quote_result(
        self,
        instrument_id: str,
        instrument: Instrument | None,
        as_of: datetime,
    ) -> tuple[USMarketProxy, tuple[str, ...], ProviderResultMeta | None]:
        symbol = self._symbol_from_instrument_id(instrument_id)
        unavailable_code = f"PROXY_{symbol}_UNAVAILABLE"
        if instrument is None:
            return self._unavailable_proxy(instrument_id), (unavailable_code,), None

        try:
            result = await self._data_service.get_quote(instrument, as_of)
        except Exception:
            return self._unavailable_proxy(instrument_id), (unavailable_code,), None

        if not result.ok or not isinstance(result.value, USQuote):
            return self._unavailable_proxy(instrument_id), (unavailable_code,), None

        quote = result.value
        if quote.instrument_id != instrument_id:
            return self._unavailable_proxy(instrument_id), (unavailable_code,), None

        change, change_warning = self._change_percent(quote)
        warnings: tuple[str, ...] = (change_warning,) if change_warning is not None else ()
        meta = result.meta if isinstance(result.meta, ProviderResultMeta) else None
        return (
            USMarketProxy(
                instrument_id=instrument_id,
                latest=quote.last,
                change_percent=change,
            ),
            warnings,
            meta,
        )

    async def get_context_result(self, as_of: datetime) -> USMarketContextResult:
        """Build context and collect successful proxy quote metas in SPY/QQQ/IWM order."""
        self._require_as_of_not_future(as_of)

        instruments: list[Instrument | None] = [
            self._resolve_proxy_instrument(instrument_id) for _, instrument_id in _PROXY_SLOTS
        ]

        gathered = await asyncio.gather(
            *(
                self._proxy_from_quote_result(instrument_id, instrument, as_of)
                for instrument, (_, instrument_id) in zip(instruments, _PROXY_SLOTS, strict=True)
            )
        )

        proxies: dict[str, USMarketProxy] = {}
        warning_list: list[str] = []
        seen: set[str] = set()
        metas: list[ProviderResultMeta] = []
        for (slot, _), (proxy, codes, meta) in zip(_PROXY_SLOTS, gathered, strict=True):
            proxies[slot] = proxy
            if meta is not None:
                metas.append(meta)
            for code in codes:
                if code not in seen:
                    seen.add(code)
                    warning_list.append(code)

        breadth: USBreadthSnapshot | None = None
        if self._breadth_service is not None:
            try:
                breadth_result = await self._breadth_service.get_current(as_of)
            except Exception:
                breadth_result = None
            if (
                breadth_result is not None
                and breadth_result.ok
                and isinstance(breadth_result.value, USBreadthSnapshot)
            ):
                breadth = breadth_result.value
                if isinstance(breadth_result.meta, ProviderResultMeta):
                    metas.append(breadth_result.meta)

        if breadth is None:
            for code in (_BREADTH_WARNING, _ROTATION_WARNING):
                if code not in seen:
                    seen.add(code)
                    warning_list.append(code)
        elif not breadth.sector_rotation and _ROTATION_WARNING not in seen:
            warning_list.append(_ROTATION_WARNING)

        community_heat: USCommunityHeatSnapshot | None = None
        community_error_code: str | None = None
        if self._community_heat_service is not None:
            try:
                community_result = await self._community_heat_service.get_current(
                    limit=self._community_heat_limit,
                    as_of=as_of,
                )
            except TradingPartnerError as exc:
                community_result = None
                community_error_code = exc.code
            except Exception:
                community_result = None
            if (
                community_result is not None
                and community_result.ok
                and isinstance(community_result.value, USCommunityHeatSnapshot)
            ):
                community_heat = community_result.value
                if isinstance(community_result.meta, ProviderResultMeta):
                    metas.append(community_result.meta)
            else:
                if (
                    community_error_code is None
                    and community_result is not None
                    and community_result.error is not None
                ):
                    community_error_code = community_result.error.code
                warning = community_error_code or _COMMUNITY_HEAT_WARNING
                if warning not in seen:
                    warning_list.append(warning)

        context = USMarketContext(
            as_of=as_of,
            spy=proxies["spy"],
            qqq=proxies["qqq"],
            iwm=proxies["iwm"],
            advancing_count=(breadth.advancing_count if breadth else None),
            declining_count=(breadth.declining_count if breadth else None),
            unchanged_count=(breadth.unchanged_count if breadth else None),
            breadth_as_of=(breadth.observed_at if breadth else None),
            breadth_basis=(breadth.basis if breadth else None),
            breadth_universe=(breadth.universe if breadth else None),
            sector_rotation=(breadth.sector_rotation if breadth else ()),
            community_heat_as_of=(community_heat.observed_at if community_heat else None),
            community_heat_basis=(community_heat.basis if community_heat else None),
            community_heat=(community_heat.items if community_heat else ()),
            warning_codes=tuple(warning_list),
        )
        return USMarketContextResult(context=context, metas=tuple(metas))

    async def get_context(self, as_of: datetime) -> USMarketContext:
        return (await self.get_context_result(as_of)).context
