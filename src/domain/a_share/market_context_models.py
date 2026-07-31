"""A-share market-board and industry performance domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.a_share.model_validation import (
    _INDUSTRY_MAX,
    _QUOTE_ASSET_TYPES,
    _require_date,
    _require_decimal,
    _require_nonnegative_int,
    _require_optional_a_share_instrument_id,
    _require_optional_decimal,
    _require_str,
    _require_tuple,
)
from domain.common.errors import DataContractError

# ---------------------------------------------------------------------------
# §17.1 Market board / industry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndustryPerformanceRow:
    industry_code: str
    industry_name: str
    trade_date: date
    change_percent: Decimal
    advancing_count: int
    declining_count: int
    unchanged_count: int
    leading_instrument_id: str | None
    leading_change_percent: Decimal | None
    turnover_amount_cny: Decimal | None

    def __post_init__(self) -> None:
        _require_str(self.industry_code, field="industry_code", max_len=64)
        _require_str(self.industry_name, field="industry_name", max_len=_INDUSTRY_MAX)
        _require_date(self.trade_date, field="trade_date")
        _require_decimal(self.change_percent, field="change_percent")
        _require_nonnegative_int(self.advancing_count, field="advancing_count")
        _require_nonnegative_int(self.declining_count, field="declining_count")
        _require_nonnegative_int(self.unchanged_count, field="unchanged_count")
        _require_optional_a_share_instrument_id(
            self.leading_instrument_id,
            field="leading_instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        _require_optional_decimal(self.leading_change_percent, field="leading_change_percent")
        _require_optional_decimal(self.turnover_amount_cny, field="turnover_amount_cny")


@dataclass(frozen=True, slots=True)
class MarketBoardSnapshot:
    trade_date: date
    advancing_count: int
    declining_count: int
    unchanged_count: int
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    total_turnover_cny: Decimal | None
    median_change_percent: Decimal | None
    industries: tuple[IndustryPerformanceRow, ...]

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        for name in (
            "advancing_count",
            "declining_count",
            "unchanged_count",
            "limit_up_count",
            "limit_down_count",
            "broken_limit_count",
        ):
            _require_nonnegative_int(getattr(self, name), field=name)
        _require_optional_decimal(self.total_turnover_cny, field="total_turnover_cny")
        _require_optional_decimal(self.median_change_percent, field="median_change_percent")
        industries = _require_tuple(self.industries, field="industries")
        seen_codes: set[str] = set()
        for idx, row in enumerate(industries):
            if not isinstance(row, IndustryPerformanceRow):
                raise DataContractError(
                    "industries elements must be IndustryPerformanceRow",
                    details={"field": "industries", "index": idx, "rule": "type"},
                )
            if row.industry_code in seen_codes:
                raise DataContractError(
                    "industry_code must be unique within industries",
                    details={"field": "industries", "rule": "unique_industry_code"},
                )
            seen_codes.add(row.industry_code)
