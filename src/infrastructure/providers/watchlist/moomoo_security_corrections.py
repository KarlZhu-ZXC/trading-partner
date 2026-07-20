"""Strict, versioned manual corrections for erroneous Moomoo security metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Self

import yaml

from domain.common.enums import AssetType
from domain.common.errors import ConfigurationError

_SUPPORTED_VERSION = 1
_ROOT_KEYS = frozenset({"version", "corrections"})
_ENTRY_KEYS = frozenset(
    {"provider_code", "asset_type", "display_name", "reason", "verified_on"}
)
_SUPPORTED_ASSET_TYPES = frozenset(
    {AssetType.EQUITY, AssetType.ETF, AssetType.INDEX, AssetType.OPTION}
)
_WORKSPACE_DEFAULT = (
    Path(__file__).resolve().parents[4] / "config" / "moomoo_security_corrections.yaml"
)
_PACKAGED_DEFAULT = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "moomoo_security_corrections.yaml"
)


def _configuration_error(message: str, *, reason: str) -> ConfigurationError:
    return ConfigurationError(message, details={"reason": reason})


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _configuration_error(
            "Moomoo correction text field is invalid",
            reason=f"invalid_{field}",
        )
    return value.strip()


@dataclass(frozen=True, slots=True)
class MoomooSecurityCorrection:
    provider_code: str
    asset_type: AssetType
    display_name: str
    reason: str
    verified_on: date


class MoomooSecurityCorrections:
    """Immutable lookup loaded from the single tracked correction file."""

    def __init__(self, corrections: Mapping[str, MoomooSecurityCorrection]) -> None:
        self._corrections = MappingProxyType(dict(corrections))

    @classmethod
    def empty(cls) -> Self:
        return cls({})

    @classmethod
    def load_default(cls) -> Self:
        path = _WORKSPACE_DEFAULT if _WORKSPACE_DEFAULT.is_file() else _PACKAGED_DEFAULT
        return cls.load(path)

    @classmethod
    def load(cls, path: Path) -> Self:
        try:
            raw_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise _configuration_error(
                "Moomoo correction file is unavailable",
                reason="file_unavailable",
            ) from exc
        try:
            payload = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            raise _configuration_error(
                "Moomoo correction file is malformed YAML",
                reason="malformed_yaml",
            ) from None
        return cls(_parse_document(payload))

    def for_code(self, provider_code: str) -> MoomooSecurityCorrection | None:
        return self._corrections.get(provider_code)


def _parse_document(payload: object) -> dict[str, MoomooSecurityCorrection]:
    if not isinstance(payload, dict) or set(payload) != _ROOT_KEYS:
        raise _configuration_error(
            "Moomoo correction root is invalid",
            reason="invalid_root",
        )
    if payload["version"] != _SUPPORTED_VERSION:
        raise _configuration_error(
            "Moomoo correction version is unsupported",
            reason="unsupported_version",
        )
    entries = payload["corrections"]
    if not isinstance(entries, list):
        raise _configuration_error(
            "Moomoo corrections must be a list",
            reason="invalid_corrections",
        )
    result: dict[str, MoomooSecurityCorrection] = {}
    for entry in entries:
        correction = _parse_entry(entry)
        if correction.provider_code in result:
            raise _configuration_error(
                "Moomoo correction provider code is duplicated",
                reason="duplicate_provider_code",
            )
        result[correction.provider_code] = correction
    return result


def _parse_entry(entry: object) -> MoomooSecurityCorrection:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
        raise _configuration_error(
            "Moomoo correction entry is invalid",
            reason="invalid_entry",
        )
    provider_code = _required_text(entry["provider_code"], field="provider_code").upper()
    if "." not in provider_code:
        raise _configuration_error(
            "Moomoo correction provider code is invalid",
            reason="invalid_provider_code",
        )
    asset_name = _required_text(entry["asset_type"], field="asset_type").upper()
    try:
        asset_type = AssetType[asset_name]
    except KeyError:
        raise _configuration_error(
            "Moomoo correction asset type is unsupported",
            reason="unsupported_asset_type",
        ) from None
    if asset_type not in _SUPPORTED_ASSET_TYPES:
        raise _configuration_error(
            "Moomoo correction asset type is unsupported",
            reason="unsupported_asset_type",
        )
    verified_on = entry["verified_on"]
    if not isinstance(verified_on, date):
        raise _configuration_error(
            "Moomoo correction verification date is invalid",
            reason="invalid_verified_on",
        )
    return MoomooSecurityCorrection(
        provider_code=provider_code,
        asset_type=asset_type,
        display_name=_required_text(entry["display_name"], field="display_name"),
        reason=_required_text(entry["reason"], field="reason"),
        verified_on=verified_on,
    )
