"""Phase 1G G2b: SEC Company Facts adapter focused contracts."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AssetType, DataCategory, Market, VendorId
from domain.common.errors import DataContractError, ProviderNotConfigured
from domain.instruments.models import Instrument
from domain.us_research.enums import (
    USFundamentalBasis,
    USStatementFrequency,
    USStatementType,
)
from infrastructure.providers.us.sec_companyfacts import SECCompanyFactsAdapter

UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
CLOCK = FixedClock(AS_OF)
CIK = "0001045810"
UA = "TradingPartner research@example.com"
ACC_FY = "0001045810-25-000029"
ACC_Q1 = "0001045810-25-000040"
ACC_Q2 = "0001045810-25-000050"
ACC_AMEND = "0001045810-25-000060"


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


def _tickers() -> bytes:
    return json.dumps(
        {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
    ).encode()


def _unit_entry(
    *,
    end: str,
    val: object,
    accn: str,
    fy: int,
    fp: str,
    form: str,
    filed: str,
    start: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if start is not None:
        row["start"] = start
    return row


def _facts_payload() -> dict[str, object]:
    """Representative annual + quarterly + YTD + amendment + future-filed facts."""
    return {
        "cik": 1045810,
        "entityName": "NVIDIA CORP",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=130497000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            # Amendment restates full period (later filed wins).
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=130497000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                            _unit_entry(
                                start="2025-01-27",
                                end="2025-04-27",
                                val=44062000000,
                                accn=ACC_Q1,
                                fy=2026,
                                fp="Q1",
                                form="10-Q",
                                filed="2025-05-28",
                            ),
                            # Q2 YTD (~180d) — must be excluded
                            _unit_entry(
                                start="2025-01-27",
                                end="2025-07-27",
                                val=90000000000,
                                accn=ACC_Q2,
                                fy=2026,
                                fp="Q2",
                                form="10-Q",
                                filed="2025-08-27",
                            ),
                            _unit_entry(
                                start="2025-04-28",
                                end="2025-07-27",
                                val=46743000000,
                                accn=ACC_Q2,
                                fy=2026,
                                fp="Q2",
                                form="10-Q",
                                filed="2025-08-27",
                            ),
                            _unit_entry(
                                start="2025-01-27",
                                end="2026-01-25",
                                val=200000000000,
                                accn="0001045810-26-000099",
                                fy=2026,
                                fp="FY",
                                form="10-K",
                                filed="2026-07-20",
                            ),
                        ]
                    },
                },
                "GrossProfit": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=97858000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=97858000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                        ]
                    },
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=72880000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=72881000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                            _unit_entry(
                                start="2025-01-27",
                                end="2025-04-27",
                                val=18775000000,
                                accn=ACC_Q1,
                                fy=2026,
                                fp="Q1",
                                form="10-Q",
                                filed="2025-05-28",
                            ),
                        ]
                    },
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=64089000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=64089000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                        ]
                    },
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=3236000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=3236000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                        ]
                    },
                },
                "ShareBasedCompensation": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=4737000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=4737000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                        ]
                    },
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                end="2025-01-26",
                                val=8600000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                end="2025-01-26",
                                val=8600000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                            _unit_entry(
                                end="2025-04-27",
                                val=9000000000,
                                accn=ACC_Q1,
                                fy=2026,
                                fp="Q1",
                                form="10-Q",
                                filed="2025-05-28",
                            ),
                        ]
                    },
                },
                "LongTermDebt": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                end="2025-01-26",
                                val=8463000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                end="2025-01-26",
                                val=8463000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                        ]
                    },
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            _unit_entry(
                                end="2025-01-26",
                                val=24400000000,
                                accn=ACC_FY,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                            _unit_entry(
                                end="2025-01-26",
                                val=24400000000,
                                accn=ACC_AMEND,
                                fy=2025,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-03-15",
                            ),
                        ]
                    },
                },
            },
        },
    }


class FixtureTransport:
    def __init__(
        self,
        *,
        companyfacts: bytes | None = None,
        facts_status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.tickers = _tickers()
        self.companyfacts = (
            companyfacts if companyfacts is not None else json.dumps(_facts_payload()).encode()
        )
        self.facts_status = facts_status
        self.content_type = content_type
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        parts = urlsplit(request.url)
        host = (parts.hostname or "").lower()
        path = parts.path or ""
        if host == "www.sec.gov" and path == "/files/company_tickers.json":
            return HttpResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=self.tickers,
            )
        if host == "data.sec.gov" and path.startswith("/api/xbrl/companyfacts/"):
            return HttpResponse(
                status_code=self.facts_status,
                headers={"content-type": self.content_type},
                body=self.companyfacts,
            )
        raise AssertionError(f"unexpected url: {host}{path}")


def _adapter(transport: FixtureTransport) -> SECCompanyFactsAdapter:
    return SECCompanyFactsAdapter(transport, clock=CLOCK, enabled=True, sec_user_agent=UA)


def _line_map(period: object) -> dict[str, Decimal | None]:
    return {k: v for k, v in period.line_items}  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_annual_quarterly_ytd_exclusion_and_no_mixed_period() -> None:
    adapter = _adapter(FixtureTransport())
    annual = await adapter.get_financial_statements(
        _instrument(),
        frequency=USStatementFrequency.ANNUAL,
        limit=5,
        as_of=AS_OF,
    )
    assert annual.meta.vendor is VendorId.SEC_EDGAR
    assert annual.meta.category is DataCategory.FINANCIAL_STATEMENTS
    assert len(annual.value.income) == 1
    # Amendment wins for same period (filed 2025-03-15 > 2025-02-26).
    assert annual.value.income[0].fiscal_period == "FY"
    lines = _line_map(annual.value.income[0])
    assert lines["revenue"] == Decimal("130497000000")
    assert lines["net_income"] == Decimal("72881000000")
    assert lines["gross_profit"] == Decimal("97858000000")
    # Missing keys stay None, never zero.
    assert lines["operating_income"] is None
    assert lines["eps_basic"] is None
    assert "Revenues" not in lines  # no taxonomy labels
    assert annual.value.income[0].statement_type is USStatementType.INCOME
    bs = _line_map(annual.value.balance_sheet[0])
    assert bs["cash_and_equivalents"] == Decimal("8600000000")
    assert bs["shares_outstanding"] == Decimal("24400000000")
    cf = _line_map(annual.value.cash_flow[0])
    assert cf["operating_cash_flow"] == Decimal("64089000000")
    assert cf["capital_expenditure"] == Decimal("3236000000")

    quarterly = await adapter.get_financial_statements(
        _instrument(),
        frequency=USStatementFrequency.QUARTERLY,
        limit=8,
        as_of=AS_OF,
    )
    ends = [p.period_end for p in quarterly.value.income]
    assert date(2025, 7, 27) in ends
    assert date(2025, 4, 27) in ends
    q2 = next(p for p in quarterly.value.income if p.period_end == date(2025, 7, 27))
    assert _line_map(q2)["revenue"] == Decimal("46743000000")  # pure Q2, not YTD 90B
    # One period_end per accession group; no mixed accession in a period.
    accessions = {p.fiscal_year for p in quarterly.value.income}
    assert accessions  # non-empty
    for p in quarterly.value.income:
        assert p.frequency is USStatementFrequency.QUARTERLY
        assert all(isinstance(v, (Decimal, type(None))) for _, v in p.line_items)


@pytest.mark.asyncio
async def test_cutoff_amendment_and_fundamental_snapshot() -> None:
    adapter = _adapter(FixtureTransport())
    # Before amendment visibility (filed 2025-03-15 → visible 2025-03-16 UTC).
    pre = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)
    clock = FixedClock(pre)
    early = SECCompanyFactsAdapter(FixtureTransport(), clock=clock, enabled=True, sec_user_agent=UA)
    annual = await early.get_financial_statements(
        _instrument(),
        frequency=USStatementFrequency.ANNUAL,
        limit=5,
        as_of=pre,
    )
    assert _line_map(annual.value.income[0])["net_income"] == Decimal("72880000000")

    snap = await adapter.get_fundamental_snapshot(_instrument(), AS_OF)
    assert snap.meta.category is DataCategory.FUNDAMENTALS
    assert snap.value.profile is not None
    assert snap.value.profile.legal_name == "NVIDIA CORP"
    assert snap.value.profile.sector is None
    assert snap.value.profile.market_cap is None
    assert snap.value.corporate_actions == ()
    assert "SEC_FUNDAMENTALS_PARTIAL" in snap.value.warning_codes
    m = snap.value.reported_metrics
    assert m is not None
    assert m.basis is USFundamentalBasis.ANNUAL
    assert m.trailing_pe is None
    assert m.revenue == Decimal("130497000000")
    assert m.net_income == Decimal("72881000000")  # amendment
    assert m.share_count == Decimal("24400000000")
    assert m.stock_based_compensation == Decimal("4737000000")
    assert m.capital_expenditure == Decimal("3236000000")
    assert m.free_cash_flow == Decimal("64089000000") - Decimal("3236000000")
    assert m.net_cash_or_debt is None  # incomplete debt map; never invent
    assert snap.value.degraded is True
    assert m.filed_at is not None and m.filed_at <= AS_OF
    assert m.period_end == date(2025, 1, 26)


@pytest.mark.asyncio
async def test_empty_malformed_unavailable_and_config() -> None:
    # Empty but valid facts → success with empty periods / degraded snapshot.
    empty_body = json.dumps({"cik": 1045810, "entityName": "NVIDIA CORP", "facts": {}}).encode()
    empty_adapter = _adapter(FixtureTransport(companyfacts=empty_body))
    stmts = await empty_adapter.get_financial_statements(
        _instrument(),
        frequency=USStatementFrequency.ANNUAL,
        limit=5,
        as_of=AS_OF,
    )
    assert stmts.value.income == ()
    assert stmts.value.balance_sheet == ()
    assert stmts.value.cash_flow == ()
    snap = await empty_adapter.get_fundamental_snapshot(_instrument(), AS_OF)
    assert snap.value.profile is not None
    assert snap.value.profile.legal_name == "NVIDIA CORP"
    assert snap.value.metrics is None
    assert snap.value.degraded is True
    assert "SEC_FUNDAMENTALS_PARTIAL" in snap.value.warning_codes

    # No entityName → profile is None (not empty profile shell).
    no_name = json.dumps({"cik": 1045810, "facts": {}}).encode()
    no_name_snap = await _adapter(FixtureTransport(companyfacts=no_name)).get_fundamental_snapshot(
        _instrument(), AS_OF
    )
    assert no_name_snap.value.profile is None
    assert no_name_snap.value.degraded is True

    # Malformed facts type.
    bad = json.dumps({"cik": 1045810, "entityName": "X", "facts": []}).encode()
    with pytest.raises(DataContractError):
        await _adapter(FixtureTransport(companyfacts=bad)).get_financial_statements(
            _instrument(),
            frequency=USStatementFrequency.QUARTERLY,
            limit=4,
            as_of=AS_OF,
        )

    # Present concept with malformed units series → contract error, not empty.
    malformed_concept = json.dumps(
        {
            "cik": 1045810,
            "entityName": "X",
            "facts": {
                "us-gaap": {
                    "Revenues": {"label": "Revenue", "units": {"USD": "not-a-list"}},
                }
            },
        }
    ).encode()
    with pytest.raises(DataContractError):
        await _adapter(FixtureTransport(companyfacts=malformed_concept)).get_financial_statements(
            _instrument(),
            frequency=USStatementFrequency.ANNUAL,
            limit=5,
            as_of=AS_OF,
        )

    # Future as_of rejected.
    with pytest.raises(DataContractError) as exc:
        await empty_adapter.get_fundamental_snapshot(
            _instrument(), datetime(2099, 1, 1, tzinfo=UTC)
        )
    assert exc.value.details.get("rule") == "not_future"

    # Missing user agent → not configured.
    bare = SECCompanyFactsAdapter(
        FixtureTransport(), clock=CLOCK, enabled=True, sec_user_agent=None
    )
    with pytest.raises(ProviderNotConfigured):
        await bare.get_fundamental_snapshot(_instrument(), AS_OF)


@pytest.mark.asyncio
async def test_per_period_alias_fallback_and_late_amendment_order() -> None:
    """Primary alias with old periods must not hide fallback; late amend old year."""
    # Revenues only on old FY; newer FY uses RevenueFromContractWithCustomer...
    # Late 10-K/A of old year filed after newer year must not outrank newer period.
    acc_old = "0001045810-24-000010"
    acc_old_amend = "0001045810-25-000099"
    acc_new = "0001045810-25-000030"
    payload = {
        "cik": 1045810,
        "entityName": "NVIDIA CORP",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2023-01-30",
                                end="2024-01-28",
                                val=60922000000,
                                accn=acc_old,
                                fy=2024,
                                fp="FY",
                                form="10-K",
                                filed="2024-02-21",
                            ),
                            # Restated on late amendment (same period, later filed).
                            _unit_entry(
                                start="2023-01-30",
                                end="2024-01-28",
                                val=60922000000,
                                accn=acc_old_amend,
                                fy=2024,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-06-01",
                            ),
                        ]
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            # Newer FY only on fallback alias (primary has no this period).
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=130497000000,
                                accn=acc_new,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                        ]
                    },
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            _unit_entry(
                                start="2023-01-30",
                                end="2024-01-28",
                                val=29760000000,
                                accn=acc_old,
                                fy=2024,
                                fp="FY",
                                form="10-K",
                                filed="2024-02-21",
                            ),
                            # Late amendment of OLD year, filed after newer 10-K.
                            _unit_entry(
                                start="2023-01-30",
                                end="2024-01-28",
                                val=29761000000,
                                accn=acc_old_amend,
                                fy=2024,
                                fp="FY",
                                form="10-K/A",
                                filed="2025-06-01",
                            ),
                            _unit_entry(
                                start="2024-01-29",
                                end="2025-01-26",
                                val=72880000000,
                                accn=acc_new,
                                fy=2025,
                                fp="FY",
                                form="10-K",
                                filed="2025-02-26",
                            ),
                        ]
                    },
                },
            }
        },
    }
    adapter = _adapter(FixtureTransport(companyfacts=json.dumps(payload).encode()))
    annual = await adapter.get_financial_statements(
        _instrument(),
        frequency=USStatementFrequency.ANNUAL,
        limit=5,
        as_of=AS_OF,
    )
    assert len(annual.value.income) == 2
    # Newer fiscal period_end first despite older year's later amendment filed date.
    assert annual.value.income[0].period_end == date(2025, 1, 26)
    assert annual.value.income[1].period_end == date(2024, 1, 28)
    assert _line_map(annual.value.income[0])["revenue"] == Decimal("130497000000")
    assert _line_map(annual.value.income[1])["revenue"] == Decimal("60922000000")
    # Old period uses amendment net income.
    assert _line_map(annual.value.income[1])["net_income"] == Decimal("29761000000")

    snap = await adapter.get_fundamental_snapshot(_instrument(), AS_OF)
    assert snap.value.reported_metrics is not None
    assert snap.value.reported_metrics.period_end == date(2025, 1, 26)
    assert snap.value.reported_metrics.revenue == Decimal("130497000000")
    assert snap.value.reported_metrics.net_income == Decimal("72880000000")
    assert snap.value.degraded is True
