"""Representative provider/service/MCP contracts for company operating metrics."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from application.dto.a_share import AShareGetCompanyOperatingMetricsInput
from application.dto.provider_routing import ProviderResultMeta
from application.ports.http_transport import HttpRequest, HttpResponse
from application.services.a_share_company_operating_metrics_service import (
    AShareCompanyOperatingMetricsService,
)
from application.services.provider_router import RouterExecutionResult
from conftest import FixedClock
from domain.a_share.enums import (
    CompanyDocumentParseStatus,
    IndustryMeasurementBasis,
)
from domain.a_share.models import (
    CompanyOperatingMetricObservation,
    CompanyOperatingMetricsSnapshot,
    DocumentParseReceipt,
)
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.company_operating_parser import PARSER_VERSION

NOW = datetime(2026, 7, 22, tzinfo=UTC)
INSTRUMENT_ID = "equity:A_SHARE:000001.SZ"
INSTRUMENT = Instrument(
    instrument_id=INSTRUMENT_ID,
    symbol="000001.SZ",
    name="Example Co",
    market=Market.A_SHARE,
    exchange="SZSE",
    currency="CNY",
    timezone="Asia/Shanghai",
    asset_type=AssetType.EQUITY,
)
PDF_URL = "https://static.cninfo.com.cn/finalpage/2026-07-07/1225411958.PDF"
BRIEF_TEXT = (
    "2026 年 6 月份，公司销售商品猪 622.7 万头；商品猪销售均价 9.69 元/公斤；"
    "商品猪销售收入 75.00 亿元。\n"
    "屠宰生猪 295.6 万头。\n"
    "截至 2026 年 6 月末，公司能繁母猪存栏为 311.3 万头。\n"
    "2026 年 6 月 622.7 3,861.5 75.00 501.45 9.69\n"
)


def _pdf_with_text(text: str) -> bytes:
    """Build a minimal one-page PDF whose extractable text includes ``text``."""
    writer = PdfWriter()
    # Use a simple text-bearing page via pypdf page creation helpers when available.
    page = writer.add_blank_page(width=612, height=792)
    # Attach a content stream with the UTF-16 text as a comment-like stream that
    # extract_text may not always see; for contract tests we also monkeypatch
    # extraction when needed. Prefer real extraction via a Form text annotation.
    stream = DecodedStreamObject()
    # Latin-1 safe content; Chinese is injected via parser unit tests.
    # For adapter integration, we patch extract to return BRIEF_TEXT when body matches.
    stream.set_data(f"BT /F1 12 Tf 100 700 Td ({text[:40]!r}) Tj ET".encode("latin-1", "replace"))
    page[NameObject("/Contents")] = stream
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _OperatingTransport:
    def __init__(self, *, fail_pdf: bool = False) -> None:
        self.fail_pdf = fail_pdf
        self.pdf_body = b"%PDF-1.4\n" + b"0" * 200 + b"\n%%EOF\n"

    async def send(self, request: HttpRequest) -> HttpResponse:
        url = request.url
        if "hisAnnouncement/query" in url or "hisannouncement/query" in url.lower():
            body = json.dumps(
                {
                    "announcements": [
                        {
                            "announcementId": "1225411958",
                            "announcementTitle": "某某股份有限公司2026年6月销售简报",
                            "announcementTime": "2026-07-07 16:00:00",
                            "announcementTypeName": "临时公告",
                            "adjunctUrl": "finalpage/2026-07-07/1225411958.PDF",
                        },
                        {
                            "announcementId": "1225000000",
                            "announcementTitle": "某某股份有限公司2026年5月销售简报",
                            "announcementTime": "2026-06-07 16:00:00",
                            "announcementTypeName": "临时公告",
                            "adjunctUrl": "finalpage/2026-06-07/1225000000.PDF",
                        },
                    ]
                },
                ensure_ascii=False,
            ).encode()
            return HttpResponse(200, {"content-type": "application/json"}, body)
        if "static.cninfo.com.cn/finalpage/" in url:
            if self.fail_pdf and "1225000000" in url:
                return HttpResponse(500, {"content-type": "text/plain"}, b"error")
            return HttpResponse(
                200,
                {"content-type": "application/pdf"},
                self.pdf_body,
            )
        raise AssertionError(f"unexpected URL: {url}")


@pytest.mark.asyncio
async def test_cninfo_company_operating_metrics_partial_document_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CninfoAShareAdapter(
        _OperatingTransport(fail_pdf=True),
        clock=FixedClock(NOW),
        org_id_map={"000001": "gssz0000001"},
    )

    def _fake_extract(body: bytes) -> tuple[str, int]:
        return BRIEF_TEXT, 1

    monkeypatch.setattr(CninfoAShareAdapter, "_extract_pdf_text", staticmethod(_fake_extract))

    result = await adapter.get_company_operating_metrics(
        INSTRUMENT,
        lookback_months=6,
        document_limit=5,
        metric_codes=(),
        as_of=NOW,
    )

    assert adapter.supports(Market.A_SHARE, DataCategory.COMPANY_OPERATING_METRICS)
    assert result.meta.category is DataCategory.COMPANY_OPERATING_METRICS
    assert "COMPANY_OPERATING_DOCUMENT_PARTIAL" in result.meta.warnings
    assert "COMPANY_OPERATING_UNAUDITED_SALES_BRIEF" in result.meta.warnings
    assert result.value.instrument_id == INSTRUMENT_ID
    june = next(
        item
        for item in result.value.observations
        if item.metric_code == "commercial_hog_sales_volume_10k_head"
        and str(item.period_end) == "2026-06-30"
    )
    assert str(june.value) == "622.7"
    assert june.measurement_basis is IndustryMeasurementBasis.PERIOD_TOTAL
    statuses = {doc.status for doc in result.value.documents}
    assert CompanyDocumentParseStatus.PARSED in statuses
    assert CompanyDocumentParseStatus.DOWNLOAD_FAILED in statuses


def test_company_operating_metrics_input_bounds() -> None:
    ok = AShareGetCompanyOperatingMetricsInput(
        instrument_id=INSTRUMENT_ID,
        lookback_months=12,
        document_limit=10,
        metric_codes=("commercial_hog_sales_volume_10k_head",),
    )
    assert ok.document_limit == 10
    with pytest.raises(ValidationError):
        AShareGetCompanyOperatingMetricsInput(
            instrument_id="etf:A_SHARE:510050.SH",
        )
    with pytest.raises(ValidationError):
        AShareGetCompanyOperatingMetricsInput(
            instrument_id=INSTRUMENT_ID,
            lookback_months=2,
        )
    with pytest.raises(ValidationError):
        AShareGetCompanyOperatingMetricsInput(
            instrument_id=INSTRUMENT_ID,
            document_limit=31,
        )
    with pytest.raises(ValidationError):
        AShareGetCompanyOperatingMetricsInput(
            instrument_id=INSTRUMENT_ID,
            metric_codes=("NotSnake",),
        )


@pytest.mark.asyncio
async def test_company_operating_metrics_service_maps_snapshot() -> None:
    observation = CompanyOperatingMetricObservation(
        instrument_id=INSTRUMENT_ID,
        metric_code="commercial_hog_sales_volume_10k_head",
        value=Decimal("622.7"),
        unit="10k_head",
        period_start=datetime(2026, 6, 1, tzinfo=UTC).date(),
        period_end=datetime(2026, 6, 30, tzinfo=UTC).date(),
        frequency=__import__(
            "domain.a_share.enums", fromlist=["IndustryMetricFrequency"]
        ).IndustryMetricFrequency.MONTHLY,
        measurement_basis=IndustryMeasurementBasis.PERIOD_TOTAL,
        published_at=NOW,
        source_url=PDF_URL,
        parser_version=PARSER_VERSION,
        pdf_url=PDF_URL,
        announcement_key="1225411958",
        is_audited=False,
    )
    receipt = DocumentParseReceipt(
        announcement_key="1225411958",
        title="某某股份有限公司2026年6月销售简报",
        document_type=__import__(
            "domain.a_share.enums", fromlist=["CompanyDocumentType"]
        ).CompanyDocumentType.MONTHLY_OPERATING_BRIEF,
        published_at=NOW,
        source_url=PDF_URL,
        pdf_url=PDF_URL,
        parser_version=PARSER_VERSION,
        page_count=1,
        status=CompanyDocumentParseStatus.PARSED,
        extracted_metric_count=1,
    )
    snapshot = CompanyOperatingMetricsSnapshot(
        instrument_id=INSTRUMENT_ID,
        as_of=NOW,
        lookback_months=12,
        observations=(observation,),
        documents=(receipt,),
        missing_metric_codes=("full_production_cost_cny_per_kg",),
    )
    meta = ProviderResultMeta(
        vendor=VendorId.CNINFO,
        category=DataCategory.COMPANY_OPERATING_METRICS,
        role=SourceRole.PRIMARY,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=("COMPANY_OPERATING_UNAUDITED_SALES_BRIEF",),
    )
    router = MagicMock()
    router.execute = AsyncMock(
        return_value=RouterExecutionResult(
            ok=True,
            value=snapshot,
            criticality=DataCriticality.CORE,
            meta=meta,
            attempts=(),
            warnings=(),
            error=None,
        )
    )
    service = AShareCompanyOperatingMetricsService(router=router)
    result = await service.get_company_operating_metrics(
        INSTRUMENT,
        lookback_months=12,
        document_limit=5,
        metric_codes=("commercial_hog_sales_volume_10k_head", "full_production_cost_cny_per_kg"),
        as_of=NOW,
    )
    assert result.ok is True
    assert result.data is not None
    assert len(result.data.observations) == 1
    assert result.data.missing_metric_codes == ("full_production_cost_cny_per_kg",)
    assert any(
        warning.code == "COMPANY_OPERATING_UNAUDITED_SALES_BRIEF" for warning in result.warnings
    )
