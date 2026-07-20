"""Phase 1D D8a: MarketSnapshotCategoryAdapter over old MarketSnapshotProvider."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from application.ports.category_provider import CategoryProvider
from application.ports.market_snapshot_category_provider import (
    MarketSnapshotCategoryProvider,
)
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import ConfigurationError, DataContractError, InvalidInstrument
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.mock_market import (
    MockAShareMarketSnapshotProvider,
)
from infrastructure.providers.common.market_snapshot_category_adapter import (
    MarketSnapshotCategoryAdapter,
)
from infrastructure.providers.us.mock_market import MockUSMarketSnapshotProvider

AS_OF = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
SECRET = "test-secret-malicious-value"


class _NonBoolSupportsProvider:
    provider_name = "bad"

    def supports(self, market: Market) -> object:
        return "yes"  # not exact bool

    async def get_snapshot(self, instrument: Instrument, as_of: datetime) -> Any:
        raise AssertionError("must not be called")


class _RaisingSupportsProvider:
    provider_name = "raising"

    def supports(self, market: Market) -> bool:
        raise RuntimeError(f"supports boom credential={SECRET}")

    async def get_snapshot(self, instrument: Instrument, as_of: datetime) -> Any:
        raise AssertionError("must not be called")


class _TruthinessSupportsProvider:
    """Truthy non-bool must not be treated as support."""

    provider_name = "truthy"

    def supports(self, market: Market) -> object:
        return 1

    async def get_snapshot(self, instrument: Instrument, as_of: datetime) -> Any:
        raise AssertionError("must not be called")


def _a_share_adapter() -> MarketSnapshotCategoryAdapter:
    return MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_A_SHARE,
        provider=MockAShareMarketSnapshotProvider(),
    )


def _us_adapter() -> MarketSnapshotCategoryAdapter:
    return MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_US,
        provider=MockUSMarketSnapshotProvider(),
    )


def test_rejects_non_mock_vendor() -> None:
    with pytest.raises(ConfigurationError) as ei:
        MarketSnapshotCategoryAdapter(
            vendor_id=VendorId.YFINANCE,
            provider=MockUSMarketSnapshotProvider(),
        )
    err = ei.value
    assert err.details.get("rule") == "allowed_mock_vendors"
    blob = repr(err) + str(err.details)
    assert SECRET not in blob


def test_rejects_non_vendor_id_type() -> None:
    with pytest.raises(ConfigurationError) as ei:
        MarketSnapshotCategoryAdapter(
            vendor_id="mock_us",  # type: ignore[arg-type]
            provider=MockUSMarketSnapshotProvider(),
        )
    assert ei.value.details.get("field") == "vendor_id"


def test_provider_name_is_vendor_id_value_not_free_text() -> None:
    # Old provider also returns "mock_a_share", but adapter must not trust it.
    adapter = _a_share_adapter()
    assert adapter.provider_name == VendorId.MOCK_A_SHARE.value
    assert adapter.vendor_id is VendorId.MOCK_A_SHARE
    assert adapter.provider_name == adapter.vendor_id.value


def test_is_configured_always_true() -> None:
    assert _a_share_adapter().is_configured() is True
    assert _us_adapter().is_configured() is True


def test_supports_only_market_snapshot_and_matching_market() -> None:
    a = _a_share_adapter()
    u = _us_adapter()
    assert a.supports(Market.A_SHARE, DataCategory.MARKET_SNAPSHOT) is True
    assert a.supports(Market.US, DataCategory.MARKET_SNAPSHOT) is False
    assert a.supports(Market.A_SHARE, DataCategory.MARKET_OHLCV) is False
    assert a.supports(Market.A_SHARE, DataCategory.NEWS) is False
    assert u.supports(Market.US, DataCategory.MARKET_SNAPSHOT) is True
    assert u.supports(Market.A_SHARE, DataCategory.MARKET_SNAPSHOT) is False


def test_supports_non_bool_raises_data_contract_error() -> None:
    adapter = MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_US,
        provider=_NonBoolSupportsProvider(),  # type: ignore[arg-type]
    )
    with pytest.raises(DataContractError) as ei:
        adapter.supports(Market.US, DataCategory.MARKET_SNAPSHOT)
    assert ei.value.details.get("rule") == "exact_bool"
    assert SECRET not in repr(ei.value)


def test_supports_truthy_non_bool_rejected() -> None:
    adapter = MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_US,
        provider=_TruthinessSupportsProvider(),  # type: ignore[arg-type]
    )
    with pytest.raises(DataContractError) as ei:
        adapter.supports(Market.US, DataCategory.MARKET_SNAPSHOT)
    assert ei.value.details.get("rule") == "exact_bool"


def test_supports_exception_is_secret_safe() -> None:
    adapter = MarketSnapshotCategoryAdapter(
        vendor_id=VendorId.MOCK_US,
        provider=_RaisingSupportsProvider(),  # type: ignore[arg-type]
    )
    with pytest.raises(DataContractError) as ei:
        adapter.supports(Market.US, DataCategory.MARKET_SNAPSHOT)
    err = ei.value
    assert err.details.get("rule") == "exception_safe"
    blob = f"{err!r}{err.message}{err.details}"
    assert SECRET not in blob
    assert err.__cause__ is None


@pytest.mark.asyncio
async def test_get_snapshot_delegates_a_share_fixture(
    a_share_instrument: Instrument,
) -> None:
    adapter = _a_share_adapter()
    snapshot = await adapter.get_snapshot(a_share_instrument, AS_OF)
    assert snapshot.instrument is a_share_instrument
    assert snapshot.latest_market_row.close == Decimal("1505.00")
    assert snapshot.latest_market_row.open == Decimal("1500.00")
    assert snapshot.latest_market_row.high == Decimal("1510.00")
    assert snapshot.latest_market_row.low == Decimal("1490.00")
    assert snapshot.latest_market_row.volume == Decimal("100000")
    assert snapshot.recent_closes == (
        Decimal("1498.00"),
        Decimal("1501.00"),
        Decimal("1496.00"),
        Decimal("1502.00"),
        Decimal("1505.00"),
    )
    assert snapshot.algorithm_version == "mock-1.0.0"


@pytest.mark.asyncio
async def test_get_snapshot_delegates_us_fixture(
    us_instrument: Instrument,
) -> None:
    adapter = _us_adapter()
    as_of = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
    snapshot = await adapter.get_snapshot(us_instrument, as_of)
    assert snapshot.latest_market_row.close == Decimal("173.00")
    assert snapshot.latest_market_row.volume == Decimal("50000000")
    assert snapshot.algorithm_version == "mock-1.0.0"


@pytest.mark.asyncio
async def test_get_snapshot_propagates_invalid_instrument(
    us_instrument: Instrument,
) -> None:
    adapter = _a_share_adapter()
    with pytest.raises(InvalidInstrument):
        await adapter.get_snapshot(us_instrument, AS_OF)


def test_runtime_checkable_protocol_surface() -> None:
    adapter = _us_adapter()
    assert isinstance(adapter, CategoryProvider)
    assert isinstance(adapter, MarketSnapshotCategoryProvider)


def test_export_from_common_package() -> None:
    from infrastructure.providers.common import MarketSnapshotCategoryAdapter as exported

    assert exported is MarketSnapshotCategoryAdapter
