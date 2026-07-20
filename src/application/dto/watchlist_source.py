"""Watchlist source DTOs for upstream read/write adapters."""

from __future__ import annotations

from dataclasses import dataclass

from domain.common.errors import DataContractError
from domain.common.values import parse_instrument_id
from domain.watchlist.enums import WatchlistGroupType, WatchlistSource

WatchlistSourceGroupType = WatchlistGroupType


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            "watchlist value must be text",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.strip():
        raise DataContractError("watchlist value must not be blank", details={"field": field})
    return value


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataContractError(
            "watchlist value must be text or null",
            details={"field": field, "type": type(value).__name__},
        )
    value = value.strip()
    return value if value else None


def _require_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise DataContractError("watchlist flag must be true or false", details={"field": field})
    return value


def _normalize_instrument_id(value: str | None) -> str | None:
    if value is None:
        return None
    parse_instrument_id(value)
    return value


@dataclass(frozen=True, slots=True)
class WatchlistSourceGroup:
    """Canonical upstream group representation."""

    source: WatchlistSource
    name: str
    group_type: WatchlistSourceGroupType
    writable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.source, WatchlistSource):
            raise DataContractError(
                "source must be WatchlistSource",
                details={"field": "source", "type": type(self.source).__name__},
            )
        object.__setattr__(self, "name", _require_text(self.name, field="name"))
        if not isinstance(self.group_type, WatchlistSourceGroupType):
            raise DataContractError(
                "group_type must be WatchlistSourceGroupType",
                details={"field": "group_type", "type": type(self.group_type).__name__},
            )
        object.__setattr__(self, "writable", _require_bool(self.writable, field="writable"))


@dataclass(frozen=True, slots=True)
class WatchlistSourceMembership:
    """Canonical upstream membership representation."""

    source: WatchlistSource
    group_name: str
    provider_code: str
    display_name: str
    instrument_id: str | None
    provider_asset_type: str | None
    research_supported: bool
    group_writable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.source, WatchlistSource):
            raise DataContractError(
                "source must be WatchlistSource",
                details={"field": "source", "type": type(self.source).__name__},
            )
        object.__setattr__(self, "group_name", _require_text(self.group_name, field="group_name"))
        object.__setattr__(
            self,
            "provider_code",
            _require_text(self.provider_code, field="provider_code"),
        )
        object.__setattr__(
            self,
            "display_name",
            _require_text(self.display_name, field="display_name"),
        )
        object.__setattr__(
            self,
            "provider_asset_type",
            _optional_text(self.provider_asset_type, field="provider_asset_type"),
        )
        object.__setattr__(self, "instrument_id", _normalize_instrument_id(self.instrument_id))
        object.__setattr__(
            self,
            "research_supported",
            _require_bool(self.research_supported, field="research_supported"),
        )
        object.__setattr__(
            self,
            "group_writable",
            _require_bool(self.group_writable, field="group_writable"),
        )
        if self.research_supported and self.instrument_id is None:
            raise DataContractError(
                "research_supported requires normalized instrument_id",
                details={"field": "research_supported"},
            )
        if not self.research_supported and self.instrument_id is not None:
            raise DataContractError(
                "unsupported membership cannot carry instrument_id",
                details={"field": "instrument_id"},
            )
