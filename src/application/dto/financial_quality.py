"""Deterministic, cross-market financial-quality metric DTOs.

The calculator consumes already-normalized statement facts.  It never fetches
data, guesses missing values, or turns accounting observations into forecasts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from application.dto.market import DecimalWire

_VERSION = "financial_quality_v1"


class FinancialQualityMetricDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_code: str
    period_end: date
    value: DecimalWire
    unit: str
    formula: str
    formula_version: str = _VERSION


def derive_financial_quality_metrics(
    *,
    period_end: date,
    line_items: dict[str, Decimal | None],
    currency: str | None,
) -> tuple[FinancialQualityMetricDTO, ...]:
    """Return only metrics whose required inputs are present and valid."""

    out: list[FinancialQualityMetricDTO] = []
    revenue = _first(line_items, "revenue", "total_revenue")
    net_income = _first(line_items, "net_income", "net_income_attributable_parent")
    operating_cash_flow = line_items.get("operating_cash_flow")
    capital_expenditure = line_items.get("capital_expenditure")
    current_assets = line_items.get("current_assets")
    current_liabilities = line_items.get("current_liabilities")
    cash = line_items.get("cash_and_equivalents")
    debt_parts = tuple(
        value
        for code in (
            "short_term_debt",
            "current_portion_long_term_debt",
            "long_term_debt",
            "bonds_payable",
        )
        if (value := line_items.get(code)) is not None
    )

    free_cash_flow: Decimal | None = None
    if operating_cash_flow is not None and capital_expenditure is not None:
        # All adapters normalize capex as a positive cash outflow.
        free_cash_flow = operating_cash_flow - abs(capital_expenditure)
        _append(
            out,
            "free_cash_flow",
            period_end,
            free_cash_flow,
            currency or "currency_unknown",
            "operating_cash_flow - abs(capital_expenditure)",
        )
    if operating_cash_flow is not None and net_income not in {None, Decimal(0)}:
        _append(
            out,
            "operating_cash_flow_to_net_income",
            period_end,
            operating_cash_flow / net_income,
            "ratio",
            "operating_cash_flow / net_income",
        )
    if free_cash_flow is not None and revenue not in {None, Decimal(0)}:
        _append(
            out,
            "free_cash_flow_margin",
            period_end,
            free_cash_flow / revenue,
            "ratio",
            "free_cash_flow / revenue",
        )
    if capital_expenditure is not None and revenue not in {None, Decimal(0)}:
        _append(
            out,
            "capital_expenditure_to_revenue",
            period_end,
            abs(capital_expenditure) / revenue,
            "ratio",
            "abs(capital_expenditure) / revenue",
        )
    if current_assets is not None and current_liabilities not in {None, Decimal(0)}:
        _append(
            out,
            "current_ratio",
            period_end,
            current_assets / current_liabilities,
            "ratio",
            "current_assets / current_liabilities",
        )
    if cash is not None and debt_parts:
        _append(
            out,
            "net_debt",
            period_end,
            sum(debt_parts, Decimal(0)) - cash,
            currency or "currency_unknown",
            "known_interest_bearing_debt - cash_and_equivalents",
        )
    return tuple(out)


def _first(values: dict[str, Decimal | None], *keys: str) -> Decimal | None:
    for key in keys:
        if (value := values.get(key)) is not None:
            return value
    return None


def _append(
    out: list[FinancialQualityMetricDTO],
    metric_code: str,
    period_end: date,
    value: Decimal,
    unit: str,
    formula: str,
) -> None:
    if not value.is_finite():
        return
    out.append(
        FinancialQualityMetricDTO(
            metric_code=metric_code,
            period_end=period_end,
            value=value,
            unit=unit,
            formula=formula,
        )
    )
