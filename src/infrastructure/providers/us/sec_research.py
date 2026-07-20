"""Single registry facade over the two focused SEC research adapters."""

from __future__ import annotations

from datetime import date, datetime

from application.dto.provider_routing import ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpTransport
from domain.common.enums import DataCategory, Market, VendorId
from domain.instruments.models import Instrument
from domain.us_research.enums import USFilingForm, USStatementFrequency
from domain.us_research.models import (
    USFiling,
    USFinancialStatements,
    USFundamentalSnapshot,
    USInsiderTransaction,
)
from infrastructure.providers.us.sec_companyfacts import SECCompanyFactsAdapter
from infrastructure.providers.us.sec_edgar import SECEdgarAdapter


class SECResearchAdapter:
    """Expose one CategoryProvider per VendorId while keeping parsers separated."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        sec_user_agent: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._facts = SECCompanyFactsAdapter(
            transport,
            clock=clock,
            enabled=enabled,
            sec_user_agent=sec_user_agent,
            timeout_seconds=timeout_seconds,
        )
        self._edgar = SECEdgarAdapter(
            transport,
            clock=clock,
            enabled=enabled,
            sec_user_agent=sec_user_agent,
            timeout_seconds=timeout_seconds,
        )

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SEC_EDGAR

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return self._facts.supports(market, category) or self._edgar.supports(market, category)

    def is_configured(self) -> bool:
        return self._facts.is_configured() and self._edgar.is_configured()

    async def get_fundamental_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USFundamentalSnapshot]:
        return await self._facts.get_fundamental_snapshot(instrument, as_of)

    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        frequency: USStatementFrequency,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[USFinancialStatements]:
        return await self._facts.get_financial_statements(
            instrument, frequency=frequency, limit=limit, as_of=as_of
        )

    async def get_filings(
        self,
        instrument: Instrument,
        *,
        forms: tuple[USFilingForm, ...],
        start: date | None,
        end: date | None,
        include_sections: bool,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USFiling, ...]]:
        return await self._edgar.get_filings(
            instrument,
            forms=forms,
            start=start,
            end=end,
            include_sections=include_sections,
            limit=limit,
            as_of=as_of,
        )

    async def get_insider_activity(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USInsiderTransaction, ...]]:
        return await self._edgar.get_insider_activity(
            instrument, start=start, end=end, limit=limit, as_of=as_of
        )
