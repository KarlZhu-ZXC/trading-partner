"""Focused Phase 3A-0 domain identity, continuous mapping, and basis tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from application.dto.cross_asset import (
    FuturesContractDefinitionDTO,
    SpotFutureBasisInput,
    SpotObservationDTO,
)
from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError
from domain.common.values import build_instrument_id, parse_instrument_id
from domain.cross_asset.basis_service import (
    BASIS_FORMULA_VERSION,
    BasisLeg,
    build_basis_snapshot,
    classify_curve_shape,
)
from domain.cross_asset.enums import (
    BasisComparability,
    ContinuousAdjustment,
    ContractLifecycleStatus,
    CurveCompleteness,
    CurveShape,
    PriceBasis,
    RollRule,
    SettlementMethod,
    SpotVenueBasis,
)
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesCurveContractPoint,
    FuturesCurveSnapshot,
    FuturesProductDefinition,
)
from domain.cross_asset.spot_models import SpotObservation

_PRODUCT_ID = "futures_product_01901945-7f5d-7cc3-98c4-dc0c0c07398f"
_PRODUCT_VERSION_ID = "futures_product_version_01901945-7f5d-7cc3-98c4-dc0c0c073990"
_CONTRACT_VERSION_ID = "futures_contract_version_01901945-7f5d-7cc3-98c4-dc0c0c073991"
_AS_OF = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)


def _product(**overrides: object) -> FuturesProductDefinition:
    payload: dict[str, object] = {
        "product_id": _PRODUCT_ID,
        "product_key": "CME:GC",
        "root": "GC",
        "market": Market.CME,
        "exchange": "COMEX",
        "commodity": "gold",
        "currency": "USD",
        "price_unit": "USD/oz",
        "multiplier": Decimal("100"),
        "tick_size": Decimal("0.1"),
        "settlement_method": SettlementMethod.PHYSICAL,
        "session_calendar_id": "CME_METALS",
        "source": "fixture",
        "valid_from": datetime(2020, 1, 1, tzinfo=UTC),
        "definition_as_of": _AS_OF,
        "version_id": _PRODUCT_VERSION_ID,
        "version": 1,
    }
    payload.update(overrides)
    return FuturesProductDefinition(**payload)  # type: ignore[arg-type]


def test_market_and_asset_type_append_only_wire_values() -> None:
    assert Market.CME.value == "CME"
    assert Market.DCE.value == "DCE"
    assert Market.OTC.value == "OTC"
    assert Market.LME.value == "LME"
    assert AssetType.COMMODITY_SPOT.value == "commodity_spot"
    assert AssetType.CFD.value == "cfd"
    assert AssetType.BENCHMARK.value == "benchmark"
    # Existing wire values unchanged.
    assert Market.US.value == "US"
    assert AssetType.FUTURE.value == "future"


def test_parse_instrument_id_accepts_new_markets_and_assets() -> None:
    assert parse_instrument_id("future:CME:GCZ26") == (
        AssetType.FUTURE,
        Market.CME,
        "GCZ26",
    )
    assert parse_instrument_id("future:DCE:LH2609") == (
        AssetType.FUTURE,
        Market.DCE,
        "LH2609",
    )
    assert parse_instrument_id("commodity_spot:OTC:XAUUSD") == (
        AssetType.COMMODITY_SPOT,
        Market.OTC,
        "XAUUSD",
    )
    assert build_instrument_id(AssetType.FUTURE, Market.CME, "GC.v.0") == (
        "future:CME:GC.v.0"
    )
    # Legacy Yahoo proxies remain valid and unchanged.
    assert parse_instrument_id("future:US:GC=F")[2] == "GC=F"


def test_product_and_contract_fixtures_for_gcz26_and_lh2609() -> None:
    product = _product()
    assert product.product_key == "CME:GC"
    contract = FuturesContractDefinition(
        instrument_id="future:CME:GCZ26",
        product_id=_PRODUCT_ID,
        contract_month="2026-12",
        status=ContractLifecycleStatus.ACTIVE,
        definition_as_of=_AS_OF,
        version_id=_CONTRACT_VERSION_ID,
        last_trade_at=datetime(2026, 12, 29, 17, 0, tzinfo=UTC),
        expiration_at=datetime(2026, 12, 29, 17, 0, tzinfo=UTC),
        source="fixture",
    )
    assert contract.instrument_id == "future:CME:GCZ26"

    lh_product = _product(
        product_key="DCE:LH",
        root="LH",
        market=Market.DCE,
        exchange="DCE",
        commodity="live_hogs",
        currency="CNY",
        price_unit="CNY/tonne",
        multiplier=Decimal("16"),
        tick_size=Decimal("5"),
        session_calendar_id="DCE_LH",
    )
    lh = FuturesContractDefinition(
        instrument_id="future:DCE:LH2609",
        product_id=lh_product.product_id,
        contract_month="2026-09",
        status=ContractLifecycleStatus.ACTIVE,
        definition_as_of=_AS_OF,
        source="fixture",
    )
    assert lh.instrument_id == "future:DCE:LH2609"


def test_continuous_mapping_requires_distinct_contract() -> None:
    series = ContinuousSeriesDefinition(
        instrument_id="future:CME:GC.v.0",
        product_id=_PRODUCT_ID,
        roll_rule=RollRule.VOLUME,
        rank=0,
        adjustment=ContinuousAdjustment.NONE,
        provider_methodology_version="tp_roll_v1",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    mapping = ContinuousContractMapping(
        continuous_instrument_id=series.instrument_id,
        contract_instrument_id="future:CME:GCZ26",
        effective_from=datetime(2026, 7, 1, tzinfo=UTC),
        effective_to=datetime(2026, 8, 1, tzinfo=UTC),
        mapping_source="fixture",
    )
    assert mapping.contract_instrument_id == "future:CME:GCZ26"
    with pytest.raises(DataContractError, match="must differ"):
        ContinuousContractMapping(
            continuous_instrument_id="future:CME:GCZ26",
            contract_instrument_id="future:CME:GCZ26",
            effective_from=_AS_OF,
            mapping_source="fixture",
        )


def test_curve_shape_and_front_next_spread() -> None:
    near = FuturesCurveContractPoint(
        instrument_id="future:CME:GCZ26",
        contract_month="2026-12",
        expiration_at=datetime(2026, 12, 29, 17, 0, tzinfo=UTC),
        price=Decimal("2350.0"),
    )
    far = FuturesCurveContractPoint(
        instrument_id="future:CME:GCG27",
        contract_month="2027-02",
        expiration_at=datetime(2027, 2, 24, 17, 0, tzinfo=UTC),
        price=Decimal("2360.0"),
    )
    assert classify_curve_shape((near, far)) is CurveShape.CONTANGO
    spread = far.price - near.price
    curve = FuturesCurveSnapshot(
        product_id=_PRODUCT_ID,
        as_of=_AS_OF,
        price_basis=PriceBasis.SETTLEMENT,
        contracts=(near, far),
        curve_shape=CurveShape.CONTANGO,
        completeness=CurveCompleteness.COMPLETE,
        front_next_spread=spread,
    )
    assert curve.front_next_spread == Decimal("10.0")


def test_xauusd_spot_observation_and_basis_comparability() -> None:
    spot = SpotObservation(
        instrument_id="commodity_spot:OTC:XAUUSD",
        currency="USD",
        unit="USD/oz",
        quote_at=_AS_OF,
        venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
        source="fixture",
        bid=Decimal("2348.10"),
        ask=Decimal("2348.40"),
        mid=Decimal("2348.25"),
    )
    left = BasisLeg(
        instrument_id=spot.instrument_id,
        price=spot.mid or Decimal("0"),
        currency=spot.currency,
        unit=spot.unit,
        observed_at=_AS_OF,
        price_basis=PriceBasis.MID,
    )
    right = BasisLeg(
        instrument_id="future:CME:GCZ26",
        price=Decimal("2350.00"),
        currency="USD",
        unit="USD/oz",
        observed_at=_AS_OF,
        price_basis=PriceBasis.MID,
    )
    comparable = build_basis_snapshot(
        left,
        right,
        max_observation_lag_seconds=300,
    )
    assert comparable.comparability is BasisComparability.COMPARABLE
    assert comparable.absolute_spread == Decimal("-1.75")
    assert comparable.formula_version == BASIS_FORMULA_VERSION

    mismatched = build_basis_snapshot(
        left,
        BasisLeg(
            instrument_id="future:CME:GCZ26",
            price=Decimal("2350.00"),
            currency="USD",
            unit="USD/t.oz",
            observed_at=_AS_OF,
            price_basis=PriceBasis.MID,
        ),
        max_observation_lag_seconds=300,
    )
    assert mismatched.comparability is BasisComparability.NOT_COMPARABLE
    assert "UNIT_MISMATCH" in mismatched.reason_codes
    assert mismatched.absolute_spread is None


def test_dto_round_trip_scaffolding() -> None:
    contract = FuturesContractDefinition(
        instrument_id="future:CME:GCZ26",
        product_id=_PRODUCT_ID,
        contract_month="2026-12",
        status=ContractLifecycleStatus.ACTIVE,
        definition_as_of=_AS_OF,
        source="fixture",
    )
    dto = FuturesContractDefinitionDTO.from_domain(contract)
    assert dto.instrument_id == "future:CME:GCZ26"

    spot = SpotObservation(
        instrument_id="commodity_spot:OTC:XAUUSD",
        currency="USD",
        unit="USD/oz",
        quote_at=_AS_OF,
        venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
        source="fixture",
        last=Decimal("2348.25"),
    )
    spot_dto = SpotObservationDTO.from_domain(spot)
    assert spot_dto.venue_basis is SpotVenueBasis.DUKASCOPY_SWFX

    basis_input = SpotFutureBasisInput(
        left_instrument_id="commodity_spot:OTC:XAUUSD",
        right_instrument_id="future:CME:GCZ26",
        max_observation_lag_seconds=120,
        as_of=_AS_OF,
    )
    assert basis_input.max_observation_lag_seconds == 120
    with pytest.raises(ValueError):
        SpotFutureBasisInput(
            left_instrument_id="future:CME:GCZ26",
            right_instrument_id="future:CME:GCZ26",
        )


def test_product_rejects_mismatched_product_key() -> None:
    with pytest.raises(DataContractError, match="product_key"):
        _product(product_key="CME:SI", root="GC")


def test_contract_rejects_bad_month_and_float_prices() -> None:
    with pytest.raises(DataContractError, match="YYYY-MM"):
        FuturesContractDefinition(
            instrument_id="future:CME:GCZ26",
            product_id=_PRODUCT_ID,
            contract_month="202612",
            status=ContractLifecycleStatus.ACTIVE,
            definition_as_of=_AS_OF,
        )
    with pytest.raises(DataContractError, match="float"):
        FuturesCurveContractPoint(
            instrument_id="future:CME:GCZ26",
            contract_month="2026-12",
            expiration_at=None,
            price=2350.0,  # type: ignore[arg-type]
        )
