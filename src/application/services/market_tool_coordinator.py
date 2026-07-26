"""Cross-asset market MCP coordinator (Phase 3A).

Owns ``market_get_snapshot``, ``market_get_bars``, and ``market_get_context``
routing across US equity/index/legacy continuous futures, CME specific
contracts, OTC commodity spot/CFD, futures curves, and optional basis.

US-only composite and technical surfaces stay on :class:`USToolCoordinator`.
Never imports infrastructure or MCP layers.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from application.dto.cross_asset import BasisSnapshotDTO
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import ProviderResultMeta
from application.dto.tool_envelope import (
    ErrorInfo,
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetContextInput,
    MarketGetSnapshotInput,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.commodity_spot_service import CommoditySpotService
from application.services.futures_curve_service import FuturesCurveService
from application.services.instrument_master_service import InstrumentMasterService
from application.services.instrument_resolve_service import InstrumentResolveService
from application.services.us_market_data_service import USMarketDataService
from application.services.us_tool_coordinator import USToolCoordinator
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    Freshness,
    Market,
    SourceRole,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    InvalidInstrument,
    NoMarketData,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from domain.common.values import parse_instrument_id
from domain.cross_asset.basis_service import BasisLeg, build_basis_snapshot
from domain.cross_asset.enums import OfferSide, PriceBasis
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval

_DYNAMIC_RESOLVE_MARKETS = frozenset({Market.CME, Market.DCE})
_DCE_QUOTE_BARS_UNAVAILABLE = NoMarketData(
    "DCE specific contracts have no quote/OHLCV path in Phase 3A; "
    "use market_get_context(operation=futures_curve) for official EOD facts",
    details={"code": "DCE_QUOTE_BARS_UNAVAILABLE", "market": Market.DCE.value},
)

_BASIS_UNAVAILABLE = WarningInfo(
    code="SPOT_FUTURE_BASIS_UNAVAILABLE",
    message=(
        "Spot/future basis could not be computed from current services "
        "without fabricating observations."
    ),
    details={},
)
_BASIS_NOT_COMPARABLE = WarningInfo(
    code="BASIS_NOT_COMPARABLE",
    message="Spot and futures legs are not comparable under the disclosed gate.",
    details={},
)


def _parse_offer_side(raw: str | None) -> OfferSide:
    if raw is None:
        return OfferSide.BID
    normalized = raw.strip().upper()
    if normalized in {"B", "BID"}:
        return OfferSide.BID
    if normalized in {"A", "ASK"}:
        return OfferSide.ASK
    raise DataContractError(
        "offer_side must be B/A (or bid/ask)",
        details={"field": "offer_side", "value": raw},
    )


def _is_otc_spot_or_cfd(instrument: Instrument) -> bool:
    return instrument.market is Market.OTC and instrument.asset_type in {
        AssetType.COMMODITY_SPOT,
        AssetType.CFD,
    }


def _futures_price_unit(symbol: str) -> str:
    upper = symbol.upper()
    if upper.startswith(("GC", "SI", "MGC", "PL", "PA")):
        return "USD/troy_oz"
    if upper.startswith("HG"):
        return "USD/lb"
    return "contract_price"


def _source(meta: ProviderResultMeta | None) -> tuple[SourceReference, ...]:
    if meta is None:
        return ()
    return (
        SourceReference(
            name=meta.vendor.value,
            role=meta.role,
            retrieved_at=meta.fetched_at,
            data_delay_seconds=meta.data_delay_seconds,
        ),
    )


class MarketToolCoordinator:
    """Routes generalized market tools across US, CME, OTC, and curve/basis ops."""

    def __init__(
        self,
        *,
        instrument_master: InstrumentMasterService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        us_tool_coordinator: USToolCoordinator,
        data_service: USMarketDataService,
        commodity_spot_service: CommoditySpotService | None = None,
        futures_curve_service: FuturesCurveService | None = None,
        instrument_resolve_service: InstrumentResolveService | None = None,
    ) -> None:
        self._instrument_master = instrument_master
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._us = us_tool_coordinator
        self._data_service = data_service
        self._commodity_spot = commodity_spot_service
        self._futures_curve = futures_curve_service
        self._instrument_resolve = instrument_resolve_service

    async def get_market_snapshot(
        self, request: MarketGetSnapshotInput
    ) -> ToolEnvelope[Any]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_local_or_dynamic(
                request.instrument_id, as_of=effective_as_of
            )
            if instrument.market is Market.DCE:
                raise _DCE_QUOTE_BARS_UNAVAILABLE
            if _is_otc_spot_or_cfd(instrument):
                return await self._otc_quote(
                    request_id, effective_as_of, instrument
                )
            # US equity/ETF/index/legacy future + CME specific contracts.
            return await self._us.get_market_snapshot(request)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    async def get_market_bars(
        self, request: MarketGetBarsInput
    ) -> ToolEnvelope[Any]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._resolve_local_or_dynamic(
                request.instrument_id, as_of=effective_as_of
            )
            if instrument.market is Market.DCE:
                raise _DCE_QUOTE_BARS_UNAVAILABLE
            if _is_otc_spot_or_cfd(instrument):
                adjustment = request.adjustment
                if adjustment is None:
                    adjustment = AdjustmentMethod.NONE
                return await self._otc_bars(
                    request_id,
                    effective_as_of,
                    instrument,
                    start=request.start,
                    end=request.end,
                    interval=request.interval,
                    adjustment=adjustment,
                    offer_side=_parse_offer_side(request.offer_side),
                )
            return await self._us.get_market_bars(request)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    async def get_market_context(
        self, request: MarketGetContextInput
    ) -> ToolEnvelope[Any]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            if request.operation == "us_market":
                return await self._us.get_market_context(request)
            if request.operation == "futures_curve":
                return await self._futures_curve_envelope(
                    request_id, effective_as_of, request
                )
            if request.operation == "spot_future_basis":
                return await self._spot_future_basis_envelope(
                    request_id, effective_as_of, request
                )
            raise DataContractError(
                "unsupported market_get_context operation",
                details={"operation": request.operation},
            )
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    def _begin(self, as_of: datetime | None) -> tuple[str, datetime]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        effective_as_of = self._clock.now() if as_of is None else as_of
        return request_id, effective_as_of

    async def _resolve_local_or_dynamic(
        self, instrument_id: str, *, as_of: datetime
    ) -> Instrument:
        """Local-first Instrument Master; CME/DCE miss uses dynamic directory cache."""
        try:
            return self._instrument_master.get(instrument_id)
        except InvalidInstrument:
            pass
        try:
            asset_type, market, _symbol = parse_instrument_id(instrument_id)
        except TradingPartnerError as exc:
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            ) from exc
        if (
            market not in _DYNAMIC_RESOLVE_MARKETS
            or self._instrument_resolve is None
            or asset_type is not AssetType.FUTURE
        ):
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            )
        envelope = await self._instrument_resolve.resolve_dynamic(
            market=market,
            query=instrument_id,
            asset_type_hint=asset_type,
            as_of=as_of,
        )
        if (
            not envelope.ok
            or envelope.data is None
            or envelope.data.instrument is None
        ):
            raise InvalidInstrument(
                "instrument not found",
                details={"instrument_id": instrument_id},
            )
        # resolve_dynamic upserts the validated candidate; re-read Master.
        return self._instrument_master.get(instrument_id)

    async def _otc_quote(
        self,
        request_id: str,
        effective_as_of: datetime,
        instrument: Instrument,
    ) -> ToolEnvelope[Any]:
        if self._commodity_spot is None:
            raise NoMarketData(
                "commodity spot service is not configured",
                details={"code": "PROVIDER_NOT_CONFIGURED"},
            )
        result = await self._commodity_spot.get_quote(
            instrument, as_of=effective_as_of
        )
        if not result.ok or result.data is None:
            return self._service_failure(
                request_id,
                effective_as_of,
                result.error
                or NoMarketData(
                    "commodity spot quote unavailable",
                    details={"code": "NO_MARKET_DATA"},
                ),
                warnings=result.warnings,
                market=instrument.market,
            )
        return ToolEnvelope.success(
            request_id=request_id,
            market=instrument.market,
            as_of=effective_as_of,
            fetched_at=result.meta.fetched_at if result.meta else self._clock.now(),
            freshness=result.meta.freshness if result.meta else Freshness.UNKNOWN,
            sources=_source(result.meta),
            data=result.data,
            degraded=(
                result.meta is None
                or result.meta.freshness is not Freshness.FRESH
                or bool(result.warnings)
            ),
            warnings=result.warnings,
        )

    async def _otc_bars(
        self,
        request_id: str,
        effective_as_of: datetime,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        offer_side: OfferSide,
    ) -> ToolEnvelope[Any]:
        if self._commodity_spot is None:
            raise NoMarketData(
                "commodity spot service is not configured",
                details={"code": "PROVIDER_NOT_CONFIGURED"},
            )
        if adjustment is not AdjustmentMethod.NONE:
            raise DataContractError(
                "OTC spot/CFD bars require adjustment=none",
                details={"field": "adjustment", "rule": "none_only"},
            )
        result = await self._commodity_spot.get_bars(
            instrument,
            start=start,
            end=end,
            interval=interval,
            as_of=effective_as_of,
            offer_side=offer_side,
            adjustment=AdjustmentMethod.NONE,
        )
        if not result.ok or result.data is None:
            return self._service_failure(
                request_id,
                effective_as_of,
                result.error
                or NoMarketData(
                    "commodity spot bars unavailable",
                    details={"code": "NO_MARKET_DATA"},
                ),
                warnings=result.warnings,
                market=instrument.market,
            )
        return ToolEnvelope.success(
            request_id=request_id,
            market=instrument.market,
            as_of=effective_as_of,
            fetched_at=result.meta.fetched_at if result.meta else self._clock.now(),
            freshness=result.meta.freshness if result.meta else Freshness.UNKNOWN,
            sources=_source(result.meta),
            data=result.data,
            degraded=(
                result.meta is None
                or result.meta.freshness is not Freshness.FRESH
                or bool(result.warnings)
            ),
            warnings=result.warnings,
        )

    async def _futures_curve_envelope(
        self,
        request_id: str,
        effective_as_of: datetime,
        request: MarketGetContextInput,
    ) -> ToolEnvelope[Any]:
        if self._futures_curve is None:
            raise NoMarketData(
                "futures curve service is not configured",
                details={"code": "PROVIDER_NOT_CONFIGURED"},
            )
        assert request.product_key is not None
        try:
            basis = PriceBasis(request.price_basis)
        except ValueError as exc:
            raise DataContractError(
                "price_basis must be a known PriceBasis",
                details={"field": "price_basis", "value": request.price_basis},
            ) from exc
        result = await self._futures_curve.build_curve(
            request.product_key,
            price_basis=basis,
            as_of=effective_as_of,
            contract_limit=request.contract_limit,
            trade_date=request.trade_date,
        )
        market = Market.DCE if request.product_key.startswith("DCE:") else Market.CME
        if not result.ok or result.data is None:
            return self._service_failure(
                request_id,
                effective_as_of,
                result.error
                or NoMarketData(
                    "futures curve unavailable",
                    details={"code": "FUTURES_CHAIN_UNAVAILABLE"},
                ),
                warnings=result.warnings,
                market=market,
            )
        return ToolEnvelope.success(
            request_id=request_id,
            market=market,
            as_of=effective_as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(
                SourceReference(
                    name=(
                        VendorId.DCE_OFFICIAL.value
                        if market is Market.DCE
                        else VendorId.CME_PUBLIC.value
                    ),
                    role=SourceRole.PRIMARY,
                ),
            ),
            data=result.data,
            degraded=bool(result.warnings),
            warnings=result.warnings,
        )

    async def _spot_future_basis_envelope(
        self,
        request_id: str,
        effective_as_of: datetime,
        request: MarketGetContextInput,
    ) -> ToolEnvelope[Any]:
        """Compute basis only from real service observations; never fabricate."""
        if self._commodity_spot is None:
            return self._service_failure(
                request_id,
                effective_as_of,
                NoMarketData(
                    "spot/future basis unavailable without commodity spot service",
                    details={"code": "SPOT_FUTURE_BASIS_UNAVAILABLE"},
                ),
                warnings=(_BASIS_UNAVAILABLE,),
                market=None,
            )
        assert request.left_instrument_id is not None
        assert request.right_instrument_id is not None
        left = await self._resolve_local_or_dynamic(
            request.left_instrument_id,
            as_of=effective_as_of,
        )
        right = await self._resolve_local_or_dynamic(
            request.right_instrument_id,
            as_of=effective_as_of,
        )
        try:
            left_leg = await self._basis_leg(left, effective_as_of)
            right_leg = await self._basis_leg(right, effective_as_of)
        except TradingPartnerError as exc:
            return self._service_failure(
                request_id,
                effective_as_of,
                exc,
                warnings=(_BASIS_UNAVAILABLE,),
                market=None,
            )
        snapshot = build_basis_snapshot(
            left_leg,
            right_leg,
            max_observation_lag_seconds=request.max_observation_lag_seconds,
            # OTC broker feed vs exchange futures is never a clean benchmark pair.
            indicative_only=True,
        )
        warnings: tuple[WarningInfo, ...] = ()
        if snapshot.comparability.value == "NOT_COMPARABLE":
            warnings = (_BASIS_NOT_COMPARABLE,)
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=effective_as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(
                SourceReference(name=VendorId.DUKASCOPY.value, role=SourceRole.PRIMARY),
                SourceReference(name=VendorId.YFINANCE.value, role=SourceRole.PRIMARY),
            ),
            data=BasisSnapshotDTO.from_domain(snapshot),
            degraded=bool(warnings),
            warnings=warnings,
        )

    async def _basis_leg(
        self, instrument: Instrument, as_of: datetime
    ) -> BasisLeg:
        if _is_otc_spot_or_cfd(instrument):
            assert self._commodity_spot is not None
            spot_result = await self._commodity_spot.get_quote(
                instrument, as_of=as_of
            )
            if not spot_result.ok or spot_result.data is None:
                raise spot_result.error or NoMarketData(
                    "spot leg unavailable for basis",
                    details={"instrument_id": instrument.instrument_id},
                )
            obs = spot_result.data
            price: Decimal | None = obs.mid if obs.mid is not None else obs.last
            if price is None and obs.bid is not None and obs.ask is not None:
                price = (obs.bid + obs.ask) / Decimal("2")
            if price is None or obs.quote_at is None:
                raise NoMarketData(
                    "spot observation lacks mid/last and quote_at for basis",
                    details={
                        "instrument_id": instrument.instrument_id,
                        "code": "BASIS_NOT_COMPARABLE",
                    },
                )
            return BasisLeg(
                instrument_id=obs.instrument_id,
                price=price,
                currency=obs.currency,
                unit=obs.unit,
                observed_at=obs.quote_at,
                price_basis=PriceBasis.MID if obs.mid is not None else PriceBasis.LAST,
                delivery_location=obs.delivery_location,
            )
        if (
            instrument.asset_type is AssetType.FUTURE
            and instrument.market is Market.CME
        ):
            quote_result = await self._data_service.get_quote(instrument, as_of)
            if not quote_result.ok or quote_result.value is None:
                raise quote_result.error or NoMarketData(
                    "futures leg unavailable for basis",
                    details={"instrument_id": instrument.instrument_id},
                )
            quote = quote_result.value
            return BasisLeg(
                instrument_id=quote.instrument_id,
                price=quote.last,
                currency="USD",
                unit=_futures_price_unit(instrument.symbol),
                observed_at=quote.quote_at,
                price_basis=PriceBasis.LAST,
                delivery_location=None,
            )
        raise DataContractError(
            "basis legs must be OTC spot/CFD or a specific CME future",
            details={
                "instrument_id": instrument.instrument_id,
                "market": instrument.market.value,
                "asset_type": instrument.asset_type.value,
            },
        )

    def _service_failure(
        self,
        request_id: str,
        effective_as_of: datetime,
        error: TradingPartnerError,
        *,
        warnings: tuple[WarningInfo, ...] = (),
        market: Market | None = Market.US,
    ) -> ToolEnvelope[Any]:
        return ToolEnvelope.failure(
            request_id=request_id,
            market=market,
            as_of=effective_as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[to_error_info(error, self._secret_redactor)],
            degraded=True,
            warnings=warnings,
            data=None,
        )

    def _exception_failure(
        self,
        request_id: str,
        effective_as_of: datetime,
        exc: BaseException,
        *,
        market: Market | None = Market.US,
    ) -> ToolEnvelope[Any]:
        error: ErrorInfo
        if isinstance(exc, TradingPartnerError):
            error = to_error_info(exc, self._secret_redactor)
        else:
            error = to_error_info_from_exception(exc, self._secret_redactor)
        return ToolEnvelope.failure(
            request_id=request_id,
            market=market,
            as_of=effective_as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[error],
            degraded=True,
            warnings=(),
            data=None,
        )


__all__ = ["MarketToolCoordinator"]
