"""Instrument master repository port (Phase 1D)."""

from __future__ import annotations

from typing import Protocol

from domain.common.enums import AliasType, AssetType, Market
from domain.instruments.models import Instrument, InstrumentAlias


class InstrumentRepository(Protocol):
    def count(self) -> int: ...

    def get_by_id(self, instrument_id: str) -> Instrument | None: ...

    def find_by_symbol(
        self,
        market: Market,
        symbol: str,
        *,
        asset_type: AssetType | None = None,
    ) -> tuple[Instrument, ...]: ...

    def find_by_alias(
        self,
        market: Market,
        alias_value: str,
        *,
        alias_type: AliasType | None = None,
    ) -> tuple[Instrument, ...]: ...

    def list_aliases(self, instrument_id: str) -> tuple[InstrumentAlias, ...]: ...

    def upsert_instrument(self, instrument: Instrument) -> None: ...

    def upsert_alias(self, alias: InstrumentAlias) -> None: ...

    def search_name(
        self,
        market: Market,
        name_query: str,
        *,
        limit: int = 10,
    ) -> tuple[Instrument, ...]: ...
