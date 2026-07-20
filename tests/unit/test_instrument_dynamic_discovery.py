"""Focused tests for local-first provider-backed instrument discovery."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.services.instrument_master_service import InstrumentMasterService
from application.services.instrument_resolve_service import InstrumentResolveService
from conftest import FixedClock, SequentialIdGenerator
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
from domain.common.errors import ProviderUnavailableError
from domain.instruments.models import Instrument
from infrastructure.persistence.instrument_unit_of_work import SqlAlchemyInstrumentUnitOfWork
from infrastructure.persistence.metadata import Base
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 18, 4, 0, tzinfo=UTC)


class _Directory:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.calls = 0

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.YFINANCE

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.INSTRUMENT_MASTER

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
        self.calls += 1
        if self.failure:
            raise ProviderUnavailableError("directory unavailable")
        instrument = Instrument(
            instrument_id="equity:US:KO",
            symbol="KO",
            name="The Coca-Cola Company",
            market=Market.US,
            exchange="NYSE",
            currency="USD",
            timezone="America/New_York",
            asset_type=AssetType.EQUITY,
            country="US",
        )
        return ProviderSuccess(
            value=(instrument,),
            meta=ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.INSTRUMENT_MASTER,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=NOW,
                freshness=Freshness.FRESH,
                session=TradingSession.UNKNOWN,
                latency_ms=1,
                cache_disposition=CacheDisposition.BYPASS,
                adjustment=None,
                data_delay_seconds=None,
                warnings=(),
            ),
        )


def _service(tmp_path: Path, directory: _Directory) -> InstrumentResolveService:
    engine = create_engine(f"sqlite:///{tmp_path / 'dynamic.db'}")
    Base.metadata.create_all(engine)
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    return InstrumentResolveService(
        master=InstrumentMasterService(factory),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        directories={Market.US: (directory,)},
    )


@pytest.mark.asyncio
async def test_unique_external_candidate_is_cached(tmp_path: Path) -> None:
    directory = _Directory()
    service = _service(tmp_path, directory)

    first = await service.resolve_dynamic(market=Market.US, query="KO")
    second = await service.resolve_dynamic(market=Market.US, query="KO")

    assert first.ok is True
    assert first.sources[0].name == VendorId.YFINANCE.value
    assert first.data is not None and first.data.instrument is not None
    assert first.data.instrument.instrument_id == "equity:US:KO"
    assert second.ok is True
    assert second.sources[0].name == VendorId.LOCAL_MASTER.value
    assert directory.calls == 1


@pytest.mark.asyncio
async def test_provider_failure_is_not_mislabeled_invalid_instrument(tmp_path: Path) -> None:
    service = _service(tmp_path, _Directory(failure=True))

    result = await service.resolve_dynamic(market=Market.US, query="KO")

    assert result.ok is False
    assert result.errors[0].code == "PROVIDER_UNAVAILABLE_ERROR"
