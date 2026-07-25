"""Representative parser contracts for company operating-metrics text."""

from __future__ import annotations

from datetime import UTC, datetime

from domain.a_share.enums import (
    CompanyDocumentType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from infrastructure.providers.a_share.company_operating_parser import (
    PARSER_VERSION,
    classify_document_type,
    parse_company_operating_text,
)

INSTRUMENT = "equity:A_SHARE:000001.SZ"
PUBLISHED = datetime(2026, 7, 7, 16, 0, tzinfo=UTC)
SOURCE = "https://www.cninfo.com.cn/new/disclosure/detail?announceId=1225411958"
PDF = "https://static.cninfo.com.cn/finalpage/2026-07-07/1225411958.PDF"
TITLE = "某某股份有限公司2026年6月销售简报"

JUNE_BRIEF = """
2026 年 6 月份，公司销售商品猪 622.7 万头；商品猪销售均价 9.69 元/公斤；商品猪销售收入 75.00 亿元。
屠宰生猪 295.6 万头。
截至 2026 年 6 月末，公司能繁母猪存栏为 311.3 万头。

2026 年 1 月 700.9 700.9 105.66 105.66 12.57
2026 年 2 月 650.0 1,350.9 90.00 195.66 11.20
2026 年 3 月 640.0 1,990.9 88.00 283.66 10.80
2026 年 4 月 630.0 2,620.9 80.00 363.66 10.20
2026 年 5 月 618.0 3,238.9 77.00 440.66 9.90
2026 年 6 月 622.7 3,861.5 75.00 501.45 9.69
"""


def test_classify_monthly_sales_brief() -> None:
    assert (
        classify_document_type("某某股份有限公司2026年6月销售简报")
        is CompanyDocumentType.MONTHLY_OPERATING_BRIEF
    )
    assert (
        classify_document_type("某某股份有限公司2026年半年度业绩预告")
        is CompanyDocumentType.EARNINGS_FORECAST
    )
    assert (
        classify_document_type("某某股份有限公司2026年第一季度报告")
        is CompanyDocumentType.QUARTERLY_REPORT
    )
    assert classify_document_type("独立董事年报工作制度（2025年12月）") is CompanyDocumentType.OTHER
    assert classify_document_type("半年报监事会决议公告") is CompanyDocumentType.OTHER


def test_june_sales_brief_fixture_truth() -> None:
    observations = parse_company_operating_text(
        JUNE_BRIEF,
        instrument_id=INSTRUMENT,
        title=TITLE,
        published_at=PUBLISHED,
        source_url=SOURCE,
        pdf_url=PDF,
        announcement_key="1225411958",
    )
    by_key = {
        (item.metric_code, str(item.period_end), item.measurement_basis.value): item
        for item in observations
    }

    june_volume = by_key[("commercial_hog_sales_volume_10k_head", "2026-06-30", "period_total")]
    assert str(june_volume.value) == "622.7"
    assert june_volume.unit == "10k_head"
    assert june_volume.frequency is IndustryMetricFrequency.MONTHLY
    assert june_volume.is_audited is False
    assert june_volume.parser_version == PARSER_VERSION

    june_ytd = by_key[("commercial_hog_sales_volume_ytd_10k_head", "2026-06-30", "ytd_total")]
    assert str(june_ytd.value) == "3861.5"
    assert june_ytd.period_start.isoformat() == "2026-01-01"

    june_revenue = by_key[("commercial_hog_sales_revenue_100m_cny", "2026-06-30", "period_total")]
    assert str(june_revenue.value) == "75.00"
    assert june_revenue.unit == "CNY_100m"

    june_revenue_ytd = by_key[
        ("commercial_hog_sales_revenue_ytd_100m_cny", "2026-06-30", "ytd_total")
    ]
    assert str(june_revenue_ytd.value) == "501.45"

    june_price = by_key[
        (
            "commercial_hog_avg_selling_price_cny_per_kg",
            "2026-06-30",
            "period_average",
        )
    ]
    assert str(june_price.value) == "9.69"
    assert june_price.measurement_basis is IndustryMeasurementBasis.PERIOD_AVERAGE

    slaughter = by_key[("hog_slaughter_volume_10k_head", "2026-06-30", "period_total")]
    assert str(slaughter.value) == "295.6"

    sow = by_key[("breeding_sow_inventory_10k_head", "2026-06-30", "period_end")]
    assert str(sow.value) == "311.3"
    assert sow.measurement_basis is IndustryMeasurementBasis.PERIOD_END

    # Newest-first ordering for the package.
    assert observations[0].period_end >= observations[-1].period_end
    # Six monthly table rows for volume should exist.
    monthly_volume = [
        item
        for item in observations
        if item.metric_code == "commercial_hog_sales_volume_10k_head"
        and item.measurement_basis is IndustryMeasurementBasis.PERIOD_TOTAL
    ]
    assert len(monthly_volume) == 6


def test_periodic_report_extracts_operating_facts_but_not_financial_statements() -> None:
    observations = parse_company_operating_text(
        "报告期末，公司能繁母猪存栏为 305.2 万头，养殖完全成本约 12.8 元/公斤。",
        instrument_id=INSTRUMENT,
        title="某某股份有限公司2026年第一季度报告",
        published_at=PUBLISHED,
        source_url=SOURCE,
        pdf_url=PDF,
        announcement_key="periodic-1",
    )

    by_code = {item.metric_code: item for item in observations}
    assert str(by_code["breeding_sow_inventory_10k_head"].value) == "305.2"
    assert str(by_code["full_production_cost_cny_per_kg"].value) == "12.8"
    assert by_code["full_production_cost_cny_per_kg"].period_end.isoformat() == "2026-03-31"

    financial_only = parse_company_operating_text(
        "预计归属于上市公司股东的净利润为 120 亿元。",
        instrument_id=INSTRUMENT,
        title="某某股份有限公司2026年半年度业绩预告",
        published_at=PUBLISHED,
        source_url=SOURCE,
        pdf_url=PDF,
        announcement_key="forecast-1",
    )
    assert financial_only == ()
