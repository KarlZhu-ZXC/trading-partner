"""Representative offline contracts for official hog-cycle facts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.a_share.enums import IndustryMeasurementBasis, IndustryMetricFrequency
from domain.common.enums import DataCategory, Market, VendorId
from infrastructure.providers.a_share.nahs import NahsHogCycleAdapter

NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _article(publishdate: str, month: str, live_hog: str, ratio: str) -> bytes:
    return f"""
    <html><head><meta name="publishdate" content="{publishdate}"></head><body>
    本月猪粮比价为{ratio}:1。
    <table><tr><th>项目</th><th>本月</th></tr>
      <tr><td>仔猪</td><td>22.50</td></tr>
      <tr><td>生猪</td><td>{live_hog}</td></tr>
      <tr><td>猪肉</td><td>20.00</td></tr>
      <tr><td>玉米</td><td>2.50</td></tr>
      <tr><td>豆粕</td><td>3.20</td></tr>
      <tr><td>育肥猪配合饲料</td><td>3.40</td></tr>
    </table><span>{month}</span></body></html>
    """.encode()


class _Transport:
    async def send(self, request: HttpRequest) -> HttpResponse:
        url = request.url
        if url == "https://www.nahs.org.cn/jcyj/scxs/":
            body = """<a href="./202607/t20260708_3.htm">
            2026年6月全国畜产品和饲料价格情况</a>
            <a href="./202606/t20260617_2.htm">
            2026年5月全国畜产品和饲料价格情况</a>
            <a href="./202605/t20260519_1.htm">
            2026年4月全国畜产品和饲料价格情况</a>""".encode()
        elif url == "https://www.nahs.org.cn/jcyj/jcgz/":
            body = ('<a href="./202607/t20260716_4.htm">2026年上半年畜牧生产稳定发展</a>').encode()
        elif "t20260708_3" in url:
            body = _article("2026-07-08", "June", "10.09", "4.05")
        elif "t20260617_2" in url:
            body = _article("2026-06-17", "May", "10.15", "4.06")
        elif "t20260519_1" in url:
            body = _article("2026-05-19", "April", "10.07", "4.03")
        elif "t20260716_4" in url:
            body = """<meta name="publishdate" content="2026-07-16"><body>
            猪肉产量3119万吨。全国生猪出栏37246万头，全国生猪存栏42491万头。
            能繁母猪存栏3780万头，目前为正常保有量3750万头的100.8%。</body>""".encode()
        else:
            raise AssertionError(f"unexpected test URL path: {url.split('?', 1)[0]}")
        return HttpResponse(200, {"content-type": "text/html; charset=utf-8"}, body)


@pytest.mark.asyncio
async def test_hog_cycle_parses_prices_capacity_and_discloses_frequency() -> None:
    adapter = NahsHogCycleAdapter(_Transport(), clock=FixedClock(NOW))

    result = await adapter.get_hog_cycle(lookback_months=3, as_of=NOW)

    assert adapter.vendor_id is VendorId.NAHS
    assert adapter.supports(Market.A_SHARE, DataCategory.INDUSTRY_CYCLE)
    assert result.value.dataset_code == "nahs_national_hog_cycle"
    live_hog = [
        item
        for item in result.value.observations
        if item.metric_code == "live_hog_cny_per_kg"
    ]
    assert [str(item.period_start) for item in live_hog] == [
        "2026-04-01",
        "2026-05-01",
        "2026-06-01",
    ]
    assert str(live_hog[-1].value) == "10.09"
    assert live_hog[-1].unit == "CNY/kg"
    latest_ratio = [
        item
        for item in result.value.observations
        if item.metric_code == "pig_grain_ratio"
    ][-1]
    assert str(latest_ratio.value) == "4.05"
    sow_capacity = next(
        item
        for item in result.value.observations
        if item.metric_code == "breeding_sow_inventory_10k_head"
    )
    assert str(sow_capacity.value) == "3780"
    assert sow_capacity.frequency is IndustryMetricFrequency.HALF_YEAR
    assert sow_capacity.measurement_basis is IndustryMeasurementBasis.PERIOD_END
    slaughter = next(
        item
        for item in result.value.observations
        if item.metric_code == "pig_slaughter_ytd_10k_head"
    )
    assert slaughter.measurement_basis is IndustryMeasurementBasis.YTD_TOTAL
    assert result.value.missing_components == (
        "company_operating_data",
        "live_hog_futures_curve",
    )
    assert "HOG_CYCLE_FUTURES_CURVE_UNAVAILABLE" in result.meta.warnings
