"""Shared closed-model base and validators for A-share DTOs."""

from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from domain.common.enums import AssetType, Market
from domain.common.errors import TradingPartnerError
from domain.common.values import parse_instrument_id

_DATE_WIRE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _FrozenForbid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_exact_date_wire(value: object) -> object:
    if value is None:
        return value
    if isinstance(value, datetime):
        raise ValueError("must be an exact date, not datetime")
    if isinstance(value, str) and not _DATE_WIRE_RE.fullmatch(value):
        raise ValueError("must use YYYY-MM-DD date format")
    if not isinstance(value, (date, str)):
        raise ValueError("must be an exact date")
    return value


def _validate_a_share_instrument_id(
    value: str,
    *,
    allowed_assets: frozenset[AssetType],
    field_name: str = "instrument_id",
) -> str:
    """Reject non-A_SHARE and asset types outside the frozen tool matrix."""
    try:
        asset_type, market, _symbol = parse_instrument_id(value)
    except TradingPartnerError:
        raise ValueError("invalid instrument_id syntax") from None
    if market is not Market.A_SHARE:
        raise ValueError(f"{field_name} must use Market.A_SHARE")
    if asset_type not in allowed_assets:
        allowed = ", ".join(sorted(a.value for a in allowed_assets))
        raise ValueError(f"{field_name} asset type must be one of [{allowed}] for this tool")
    return value
