"""E3 codecs roundtrip, fingerprint safety, URL/secret guards."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.research_report_search_service import (
    report_text_fingerprint_hash,
)
from domain.a_share.enums import FinancialStatementType
from domain.a_share.models import (
    AnalystReportItem,
    AnnouncementItem,
    ConsensusEstimate,
    DividendRecord,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    NewsItem,
    UnlockRecord,
)
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from infrastructure.providers.a_share._parsing import sanitize_public_url
from infrastructure.providers.a_share.codecs import (
    E3_CODEC_IDS,
    announcements_codec,
    consensus_codec,
    corporate_actions_codec,
    f10_codec,
    fundamentals_codec,
    news_codec,
    reports_codec,
    statements_codec,
)
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2024, 1, 16, 6, 30, tzinfo=UTC)
FETCHED = datetime(2024, 1, 16, 6, 30, 1, tzinfo=UTC)
EXPIRES = datetime(2024, 1, 16, 12, 30, tzinfo=UTC)
INSTRUMENT_ID = "equity:A_SHARE:600519.SH"
_FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
)


def _meta(category: DataCategory) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=FETCHED,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.CLOSED,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )


def _entry(payload: str, category: DataCategory) -> CacheEntry:
    return CacheEntry(
        key=f"v1|A_SHARE|{category.value}|{INSTRUMENT_ID}|op|abcd",
        market=Market.A_SHARE,
        category=category,
        instrument_id=INSTRUMENT_ID,
        as_of=AS_OF,
        fetched_at=FETCHED,
        expires_at=EXPIRES,
        freshness=Freshness.UNKNOWN,
        vendor=VendorId.EASTMONEY,
        payload_json=payload,
    )


def test_e3_codec_ids_inventory() -> None:
    assert (
        frozenset(
            {
                "a_share_fundamentals.v1",
                "a_share_f10.v1",
                "a_share_statements.v1",
                "a_share_reports.v1",
                "a_share_consensus.v1",
                "a_share_announcements.v1",
                "a_share_corporate_actions.v1",
                "a_share_news.v1",
            }
        )
        == E3_CODEC_IDS
    )


def test_fundamentals_codec_roundtrip() -> None:
    value = (
        FundamentalMetric(
            name="eps",
            value=Decimal("59.55"),
            unit="CNY",
            period_end=date(2023, 12, 31),
            published_at=datetime(2024, 3, 30, tzinfo=UTC),
        ),
    )
    codec = fundamentals_codec()
    success = ProviderSuccess(value=value, meta=_meta(DataCategory.FUNDAMENTALS))
    payload = codec.encode(success)
    decoded = codec.decode(_entry(payload, DataCategory.FUNDAMENTALS))
    assert decoded.value == value
    assert decoded.meta.cache_disposition is CacheDisposition.HIT


def test_statements_codec_rejects_unknown_key() -> None:
    value = (
        FinancialStatementLine(
            statement_type=FinancialStatementType.INCOME_STATEMENT,
            period_end=date(2023, 12, 31),
            published_at=None,
            item_code="NETPROFIT",
            item_name="净利润",
            value=Decimal("1"),
            unit="CNY",
        ),
    )
    codec = statements_codec()
    payload = codec.encode(
        ProviderSuccess(value=value, meta=_meta(DataCategory.FINANCIAL_STATEMENTS))
    )
    obj = json.loads(payload)
    obj["value"][0]["extra"] = "nope"
    bad = json.dumps(obj, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    with pytest.raises(DataContractError):
        codec.decode(_entry(bad, DataCategory.FINANCIAL_STATEMENTS))


def test_reports_announcements_news_actions_codecs() -> None:
    reports = (
        AnalystReportItem(
            report_key="r1",
            title="t",
            institution="inst",
            analyst_names=("a",),
            published_at=AS_OF,
            rating="买入",
            target_price=Decimal("10"),
            eps_forecasts=(
                ConsensusEstimate(
                    fiscal_year=2024,
                    metric="eps",
                    mean=Decimal("1.2"),
                    high=None,
                    low=None,
                    institution_count=3,
                ),
            ),
            source_url="https://example.com/r",
            pdf_url=None,
        ),
    )
    payload = reports_codec().encode(
        ProviderSuccess(value=reports, meta=_meta(DataCategory.RESEARCH_REPORTS))
    )
    assert reports_codec().decode(_entry(payload, DataCategory.RESEARCH_REPORTS)).value == reports

    anns = (
        AnnouncementItem(
            announcement_key="a1",
            title="年报",
            published_at=AS_OF,
            category="定期报告",
            source_url="https://www.cninfo.com.cn/x",
            pdf_url=None,
        ),
    )
    payload = announcements_codec().encode(
        ProviderSuccess(value=anns, meta=_meta(DataCategory.ANNOUNCEMENTS))
    )
    assert announcements_codec().decode(_entry(payload, DataCategory.ANNOUNCEMENTS)).value == anns

    news = (
        NewsItem(
            news_key="n1",
            title="title",
            summary="sum",
            published_at=AS_OF,
            source_name="财联社",
            source_url="https://www.cls.cn/detail/1",
        ),
    )
    payload = news_codec().encode(ProviderSuccess(value=news, meta=_meta(DataCategory.NEWS)))
    assert news_codec().decode(_entry(payload, DataCategory.NEWS)).value == news

    actions: tuple[UnlockRecord | DividendRecord, ...] = (
        UnlockRecord(
            unlock_date=date(2024, 6, 15),
            published_at=AS_OF,
            unlock_type="限售",
            unlock_shares=1000,
            tradable_shares=1000,
            market_value_cny=Decimal("1000"),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=False,
        ),
        DividendRecord(
            fiscal_year=2023,
            plan_status="实施",
            ex_date=date(2024, 6, 20),
            cash_per_share=Decimal("30.876"),
            bonus_shares_per_share=Decimal("0"),
            transfer_shares_per_share=Decimal("0"),
            published_at=AS_OF,
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
            is_authoritative=False,
        ),
    )
    payload = corporate_actions_codec().encode(
        ProviderSuccess(value=actions, meta=_meta(DataCategory.CORPORATE_ACTIONS))
    )
    assert (
        corporate_actions_codec().decode(_entry(payload, DataCategory.CORPORATE_ACTIONS)).value
        == actions
    )

    f10 = (
        F10Section(
            section="company",
            title="贵州茅台",
            body="ORG_NAME: x",
            as_of=AS_OF,
        ),
    )
    payload = f10_codec().encode(ProviderSuccess(value=f10, meta=_meta(DataCategory.FUNDAMENTALS)))
    assert f10_codec().decode(_entry(payload, DataCategory.FUNDAMENTALS)).value == f10

    cons = (
        ConsensusEstimate(
            fiscal_year=2024,
            metric="eps",
            mean=Decimal("65.5"),
            high=Decimal("66"),
            low=Decimal("65"),
            institution_count=2,
        ),
    )
    payload = consensus_codec().encode(
        ProviderSuccess(value=cons, meta=_meta(DataCategory.RESEARCH_REPORTS))
    )
    assert consensus_codec().decode(_entry(payload, DataCategory.RESEARCH_REPORTS)).value == cons


def test_report_text_fingerprint_uses_sha256_not_raw() -> None:
    redactor = DefaultSecretRedactor()
    raw = "贵州茅台 深度 research api_key=test-secret-value"
    digest = report_text_fingerprint_hash(raw, redactor=redactor)
    assert len(digest) == 64
    assert "茅台" not in digest
    assert "sk-live" not in digest
    fp = build_a_share_fingerprint(
        "a_share.reports.v1",
        "market",
        {"limit": "20", "offset": "0", "text_sha256": digest},
        AS_OF,
    )
    assert raw not in fp
    assert "sk-live" not in fp
    # Same normalized text → same digest.
    assert digest == report_text_fingerprint_hash(
        "  贵州茅台   深度 research api_key=test-secret-value ", redactor=redactor
    )


def test_sanitize_public_url_rejects_bad_schemes_and_secrets() -> None:
    assert sanitize_public_url("https://www.cninfo.com.cn/a.pdf", field="u")
    with pytest.raises(DataContractError):
        sanitize_public_url("file:///etc/passwd", field="u")
    with pytest.raises(DataContractError):
        sanitize_public_url("javascript:alert(1)", field="u")
    with pytest.raises(DataContractError):
        sanitize_public_url("https://evil.test/?token=abc", field="u")
    with pytest.raises(DataContractError):
        sanitize_public_url("https://user:pass@evil.test/x", field="u")


def test_fixture_secret_scanner() -> None:
    """Fixtures must not embed common secret patterns."""
    banned = (
        "sk-live-",
        "api_key=",
        "access_token=",
        "Authorization:",
        "Bearer ",
        "password=",
        "BEGIN " + "PRIVATE KEY",
    )
    hits: list[str] = []
    for path in _FIXTURES.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".json", ".txt", ".html"} and not path.name.endswith(".meta.json"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in banned:
            if token.lower() in text.lower():
                hits.append(f"{path}: {token}")
    assert hits == []


def test_malicious_html_not_executed_in_codec_roundtrip() -> None:
    # News/summary may contain HTML-looking text; codecs treat as opaque strings.
    news = (
        NewsItem(
            news_key="evil",
            title="<script>alert(1)</script>",
            summary="<img src=x onerror=alert(1)>",
            published_at=AS_OF,
            source_name="test",
            source_url="https://www.cls.cn/detail/1",
        ),
    )
    payload = news_codec().encode(ProviderSuccess(value=news, meta=_meta(DataCategory.NEWS)))
    decoded = news_codec().decode(_entry(payload, DataCategory.NEWS))
    assert decoded.value[0].title.startswith("<script>")
    # No execution path — plain data only.
    assert "onerror" in (decoded.value[0].summary or "")
