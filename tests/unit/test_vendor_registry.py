"""Phase 1D D6a: VendorRegistry registration and lookup semantics."""

from __future__ import annotations

import pytest

from application.ports.category_provider import CategoryProvider
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import ConfigurationError, ProviderNotConfigured
from infrastructure.providers.registry import VendorRegistry

_SECRET = "test-secret-malicious-value"


class _StubAdapter:
    def __init__(
        self,
        vendor_id: VendorId,
        *,
        name: str | None = None,
        configured: bool = True,
    ) -> None:
        self._vendor_id = vendor_id
        self._name = vendor_id.value if name is None else name
        self._configured = configured

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self._name

    def supports(self, market: Market, category: DataCategory) -> bool:
        return True

    def is_configured(self) -> bool:
        return self._configured


class _NonCallableSupportsAdapter(_StubAdapter):
    supports = "not-callable"  # type: ignore[assignment]


class _NonCallableIsConfiguredAdapter(_StubAdapter):
    is_configured = 42  # type: ignore[assignment]


class _MissingSupportsAdapter:
    """Readable identity + is_configured, but no supports method."""

    def __init__(self, vendor_id: VendorId) -> None:
        self._vendor_id = vendor_id

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self._vendor_id.value

    def is_configured(self) -> bool:
        return True


class _MissingIsConfiguredAdapter:
    """Readable identity + supports, but no is_configured method."""

    def __init__(self, vendor_id: VendorId) -> None:
        self._vendor_id = vendor_id

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self._vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return True


class _RaisingVendorIdAdapter(_StubAdapter):
    @property
    def vendor_id(self) -> VendorId:
        raise RuntimeError(f"boom with credential {_SECRET}")


class _RaisingProviderNameAdapter(_StubAdapter):
    @property
    def provider_name(self) -> str:
        raise ValueError(f"provider_name leak {_SECRET}")


class _RaisingSupportsAdapter(_StubAdapter):
    @property
    def supports(self) -> object:  # type: ignore[override]
        raise RuntimeError(f"supports getter secret {_SECRET}")


def _assert_sanitized_surface_error(exc: ConfigurationError) -> None:
    assert exc.details.get("rule") == "category_provider_surface"
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    # Public surfaces only — suppressed __context__ must not be required to be
    # empty (Python still stores it), but must not leak via message/details/repr.
    blob = f"{exc!s}{exc!r}{exc.details!r}{exc.message!r}"
    assert _SECRET not in blob
    assert "MALICIOUS" not in blob
    assert "sk-live" not in blob
    assert "boom" not in blob
    assert "credential" not in blob
    # Chain: no explicit cause, and context is suppressed from display.
    assert getattr(exc, "__cause__", None) is None


def test_register_and_get_roundtrip() -> None:
    reg = VendorRegistry()
    adapter = _StubAdapter(VendorId.MOCK_US)
    reg.register(VendorId.MOCK_US, adapter)
    assert reg.get(VendorId.MOCK_US) is adapter
    assert isinstance(adapter, CategoryProvider)


def test_get_missing_raises_provider_not_configured() -> None:
    reg = VendorRegistry()
    with pytest.raises(ProviderNotConfigured) as exc_info:
        reg.get(VendorId.YFINANCE)
    assert exc_info.value.details.get("vendor_id") == "yfinance"
    assert "api_key" not in str(exc_info.value).lower()


def test_get_optional_missing_returns_none() -> None:
    reg = VendorRegistry()
    assert reg.get_optional(VendorId.EASTMONEY) is None


def test_duplicate_registration_raises_configuration_error() -> None:
    reg = VendorRegistry()
    reg.register(VendorId.NULL, _StubAdapter(VendorId.NULL))
    with pytest.raises(ConfigurationError, match="already registered") as exc_info:
        reg.register(VendorId.NULL, _StubAdapter(VendorId.NULL))
    assert exc_info.value.details.get("rule") == "duplicate_registration"
    # Original registration retained
    assert reg.get(VendorId.NULL).vendor_id is VendorId.NULL


def test_vendor_id_mismatch_raises() -> None:
    reg = VendorRegistry()
    adapter = _StubAdapter(VendorId.MOCK_A_SHARE)
    with pytest.raises(ConfigurationError, match="vendor_id must equal") as exc_info:
        reg.register(VendorId.MOCK_US, adapter)
    assert exc_info.value.details.get("rule") == "vendor_id_mismatch"
    assert exc_info.value.details.get("expected") == "mock_us"


def test_provider_name_mismatch_raises_without_echoing_bad_name() -> None:
    reg = VendorRegistry()
    bad_name = "evil-name-with-test-secret-token"
    adapter = _StubAdapter(VendorId.MOCK_US, name=bad_name)
    with pytest.raises(ConfigurationError, match="provider_name") as exc_info:
        reg.register(VendorId.MOCK_US, adapter)
    blob = str(exc_info.value) + str(exc_info.value.details)
    assert bad_name not in blob
    assert "SECRET_TOKEN" not in blob
    assert exc_info.value.details.get("expected") == "mock_us"


def test_register_rejects_missing_supports() -> None:
    reg = VendorRegistry()
    adapter = _MissingSupportsAdapter(VendorId.MOCK_US)
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.MOCK_US, adapter)  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "adapter.supports"
    _assert_sanitized_surface_error(exc_info.value)
    assert reg.get_optional(VendorId.MOCK_US) is None


def test_register_rejects_non_callable_supports() -> None:
    reg = VendorRegistry()
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.MOCK_US, _NonCallableSupportsAdapter(VendorId.MOCK_US))
    assert exc_info.value.details.get("field") == "adapter.supports"
    _assert_sanitized_surface_error(exc_info.value)


def test_register_rejects_non_callable_is_configured() -> None:
    reg = VendorRegistry()
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.MOCK_US, _NonCallableIsConfiguredAdapter(VendorId.MOCK_US))
    assert exc_info.value.details.get("field") == "adapter.is_configured"
    _assert_sanitized_surface_error(exc_info.value)


def test_register_rejects_missing_is_configured_method() -> None:
    reg = VendorRegistry()
    adapter = _MissingIsConfiguredAdapter(VendorId.NULL)
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.NULL, adapter)  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "adapter.is_configured"
    _assert_sanitized_surface_error(exc_info.value)
    # Missing registration must not leave a partial entry
    assert reg.get_optional(VendorId.NULL) is None


def test_register_sanitizes_vendor_id_property_exception() -> None:
    reg = VendorRegistry()
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.MOCK_US, _RaisingVendorIdAdapter(VendorId.MOCK_US))
    assert exc_info.value.details.get("field") == "adapter"
    _assert_sanitized_surface_error(exc_info.value)
    assert reg.get_optional(VendorId.MOCK_US) is None


def test_register_sanitizes_provider_name_property_exception() -> None:
    reg = VendorRegistry()
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.MOCK_US, _RaisingProviderNameAdapter(VendorId.MOCK_US))
    assert exc_info.value.details.get("field") == "adapter"
    _assert_sanitized_surface_error(exc_info.value)


def test_register_sanitizes_supports_property_exception() -> None:
    reg = VendorRegistry()
    with pytest.raises(ConfigurationError) as exc_info:
        reg.register(VendorId.MOCK_US, _RaisingSupportsAdapter(VendorId.MOCK_US))
    assert exc_info.value.details.get("field") == "adapter.supports"
    _assert_sanitized_surface_error(exc_info.value)


def test_list_registered_sorted_by_value_not_insertion_order() -> None:
    reg = VendorRegistry()
    # Insert out of alphabetical order
    for vid in (
        VendorId.YFINANCE,
        VendorId.MOCK_A_SHARE,
        VendorId.NULL,
        VendorId.ALPHA_VANTAGE,
    ):
        reg.register(vid, _StubAdapter(vid))
    listed = reg.list_registered()
    assert listed == tuple(sorted(listed, key=lambda v: v.value))
    assert listed == (
        VendorId.ALPHA_VANTAGE,
        VendorId.MOCK_A_SHARE,
        VendorId.NULL,
        VendorId.YFINANCE,
    )


def test_list_registered_empty() -> None:
    assert VendorRegistry().list_registered() == ()
