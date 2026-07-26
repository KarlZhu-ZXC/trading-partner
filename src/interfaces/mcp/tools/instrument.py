"""Compact instrument-resolution adapter."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from bootstrap import ApplicationContainer
from domain.common.enums import AssetType, Market
from interfaces.mcp.schemas import (
    InstrumentResolveInput,
)
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_instrument_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build the compact instrument adapter."""

    # ---------------------------------------------------------- Phase 1D instrument
    async def instrument_resolve(
        market: str,
        query: str,
        asset_type: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve locally, then discover and cache a validated external instrument."""
        try:
            inp = InstrumentResolveInput.model_validate(
                {
                    "market": market,
                    "query": query,
                    "asset_type": asset_type,
                    "as_of": as_of,
                }
            )
            market_enum = inp.market if isinstance(inp.market, Market) else Market(inp.market)
            asset_hint: AssetType | None
            if inp.asset_type is None:
                asset_hint = None
            elif isinstance(inp.asset_type, AssetType):
                asset_hint = inp.asset_type
            else:
                asset_hint = AssetType(inp.asset_type)
            envelope = await container.instrument_resolve_service.resolve_dynamic(
                market=market_enum,
                query=inp.query,
                asset_type_hint=asset_hint,
                as_of=inp.as_of,
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(instrument_resolve=instrument_resolve)
