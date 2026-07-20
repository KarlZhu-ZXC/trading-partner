"""Phase 1G G2: US research cache codec roundtrip + corruption."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.us_research.enums import (
    USFilingForm,
    USFundamentalBasis,
    USInsiderAcquiredDisposed,
    USStatementFrequency,
    USStatementType,
)
from domain.us_research.models import (
    USCompanyProfile,
    USFiling,
    USFilingSection,
    USFinancialStatements,
    USFundamentalMetrics,
    USFundamentalSnapshot,
    USInsiderTransaction,
    USStatementPeriod,
)
from infrastructure.providers.us.research_codecs import (
    CODEC_US_FILINGS,
    CODEC_US_FINANCIAL_STATEMENTS,
    CODEC_US_FUNDAMENTAL_SNAPSHOT,
    CODEC_US_INSIDER_ACTIVITY,
    us_filings_codec,
    us_financial_statements_codec,
    us_fundamental_snapshot_codec,
    us_insider_activity_codec,
)

UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
FETCHED = datetime(2026, 7, 17, 20, 0, 1, tzinfo=UTC)
EXPIRES = datetime(2026, 7, 17, 20, 5, tzinfo=UTC)
INSTRUMENT = "equity:US:NVDA"


def _meta(category: DataCategory) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.SEC_EDGAR,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=FETCHED,
        freshness=Freshness.FRESH,
        session=TradingSession.CLOSED,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=("SECTIONS_UNAVAILABLE",),
    )


def _entry(payload: str, category: DataCategory) -> CacheEntry:
    return CacheEntry(
        key=f"v1|US|{category.value}|{INSTRUMENT}|{AS_OF.isoformat()}|op|abcdef0123456789",
        market=Market.US,
        category=category,
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        fetched_at=FETCHED,
        expires_at=EXPIRES,
        freshness=Freshness.FRESH,
        vendor=VendorId.SEC_EDGAR,
        payload_json=payload,
    )


def _filing() -> USFiling:
    return USFiling(
        instrument_id=INSTRUMENT,
        accession="0001045810-26-000010",
        form=USFilingForm.FORM_10K,
        is_amendment=True,
        filed_date=date(2026, 3, 1),
        accepted_at=datetime(2026, 3, 1, 16, 0, tzinfo=UTC),
        period_of_report=date(2025, 12, 31),
        primary_document="tenka.htm",
        url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000010/tenka.htm",
        items=(),
        sections=(
            USFilingSection(
                section_name="Item 1 Business",
                document_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000010/tenka.htm",
                text="We design GPUs.",
                algorithm_version="sec_sections_v1",
            ),
        ),
    )


def _insider() -> USInsiderTransaction:
    return USInsiderTransaction(
        instrument_id=INSTRUMENT,
        owner_name="SMITH JANE",
        relationship="CEO",
        transaction_date=date(2026, 6, 1),
        filed_at=datetime(2026, 6, 2, 15, 0, tzinfo=UTC),
        accepted_at=datetime(2026, 6, 2, 15, 0, tzinfo=UTC),
        transaction_code="S",
        acquired_disposed=USInsiderAcquiredDisposed.DISPOSED,
        shares=Decimal("1000"),
        price=Decimal("120.50"),
        post_transaction_shares=Decimal("50000"),
        is_direct=True,
        rule_10b5_1=True,
    )


def _snapshot() -> USFundamentalSnapshot:
    return USFundamentalSnapshot(
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        profile=USCompanyProfile(
            instrument_id=INSTRUMENT,
            legal_name="NVIDIA CORP",
            description=None,
            sector=None,
            industry=None,
            country=None,
            website=None,
            employees=None,
            market_cap=None,
        ),
        metrics=USFundamentalMetrics(
            trailing_pe=None,
            forward_pe=None,
            peg_ratio=None,
            price_to_book=None,
            price_to_sales=None,
            enterprise_to_ebitda=None,
            dividend_yield=None,
            beta=None,
            eps_ttm=None,
            eps_forward=None,
            book_value_per_share=None,
            revenue_per_share=None,
            revenue=Decimal("130497000000"),
            gross_profit=Decimal("97858000000"),
            ebitda=None,
            net_income=Decimal("72881000000"),
            profit_margin=None,
            operating_margin=None,
            roe=None,
            roa=None,
            debt_to_equity=None,
            current_ratio=None,
            revenue_growth=None,
            eps_growth=None,
            estimate_revision=None,
            share_count=Decimal("24400000000"),
            stock_based_compensation=Decimal("4737000000"),
            capital_expenditure=Decimal("3236000000"),
            free_cash_flow=Decimal("60853000000"),
            net_cash_or_debt=Decimal("137000000"),
            period_end=date(2025, 1, 26),
            filed_at=datetime(2025, 3, 16, tzinfo=UTC),
            basis=USFundamentalBasis.ANNUAL,
        ),
        corporate_actions=(),
        degraded=False,
        warning_codes=("SEC_FUNDAMENTALS_PARTIAL",),
    )


def _statements() -> USFinancialStatements:
    period = USStatementPeriod(
        statement_type=USStatementType.INCOME,
        frequency=USStatementFrequency.ANNUAL,
        fiscal_year=2025,
        fiscal_period="FY",
        period_end=date(2025, 1, 26),
        filed_at=datetime(2025, 3, 16, tzinfo=UTC),
        currency="USD",
        line_items=(
            ("revenue", Decimal("130497000000")),
            ("net_income", Decimal("72881000000")),
            ("operating_income", None),
        ),
    )
    return USFinancialStatements(
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        frequency=USStatementFrequency.ANNUAL,
        income=(period,),
        balance_sheet=(),
        cash_flow=(),
    )


@pytest.mark.parametrize(
    ("codec_factory", "codec_id", "category", "value"),
    [
        (us_filings_codec, CODEC_US_FILINGS, DataCategory.FILINGS, (_filing(),)),
        (
            us_insider_activity_codec,
            CODEC_US_INSIDER_ACTIVITY,
            DataCategory.INSIDER_ACTIVITY,
            (_insider(),),
        ),
        (
            us_fundamental_snapshot_codec,
            CODEC_US_FUNDAMENTAL_SNAPSHOT,
            DataCategory.FUNDAMENTALS,
            _snapshot(),
        ),
        (
            us_financial_statements_codec,
            CODEC_US_FINANCIAL_STATEMENTS,
            DataCategory.FINANCIAL_STATEMENTS,
            _statements(),
        ),
    ],
)
def test_research_codec_roundtrip_and_corruption(
    codec_factory: object,
    codec_id: str,
    category: DataCategory,
    value: object,
) -> None:
    codec = codec_factory()  # type: ignore[operator]
    assert codec.codec_id == codec_id
    success = ProviderSuccess(value=value, meta=_meta(category))  # type: ignore[arg-type]
    payload = codec.encode(success)
    assert f'"codec":"{codec_id}"' in payload
    assert "130497000000" not in payload or '"130497000000"' in payload
    assert "1.30497e" not in payload.lower()
    decoded = codec.decode(_entry(payload, category))
    assert decoded.value == value
    assert decoded.meta.vendor is VendorId.SEC_EDGAR
    assert decoded.meta.category is category
    assert decoded.meta.cache_disposition is CacheDisposition.HIT
    assert decoded.meta.warnings == ("SECTIONS_UNAVAILABLE",)
    if category is DataCategory.INSIDER_ACTIVITY:
        assert type(decoded.value[0].shares) is Decimal
    if category is DataCategory.FUNDAMENTALS:
        assert type(decoded.value.metrics.revenue) is Decimal  # type: ignore[union-attr]
    if category is DataCategory.FINANCIAL_STATEMENTS:
        assert type(decoded.value.income[0].line_items[0][1]) is Decimal

    bad = payload.replace(codec_id, "us.x.v0")
    with pytest.raises(DataContractError) as exc:
        codec.decode(_entry(bad, category))
    assert exc.value.details.get("rule") == "codec_id"

    import json

    obj = json.loads(payload)
    obj["evil"] = True
    with pytest.raises(DataContractError) as exc2:
        codec.decode(_entry(json.dumps(obj, separators=(",", ":"), sort_keys=True), category))
    assert exc2.value.details.get("rule") == "extra_keys"


def test_research_codec_rejects_float_in_value() -> None:
    import json

    codec = us_fundamental_snapshot_codec()
    success = ProviderSuccess(value=_snapshot(), meta=_meta(DataCategory.FUNDAMENTALS))
    payload = codec.encode(success)
    obj = json.loads(payload)
    obj["value"]["metrics"]["revenue"] = 1.5  # float — forbidden
    with pytest.raises(DataContractError) as exc:
        codec.decode(
            _entry(
                json.dumps(obj, separators=(",", ":"), sort_keys=True, allow_nan=False),
                DataCategory.FUNDAMENTALS,
            )
        )
    assert exc.value.details.get("rule") in {"no_float", "value_schema"}
