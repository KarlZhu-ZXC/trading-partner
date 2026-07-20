"""Phase 1G G2a: SEC EDGAR filings/insider adapter focused contracts."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderUnavailableError,
)
from domain.instruments.models import Instrument
from domain.us_research.enums import USFilingForm, USInsiderAcquiredDisposed
from infrastructure.providers.us.sec_edgar import SECEdgarAdapter

UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
CLOCK = FixedClock(AS_OF)
CIK = "0001045810"
UA = "TradingPartner research@example.com"
SHARD_NAME = "CIK0001045810-submissions-001.json"


def _instrument(symbol: str = "NVDA") -> Instrument:
    return Instrument(
        instrument_id=f"equity:US:{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _tickers_body() -> bytes:
    return json.dumps(
        {
            "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }
    ).encode("utf-8")


def _aligned(
    *,
    forms: list[str],
    filing_dates: list[str],
    acceptance: list[str | None],
    accessions: list[str] | None = None,
    report_dates: list[str | None] | None = None,
    primary_docs: list[str] | None = None,
    items: list[str | None] | None = None,
) -> dict[str, object]:
    n = len(forms)
    return {
        "accessionNumber": accessions or [f"0001045810-24-{i:06d}" for i in range(1, n + 1)],
        "filingDate": filing_dates,
        "reportDate": report_dates if report_dates is not None else [None] * n,
        "acceptanceDateTime": acceptance,
        "form": forms,
        "primaryDocument": primary_docs or [f"doc{i}.htm" for i in range(1, n + 1)],
        "items": items if items is not None else [""] * n,
    }


def _main_submissions(
    recent: dict[str, object],
    *,
    files: list[dict[str, object]] | None = None,
) -> bytes:
    filings: dict[str, object] = {"recent": recent}
    if files is not None:
        filings["files"] = files
    return json.dumps({"cik": CIK, "filings": filings}).encode("utf-8")


def _shard_body(arrays: dict[str, object]) -> bytes:
    # Historical shards: direct aligned arrays (not filings.recent).
    return json.dumps(arrays).encode("utf-8")


_10K_HTML = b"""<!DOCTYPE html><html><body>
<script>alert('x')</script>
<p>ITEM 1. Business</p>
<p>We design GPUs.</p>
<p>ITEM 1A. Risk Factors</p>
<p>Competition is intense.</p>
</body></html>"""

_FORM4_XML = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2026-06-01</periodOfReport>
  <footnotes>
    <footnote id="F1">Sale pursuant to Rule 10b5-1 trading plan.</footnote>
  </footnotes>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerName>SMITH JANE</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-01</value></transactionDate>
      <transactionCoding>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>120.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>50000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

# Transaction date inside May window; filing itself dated June (outside window).
_FORM4_TX_IN_RANGE = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JOHN</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>10.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>1000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


class FixtureTransport:
    """Route SEC allowlisted URLs to fixture bodies."""

    def __init__(
        self,
        *,
        tickers: bytes | None = None,
        submissions: dict[str, bytes] | bytes | None = None,
        documents: dict[str, tuple[int, dict[str, str], bytes]] | None = None,
        submissions_status: int = 200,
        tickers_status: int = 200,
    ) -> None:
        self.tickers = tickers if tickers is not None else _tickers_body()
        if submissions is None:
            self.submissions: dict[str, bytes] = {}
        elif isinstance(submissions, bytes):
            self.submissions = {f"CIK{CIK}.json": submissions}
        else:
            self.submissions = dict(submissions)
        self.documents = documents or {}
        self.submissions_status = submissions_status
        self.tickers_status = tickers_status
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        parts = urlsplit(request.url)
        host = (parts.hostname or "").lower()
        path = parts.path or ""
        if host == "www.sec.gov" and path == "/files/company_tickers.json":
            return HttpResponse(
                status_code=self.tickers_status,
                headers={"content-type": "application/json"},
                body=self.tickers,
            )
        if host == "data.sec.gov" and path.startswith("/submissions/"):
            name = path.rsplit("/", 1)[-1]
            body = self.submissions.get(name, b"{}")
            return HttpResponse(
                status_code=self.submissions_status,
                headers={"content-type": "application/json"},
                body=body,
            )
        if host == "www.sec.gov" and path.startswith("/Archives/edgar/data/"):
            name = path.rsplit("/", 1)[-1]
            for key, (status, headers, body) in self.documents.items():
                if key == name or key in path:
                    return HttpResponse(status_code=status, headers=headers, body=body)
            return HttpResponse(
                status_code=404,
                headers={"content-type": "text/plain"},
                body=b"missing",
            )
        raise AssertionError(f"unexpected url host/path: {host}{path}")


def _adapter(transport: FixtureTransport) -> SECEdgarAdapter:
    return SECEdgarAdapter(
        transport,
        clock=CLOCK,
        enabled=True,
        sec_user_agent=UA,
    )


@pytest.mark.asyncio
async def test_filings_filters_cutoff_amendment_sections_shard_and_insider() -> None:
    """Happy path: filters/cutoff/amendment/sections + historical shard dedupe + Form 4."""
    # Recent includes a duplicate of the historical accession to prove dedupe.
    recent = _aligned(
        forms=["10-K/A", "10-K", "8-K", "4", "10-Q"],
        filing_dates=[
            "2026-03-01",
            "2026-02-20",
            "2026-07-18",
            "2026-06-02",
            "2025-11-01",
        ],
        acceptance=[
            "2026-03-01T16:00:00.000Z",
            "2026-02-20T16:00:00.000Z",
            "2026-07-18T12:00:00.000Z",  # after as_of → invisible
            "2026-06-02T15:00:00.000Z",
            None,
        ],
        accessions=[
            "0001045810-26-000010",
            "0001045810-26-000009",
            "0001045810-26-000020",
            "0001045810-26-000015",
            "0001045810-25-000050",
        ],
        report_dates=[
            "2025-12-31",
            "2025-12-31",
            "2026-07-15",
            "2026-06-01",
            "2025-09-30",
        ],
        primary_docs=["tenka.htm", "tenk.htm", "eightk.htm", "form4.xml", "tenq.htm"],
        items=["", "", "2.02,9.01", "", ""],
    )
    # Older 10-K only in historical shard; also re-list one recent accession for dedupe.
    shard = _aligned(
        forms=["10-K", "10-K"],
        filing_dates=["2019-02-21", "2026-03-01"],
        acceptance=["2019-02-21T16:00:00.000Z", "2026-03-01T16:00:00.000Z"],
        accessions=["0001045810-19-000001", "0001045810-26-000010"],
        report_dates=["2018-12-31", "2025-12-31"],
        primary_docs=["old10k.htm", "tenka.htm"],
        items=[None, ""],
    )
    main = _main_submissions(
        recent,
        files=[
            {
                "name": SHARD_NAME,
                "filingCount": 2,
                "filingFrom": "2019-01-01",
                "filingTo": "2019-12-31",
            }
        ],
    )
    transport = FixtureTransport(
        submissions={
            f"CIK{CIK}.json": main,
            SHARD_NAME: _shard_body(shard),
        },
        documents={
            "tenka.htm": (200, {"content-type": "text/html"}, _10K_HTML),
            "tenk.htm": (500, {"content-type": "text/html"}, b"fail"),
            "form4.xml": (200, {"content-type": "application/xml"}, _FORM4_XML),
        },
    )
    adapter = _adapter(transport)

    assert adapter.supports(Market.US, DataCategory.FILINGS) is True
    assert adapter.supports(Market.US, DataCategory.INSIDER_ACTIVITY) is True
    assert adapter.supports(Market.A_SHARE, DataCategory.FILINGS) is False
    assert adapter.is_configured() is True

    filings = await adapter.get_filings(
        _instrument(),
        forms=(USFilingForm.FORM_10K, USFilingForm.FORM_4, USFilingForm.FORM_10Q),
        start=date(2018, 1, 1),
        end=date(2026, 12, 31),
        include_sections=True,
        limit=10,
        as_of=AS_OF,
    )
    assert filings.meta.vendor is VendorId.SEC_EDGAR
    assert filings.meta.category is DataCategory.FILINGS
    assert filings.meta.role is SourceRole.PRIMARY
    assert filings.meta.as_of == AS_OF
    assert filings.meta.fetched_at == AS_OF
    assert filings.meta.freshness is Freshness.FRESH
    assert filings.meta.cache_disposition is CacheDisposition.MISS
    assert USFilingForm.FORM_8K not in {f.form for f in filings.value}

    accessions = [f.accession for f in filings.value]
    # Historical 2019 10-K discovered; duplicate recent/shard accession appears once.
    assert "0001045810-19-000001" in accessions
    assert accessions.count("0001045810-26-000010") == 1
    # Newest-visible-first with shard row included.
    assert accessions == [
        "0001045810-26-000015",
        "0001045810-26-000010",
        "0001045810-26-000009",
        "0001045810-25-000050",
        "0001045810-19-000001",
    ]
    # Shard path was fetched because start is old and recent alone is incomplete
    # relative to the requested window (form/date filter still needs the shard row).
    shard_paths = [
        urlsplit(r.url).path
        for r in transport.requests
        if (urlsplit(r.url).hostname or "") == "data.sec.gov"
    ]
    assert any(SHARD_NAME in p for p in shard_paths)

    amendment = next(f for f in filings.value if f.accession == "0001045810-26-000010")
    assert amendment.form is USFilingForm.FORM_10K
    assert amendment.is_amendment is True
    assert amendment.url is not None
    assert amendment.url.startswith("https://www.sec.gov/Archives/edgar/data/1045810/")
    assert amendment.sections
    assert amendment.sections[0].algorithm_version == "sec_sections_v1"
    assert "Business" in (amendment.sections[0].section_name or "")
    original = next(f for f in filings.value if f.accession == "0001045810-26-000009")
    assert original.sections == ()
    assert "SECTIONS_UNAVAILABLE" in filings.meta.warnings
    tenq = next(f for f in filings.value if f.form is USFilingForm.FORM_10Q)
    assert tenq.accepted_at is None

    insider = await adapter.get_insider_activity(
        _instrument(),
        start=None,
        end=None,
        limit=50,
        as_of=AS_OF,
    )
    assert insider.meta.vendor is VendorId.SEC_EDGAR
    assert insider.meta.category is DataCategory.INSIDER_ACTIVITY
    assert insider.meta.freshness is Freshness.FRESH
    assert len(insider.value) == 1
    tx = insider.value[0]
    assert tx.owner_name == "SMITH JANE"
    assert tx.relationship == "CEO"
    assert tx.transaction_date == date(2026, 6, 1)
    assert tx.acquired_disposed is USInsiderAcquiredDisposed.DISPOSED
    assert tx.shares == Decimal("1000")
    assert tx.price == Decimal("120.50")
    assert tx.post_transaction_shares == Decimal("50000")
    assert tx.is_direct is True
    assert tx.rule_10b5_1 is True
    assert type(tx.shares) is Decimal

    assert transport.requests
    for req in transport.requests:
        assert req.method == "GET"
        assert req.headers.get("User-Agent") == UA
        host = urlsplit(req.url).hostname
        assert host in {"www.sec.gov", "data.sec.gov"}


@pytest.mark.asyncio
async def test_empty_form4_vs_provider_failure_guards_and_tx_date_filter() -> None:
    # Empty valid Form 4 set → success empty tuple.
    empty_main = _main_submissions(
        _aligned(
            forms=["10-K"],
            filing_dates=["2026-01-01"],
            acceptance=["2026-01-01T12:00:00.000Z"],
            accessions=["0001045810-26-000001"],
            primary_docs=["tenk.htm"],
        )
    )
    transport = FixtureTransport(submissions=empty_main)
    adapter = _adapter(transport)
    empty = await adapter.get_insider_activity(
        _instrument(), start=None, end=None, limit=10, as_of=AS_OF
    )
    assert empty.value == ()
    assert empty.meta.warnings == ()

    # Form 4 filed outside start/end, but transaction_date inside → included.
    form4_main = _main_submissions(
        _aligned(
            forms=["4"],
            filing_dates=["2026-06-02"],  # outside May window
            acceptance=["2026-06-02T15:00:00.000Z"],
            accessions=["0001045810-26-000099"],
            primary_docs=["form4_may_tx.xml"],
        )
    )
    form4_transport = FixtureTransport(
        submissions=form4_main,
        documents={
            "form4_may_tx.xml": (
                200,
                {"content-type": "application/xml"},
                _FORM4_TX_IN_RANGE,
            )
        },
    )
    form4_adapter = _adapter(form4_transport)
    may_window = await form4_adapter.get_insider_activity(
        _instrument(),
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
        limit=10,
        as_of=AS_OF,
    )
    assert len(may_window.value) == 1
    assert may_window.value[0].owner_name == "DOE JOHN"
    assert may_window.value[0].transaction_date == date(2026, 5, 15)
    assert may_window.value[0].acquired_disposed is USInsiderAcquiredDisposed.ACQUIRED
    # Filing-date-only filter would have dropped this Form 4; transaction filter keeps it.
    assert may_window.value[0].filed_at is not None
    assert may_window.value[0].filed_at.date() == date(2026, 6, 2)

    # Submissions HTTP failure → typed provider failure (not empty success).
    fail_transport = FixtureTransport(
        submissions=b"{}",
        submissions_status=503,
    )
    fail_adapter = _adapter(fail_transport)
    with pytest.raises(ProviderUnavailableError):
        await fail_adapter.get_filings(
            _instrument(),
            forms=(),
            start=None,
            end=None,
            include_sections=False,
            limit=5,
            as_of=AS_OF,
        )

    # Unknown symbol → NoMarketData (after tickers load).
    with pytest.raises(NoMarketData):
        await adapter.get_filings(
            _instrument("ZZZZ"),
            forms=(),
            start=None,
            end=None,
            include_sections=False,
            limit=5,
            as_of=AS_OF,
        )

    # Future as_of rejected pre-network.
    with pytest.raises(DataContractError) as exc:
        await adapter.get_filings(
            _instrument(),
            forms=(),
            start=None,
            end=None,
            include_sections=False,
            limit=5,
            as_of=AS_OF + timedelta(days=1),
        )
    assert exc.value.details.get("rule") == "not_future"

    # Non-equity rejected.
    etf = Instrument(
        instrument_id="etf:US:SPY",
        symbol="SPY",
        name="SPY",
        market=Market.US,
        exchange="ARCA",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.ETF,
    )
    with pytest.raises(DataContractError) as exc2:
        await adapter.get_filings(
            etf,
            forms=(),
            start=None,
            end=None,
            include_sections=False,
            limit=5,
            as_of=AS_OF,
        )
    assert exc2.value.details.get("rule") == "asset_type"

    # Not configured without user-agent.
    bare = SECEdgarAdapter(transport, clock=CLOCK, enabled=True, sec_user_agent=None)
    assert bare.is_configured() is False
    with pytest.raises(ProviderNotConfigured):
        await bare.get_filings(
            _instrument(),
            forms=(),
            start=None,
            end=None,
            include_sections=False,
            limit=5,
            as_of=AS_OF,
        )
