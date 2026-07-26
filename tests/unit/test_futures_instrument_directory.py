"""Focused FuturesInstrumentDirectory discovery/cache tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.cross_asset import (
    FuturesContractDefinitionDTO,
    FuturesProductDefinitionDTO,
)
from application.services.futures_contract_service import (
    FuturesContractListResult,
    FuturesProductResult,
)
from application.services.futures_instrument_directory import FuturesInstrumentDirectory
from conftest import FixedClock
from domain.common.enums import AssetType, DataCategory, Market, VendorId
from domain.cross_asset.enums import ContractLifecycleStatus, SettlementMethod

AS_OF = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
PRODUCT_ID = "futures_product_019f3a01-c0e0-7000-8000-0000000000a1"


def _product() -> FuturesProductDefinitionDTO:
    return FuturesProductDefinitionDTO(
        product_id=PRODUCT_ID,
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
        definition_as_of=AS_OF,
        version_id="futures_product_version_019f3a01-c0e0-7000-8000-0000000000b1",
        version=1,
    )


def _contract(
    *,
    instrument_id: str = "future:CME:GCZ26",
    status: ContractLifecycleStatus = ContractLifecycleStatus.ACTIVE,
) -> FuturesContractDefinitionDTO:
    return FuturesContractDefinitionDTO(
        instrument_id=instrument_id,
        product_id=PRODUCT_ID,
        contract_month="2026-12",
        status=status,
        definition_as_of=AS_OF,
        version_id="futures_contract_version_019f3a01-c0e0-7000-8000-0000000000c1",
        version=1,
        source="cme_public",
    )


def _directory(service: MagicMock) -> FuturesInstrumentDirectory:
    return FuturesInstrumentDirectory(
        market=Market.CME,
        vendor_id=VendorId.CME_PUBLIC,
        contract_service=service,
        clock=FixedClock(AS_OF),
    )


@pytest.mark.asyncio
async def test_lookup_returns_exactly_one_provider_validated_active_contract() -> None:
    service = MagicMock()
    service.get_product = AsyncMock(
        return_value=FuturesProductResult(
            ok=True, data=_product(), warnings=(), error=None, from_cache=True
        )
    )
    service.list_contracts = AsyncMock(
        return_value=FuturesContractListResult(
            ok=True,
            data=(
                _contract(instrument_id="future:CME:GCG27", status=ContractLifecycleStatus.ACTIVE),
                _contract(instrument_id="future:CME:GCZ26", status=ContractLifecycleStatus.ACTIVE),
            ),
            warnings=(),
            error=None,
            from_cache=False,
        )
    )
    directory = _directory(service)

    result = await directory.lookup(
        market=Market.CME,
        query="future:CME:GCZ26",
        asset_type_hint=AssetType.FUTURE,
        as_of=AS_OF,
    )

    assert len(result.value) == 1
    instrument = result.value[0]
    assert instrument.instrument_id == "future:CME:GCZ26"
    assert instrument.symbol == "GCZ26"
    assert instrument.exchange == "COMEX"
    assert instrument.currency == "USD"
    assert instrument.timezone == "America/New_York"
    assert instrument.multiplier == Decimal("100")
    assert instrument.tick_size == Decimal("0.1")
    assert instrument.asset_type is AssetType.FUTURE
    assert instrument.market is Market.CME
    assert result.meta.vendor is VendorId.CME_PUBLIC
    service.list_contracts.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_rejects_expired_and_does_not_guess_missing_contract() -> None:
    service = MagicMock()
    service.get_product = AsyncMock(
        return_value=FuturesProductResult(
            ok=True, data=_product(), warnings=(), error=None, from_cache=True
        )
    )
    service.list_contracts = AsyncMock(
        return_value=FuturesContractListResult(
            ok=True,
            data=(
                _contract(
                    instrument_id="future:CME:GCZ24",
                    status=ContractLifecycleStatus.EXPIRED,
                ),
            ),
            warnings=(),
            error=None,
            from_cache=True,
        )
    )
    directory = _directory(service)

    expired = await directory.lookup(
        market=Market.CME,
        query="GCZ24",
        asset_type_hint=AssetType.FUTURE,
        as_of=AS_OF,
    )
    missing = await directory.lookup(
        market=Market.CME,
        query="GCZ99",
        asset_type_hint=AssetType.FUTURE,
        as_of=AS_OF,
    )

    assert expired.value == ()
    assert missing.value == ()


@pytest.mark.asyncio
async def test_lookup_ignores_continuous_series_symbols() -> None:
    service = MagicMock()
    directory = _directory(service)

    result = await directory.lookup(
        market=Market.CME,
        query="GC.v.0",
        asset_type_hint=AssetType.FUTURE,
        as_of=AS_OF,
    )

    assert result.value == ()
    service.get_product.assert_not_called()


def test_directory_supports_only_configured_market() -> None:
    directory = _directory(MagicMock())
    assert directory.supports(Market.CME, DataCategory.INSTRUMENT_MASTER) is True
    assert directory.supports(Market.DCE, DataCategory.INSTRUMENT_MASTER) is False
    assert directory.supports(Market.CME, DataCategory.MARKET_QUOTE) is False
