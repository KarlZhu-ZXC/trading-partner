"""Vendor adapter registry (Phase 1D D6a).

Registration and lookup only — no fallback, timeout, retry, cache, or health.
"""

from __future__ import annotations

from application.ports.category_provider import CategoryProvider
from domain.common.enums import VendorId
from domain.common.errors import ConfigurationError, ProviderNotConfigured


def _surface_configuration_error(*, field: str) -> ConfigurationError:
    """Sanitized surface failure — never attach raw cause/context or messages."""
    return ConfigurationError(
        "adapter must expose a readable CategoryProvider surface",
        details={"field": field, "rule": "category_provider_surface"},
    )


class VendorRegistry:
    """In-memory registry of CategoryProvider adapters keyed by VendorId."""

    def __init__(self) -> None:
        self._adapters: dict[VendorId, CategoryProvider] = {}

    def register(self, vendor_id: VendorId, adapter: CategoryProvider) -> None:
        """Register ``adapter`` under ``vendor_id``.

        Validates the full CategoryProvider surface: readable/coherent
        ``vendor_id`` and ``provider_name``, and callable ``supports`` /
        ``is_configured``. Surface inspection failures raise a sanitized
        :class:`~domain.common.errors.ConfigurationError` with no raw
        exception chain. Duplicate registration raises ConfigurationError
        (no overwrite).
        """
        if not isinstance(vendor_id, VendorId):
            raise ConfigurationError(
                "vendor_id must be a VendorId",
                details={"field": "vendor_id", "type": type(vendor_id).__name__},
            )
        adapter_vendor, adapter_name = self._read_identity(adapter)
        self._require_callable_surface(adapter, "supports")
        self._require_callable_surface(adapter, "is_configured")
        if adapter_vendor != vendor_id:
            raise ConfigurationError(
                "adapter.vendor_id must equal registered vendor_id",
                details={
                    "field": "adapter.vendor_id",
                    "rule": "vendor_id_mismatch",
                    "expected": vendor_id.value,
                    "actual": (
                        adapter_vendor.value
                        if isinstance(adapter_vendor, VendorId)
                        else type(adapter_vendor).__name__
                    ),
                },
            )
        if not isinstance(adapter_name, str) or adapter_name != vendor_id.value:
            raise ConfigurationError(
                "adapter.provider_name must equal vendor_id.value",
                details={
                    "field": "adapter.provider_name",
                    "rule": "provider_name_coherence",
                    "expected": vendor_id.value,
                },
            )
        if vendor_id in self._adapters:
            raise ConfigurationError(
                "vendor already registered",
                details={
                    "field": "vendor_id",
                    "rule": "duplicate_registration",
                    "vendor_id": vendor_id.value,
                },
            )
        self._adapters[vendor_id] = adapter

    @staticmethod
    def _read_identity(adapter: CategoryProvider) -> tuple[object, object]:
        """Read vendor_id / provider_name without leaking adapter exceptions."""
        try:
            adapter_vendor = adapter.vendor_id
            adapter_name = adapter.provider_name
        except Exception:
            # from None: no cause chain; context suppressed so secret-bearing
            # adapter exceptions never surface via message/details/repr/chain.
            raise _surface_configuration_error(field="adapter") from None
        return adapter_vendor, adapter_name

    @staticmethod
    def _require_callable_surface(adapter: CategoryProvider, attr_name: str) -> None:
        """Require ``attr_name`` to exist and be callable; sanitize failures."""
        field = f"adapter.{attr_name}"
        try:
            attr = getattr(adapter, attr_name, None)
        except Exception:
            raise _surface_configuration_error(field=field) from None
        if not callable(attr):
            # from None keeps surface errors uniform (no accidental chain).
            raise _surface_configuration_error(field=field) from None

    def get(self, vendor_id: VendorId) -> CategoryProvider:
        """Return registered adapter or raise ProviderNotConfigured."""
        if not isinstance(vendor_id, VendorId):
            raise ConfigurationError(
                "vendor_id must be a VendorId",
                details={"field": "vendor_id", "type": type(vendor_id).__name__},
            )
        adapter = self._adapters.get(vendor_id)
        if adapter is None:
            raise ProviderNotConfigured(
                "vendor is not registered",
                details={"vendor_id": vendor_id.value},
            )
        return adapter

    def get_optional(self, vendor_id: VendorId) -> CategoryProvider | None:
        """Return registered adapter or ``None`` if missing (never raises)."""
        if not isinstance(vendor_id, VendorId):
            raise ConfigurationError(
                "vendor_id must be a VendorId",
                details={"field": "vendor_id", "type": type(vendor_id).__name__},
            )
        return self._adapters.get(vendor_id)

    def list_registered(self) -> tuple[VendorId, ...]:
        """Registered vendors sorted by ``VendorId.value`` ascending."""
        return tuple(sorted(self._adapters.keys(), key=lambda v: v.value))
