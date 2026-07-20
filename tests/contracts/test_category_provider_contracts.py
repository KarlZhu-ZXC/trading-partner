"""Phase 1D D9 contracts for NullCategoryProvider and UnimplementedVendorAdapter."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from application.ports.category_provider import CategoryProvider
from application.ports.market_snapshot_category_provider import (
    MarketSnapshotCategoryProvider,
)
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    DataCategory,
    Market,
    VendorId,
)
from domain.common.errors import ConfigurationError, ProviderNotConfigured
from domain.instruments.models import Instrument
from infrastructure.providers.common.null_category_provider import NullCategoryProvider
from infrastructure.providers.common.unimplemented_vendor_adapter import (
    UnimplementedVendorAdapter,
)
from infrastructure.providers.registry import VendorRegistry

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
SECRET = "test-secret-malicious-value"

_DATA_METHODS = (
    "get_snapshot",
    "get_quote",
    "get_ohlcv",
    "get_fundamentals",
    "list_filings",
    "get_news",
    "get_macro_series",
    "get_sentiment",
    "get_account_snapshot",
    "lookup",
)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _assert_provider_not_configured(
    exc: ProviderNotConfigured,
    *,
    expected_vendor: str,
    forbidden: str = SECRET,
) -> None:
    assert isinstance(exc, ProviderNotConfigured)
    assert exc.code == "PROVIDER_NOT_CONFIGURED"
    assert exc.retryable is False
    assert exc.__cause__ is None
    assert forbidden not in exc.message
    assert forbidden not in repr(exc.details)
    assert forbidden not in str(exc.details)
    assert exc.details.get("vendor") == expected_vendor


# --- NullCategoryProvider ----------------------------------------------------


def test_null_identity_and_category_provider_surface() -> None:
    provider = NullCategoryProvider()
    assert isinstance(provider, CategoryProvider)
    assert isinstance(provider, MarketSnapshotCategoryProvider)
    assert provider.vendor_id is VendorId.NULL
    assert provider.provider_name == VendorId.NULL.value == "null"
    assert provider.is_configured() is True
    assert type(provider.is_configured()) is bool
    for market in Market:
        for category in DataCategory:
            assert provider.supports(market, category) is True
            assert type(provider.supports(market, category)) is bool


def test_null_data_methods_are_async() -> None:
    provider = NullCategoryProvider()
    for name in _DATA_METHODS:
        assert inspect.iscoroutinefunction(getattr(provider, name)), name


@pytest.mark.asyncio
async def test_null_all_data_methods_raise_provider_not_configured() -> None:
    provider = NullCategoryProvider()
    instrument = _instrument()

    with pytest.raises(ProviderNotConfigured) as snap_info:
        await provider.get_snapshot(instrument, AS_OF)
    _assert_provider_not_configured(snap_info.value, expected_vendor="null")
    assert "chain placeholder" in snap_info.value.message

    with pytest.raises(ProviderNotConfigured) as q_info:
        await provider.get_quote(instrument, AS_OF)
    _assert_provider_not_configured(q_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as o_info:
        await provider.get_ohlcv(
            instrument,
            start=AS_OF,
            end=AS_OF,
            as_of=AS_OF,
            adjustment=AdjustmentMethod.NONE,
        )
    _assert_provider_not_configured(o_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as f_info:
        await provider.get_fundamentals(instrument, AS_OF)
    _assert_provider_not_configured(f_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as fil_info:
        await provider.list_filings(instrument, AS_OF)
    _assert_provider_not_configured(fil_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as n_info:
        await provider.get_news(instrument, start=AS_OF, end=AS_OF, as_of=AS_OF)
    _assert_provider_not_configured(n_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as m_info:
        await provider.get_macro_series("GDP", AS_OF)
    _assert_provider_not_configured(m_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as s_info:
        await provider.get_sentiment(instrument, AS_OF)
    _assert_provider_not_configured(s_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as a_info:
        await provider.get_account_snapshot(AS_OF)
    _assert_provider_not_configured(a_info.value, expected_vendor="null")

    with pytest.raises(ProviderNotConfigured) as l_info:
        await provider.lookup(Market.US, "NVDA", AS_OF)
    _assert_provider_not_configured(l_info.value, expected_vendor="null")


def test_null_registers_under_vendor_id_null() -> None:
    registry = VendorRegistry()
    registry.register(VendorId.NULL, NullCategoryProvider())
    got = registry.get(VendorId.NULL)
    assert got.vendor_id is VendorId.NULL
    assert got.provider_name == "null"
    assert registry.list_registered() == (VendorId.NULL,)


# --- UnimplementedVendorAdapter ---------------------------------------------


def test_unimplemented_identity_and_category_provider_surface() -> None:
    adapter = UnimplementedVendorAdapter(VendorId.EASTMONEY)
    assert isinstance(adapter, CategoryProvider)
    assert isinstance(adapter, MarketSnapshotCategoryProvider)
    assert adapter.vendor_id is VendorId.EASTMONEY
    assert adapter.provider_name == VendorId.EASTMONEY.value
    assert adapter.is_configured() is True
    assert adapter.supports(Market.A_SHARE, DataCategory.MARKET_SNAPSHOT) is True
    assert adapter.supports(Market.US, DataCategory.NEWS) is True


def test_unimplemented_rejects_null_vendor() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        UnimplementedVendorAdapter(VendorId.NULL)
    assert exc_info.value.details.get("rule") == "null_use_null_provider"
    assert SECRET not in exc_info.value.message
    assert SECRET not in str(exc_info.value.details)


def test_unimplemented_rejects_non_vendor_id() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        UnimplementedVendorAdapter("eastmoney")  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "vendor_id"


def test_unimplemented_data_methods_are_async() -> None:
    adapter = UnimplementedVendorAdapter(VendorId.YFINANCE)
    for name in _DATA_METHODS:
        assert inspect.iscoroutinefunction(getattr(adapter, name)), name


@pytest.mark.asyncio
async def test_unimplemented_all_data_methods_raise_provider_not_configured() -> None:
    adapter = UnimplementedVendorAdapter(VendorId.YFINANCE)
    instrument = _instrument()

    with pytest.raises(ProviderNotConfigured) as snap_info:
        await adapter.get_snapshot(instrument, AS_OF)
    _assert_provider_not_configured(snap_info.value, expected_vendor="yfinance")
    assert "not implemented in Phase 1D" in snap_info.value.message
    assert snap_info.value.details.get("phase") == "1D"

    with pytest.raises(ProviderNotConfigured) as q_info:
        await adapter.get_quote(instrument, AS_OF)
    _assert_provider_not_configured(q_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as o_info:
        await adapter.get_ohlcv(
            instrument,
            start=AS_OF,
            end=AS_OF,
            as_of=AS_OF,
            adjustment=AdjustmentMethod.NONE,
        )
    _assert_provider_not_configured(o_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as f_info:
        await adapter.get_fundamentals(instrument, AS_OF)
    _assert_provider_not_configured(f_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as fil_info:
        await adapter.list_filings(instrument, AS_OF)
    _assert_provider_not_configured(fil_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as n_info:
        await adapter.get_news(None, start=AS_OF, end=AS_OF, as_of=AS_OF)
    _assert_provider_not_configured(n_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as m_info:
        await adapter.get_macro_series("CPI", AS_OF)
    _assert_provider_not_configured(m_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as s_info:
        await adapter.get_sentiment(instrument, AS_OF)
    _assert_provider_not_configured(s_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as a_info:
        await adapter.get_account_snapshot(AS_OF)
    _assert_provider_not_configured(a_info.value, expected_vendor="yfinance")

    with pytest.raises(ProviderNotConfigured) as l_info:
        await adapter.lookup(Market.US, "NVDA", AS_OF)
    _assert_provider_not_configured(l_info.value, expected_vendor="yfinance")


def test_unimplemented_registers_for_tests_only_not_bootstrap_default() -> None:
    """Phase 1D may register Unimplemented only when tests assemble it."""
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, UnimplementedVendorAdapter(VendorId.EASTMONEY))
    assert registry.get(VendorId.EASTMONEY).vendor_id is VendorId.EASTMONEY
    # Real 1E/1F vendors must not be assumed present without explicit register.
    assert registry.get_optional(VendorId.YFINANCE) is None
