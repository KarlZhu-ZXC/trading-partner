"""YAML-backed VendorChainConfig loader (Phase 1D D4).

Strict validation only. Errors raise ConfigurationError without embedding raw
YAML or secrets. Load reads a single path — no directory scanning and no
environment-based vendor injection.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Self

import yaml

from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import ConfigurationError

_REQUIRED_ROOT_KEYS = frozenset({"version", "markets"})
_REQUIRED_MARKETS = frozenset({Market.A_SHARE, Market.US})
_SUPPORTED_VERSION = 1
_CATEGORY_OBJECT_KEYS = frozenset({"vendors"})

# Lookup tables avoid enum constructors so unknown YAML scalars never become
# ValueError messages chained as ConfigurationError causes.
_MARKET_BY_VALUE: Mapping[str, Market] = {m.value: m for m in Market}
_CATEGORY_BY_VALUE: Mapping[str, DataCategory] = {c.value: c for c in DataCategory}
_VENDOR_BY_VALUE: Mapping[str, VendorId] = {v.value: v for v in VendorId}


def _config_error(message: str, *, reason: str, **details: object) -> ConfigurationError:
    payload: dict[str, object] = {"reason": reason, **details}
    return ConfigurationError(message, details=payload)


class YamlVendorChainConfig:
    """Immutable vendor-chain config loaded from a versioned YAML file."""

    __slots__ = ("_chains",)

    def __init__(
        self,
        chains: Mapping[Market, Mapping[DataCategory, tuple[VendorId, ...]]],
    ) -> None:
        frozen: dict[Market, Mapping[DataCategory, tuple[VendorId, ...]]] = {}
        for market, categories in chains.items():
            frozen[market] = MappingProxyType(dict(categories))
        self._chains: Mapping[Market, Mapping[DataCategory, tuple[VendorId, ...]]] = (
            MappingProxyType(frozen)
        )

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load and validate vendor chains from ``path`` only."""
        file_path = Path(path)
        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise _config_error(
                "Vendor chain configuration file not found",
                reason="file_not_found",
                path=str(file_path),
            ) from exc
        except OSError as exc:
            raise _config_error(
                "Vendor chain configuration file is not readable",
                reason="file_unreadable",
                path=str(file_path),
                error_type=type(exc).__name__,
            ) from exc

        # Raise sanitized ConfigurationError outside the except so YAMLError
        # snippets never appear as __cause__/__context__.
        yaml_error_type: str | None = None
        try:
            payload = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            yaml_error_type = type(exc).__name__
        if yaml_error_type is not None:
            raise _config_error(
                "Vendor chain configuration is malformed YAML",
                reason="malformed_yaml",
                error_type=yaml_error_type,
            )

        parse_error_type: str | None = None
        try:
            chains = _parse_document(payload)
        except ConfigurationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            parse_error_type = type(exc).__name__
        else:
            return cls(chains)

        # Only error_type — never raw exception text or chained cause.
        raise _config_error(
            "Vendor chain configuration is invalid",
            reason="parse_failed",
            error_type=parse_error_type,
        )

    def chain_for(self, market: Market, category: DataCategory) -> tuple[VendorId, ...]:
        market_chains = self._chains.get(market)
        if market_chains is None:
            return ()
        return market_chains.get(category, ())

    def all_categories(self, market: Market) -> Mapping[DataCategory, tuple[VendorId, ...]]:
        market_chains = self._chains.get(market)
        if market_chains is None:
            return MappingProxyType({})
        return market_chains


def _parse_document(
    payload: object,
) -> dict[Market, dict[DataCategory, tuple[VendorId, ...]]]:
    if not isinstance(payload, dict):
        raise _config_error(
            "Vendor chain configuration root must be a mapping",
            reason="root_not_mapping",
            root_type=type(payload).__name__,
        )

    root_keys = set(payload.keys())
    if root_keys != _REQUIRED_ROOT_KEYS:
        # Never echo unknown (user-controlled) key names; missing known keys are safe.
        missing = sorted(_REQUIRED_ROOT_KEYS - root_keys)
        raise _config_error(
            "Vendor chain configuration root keys must be exactly version and markets",
            reason="invalid_root_keys",
            unknown_key_count=len(root_keys - _REQUIRED_ROOT_KEYS),
            missing_keys=missing,
        )

    version = payload["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise _config_error(
            "Vendor chain configuration version must be integer 1",
            reason="invalid_version_type",
            version_type=type(version).__name__,
        )
    if version != _SUPPORTED_VERSION:
        # Do not echo the raw user-provided version (may be large / secret-like).
        raise _config_error(
            "Vendor chain configuration version must be integer 1",
            reason="unsupported_version",
            version_type=type(version).__name__,
            expected_version=_SUPPORTED_VERSION,
        )

    markets_raw = payload["markets"]
    if not isinstance(markets_raw, dict):
        raise _config_error(
            "Vendor chain configuration markets must be a mapping",
            reason="markets_not_mapping",
            markets_type=type(markets_raw).__name__,
        )

    parsed_markets: dict[Market, dict[DataCategory, tuple[VendorId, ...]]] = {}
    seen_markets: set[Market] = set()
    for market_key, categories_raw in markets_raw.items():
        if not isinstance(market_key, str):
            raise _config_error(
                "Vendor chain market key must be a string",
                reason="invalid_market_key_type",
                market_key_type=type(market_key).__name__,
            )
        market = _MARKET_BY_VALUE.get(market_key)
        if market is None:
            # Do not echo the unknown market key (user-controlled YAML scalar).
            raise _config_error(
                "Vendor chain configuration contains an unknown market",
                reason="unknown_market",
            )
        if market in seen_markets:
            raise _config_error(
                "Vendor chain configuration has a duplicate market key",
                reason="duplicate_market",
                market=market.value,
            )
        seen_markets.add(market)
        parsed_markets[market] = _parse_market_categories(market, categories_raw)

    if seen_markets != _REQUIRED_MARKETS:
        missing = sorted(m.value for m in _REQUIRED_MARKETS - seen_markets)
        raise _config_error(
            "Vendor chain configuration requires both A_SHARE and US markets",
            reason="missing_required_markets",
            missing_markets=missing,
        )

    return parsed_markets


def _parse_market_categories(
    market: Market,
    categories_raw: object,
) -> dict[DataCategory, tuple[VendorId, ...]]:
    if not isinstance(categories_raw, dict):
        raise _config_error(
            "Vendor chain market entry must be a mapping of categories",
            reason="market_not_mapping",
            market=market.value,
            market_type=type(categories_raw).__name__,
        )

    result: dict[DataCategory, tuple[VendorId, ...]] = {}
    for category_key, category_obj in categories_raw.items():
        if not isinstance(category_key, str):
            raise _config_error(
                "Vendor chain category key must be a string",
                reason="invalid_category_key_type",
                market=market.value,
                category_key_type=type(category_key).__name__,
            )
        category = _CATEGORY_BY_VALUE.get(category_key)
        if category is None:
            # Do not echo the unknown category key (user-controlled YAML scalar).
            raise _config_error(
                "Vendor chain configuration contains an unknown data category",
                reason="unknown_category",
                market=market.value,
            )
        if category in result:
            raise _config_error(
                "Vendor chain configuration has a duplicate category key",
                reason="duplicate_category",
                market=market.value,
                category=category.value,
            )
        result[category] = _parse_category_vendors(market, category, category_obj)
    return result


def _parse_category_vendors(
    market: Market,
    category: DataCategory,
    category_obj: object,
) -> tuple[VendorId, ...]:
    if not isinstance(category_obj, dict):
        raise _config_error(
            "Vendor chain category entry must be a mapping with vendors only",
            reason="category_not_mapping",
            market=market.value,
            category=category.value,
            category_type=type(category_obj).__name__,
        )

    obj_keys = set(category_obj.keys())
    if obj_keys != _CATEGORY_OBJECT_KEYS:
        # Never echo unknown (user-controlled) key names; missing known keys are safe.
        missing = sorted(_CATEGORY_OBJECT_KEYS - obj_keys)
        raise _config_error(
            "Vendor chain category object keys must be exactly vendors",
            reason="invalid_category_object_keys",
            market=market.value,
            category=category.value,
            unknown_key_count=len(obj_keys - _CATEGORY_OBJECT_KEYS),
            missing_keys=missing,
        )

    vendors_raw = category_obj["vendors"]
    if not isinstance(vendors_raw, list):
        raise _config_error(
            "Vendor chain vendors must be a list",
            reason="vendors_not_list",
            market=market.value,
            category=category.value,
            vendors_type=type(vendors_raw).__name__,
        )

    vendors: list[VendorId] = []
    seen: set[VendorId] = set()
    for index, item in enumerate(vendors_raw):
        vendor = _coerce_vendor_id(item, market=market, category=category, index=index)
        if vendor in seen:
            raise _config_error(
                "Vendor chain must not contain duplicate vendors",
                reason="duplicate_vendor",
                market=market.value,
                category=category.value,
                vendor=vendor.value,
            )
        seen.add(vendor)
        vendors.append(vendor)
    return tuple(vendors)


def _coerce_vendor_id(
    item: object,
    *,
    market: Market,
    category: DataCategory,
    index: int,
) -> VendorId:
    # YAML unquoted null → None; quoted "null" → str "null". Both are VendorId.NULL.
    if item is None:
        return VendorId.NULL
    if not isinstance(item, str):
        raise _config_error(
            "Vendor chain vendor entries must be strings or null",
            reason="invalid_vendor_type",
            market=market.value,
            category=category.value,
            index=index,
            vendor_type=type(item).__name__,
        )
    vendor = _VENDOR_BY_VALUE.get(item)
    if vendor is None:
        # Do not echo the unknown vendor string (user-controlled YAML scalar).
        # Known VendorId values (e.g. duplicate_vendor) may still appear in details.
        raise _config_error(
            "Vendor chain configuration contains an unknown vendor",
            reason="unknown_vendor",
            market=market.value,
            category=category.value,
            index=index,
        )
    return vendor
