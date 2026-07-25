"""Generic Chinese disclosure text parser for company operating metrics.

Versioned, company-agnostic extraction of operating metrics disclosed in
monthly briefs, forecasts, and periodic reports. Financial-statement values
remain owned by the existing fundamentals/statements path.
Raw PDF bytes never enter this module — callers pass extracted plain text only.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from domain.a_share.enums import (
    CompanyDocumentType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from domain.a_share.models import CompanyOperatingMetricObservation
from domain.common.errors import DataContractError

PARSER_VERSION = "company_operating_cn_text_v1"

_NUMBER = r"(?P<num>[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"
_YEAR_MONTH = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月")
_MONTH_ROW = re.compile(
    rf"(?P<year>20\d{{2}})\s*年\s*(?P<month>\d{{1,2}})\s*月"
    rf"(?:\s*份)?\s+"
    rf"{_NUMBER.replace('num', 'vol_m')}\s+"
    rf"{_NUMBER.replace('num', 'vol_ytd')}\s+"
    rf"{_NUMBER.replace('num', 'rev_m')}\s+"
    rf"{_NUMBER.replace('num', 'rev_ytd')}\s+"
    rf"{_NUMBER.replace('num', 'price')}"
)
_PROSE_VOLUME = re.compile(rf"(?:销售)?商品猪\s*{_NUMBER.replace('num', 'val')}\s*万头")
_PROSE_PRICE = re.compile(rf"商品猪销售均价\s*{_NUMBER.replace('num', 'val')}\s*元\s*/?\s*公斤")
_PROSE_REVENUE = re.compile(rf"商品猪销售收入\s*{_NUMBER.replace('num', 'val')}\s*亿元")
_SLAUGHTER = re.compile(rf"屠宰生猪\s*{_NUMBER.replace('num', 'val')}\s*万头")
_BREEDING_SOW = re.compile(rf"能繁母猪存栏(?:为|是)?\s*{_NUMBER.replace('num', 'val')}\s*万头")
_FULL_COST = re.compile(
    rf"(?:养殖)?完全成本(?:为|是|约)?\s*{_NUMBER.replace('num', 'val')}\s*元\s*/?\s*公斤"
)
_TITLE_YEAR_MONTH = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月")
_TITLE_HALF = re.compile(r"(?P<year>20\d{2})\s*年\s*(?:半年度|中期)")
_TITLE_Q1 = re.compile(r"(?P<year>20\d{2})\s*年\s*(?:第一季度|一季报|一季度)")
_TITLE_Q3 = re.compile(r"(?P<year>20\d{2})\s*年\s*(?:第三季度|三季报|三季度)")
_TITLE_ANNUAL = re.compile(r"(?P<year>20\d{2})\s*年\s*(?:年度报告|年报)")
_TITLE_FORECAST = re.compile(r"业绩(?:预告|快报)")
_REPORT_SUFFIX = r"(?:全文|摘要)?(?:[（(][^）)]*修订[^）)]*[）)])?"
_QUARTER_REPORT_TITLE = re.compile(
    rf"20\d{{2}}\s*年\s*(?:第一季度|一季报|一季度|第三季度|三季报|三季度)"
    rf"(?:报告)?{_REPORT_SUFFIX}$"
)
_HALF_REPORT_TITLE = re.compile(rf"20\d{{2}}\s*年\s*(?:半年度|中期)(?:报告)?{_REPORT_SUFFIX}$")
_ANNUAL_REPORT_TITLE = re.compile(rf"20\d{{2}}\s*年\s*(?:年度报告|年报){_REPORT_SUFFIX}$")


@dataclass(frozen=True, slots=True)
class _RawMetric:
    metric_code: str
    value: Decimal
    unit: str
    period_start: date
    period_end: date
    frequency: IndustryMetricFrequency
    measurement_basis: IndustryMeasurementBasis
    is_audited: bool
    is_estimated: bool = False


def classify_document_type(title: str) -> CompanyDocumentType:
    text = title.strip()
    if any(token in text for token in ("销售简报", "经营简报", "产销快报", "月度经营")):
        return CompanyDocumentType.MONTHLY_OPERATING_BRIEF
    if _TITLE_FORECAST.search(text) is not None or "业绩预告" in text or "业绩快报" in text:
        return CompanyDocumentType.EARNINGS_FORECAST
    if _HALF_REPORT_TITLE.search(text) is not None:
        return CompanyDocumentType.HALF_YEAR_REPORT
    if _QUARTER_REPORT_TITLE.search(text) is not None:
        return CompanyDocumentType.QUARTERLY_REPORT
    if _ANNUAL_REPORT_TITLE.search(text) is not None:
        return CompanyDocumentType.ANNUAL_REPORT
    return CompanyDocumentType.OTHER


def is_relevant_operating_title(title: str) -> bool:
    doc_type = classify_document_type(title)
    return doc_type is not CompanyDocumentType.OTHER


def parse_company_operating_text(
    text: str,
    *,
    instrument_id: str,
    title: str,
    published_at: datetime,
    source_url: str,
    pdf_url: str | None,
    announcement_key: str | None,
) -> tuple[CompanyOperatingMetricObservation, ...]:
    """Extract structured metrics from plain disclosure text.

    Returns a newest-first ordered tuple. Empty when no unambiguous metrics are
    present. Never fabricates values or periods.
    """
    if not isinstance(text, str) or not text.strip():
        return ()
    normalized = _normalize_text(text)
    doc_type = classify_document_type(title)
    title_period = _period_from_title(title, doc_type)
    raw: list[_RawMetric] = []

    if doc_type is CompanyDocumentType.MONTHLY_OPERATING_BRIEF:
        raw.extend(_parse_monthly_table(normalized))
        raw.extend(_parse_monthly_prose(normalized, title_period))
    else:
        # Forecasts and periodic reports are parsed for explicit operating
        # disclosures only. Financial statement facts belong to the existing
        # fundamentals/statements provider and must not be duplicated here.
        raw.extend(_parse_optional_operating_prose(normalized, title_period))

    if not raw:
        return ()

    # Deduplicate by (metric_code, period_end, measurement_basis); first wins
    # (table rows before prose so table structure is preferred).
    seen: set[tuple[str, date, str]] = set()
    observations: list[CompanyOperatingMetricObservation] = []
    for item in raw:
        key = (item.metric_code, item.period_end, item.measurement_basis.value)
        if key in seen:
            continue
        seen.add(key)
        observations.append(
            CompanyOperatingMetricObservation(
                instrument_id=instrument_id,
                metric_code=item.metric_code,
                value=item.value,
                unit=item.unit,
                period_start=item.period_start,
                period_end=item.period_end,
                frequency=item.frequency,
                measurement_basis=item.measurement_basis,
                published_at=published_at,
                source_url=source_url,
                parser_version=PARSER_VERSION,
                pdf_url=pdf_url,
                announcement_key=announcement_key,
                is_audited=item.is_audited,
                is_estimated=item.is_estimated,
            )
        )
    observations.sort(
        key=lambda obs: (
            -obs.period_end.toordinal(),
            obs.metric_code,
            obs.measurement_basis.value,
        )
    )
    return tuple(observations)


def _normalize_text(text: str) -> str:
    # Collapse whitespace but keep newlines so table rows stay line-scoped.
    lines = []
    for line in text.replace("\u3000", " ").splitlines():
        collapsed = re.sub(r"[ \t]+", " ", line).strip()
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def _parse_decimal(raw: str) -> Decimal:
    cleaned = raw.replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise DataContractError(
            "metric value is not a finite decimal",
            details={"field": "value", "rule": "decimal"},
        ) from exc
    if not value.is_finite() or value < 0:
        raise DataContractError(
            "metric value must be a nonnegative finite decimal",
            details={"field": "value", "rule": "nonnegative_finite"},
        )
    return value


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    if month < 1 or month > 12:
        raise DataContractError("month out of range")
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return start, end


def _period_from_title(
    title: str, doc_type: CompanyDocumentType
) -> tuple[date, date, IndustryMetricFrequency] | None:
    text = title.strip()
    if doc_type is CompanyDocumentType.MONTHLY_OPERATING_BRIEF:
        match = _TITLE_YEAR_MONTH.search(text)
        if match is None:
            return None
        start, end = _month_bounds(int(match.group("year")), int(match.group("month")))
        return start, end, IndustryMetricFrequency.MONTHLY
    half = _TITLE_HALF.search(text)
    if half is not None:
        year = int(half.group("year"))
        return date(year, 1, 1), date(year, 6, 30), IndustryMetricFrequency.HALF_YEAR
    q1 = _TITLE_Q1.search(text)
    if q1 is not None:
        year = int(q1.group("year"))
        return date(year, 1, 1), date(year, 3, 31), IndustryMetricFrequency.QUARTERLY
    q3 = _TITLE_Q3.search(text)
    if q3 is not None:
        year = int(q3.group("year"))
        return date(year, 1, 1), date(year, 9, 30), IndustryMetricFrequency.QUARTERLY
    annual = _TITLE_ANNUAL.search(text)
    if annual is not None:
        year = int(annual.group("year"))
        return date(year, 1, 1), date(year, 12, 31), IndustryMetricFrequency.ANNUAL
    # Earnings forecast may still name a half-year/quarter/year period.
    ym = _TITLE_YEAR_MONTH.search(text)
    if ym is not None:
        start, end = _month_bounds(int(ym.group("year")), int(ym.group("month")))
        return start, end, IndustryMetricFrequency.MONTHLY
    return None


def _parse_monthly_table(text: str) -> list[_RawMetric]:
    out: list[_RawMetric] = []
    for match in _MONTH_ROW.finditer(text):
        year = int(match.group("year"))
        month = int(match.group("month"))
        start, end = _month_bounds(year, month)
        ytd_start = date(year, 1, 1)
        values = {
            "vol_m": match.group("vol_m"),
            "vol_ytd": match.group("vol_ytd"),
            "rev_m": match.group("rev_m"),
            "rev_ytd": match.group("rev_ytd"),
            "price": match.group("price"),
        }
        out.append(
            _RawMetric(
                metric_code="commercial_hog_sales_volume_10k_head",
                value=_parse_decimal(values["vol_m"]),
                unit="10k_head",
                period_start=start,
                period_end=end,
                frequency=IndustryMetricFrequency.MONTHLY,
                measurement_basis=IndustryMeasurementBasis.PERIOD_TOTAL,
                is_audited=False,
            )
        )
        out.append(
            _RawMetric(
                metric_code="commercial_hog_sales_volume_ytd_10k_head",
                value=_parse_decimal(values["vol_ytd"]),
                unit="10k_head",
                period_start=ytd_start,
                period_end=end,
                frequency=IndustryMetricFrequency.MONTHLY,
                measurement_basis=IndustryMeasurementBasis.YTD_TOTAL,
                is_audited=False,
            )
        )
        out.append(
            _RawMetric(
                metric_code="commercial_hog_sales_revenue_100m_cny",
                value=_parse_decimal(values["rev_m"]),
                unit="CNY_100m",
                period_start=start,
                period_end=end,
                frequency=IndustryMetricFrequency.MONTHLY,
                measurement_basis=IndustryMeasurementBasis.PERIOD_TOTAL,
                is_audited=False,
            )
        )
        out.append(
            _RawMetric(
                metric_code="commercial_hog_sales_revenue_ytd_100m_cny",
                value=_parse_decimal(values["rev_ytd"]),
                unit="CNY_100m",
                period_start=ytd_start,
                period_end=end,
                frequency=IndustryMetricFrequency.MONTHLY,
                measurement_basis=IndustryMeasurementBasis.YTD_TOTAL,
                is_audited=False,
            )
        )
        out.append(
            _RawMetric(
                metric_code="commercial_hog_avg_selling_price_cny_per_kg",
                value=_parse_decimal(values["price"]),
                unit="CNY/kg",
                period_start=start,
                period_end=end,
                frequency=IndustryMetricFrequency.MONTHLY,
                measurement_basis=IndustryMeasurementBasis.PERIOD_AVERAGE,
                is_audited=False,
            )
        )
    return out


def _parse_monthly_prose(
    text: str,
    title_period: tuple[date, date, IndustryMetricFrequency] | None,
) -> list[_RawMetric]:
    out: list[_RawMetric] = []
    # Prefer explicit month anchors near each fact; fall back to title period.
    for line in text.splitlines():
        month_match = _YEAR_MONTH.search(line)
        if month_match is not None:
            start, end = _month_bounds(
                int(month_match.group("year")), int(month_match.group("month"))
            )
            frequency = IndustryMetricFrequency.MONTHLY
        elif title_period is not None:
            start, end, frequency = title_period
        else:
            continue
        is_audited = False
        if (match := _PROSE_VOLUME.search(line)) is not None:
            out.append(
                _RawMetric(
                    metric_code="commercial_hog_sales_volume_10k_head",
                    value=_parse_decimal(match.group("val")),
                    unit="10k_head",
                    period_start=start,
                    period_end=end,
                    frequency=frequency,
                    measurement_basis=IndustryMeasurementBasis.PERIOD_TOTAL,
                    is_audited=is_audited,
                )
            )
        if (match := _PROSE_PRICE.search(line)) is not None:
            out.append(
                _RawMetric(
                    metric_code="commercial_hog_avg_selling_price_cny_per_kg",
                    value=_parse_decimal(match.group("val")),
                    unit="CNY/kg",
                    period_start=start,
                    period_end=end,
                    frequency=frequency,
                    measurement_basis=IndustryMeasurementBasis.PERIOD_AVERAGE,
                    is_audited=is_audited,
                )
            )
        if (match := _PROSE_REVENUE.search(line)) is not None:
            out.append(
                _RawMetric(
                    metric_code="commercial_hog_sales_revenue_100m_cny",
                    value=_parse_decimal(match.group("val")),
                    unit="CNY_100m",
                    period_start=start,
                    period_end=end,
                    frequency=frequency,
                    measurement_basis=IndustryMeasurementBasis.PERIOD_TOTAL,
                    is_audited=is_audited,
                )
            )
        if (match := _SLAUGHTER.search(line)) is not None:
            out.append(
                _RawMetric(
                    metric_code="hog_slaughter_volume_10k_head",
                    value=_parse_decimal(match.group("val")),
                    unit="10k_head",
                    period_start=start,
                    period_end=end,
                    frequency=frequency,
                    measurement_basis=IndustryMeasurementBasis.PERIOD_TOTAL,
                    is_audited=is_audited,
                )
            )
        if (match := _BREEDING_SOW.search(line)) is not None:
            out.append(
                _RawMetric(
                    metric_code="breeding_sow_inventory_10k_head",
                    value=_parse_decimal(match.group("val")),
                    unit="10k_head",
                    period_start=end,
                    period_end=end,
                    frequency=frequency,
                    measurement_basis=IndustryMeasurementBasis.PERIOD_END,
                    is_audited=is_audited,
                )
            )
        if (match := _FULL_COST.search(line)) is not None:
            out.append(
                _RawMetric(
                    metric_code="full_production_cost_cny_per_kg",
                    value=_parse_decimal(match.group("val")),
                    unit="CNY/kg",
                    period_start=start,
                    period_end=end,
                    frequency=frequency,
                    measurement_basis=IndustryMeasurementBasis.PERIOD_AVERAGE,
                    is_audited=is_audited,
                )
            )
    return out


def _parse_optional_operating_prose(
    text: str,
    title_period: tuple[date, date, IndustryMetricFrequency] | None,
) -> list[_RawMetric]:
    # Reuse monthly prose extractors when a report also discloses these facts.
    return _parse_monthly_prose(text, title_period)
