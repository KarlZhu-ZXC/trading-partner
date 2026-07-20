"""Lean application-service contracts for Phase 1G research routing."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.services.us_filing_service import USFilingService
from application.services.us_fundamental_service import USFundamentalService
from conftest import FixedClock
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.us_research.enums import (
    USCorporateActionType,
    USFilingForm,
    USInsiderAcquiredDisposed,
    USStatementFrequency,
)
from domain.us_research.models import (
    USCorporateAction,
    USFiling,
    USFinancialStatements,
    USFundamentalSnapshot,
    USInsiderTransaction,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 16, 5, tzinfo=NY)


class Codec:
    codec_id = "test.v1"


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _meta(category: DataCategory) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.SEC_EDGAR,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        session=TradingSession.CLOSED,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )


class Provider:
    vendor_id = VendorId.SEC_EDGAR
    provider_name = VendorId.SEC_EDGAR.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US

    def is_configured(self) -> bool:
        return True

    async def get_fundamental_snapshot(self, instrument: Instrument, as_of: datetime):
        value = USFundamentalSnapshot(
            instrument.instrument_id, as_of, None, None, (), True, ("PARTIAL",)
        )
        return ProviderSuccess(value, _meta(DataCategory.FUNDAMENTALS))

    async def get_financial_statements(self, instrument: Instrument, *, frequency, limit, as_of):
        value = USFinancialStatements(instrument.instrument_id, as_of, frequency, (), (), ())
        return ProviderSuccess(value, _meta(DataCategory.FINANCIAL_STATEMENTS))

    async def get_corporate_actions(self, instrument: Instrument, *, start, end, as_of):
        value = (
            USCorporateAction(
                instrument.instrument_id,
                USCorporateActionType.DIVIDEND,
                date(2026, 7, 16),
                None,
                None,
                Decimal("0.01"),
                None,
                "USD",
                None,
                None,
            ),
        )
        return ProviderSuccess(value, _meta(DataCategory.CORPORATE_ACTIONS))

    async def get_filings(
        self, instrument: Instrument, *, forms, start, end, include_sections, limit, as_of
    ):
        value = (
            USFiling(
                instrument.instrument_id,
                "0001045810-26-000001",
                USFilingForm.FORM_10Q,
                False,
                date(2026, 7, 16),
                datetime(2026, 7, 16, 12, tzinfo=NY),
                date(2026, 6, 30),
                "report.htm",
                None,
                (),
                (),
            ),
        )
        return ProviderSuccess(value, _meta(DataCategory.FILINGS))

    async def get_insider_activity(self, instrument: Instrument, *, start, end, limit, as_of):
        value = (
            USInsiderTransaction(
                instrument.instrument_id,
                "Jane Doe",
                "Director",
                date(2026, 7, 15),
                datetime(2026, 7, 16, 12, tzinfo=NY),
                datetime(2026, 7, 16, 12, tzinfo=NY),
                "S",
                USInsiderAcquiredDisposed.DISPOSED,
                Decimal("100"),
                Decimal("150"),
                None,
                True,
                None,
            ),
        )
        return ProviderSuccess(value, _meta(DataCategory.INSIDER_ACTIVITY))


class Router:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        success = await kwargs["call"](self.provider)
        kwargs["result_validator"](success)
        return success.value


@pytest.mark.asyncio
async def test_fundamental_service_routes_three_capabilities() -> None:
    router = Router(Provider())
    service = USFundamentalService(
        router,
        FixedClock(NOW),
        Codec(),
        Codec(),
        Codec(),  # type: ignore[arg-type]
    )
    instrument = _instrument()

    snapshot = await service.get_snapshot(instrument, NOW)
    official = await service.get_official_snapshot(instrument, NOW)
    statements = await service.get_statements(
        instrument,
        frequency=USStatementFrequency.QUARTERLY,
        limit=8,
        as_of=NOW,
    )
    actions = await service.get_corporate_actions(
        instrument, start=date(2026, 7, 1), end=None, as_of=NOW
    )

    assert isinstance(snapshot, USFundamentalSnapshot)
    assert isinstance(official, USFundamentalSnapshot)
    assert isinstance(statements, USFinancialStatements)
    assert actions[0].amount == Decimal("0.01")
    assert [call["category"] for call in router.calls] == [
        DataCategory.FUNDAMENTALS,
        DataCategory.FUNDAMENTALS,
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.CORPORATE_ACTIONS,
    ]


@pytest.mark.asyncio
async def test_filing_service_routes_filings_and_insider_visibility() -> None:
    router = Router(Provider())
    service = USFilingService(
        router,
        FixedClock(NOW),
        Codec(),
        Codec(),  # type: ignore[arg-type]
    )
    instrument = _instrument()

    filings = await service.get_filings(
        instrument,
        forms=(USFilingForm.FORM_10Q,),
        start=None,
        end=None,
        include_sections=False,
        limit=20,
        as_of=NOW,
    )
    insiders = await service.get_insider_activity(
        instrument, start=None, end=None, limit=50, as_of=NOW
    )

    assert filings[0].form is USFilingForm.FORM_10Q
    assert insiders[0].rule_10b5_1 is None
    assert [call["category"] for call in router.calls] == [
        DataCategory.FILINGS,
        DataCategory.INSIDER_ACTIVITY,
    ]


@pytest.mark.asyncio
async def test_services_reject_invalid_inputs_before_router() -> None:
    router = Router(Provider())
    fundamental = USFundamentalService(
        router,
        FixedClock(NOW),
        Codec(),
        Codec(),
        Codec(),  # type: ignore[arg-type]
    )
    filing = USFilingService(
        router,
        FixedClock(NOW),
        Codec(),
        Codec(),  # type: ignore[arg-type]
    )

    with pytest.raises(DataContractError):
        await fundamental.get_statements(
            _instrument(),
            frequency=USStatementFrequency.QUARTERLY,
            limit=9,
            as_of=NOW,
        )
    with pytest.raises(DataContractError):
        await filing.get_filings(
            _instrument(),
            forms=(),
            start=date(2026, 7, 2),
            end=date(2026, 7, 1),
            include_sections=False,
            limit=20,
            as_of=NOW,
        )
    assert router.calls == []
