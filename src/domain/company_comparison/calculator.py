"""Deterministic peer-comparison calculator over normalized company facts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from domain.common.enums import Market
from domain.common.errors import DataContractError
from domain.company_comparison.enums import PeerComparisonPeriodMode, PeerComparisonStatus
from domain.company_comparison.models import (
    PeerCompanyFacts,
    PeerCompanyPeriod,
    PeerComparisonCell,
    PeerComparisonFactPackage,
    PeerComparisonRow,
)

_FORMULA_VERSION = "peer_comparison_v1"
_AMOUNT_CODES = (
    ("scale", "revenue"),
    ("scale", "operating_income"),
    ("scale", "net_income"),
    ("scale", "operating_cash_flow"),
    ("scale", "free_cash_flow"),
    ("scale", "total_assets"),
    ("scale", "stockholders_equity"),
    ("balance_sheet", "net_debt"),
)
_RATIO_CODES = (
    ("profitability", "operating_margin"),
    ("profitability", "net_margin"),
    ("cash_flow_quality", "operating_cash_flow_to_net_income"),
    ("cash_flow_quality", "free_cash_flow_margin"),
    ("cash_flow_quality", "capital_expenditure_to_revenue"),
    ("balance_sheet", "current_ratio"),
    ("balance_sheet", "debt_to_equity"),
)
_GROWTH_CODES = (
    "revenue_yoy",
    "operating_income_yoy",
    "net_income_yoy",
    "operating_cash_flow_yoy",
)
_VALUATION_CODES = (
    "market_cap",
    "trailing_pe",
    "price_to_book",
    "price_to_sales",
)


def _first(values: dict[str, Decimal | None], *codes: str) -> Decimal | None:
    for code in codes:
        if (value := values.get(code)) is not None:
            return value
    return None


def _safe_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in {None, Decimal(0)}:
        return None
    value = numerator / denominator
    return value if value.is_finite() else None


def _metric_values(
    period: PeerCompanyPeriod,
    previous: PeerCompanyPeriod | None,
) -> dict[str, tuple[Decimal | None, str, str, str]]:
    lines = dict(period.line_items)
    revenue = _first(lines, "revenue", "total_revenue")
    operating_income = lines.get("operating_income")
    net_income = _first(lines, "net_income", "net_income_attributable_parent")
    ocf = lines.get("operating_cash_flow")
    capex = lines.get("capital_expenditure")
    fcf = ocf - abs(capex) if ocf is not None and capex is not None else None
    cash = lines.get("cash_and_equivalents")
    debt = sum(
        (
            value
            for code in (
                "short_term_debt",
                "current_portion_long_term_debt",
                "long_term_debt",
                "bonds_payable",
            )
            if (value := lines.get(code)) is not None
        ),
        Decimal(0),
    )
    has_debt = any(
        lines.get(code) is not None
        for code in (
            "short_term_debt",
            "current_portion_long_term_debt",
            "long_term_debt",
            "bonds_payable",
        )
    )
    equity = lines.get("stockholders_equity")
    out: dict[str, tuple[Decimal | None, str, str, str]] = {
        "revenue": (revenue, period.currency, "reported", "scale"),
        "operating_income": (operating_income, period.currency, "reported", "scale"),
        "net_income": (net_income, period.currency, "reported", "scale"),
        "operating_cash_flow": (ocf, period.currency, "reported", "scale"),
        "free_cash_flow": (
            fcf,
            period.currency,
            "operating_cash_flow - abs(capital_expenditure)",
            "scale",
        ),
        "total_assets": (lines.get("total_assets"), period.currency, "reported", "scale"),
        "stockholders_equity": (equity, period.currency, "reported", "scale"),
        "operating_margin": (
            _safe_ratio(operating_income, revenue),
            "ratio",
            "operating_income / revenue",
            "profitability",
        ),
        "net_margin": (
            _safe_ratio(net_income, revenue),
            "ratio",
            "net_income / revenue",
            "profitability",
        ),
        "operating_cash_flow_to_net_income": (
            _safe_ratio(ocf, net_income),
            "ratio",
            "operating_cash_flow / net_income",
            "cash_flow_quality",
        ),
        "free_cash_flow_margin": (
            _safe_ratio(fcf, revenue),
            "ratio",
            "free_cash_flow / revenue",
            "cash_flow_quality",
        ),
        "capital_expenditure_to_revenue": (
            _safe_ratio(abs(capex) if capex is not None else None, revenue),
            "ratio",
            "abs(capital_expenditure) / revenue",
            "cash_flow_quality",
        ),
        "current_ratio": (
            _safe_ratio(lines.get("current_assets"), lines.get("current_liabilities")),
            "ratio",
            "current_assets / current_liabilities",
            "balance_sheet",
        ),
        "net_debt": (
            debt - cash if has_debt and cash is not None else None,
            period.currency,
            "known_interest_bearing_debt - cash_and_equivalents",
            "balance_sheet",
        ),
        "debt_to_equity": (
            _safe_ratio(debt if has_debt else None, equity),
            "ratio",
            "known_interest_bearing_debt / stockholders_equity",
            "balance_sheet",
        ),
    }
    previous_lines = dict(previous.line_items) if previous is not None else {}
    growth_inputs = {
        "revenue_yoy": (revenue, _first(previous_lines, "revenue", "total_revenue")),
        "operating_income_yoy": (operating_income, previous_lines.get("operating_income")),
        "net_income_yoy": (
            net_income,
            _first(previous_lines, "net_income", "net_income_attributable_parent"),
        ),
        "operating_cash_flow_yoy": (ocf, previous_lines.get("operating_cash_flow")),
    }
    for code, (current, prior) in growth_inputs.items():
        growth = (
            (current - prior) / abs(prior)
            if current is not None and prior not in {None, Decimal(0)}
            else None
        )
        out[code] = (growth, "ratio", f"({code[:-4]} - prior) / abs(prior)", "growth")
    return out


def _selected_periods(
    company: PeerCompanyFacts,
    mode: PeerComparisonPeriodMode,
    limit: int,
) -> tuple[PeerCompanyPeriod, ...]:
    periods = company.periods
    if mode is PeerComparisonPeriodMode.ANNUAL:
        periods = tuple(item for item in periods if item.basis == "annual")
    return tuple(sorted(periods, key=lambda item: item.period_end, reverse=True)[: limit + 1])


def _same_period(
    candidate: PeerCompanyPeriod,
    anchor: PeerCompanyPeriod,
    mode: PeerComparisonPeriodMode,
) -> bool:
    if mode is PeerComparisonPeriodMode.ANNUAL:
        return candidate.basis == "annual" and candidate.fiscal_year == anchor.fiscal_year
    return candidate.basis == anchor.basis and candidate.fiscal_year == anchor.fiscal_year


def _status(cells: tuple[PeerComparisonCell, ...], units: tuple[str, ...]) -> PeerComparisonStatus:
    present = sum(item.value is not None for item in cells)
    if present == 0 or len(set(units)) > 1:
        return PeerComparisonStatus.NOT_COMPARABLE
    if present != len(cells):
        return PeerComparisonStatus.PARTIAL
    return PeerComparisonStatus.COMPARABLE


class PeerComparisonCalculator:
    """Create aligned fact rows without rankings, scores, or forecasts."""

    def compare(
        self,
        *,
        primary_instrument_id: str,
        peer_instrument_ids: tuple[str, ...],
        market: Market,
        as_of: datetime,
        period_mode: PeerComparisonPeriodMode,
        periods: int,
        companies: tuple[PeerCompanyFacts, ...],
        unavailable_instrument_ids: tuple[str, ...] = (),
    ) -> PeerComparisonFactPackage:
        if not 1 <= periods <= 5:
            raise DataContractError("periods must be in [1,5]")
        order = (primary_instrument_id, *peer_instrument_ids)
        by_id = {item.instrument_id: item for item in companies}
        primary = by_id.get(primary_instrument_id)
        anchors = _selected_periods(primary, period_mode, periods) if primary else ()
        rows: list[PeerComparisonRow] = []
        for anchor in anchors[:periods]:
            period_key = f"FY{anchor.fiscal_year or anchor.period_end.year}:{anchor.basis}"
            selected: dict[str, tuple[PeerCompanyPeriod, PeerCompanyPeriod | None]] = {}
            for instrument_id in order:
                company = by_id.get(instrument_id)
                if company is None:
                    continue
                candidates = _selected_periods(company, period_mode, periods)
                for index, candidate in enumerate(candidates):
                    if _same_period(candidate, anchor, period_mode):
                        previous = candidates[index + 1] if index + 1 < len(candidates) else None
                        if previous is not None and previous.basis != candidate.basis:
                            previous = None
                        selected[instrument_id] = (candidate, previous)
                        break
            metric_codes = (
                *_AMOUNT_CODES,
                *_RATIO_CODES,
                *(("growth", code) for code in _GROWTH_CODES),
            )
            for group, code in metric_codes:
                cells: list[PeerComparisonCell] = []
                present_units: list[str] = []
                formula = "reported"
                for instrument_id in order:
                    selected_period = selected.get(instrument_id)
                    if selected_period is None:
                        cells.append(
                            PeerComparisonCell(
                                instrument_id,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                (),
                                "period_unavailable",
                            )
                        )
                        continue
                    current, previous = selected_period
                    value, unit, formula, _ = _metric_values(current, previous)[code]
                    if value is not None:
                        present_units.append(unit)
                    cells.append(
                        PeerComparisonCell(
                            instrument_id,
                            value,
                            current.period_start,
                            current.period_end,
                            current.fiscal_year,
                            current.basis,
                            current.published_at,
                            current.source_names,
                            None if value is not None else "metric_unavailable",
                        )
                    )
                cells_tuple = tuple(cells)
                if not any(item.value is not None for item in cells_tuple):
                    continue
                unit = present_units[0] if len(set(present_units)) == 1 else "mixed"
                rows.append(
                    PeerComparisonRow(
                        group,
                        code,
                        period_key,
                        unit,
                        formula,
                        _FORMULA_VERSION,
                        _status(cells_tuple, tuple(present_units)),
                        cells_tuple,
                    )
                )
        rows.extend(self._valuation_rows(order, by_id))
        appendix = self._operating_rows(order, by_id)
        return PeerComparisonFactPackage(
            primary_instrument_id=primary_instrument_id,
            peer_instrument_ids=peer_instrument_ids,
            market=market,
            as_of=as_of,
            period_mode=period_mode,
            comparison_rows=tuple(rows),
            operating_metric_appendix=appendix,
            unavailable_instrument_ids=unavailable_instrument_ids,
        )

    @staticmethod
    def _valuation_rows(
        order: tuple[str, ...], by_id: dict[str, PeerCompanyFacts]
    ) -> list[PeerComparisonRow]:
        rows: list[PeerComparisonRow] = []
        for code in _VALUATION_CODES:
            cells: list[PeerComparisonCell] = []
            units: list[str] = []
            for instrument_id in order:
                valuation = getattr(by_id.get(instrument_id), "valuation", None)
                value = dict(valuation.values).get(code) if valuation is not None else None
                unit = valuation.currency if code == "market_cap" and valuation else "ratio"
                if value is not None:
                    units.append(unit)
                cells.append(
                    PeerComparisonCell(
                        instrument_id,
                        value,
                        None,
                        valuation.observed_at.date() if valuation else None,
                        None,
                        "current_snapshot" if valuation else None,
                        valuation.observed_at if valuation else None,
                        valuation.source_names if valuation else (),
                        None if value is not None else "valuation_unavailable",
                    )
                )
            cells_tuple = tuple(cells)
            if not any(item.value is not None for item in cells_tuple):
                continue
            rows.append(
                PeerComparisonRow(
                    "valuation",
                    code,
                    "current_snapshot",
                    units[0] if len(set(units)) == 1 else "mixed",
                    "provider_reported",
                    _FORMULA_VERSION,
                    _status(cells_tuple, tuple(units)),
                    cells_tuple,
                )
            )
        return rows

    @staticmethod
    def _operating_rows(
        order: tuple[str, ...], by_id: dict[str, PeerCompanyFacts]
    ) -> tuple[PeerComparisonRow, ...]:
        signatures: set[tuple[str, str, str, str, date]] = set()
        for company in by_id.values():
            signatures.update(
                (
                    item.metric_code,
                    item.unit,
                    item.frequency,
                    item.measurement_basis,
                    item.period_end,
                )
                for item in company.operating_facts
            )
        rows: list[PeerComparisonRow] = []
        for code, unit, frequency, basis, period_end in sorted(signatures):
            cells: list[PeerComparisonCell] = []
            for instrument_id in order:
                candidate_company = by_id.get(instrument_id)
                match = next(
                    (
                        item
                        for item in (
                            candidate_company.operating_facts if candidate_company else ()
                        )
                        if (
                            item.metric_code,
                            item.unit,
                            item.frequency,
                            item.measurement_basis,
                            item.period_end,
                        )
                        == (code, unit, frequency, basis, period_end)
                    ),
                    None,
                )
                cells.append(
                    PeerComparisonCell(
                        instrument_id,
                        match.value if match else None,
                        match.period_start if match else None,
                        match.period_end if match else None,
                        None,
                        basis if match else None,
                        match.published_at if match else None,
                        match.source_names if match else (),
                        None if match else "operating_metric_not_comparable",
                    )
                )
            cells_tuple = tuple(cells)
            rows.append(
                PeerComparisonRow(
                    "operating",
                    code,
                    f"{period_end.isoformat()}:{frequency}:{basis}",
                    unit,
                    "provider_reported",
                    _FORMULA_VERSION,
                    _status(
                        cells_tuple,
                        (unit,) if any(c.value is not None for c in cells) else (),
                    ),
                    cells_tuple,
                )
            )
        return tuple(rows)
