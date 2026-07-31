"""Uniform local-first access to the durable Instrument Master.

Public instrument-scoped application services use this gateway instead of reading
the Master directly.  A supported typed ID is discovered only after a true local
miss; provider failures retain their original typed error semantics.
"""

from __future__ import annotations

from datetime import datetime

from application.services.instrument_master_service import InstrumentMasterService
from application.services.instrument_resolve_service import InstrumentResolveService
from domain.common.enums import AssetType, Market
from domain.common.errors import InvalidInstrument, TradingPartnerError
from domain.common.values import parse_instrument_id
from domain.instruments.models import Instrument

_DISCOVERABLE_ASSETS: dict[Market, frozenset[AssetType]] = {
    Market.A_SHARE: frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX}),
    Market.US: frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX, AssetType.FUTURE}),
    Market.KR: frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX}),
    Market.CME: frozenset({AssetType.FUTURE}),
    Market.DCE: frozenset({AssetType.FUTURE}),
}


class InstrumentAccessService:
    """Return one canonical Instrument, dynamically caching supported misses."""

    def __init__(
        self,
        master: InstrumentMasterService,
        resolver: InstrumentResolveService,
    ) -> None:
        self._master = master
        self._resolver = resolver

    async def get(self, instrument_id: str, *, as_of: datetime) -> Instrument:
        try:
            return self._master.get(instrument_id)
        except InvalidInstrument:
            pass

        try:
            asset_type, market, _symbol = parse_instrument_id(instrument_id)
        except TradingPartnerError as exc:
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            ) from exc

        if asset_type not in _DISCOVERABLE_ASSETS.get(market, frozenset()):
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            )

        envelope = await self._resolver.resolve_dynamic(
            market=market,
            query=instrument_id,
            asset_type_hint=asset_type,
            as_of=as_of,
        )
        if not envelope.ok or envelope.data is None or envelope.data.instrument is None:
            if envelope.errors:
                error = envelope.errors[0]
                raise TradingPartnerError(
                    error.message,
                    code=error.code,
                    retryable=error.retryable,
                    details=error.details,
                )
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            )

        return self._master.get(envelope.data.instrument.instrument_id)

    async def get_optional(
        self,
        instrument_id: str | None,
        *,
        as_of: datetime,
    ) -> Instrument | None:
        if instrument_id is None:
            return None
        return await self.get(instrument_id, as_of=as_of)
