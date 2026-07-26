"""Application-layer futures instrument directory (Phase 3A).

Discovers exactly one provider-validated specific futures contract via
:class:`FuturesContractService` and maps product metadata onto Instrument
Master candidates. Never invents expiries or unlisted contract codes.

Implements :class:`InstrumentDirectoryProvider` without infrastructure imports.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from application.dto.cross_asset import (
    FuturesContractDefinitionDTO,
    FuturesProductDefinitionDTO,
)
from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.services.futures_contract_service import FuturesContractService
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    InvalidInstrument,
    NoMarketData,
    ProviderNotConfigured,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.cross_asset.cme_identity import parse_cme_contract_code
from domain.cross_asset.dce_identity import parse_dce_lh_contract_code
from domain.cross_asset.enums import ContractLifecycleStatus
from domain.instruments.identity import build_canonical_instrument
from domain.instruments.models import Instrument

_KNOWN_LIFECYCLE = frozenset(
    {
        ContractLifecycleStatus.ACTIVE,
        ContractLifecycleStatus.LISTED,
    }
)

_MARKET_TIMEZONE: dict[Market, str] = {
    Market.CME: "America/New_York",
    Market.DCE: "Asia/Shanghai",
}
_MARKET_COUNTRY: dict[Market, str] = {
    Market.CME: "US",
    Market.DCE: "CN",
}
_SESSION_CALENDAR_TIMEZONE: dict[str, str] = {
    "CME_METALS": "America/New_York",
    "DCE_LH": "Asia/Shanghai",
}


class FuturesInstrumentDirectory:
    """Directory provider for one futures market (CME or DCE).

    Lookup only returns a candidate when the FuturesContractService product chain
    includes an exact active/listed match for the requested symbol. Product
    exchange/currency/timezone/multiplier/tick come from product metadata — never
    guessed per-contract expiry fields beyond what the provider already validated.
    """

    def __init__(
        self,
        *,
        market: Market,
        vendor_id: VendorId,
        contract_service: FuturesContractService,
        clock: Clock,
    ) -> None:
        if market not in {Market.CME, Market.DCE}:
            raise DataContractError(
                "FuturesInstrumentDirectory only supports Market.CME or Market.DCE",
                details={"market": getattr(market, "value", market)},
            )
        if not isinstance(vendor_id, VendorId):
            raise DataContractError(
                "vendor_id must be VendorId",
                details={"field": "vendor_id"},
            )
        self._market = market
        self._vendor_id = vendor_id
        self._contracts = contract_service
        self._clock = clock

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is self._market and category is DataCategory.INSTRUMENT_MASTER

    def is_configured(self) -> bool:
        return True

    async def lookup(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Instrument, ...]]:
        as_of = require_aware_datetime(as_of, field_name="as_of")
        if market is not self._market:
            raise DataContractError(
                "futures directory market mismatch",
                details={
                    "market": market.value,
                    "directory_market": self._market.value,
                },
            )
        if asset_type_hint is not None and asset_type_hint is not AssetType.FUTURE:
            return self._empty_success(as_of)

        try:
            symbol, product_key = self._parse_query(query)
        except InvalidInstrument:
            return self._empty_success(as_of)

        try:
            product_result = await self._contracts.get_product(product_key, as_of)
        except TradingPartnerError as exc:
            raise exc
        if not product_result.ok or product_result.data is None:
            if product_result.error is not None:
                raise product_result.error
            raise NoMarketData(
                "futures product definition unavailable for directory lookup",
                details={
                    "code": "CONTRACT_DEFINITION_UNAVAILABLE",
                    "product_key": product_key,
                },
            )

        try:
            list_result = await self._contracts.list_contracts(
                product_key,
                as_of,
                include_expired=False,
            )
        except TradingPartnerError as exc:
            raise exc
        if not list_result.ok or list_result.data is None:
            if list_result.error is not None:
                raise list_result.error
            return self._empty_success(as_of)

        matches = tuple(
            item
            for item in list_result.data
            if self._is_exact_known_match(item, symbol=symbol)
        )
        # Exactly one provider-validated active/listed contract — never guess.
        if len(matches) != 1:
            return self._empty_success(as_of)

        instrument = self._to_instrument(matches[0], product_result.data)
        return ProviderSuccess(
            value=(instrument,),
            meta=ProviderResultMeta(
                vendor=self._vendor_id,
                category=DataCategory.INSTRUMENT_MASTER,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.FRESH,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=(
                    CacheDisposition.HIT
                    if list_result.from_cache or product_result.from_cache
                    else CacheDisposition.MISS
                ),
                adjustment=None,
                data_delay_seconds=None,
                warnings=(),
            ),
        )

    def _parse_query(self, query: str) -> tuple[str, str]:
        if not isinstance(query, str) or not query.strip():
            raise InvalidInstrument(
                "futures directory query must be non-blank",
                details={"field": "query"},
            )
        cleaned = query.strip()
        if ":" in cleaned:
            asset_type, market, symbol = parse_instrument_id(cleaned)
            if asset_type is not AssetType.FUTURE:
                raise InvalidInstrument(
                    "futures directory requires AssetType.FUTURE",
                    details={"asset_type": asset_type.value},
                )
            if market is not self._market:
                raise InvalidInstrument(
                    "instrument_id market does not match directory market",
                    details={
                        "market": market.value,
                        "directory_market": self._market.value,
                    },
                )
        else:
            symbol = cleaned.upper()

        if self._market is Market.CME:
            # Continuous series (ROOT.v.0) are not specific contracts.
            if "." in symbol:
                raise InvalidInstrument(
                    "continuous series are not directory-discoverable contracts",
                    details={"symbol": symbol, "rule": "specific_contract_only"},
                )
            cme_code = parse_cme_contract_code(symbol)
            return cme_code.symbol, cme_code.product_key

        if self._market is Market.DCE:
            dce_code = parse_dce_lh_contract_code(symbol)
            return dce_code.symbol, dce_code.product_key

        raise ProviderNotConfigured(
            "futures directory market is not configured",
            details={"market": self._market.value},
        )

    @staticmethod
    def _is_exact_known_match(
        contract: FuturesContractDefinitionDTO,
        *,
        symbol: str,
    ) -> bool:
        if contract.status not in _KNOWN_LIFECYCLE:
            return False
        try:
            _asset, _market, contract_symbol = parse_instrument_id(contract.instrument_id)
        except DataContractError:
            return False
        return contract_symbol.casefold() == symbol.casefold()

    def _to_instrument(
        self,
        contract: FuturesContractDefinitionDTO,
        product: FuturesProductDefinitionDTO,
    ) -> Instrument:
        _asset, market, symbol = parse_instrument_id(contract.instrument_id)
        if market is not self._market or market is not product.market:
            raise DataContractError(
                "contract market must match product and directory market",
                details={
                    "contract_market": market.value,
                    "product_market": product.market.value,
                    "directory_market": self._market.value,
                },
            )
        timezone = _SESSION_CALENDAR_TIMEZONE.get(
            product.session_calendar_id,
            _MARKET_TIMEZONE[self._market],
        )
        commodity = product.commodity.replace("_", " ").strip()
        name = f"{commodity.title()} {contract.contract_month}".strip()
        multiplier = (
            product.multiplier
            if isinstance(product.multiplier, Decimal)
            else Decimal(str(product.multiplier))
        )
        tick_size = (
            product.tick_size
            if isinstance(product.tick_size, Decimal)
            else Decimal(str(product.tick_size))
        )
        return build_canonical_instrument(
            asset_type=AssetType.FUTURE,
            market=self._market,
            canonical_symbol=symbol,
            name=name,
            exchange=product.exchange,
            currency=product.currency,
            timezone=timezone,
            is_active=True,
            listing_status="active",
            country=_MARKET_COUNTRY[self._market],
            multiplier=multiplier,
            tick_size=tick_size,
            lot_size=None,
            metadata_version=1,
        )

    def _empty_success(
        self, as_of: datetime
    ) -> ProviderSuccess[tuple[Instrument, ...]]:
        return ProviderSuccess(
            value=(),
            meta=ProviderResultMeta(
                vendor=self._vendor_id,
                category=DataCategory.INSTRUMENT_MASTER,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=CacheDisposition.BYPASS,
                adjustment=None,
                data_delay_seconds=None,
                warnings=(),
            ),
        )


__all__ = ["FuturesInstrumentDirectory"]
