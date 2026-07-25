"""US research CategoryProvider protocol surfaces (Phase 1G G1).

All protocols are ``@runtime_checkable`` and extend ``CategoryProvider``.
Router callbacks must narrow with ``isinstance`` — no getattr/reflection.

Company updates and events search are service-layer compositions over filings,
insider activity, and corporate actions (no dedicated provider protocols).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.instruments.models import Instrument
from domain.us_research.enums import USFilingForm, USStatementFrequency, USStatementView
from domain.us_research.models import (
    USCorporateAction,
    USFiling,
    USFinancialStatements,
    USFundamentalSnapshot,
    USInsiderTransaction,
)


@runtime_checkable
class USFundamentalProvider(CategoryProvider, Protocol):
    async def get_fundamental_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USFundamentalSnapshot]: ...


@runtime_checkable
class USFinancialStatementsProvider(CategoryProvider, Protocol):
    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        frequency: USStatementFrequency,
        limit: int,
        as_of: datetime,
        view: USStatementView = USStatementView.LATEST,
    ) -> ProviderSuccess[USFinancialStatements]: ...


@runtime_checkable
class USFilingsProvider(CategoryProvider, Protocol):
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
    ) -> ProviderSuccess[tuple[USFiling, ...]]: ...


@runtime_checkable
class USInsiderActivityProvider(CategoryProvider, Protocol):
    async def get_insider_activity(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USInsiderTransaction, ...]]: ...


@runtime_checkable
class USCorporateActionsProvider(CategoryProvider, Protocol):
    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USCorporateAction, ...]]: ...


# Explicit inventory for architecture / completeness tests (order frozen).
US_RESEARCH_RUNTIME_PROTOCOLS: tuple[type, ...] = (
    USFundamentalProvider,
    USFinancialStatementsProvider,
    USFilingsProvider,
    USInsiderActivityProvider,
    USCorporateActionsProvider,
)
