"""Append-only futures definition repository tests (migration 0017 tables)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.ports.futures_definition_repository import FuturesDefinitionBatch
from domain.common.enums import Market
from domain.common.errors import PersistenceError
from domain.cross_asset.enums import (
    ContinuousAdjustment,
    ContractLifecycleStatus,
    RollRule,
    SettlementMethod,
    SettlementStatus,
)
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesProductDefinition,
)
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.sqlalchemy_futures_definition_repository import (
    SqlAlchemyFuturesDefinitionRepository,
)

_PRODUCT_ID = "futures_product_019f3a01-c0e0-7000-8000-0000000000a1"
_PRODUCT_VERSION = "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b1"
_CONTRACT_VERSION = "futures_contract_version_019f3a01-c0e0-7000-8000-0000000000c1"
_AS_OF = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)


def _repo() -> SqlAlchemyFuturesDefinitionRepository:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return SqlAlchemyFuturesDefinitionRepository(engine)


def _product() -> FuturesProductDefinition:
    return FuturesProductDefinition(
        product_id=_PRODUCT_ID,
        product_key="CME:GC",
        root="GC",
        market=Market.CME,
        exchange="COMEX",
        commodity="gold",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("100"),
        tick_size=Decimal("0.1"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        source="cme_public_seed",
        valid_from=datetime(2010, 1, 1, tzinfo=UTC),
        definition_as_of=_AS_OF,
        version_id=_PRODUCT_VERSION,
        version=1,
    )


def _contract() -> FuturesContractDefinition:
    return FuturesContractDefinition(
        instrument_id="future:CME:GCZ26",
        product_id=_PRODUCT_ID,
        contract_month="2026-12",
        status=ContractLifecycleStatus.ACTIVE,
        definition_as_of=_AS_OF,
        version_id=_CONTRACT_VERSION,
        version=1,
        expiration_at=datetime(2026, 12, 29, 17, 0, tzinfo=UTC),
        last_trade_at=datetime(2026, 12, 29, 17, 0, tzinfo=UTC),
        source="cme_public",
    )


def test_append_only_product_and_contract_round_trip() -> None:
    repo = _repo()
    product = _product()
    contract = _contract()
    repo.save_definition_batch(
        FuturesDefinitionBatch(products=(product,), contracts=(contract,))
    )
    # Idempotent re-save of same version.
    repo.save_definition_batch(
        FuturesDefinitionBatch(products=(product,), contracts=(contract,))
    )
    loaded = repo.get_product("CME:GC", _AS_OF)
    assert loaded is not None
    assert loaded.multiplier == Decimal("100")
    contracts = repo.list_contracts(_PRODUCT_ID, _AS_OF)
    assert len(contracts) == 1
    assert contracts[0].instrument_id == "future:CME:GCZ26"


def test_product_version_conflict_is_typed() -> None:
    repo = _repo()
    product = _product()
    repo.save_definition_batch(FuturesDefinitionBatch(products=(product,)))
    drifted = FuturesProductDefinition(
        product_id=_PRODUCT_ID,
        product_key="CME:GC",
        root="GC",
        market=Market.CME,
        exchange="COMEX",
        commodity="gold",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("200"),  # conflict
        tick_size=Decimal("0.1"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        source="cme_public_seed",
        valid_from=datetime(2010, 1, 1, tzinfo=UTC),
        definition_as_of=_AS_OF,
        version_id=_PRODUCT_VERSION,
        version=1,
    )
    with pytest.raises(PersistenceError):
        repo.save_definition_batch(FuturesDefinitionBatch(products=(drifted,)))


def test_continuous_series_and_mapping() -> None:
    repo = _repo()
    product = _product()
    contract = _contract()
    series = ContinuousSeriesDefinition(
        instrument_id="future:CME:GC.v.0",
        product_id=_PRODUCT_ID,
        roll_rule=RollRule.VOLUME,
        rank=0,
        adjustment=ContinuousAdjustment.NONE,
        provider_methodology_version="tp_continuous_v1",
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
    )
    mapping = ContinuousContractMapping(
        continuous_instrument_id="future:CME:GC.v.0",
        contract_instrument_id="future:CME:GCZ26",
        effective_from=_AS_OF,
        mapping_source="cme_public:volume",
    )
    repo.save_definition_batch(
        FuturesDefinitionBatch(
            products=(product,),
            contracts=(contract,),
            continuous_series=(series,),
            mappings=(mapping,),
        )
    )
    loaded = repo.get_continuous_series("future:CME:GC.v.0", _AS_OF)
    assert loaded is not None
    assert loaded.roll_rule is RollRule.VOLUME
    mappings = repo.list_continuous_mappings(
        "future:CME:GC.v.0", start=_AS_OF, end=_AS_OF
    )
    assert len(mappings) == 1
    assert mappings[0].contract_instrument_id == "future:CME:GCZ26"


def test_explicit_statistics_sync_is_idempotent() -> None:
    repo = _repo()
    repo.save_definition_batch(
        FuturesDefinitionBatch(products=(_product(),), contracts=(_contract(),))
    )
    observation = FuturesContractStatistics(
        instrument_id="future:CME:GCZ26",
        trade_date=date(2026, 7, 24),
        settlement=Decimal("3375.4"),
        settlement_status=SettlementStatus.FINAL,
        session_volume=Decimal("12345"),
        open_interest=Decimal("98765"),
        published_at=_AS_OF,
        source="cme_public",
    )
    assert repo.save_statistics((observation,)) == 1
    assert repo.save_statistics((observation,)) == 0
