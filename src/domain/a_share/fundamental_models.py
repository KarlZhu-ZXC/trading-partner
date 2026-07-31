"""A-share company fundamentals and financial-statement domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.a_share.enums import FinancialStatementType
from domain.a_share.model_validation import (
    _F10_BODY_MAX,
    _ITEM_CODE_MAX,
    _ITEM_NAME_MAX,
    _METRIC_NAME_MAX,
    _SECTION_MAX,
    _TITLE_MAX,
    _UNIT_MAX,
    _require_date,
    _require_decimal,
    _require_enum,
    _require_optional_date,
    _require_optional_decimal,
    _require_optional_str,
    _require_str,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# ---------------------------------------------------------------------------
# §4.2 Fundamentals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundamentalMetric:
    name: str
    value: Decimal | str | int | None
    unit: str | None
    period_end: date | None
    published_at: datetime | None

    def __post_init__(self) -> None:
        _require_str(self.name, field="name", max_len=_METRIC_NAME_MAX)
        if self.value is not None:
            if isinstance(self.value, float):
                raise DataContractError(
                    "value must not be float",
                    details={"field": "value", "rule": "no_float"},
                )
            if type(self.value) is Decimal:
                _require_decimal(self.value, field="value")
            elif type(self.value) is int:
                pass
            elif isinstance(self.value, str):
                _require_str(self.value, field="value", max_len=200, allow_blank=True)
            else:
                raise DataContractError(
                    "value must be Decimal, str, int, or None",
                    details={
                        "field": "value",
                        "rule": "value_type",
                        "type": type(self.value).__name__,
                    },
                )
        _require_optional_str(self.unit, field="unit", max_len=_UNIT_MAX)
        _require_optional_date(self.period_end, field="period_end")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")


@dataclass(frozen=True, slots=True)
class FinancialStatementLine:
    statement_type: FinancialStatementType
    period_end: date
    published_at: datetime | None
    item_code: str
    item_name: str
    value: Decimal | None
    unit: str

    def __post_init__(self) -> None:
        _require_enum(self.statement_type, FinancialStatementType, field="statement_type")
        _require_date(self.period_end, field="period_end")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.item_code, field="item_code", max_len=_ITEM_CODE_MAX)
        _require_str(self.item_name, field="item_name", max_len=_ITEM_NAME_MAX)
        _require_optional_decimal(self.value, field="value")
        _require_str(self.unit, field="unit", max_len=_UNIT_MAX)


@dataclass(frozen=True, slots=True)
class F10Section:
    section: str
    title: str
    body: str
    as_of: datetime

    def __post_init__(self) -> None:
        _require_str(self.section, field="section", max_len=_SECTION_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_str(self.body, field="body", max_len=_F10_BODY_MAX, allow_blank=True)
        require_aware_datetime(self.as_of, field_name="as_of")


