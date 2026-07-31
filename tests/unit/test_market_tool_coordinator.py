"""Focused Phase 3A MarketToolCoordinator routing tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.cross_asset import FuturesCurveSnapshotDTO, SpotObservationDTO
from application.dto.provider_routing import ProviderResultMeta
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetBatchQuotesInput,
    MarketGetContextInput,
    MarketGetSnapshotInput,
    USQuoteDTO,
)
from application.services.commodity_spot_service import (
    CommoditySpotBarsResult,
    CommoditySpotQuoteResult,
)
from application.services.futures_curve_service import FuturesCurveResult
from application.services.instrument_access_service import InstrumentAccessService
from application.services.market_tool_coordinator import MarketToolCoordinator
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.cross_asset.enums import (
    CurveCompleteness,
    CurveShape,
    OfferSide,
    PriceBasis,
    SpotVenueBasis,
)
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)

_US_EQ = Instrument(
    instrument_id="equity:US:NVDA",
    symbol="NVDA",
    name="NVDA",
    market=Market.US,
    exchange="NASDAQ",
    currency="USD",
    timezone="America/New_York",
    asset_type=AssetType.EQUITY,
)
_US_ETF = Instrument(
    instrument_id="etf:US:UGL",
    symbol="UGL",
    name="ProShares Ultra Gold",
    market=Market.US,
    exchange="ARCA",
    currency="USD",
    timezone="America/New_York",
    asset_type=AssetType.ETF,
)
_CME_FUT = Instrument(
    instrument_id="future:CME:GCZ26",
    symbol="GCZ26",
    name="Gold Dec 2026",
    market=Market.CME,
    exchange="COMEX",
    currency="USD",
    timezone="America/New_York",
    asset_type=AssetType.FUTURE,
)
_OTC_XAU = Instrument(
    instrument_id="commodity_spot:OTC:XAUUSD",
    symbol="XAUUSD",
    name="Gold OTC",
    market=Market.OTC,
    exchange="SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)


def _envelope_us_quote() -> ToolEnvelope[USQuoteDTO]:
    dto = USQuoteDTO(
        instrument_id="equity:US:NVDA",
        quote_at=AS_OF,
        session=TradingSession.REGULAR,
        last=Decimal("100"),
        open=None,
        high=None,
        low=None,
        previous_close=None,
        volume=None,
        average_volume=None,
        market_cap=None,
        beta=None,
        week_52_low=None,
        week_52_high=None,
    )
    return ToolEnvelope.success(
        request_id="req_us",
        market=Market.US,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.FRESH,
        sources=(),
        data=dto,
        degraded=False,
        warnings=(),
    )


def _coordinator(
    *,
    instruments: dict[str, Instrument] | None = None,
    commodity: MagicMock | None = None,
    curve: MagicMock | None = None,
) -> tuple[MarketToolCoordinator, MagicMock]:
    instruments = instruments or {
        _US_EQ.instrument_id: _US_EQ,
        _CME_FUT.instrument_id: _CME_FUT,
        _OTC_XAU.instrument_id: _OTC_XAU,
    }
    master = MagicMock()
    master.get.side_effect = lambda iid: instruments[iid]
    us = MagicMock()
    us.get_market_snapshot = AsyncMock(return_value=_envelope_us_quote())
    us.get_market_bars = AsyncMock(return_value=_envelope_us_quote())
    # Minimal non-null data placeholder for us_market context delegation.
    us.get_market_context = AsyncMock(
        return_value=ToolEnvelope.success(
            request_id="req_ctx",
            market=Market.US,
            as_of=AS_OF,
            fetched_at=AS_OF,
            freshness=Freshness.FRESH,
            sources=(),
            data={"operation": "us_market"},
            degraded=False,
            warnings=(),
        )
    )
    data = MagicMock()
    access = InstrumentAccessService(master, MagicMock())
    coord = MarketToolCoordinator(
        instrument_access=access,
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        us_tool_coordinator=us,
        data_service=data,
        commodity_spot_service=commodity,
        futures_curve_service=curve,
    )
    return coord, us


@pytest.mark.asyncio
async def test_snapshot_routes_us_to_us_coordinator() -> None:
    coord, us = _coordinator()
    env = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_US_EQ.instrument_id, as_of=AS_OF)
    )
    assert env.ok is True
    us.get_market_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_routes_cme_to_us_coordinator() -> None:
    coord, us = _coordinator()
    await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_CME_FUT.instrument_id, as_of=AS_OF)
    )
    us.get_market_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_quotes_return_one_typed_result_per_requested_instrument() -> None:
    coord, us = _coordinator()

    envelope = await coord.get_market_snapshots(
        MarketGetBatchQuotesInput(
            instrument_ids=(_US_EQ.instrument_id, _CME_FUT.instrument_id),
            as_of=AS_OF,
        )
    )

    assert envelope.ok is True
    assert envelope.data is not None
    assert envelope.data.total_requested == 2
    assert envelope.data.succeeded == 2
    assert [item.instrument_id for item in envelope.data.items] == [
        _US_EQ.instrument_id,
        _CME_FUT.instrument_id,
    ]
    assert us.get_market_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_batch_quotes_propagate_successful_degraded_items() -> None:
    coord, us = _coordinator()
    original = _envelope_us_quote()
    us.get_market_snapshot.return_value = ToolEnvelope.success(
        request_id=original.request_id,
        market=original.market,
        as_of=original.as_of,
        fetched_at=original.fetched_at,
        freshness=Freshness.DELAYED,
        sources=original.sources,
        data=original.data,
        degraded=True,
        warnings=(WarningInfo(code="DELAYED_US_DATA", message="delayed"),),
    )

    envelope = await coord.get_market_snapshots(
        MarketGetBatchQuotesInput(
            instrument_ids=(_US_EQ.instrument_id,),
            as_of=AS_OF,
        )
    )

    assert envelope.ok is True
    assert envelope.degraded is True
    assert [warning.code for warning in envelope.warnings] == [
        "BATCH_QUOTE_ITEMS_DEGRADED"
    ]


@pytest.mark.asyncio
async def test_snapshot_dynamic_resolves_cme_on_local_miss() -> None:
    from domain.common.errors import InvalidInstrument

    instruments: dict[str, Instrument] = {
        _US_EQ.instrument_id: _US_EQ,
        _OTC_XAU.instrument_id: _OTC_XAU,
    }
    master = MagicMock()

    def _get(iid: str) -> Instrument:
        if iid not in instruments:
            raise InvalidInstrument("instrument not found", details={"instrument_id": iid})
        return instruments[iid]

    master.get.side_effect = _get

    async def _resolve_dynamic(**kwargs: object) -> ToolEnvelope[object]:
        instruments[_CME_FUT.instrument_id] = _CME_FUT
        return ToolEnvelope.success(
            request_id="req_resolve",
            market=Market.CME,
            as_of=AS_OF,
            fetched_at=AS_OF,
            freshness=Freshness.FRESH,
            sources=(),
            data=MagicMock(instrument=MagicMock(instrument_id=_CME_FUT.instrument_id)),
            degraded=False,
            warnings=(),
        )

    resolve = MagicMock()
    resolve.resolve_dynamic = AsyncMock(side_effect=_resolve_dynamic)
    us = MagicMock()
    us.get_market_snapshot = AsyncMock(return_value=_envelope_us_quote())
    coord = MarketToolCoordinator(
        instrument_access=InstrumentAccessService(master, resolve),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        us_tool_coordinator=us,
        data_service=MagicMock(),
    )
    env = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_CME_FUT.instrument_id, as_of=AS_OF)
    )
    assert env.ok is True
    resolve.resolve_dynamic.assert_awaited_once()
    us.get_market_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_dynamic_resolves_us_etf_on_local_miss() -> None:
    from domain.common.errors import InvalidInstrument

    instruments: dict[str, Instrument] = {}
    master = MagicMock()

    def _get(iid: str) -> Instrument:
        if iid not in instruments:
            raise InvalidInstrument("instrument not found", details={"instrument_id": iid})
        return instruments[iid]

    master.get.side_effect = _get

    async def _resolve_dynamic(**kwargs: object) -> ToolEnvelope[object]:
        instruments[_US_ETF.instrument_id] = _US_ETF
        return ToolEnvelope.success(
            request_id="req_resolve",
            market=Market.US,
            as_of=AS_OF,
            fetched_at=AS_OF,
            freshness=Freshness.FRESH,
            sources=(),
            data=MagicMock(instrument=MagicMock(instrument_id=_US_ETF.instrument_id)),
            degraded=False,
            warnings=(),
        )

    resolve = MagicMock()
    resolve.resolve_dynamic = AsyncMock(side_effect=_resolve_dynamic)
    us = MagicMock()
    us.get_market_snapshot = AsyncMock(return_value=_envelope_us_quote())
    coord = MarketToolCoordinator(
        instrument_access=InstrumentAccessService(master, resolve),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        us_tool_coordinator=us,
        data_service=MagicMock(),
    )

    env = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_US_ETF.instrument_id, as_of=AS_OF)
    )

    assert env.ok is True
    resolve.resolve_dynamic.assert_awaited_once_with(
        market=Market.US,
        query=_US_ETF.instrument_id,
        asset_type_hint=AssetType.ETF,
        as_of=AS_OF,
    )
    us.get_market_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_preserves_dynamic_directory_failure_type() -> None:
    from application.dto.tool_envelope import ErrorInfo
    from domain.common.errors import InvalidInstrument

    master = MagicMock()
    master.get.side_effect = InvalidInstrument("instrument not found")
    resolve = MagicMock()
    resolve.resolve_dynamic = AsyncMock(
        return_value=ToolEnvelope.failure(
            request_id="req_resolve",
            market=Market.US,
            as_of=AS_OF,
            fetched_at=AS_OF,
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=(
                ErrorInfo(
                    code="PROVIDER_UNAVAILABLE_ERROR",
                    message="instrument directory unavailable",
                    retryable=True,
                    details={"vendor": "yfinance"},
                ),
            ),
            degraded=True,
        )
    )
    us = MagicMock()
    coord = MarketToolCoordinator(
        instrument_access=InstrumentAccessService(master, resolve),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        us_tool_coordinator=us,
        data_service=MagicMock(),
    )

    env = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_US_ETF.instrument_id, as_of=AS_OF)
    )

    assert env.ok is False
    assert env.errors[0].code == "PROVIDER_UNAVAILABLE_ERROR"
    assert env.errors[0].retryable is True
    us.get_market_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_dce_is_typed_unavailable() -> None:
    dce = Instrument(
        instrument_id="future:DCE:LH2609",
        symbol="LH2609",
        name="Live Hogs 2026-09",
        market=Market.DCE,
        exchange="DCE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.FUTURE,
        multiplier=Decimal("16"),
        tick_size=Decimal("5"),
    )
    # MarketGetSnapshotInput rejects DCE at DTO layer; exercise coordinator path
    # via internal resolve after bypassing the input validator by using CME id
    # that is remapped... Instead call with a pre-resolved DCE instrument through
    # a local master hit and a request that uses an accepted market id shape.
    # Direct coordinator method with monkeypatched resolve is cleaner:
    coord, us = _coordinator(instruments={dce.instrument_id: dce})
    # Patch request validation is strict; call private path via get with OTC-like
    # instrument_id not available. Use object.__setattr__ free Input construction:
    request = MarketGetSnapshotInput.model_construct(instrument_id=dce.instrument_id, as_of=AS_OF)
    env = await coord.get_market_snapshot(request)
    assert env.ok is False
    assert any(err.code in {"NO_MARKET_DATA", "DATA_CONTRACT_ERROR"} for err in env.errors)
    us.get_market_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_routes_otc_to_commodity_spot() -> None:
    commodity = MagicMock()
    spot = SpotObservationDTO(
        instrument_id=_OTC_XAU.instrument_id,
        currency="USD",
        unit="USD/oz",
        quote_at=AS_OF,
        venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
        source="dukascopy",
        bid=Decimal("2400"),
        ask=Decimal("2401"),
        mid=Decimal("2400.5"),
        last=None,
        delivery_location=None,
    )
    commodity.get_quote = AsyncMock(
        return_value=CommoditySpotQuoteResult(
            ok=True,
            data=spot,
            warnings=(WarningInfo(code="DUKASCOPY_SWFX_NOT_LBMA", message="not lbma"),),
            error=None,
        )
    )
    coord, us = _coordinator(commodity=commodity)
    env = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_OTC_XAU.instrument_id, as_of=AS_OF)
    )
    assert env.ok is True
    assert env.market == Market.OTC
    assert env.data is not None
    assert env.data.instrument_id == _OTC_XAU.instrument_id
    us.get_market_snapshot.assert_not_awaited()
    commodity.get_quote.assert_awaited_once()


@pytest.mark.asyncio
async def test_bars_otc_forces_none_and_offer_side() -> None:
    from application.dto.cross_asset import CommoditySpotBarSeriesDTO
    from application.dto.market import MarketBarDTO
    from domain.cross_asset.enums import SpotVolumeBasis

    commodity = MagicMock()
    series = CommoditySpotBarSeriesDTO(
        instrument_id=_OTC_XAU.instrument_id,
        interval=USBarInterval.ONE_DAY,
        offer_side=OfferSide.ASK,
        start=date(2026, 7, 1),
        end=date(2026, 7, 2),
        adjustment=AdjustmentMethod.NONE,
        bars=(
            MarketBarDTO(
                timestamp=AS_OF,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("0"),
            ),
        ),
        volume_basis=SpotVolumeBasis.BEST_BID_ASK_VOLUME,
    )
    commodity.get_bars = AsyncMock(
        return_value=CommoditySpotBarsResult(
            ok=True,
            data=series,
            warnings=(),
            error=None,
            meta=ProviderResultMeta(
                vendor=VendorId.DUKASCOPY,
                category=DataCategory.MARKET_OHLCV,
                role=SourceRole.PRIMARY,
                as_of=AS_OF,
                fetched_at=AS_OF,
                freshness=Freshness.FRESH,
                session=TradingSession.UNKNOWN,
                latency_ms=1,
                cache_disposition=CacheDisposition.MISS,
                adjustment=AdjustmentMethod.NONE,
                data_delay_seconds=0,
                warnings=(),
            ),
        )
    )
    coord, us = _coordinator(commodity=commodity)
    env = await coord.get_market_bars(
        MarketGetBarsInput(
            instrument_id=_OTC_XAU.instrument_id,
            start=date(2026, 7, 1),
            end=date(2026, 7, 2),
            offer_side="A",
            as_of=AS_OF,
        )
    )
    assert env.ok is True
    assert env.data is not None
    assert env.data.offer_side is OfferSide.ASK
    us.get_market_bars.assert_not_awaited()
    kwargs = commodity.get_bars.await_args.kwargs
    assert kwargs["offer_side"] is OfferSide.ASK
    assert kwargs["adjustment"] is AdjustmentMethod.NONE


@pytest.mark.asyncio
async def test_context_futures_curve_uses_curve_service() -> None:
    curve_svc = MagicMock()
    curve_data = FuturesCurveSnapshotDTO(
        product_id="futures_product_test",
        as_of=AS_OF,
        price_basis=PriceBasis.SETTLEMENT,
        contracts=(),
        curve_shape=CurveShape.NOT_EVALUATED,
        completeness=CurveCompleteness.EMPTY,
        front_next_spread=None,
    )
    curve_svc.build_curve = AsyncMock(
        return_value=FuturesCurveResult(ok=True, data=curve_data, warnings=(), error=None)
    )
    coord, us = _coordinator(curve=curve_svc)
    env = await coord.get_market_context(
        MarketGetContextInput(
            operation="futures_curve",
            product_key="DCE:LH",
            price_basis="settlement",
            contract_limit=6,
            as_of=AS_OF,
        )
    )
    assert env.ok is True
    assert env.market == Market.DCE
    curve_svc.build_curve.assert_awaited_once()
    us.get_market_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_spot_future_basis_unavailable_without_spot_service() -> None:
    coord, _us = _coordinator(commodity=None)
    env = await coord.get_market_context(
        MarketGetContextInput(
            operation="spot_future_basis",
            left_instrument_id=_OTC_XAU.instrument_id,
            right_instrument_id=_CME_FUT.instrument_id,
            as_of=AS_OF,
        )
    )
    assert env.ok is False
    assert any(e.code for e in env.errors)
    assert any(w.code == "SPOT_FUTURE_BASIS_UNAVAILABLE" for w in env.warnings)


@pytest.mark.asyncio
async def test_context_default_us_market_delegates() -> None:
    coord, us = _coordinator()
    await coord.get_market_context(MarketGetContextInput(as_of=AS_OF))
    us.get_market_context.assert_awaited_once()


def test_snapshot_input_accepts_cme_and_otc() -> None:
    cme = MarketGetSnapshotInput.model_validate({"instrument_id": "future:CME:GCZ26"})
    otc = MarketGetSnapshotInput.model_validate({"instrument_id": "commodity_spot:OTC:XAUUSD"})
    cfd = MarketGetSnapshotInput.model_validate({"instrument_id": "cfd:OTC:COPPER_CMD_USD"})
    assert cme.instrument_id.startswith("future:CME:")
    assert otc.instrument_id.startswith("commodity_spot:OTC:")
    assert cfd.instrument_id.startswith("cfd:OTC:")


def test_context_input_requires_product_key_for_curve() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MarketGetContextInput.model_validate({"operation": "futures_curve"})
    ok = MarketGetContextInput.model_validate(
        {"operation": "futures_curve", "product_key": "CME:GC"}
    )
    assert ok.product_key == "CME:GC"
    assert ok.operation == "futures_curve"
    assert ok.price_basis == "settlement"


@pytest.mark.asyncio
async def test_otc_quote_unavailable_when_service_missing() -> None:
    coord, _us = _coordinator(commodity=None)
    env = await coord.get_market_snapshot(
        MarketGetSnapshotInput(instrument_id=_OTC_XAU.instrument_id, as_of=AS_OF)
    )
    assert env.ok is False
    assert env.errors
