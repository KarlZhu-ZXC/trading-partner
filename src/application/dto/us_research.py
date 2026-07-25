"""Closed US research Pydantic DTOs (Phase 1G G1).

Input models implement design §6 MCP schema validation. Output DTOs mirror
frozen domain models with ``extra=forbid``, Decimal fixed-point wire strings,
and JSON-native datetimes.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.financial_quality import (
    FinancialQualityMetricDTO,
    derive_financial_quality_metrics,
)
from application.dto.market import DecimalWire
from application.dto.us_context import USNewsArticleDTO
from domain.common.enums import AssetType, Market
from domain.common.errors import TradingPartnerError
from domain.common.values import parse_instrument_id
from domain.us_research.enums import (
    USCorporateActionType,
    USExternalEventType,
    USFilingForm,
    USFundamentalBasis,
    USInsiderAcquiredDisposed,
    USStatementFrequency,
    USStatementType,
    USStatementView,
)
from domain.us_research.models import (
    USCompanyProfile,
    USCompanyUpdate,
    USCorporateAction,
    USExternalEvent,
    USFiling,
    USFilingSection,
    USFinancialStatements,
    USFundamentalMetrics,
    USFundamentalSnapshot,
    USInsiderTransaction,
    USStatementPeriod,
)

_DATE_WIRE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EQUITY_ONLY = frozenset({AssetType.EQUITY})


def _require_exact_date_wire(value: object) -> object:
    if value is None:
        return value
    if isinstance(value, datetime):
        raise ValueError("must be an exact date, not datetime")
    if isinstance(value, str) and not _DATE_WIRE_RE.fullmatch(value):
        raise ValueError("must use YYYY-MM-DD date format")
    if not isinstance(value, (date, str)):
        raise ValueError("must be an exact date")
    return value


class _FrozenForbid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_us_equity_instrument_id(value: str, *, field_name: str = "instrument_id") -> str:
    try:
        asset_type, market, _symbol = parse_instrument_id(value)
    except TradingPartnerError:
        raise ValueError("invalid instrument_id syntax") from None
    if market is not Market.US:
        raise ValueError(f"{field_name} must use Market.US")
    if asset_type not in _EQUITY_ONLY:
        raise ValueError(f"{field_name} asset type must be equity for US research")
    return value


def _require_aware_as_of(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("as_of must be timezone-aware")
    return value


def _require_aware_datetime(value: datetime | None, *, field: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field} must be timezone-aware")
    return value


# ---------------------------------------------------------------------------
# §6 MCP input models
# ---------------------------------------------------------------------------


class FundamentalGetSnapshotInput(_FrozenForbid):
    instrument_id: str
    as_of: datetime | None = None

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)


class FundamentalGetStatementsInput(_FrozenForbid):
    instrument_id: str
    frequency: USStatementFrequency = USStatementFrequency.QUARTERLY
    as_of: datetime | None = None
    limit: int = Field(default=8, ge=1, le=8)
    view: USStatementView = USStatementView.LATEST

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)


class USGetFilingsInput(_FrozenForbid):
    instrument_id: str
    forms: tuple[USFilingForm, ...] = ()
    start: date | None = None
    end: date | None = None
    as_of: datetime | None = None
    include_sections: bool = False
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @model_validator(mode="after")
    def _inclusive_range(self) -> Self:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class USGetInsiderActivityInput(_FrozenForbid):
    instrument_id: str
    start: date | None = None
    end: date | None = None
    as_of: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @model_validator(mode="after")
    def _inclusive_range(self) -> Self:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class ResearchGetCompanyUpdatesInput(_FrozenForbid):
    instrument_id: str
    since: datetime | None = None
    as_of: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)

    @field_validator("since")
    @classmethod
    def _aware_since(cls, value: datetime | None) -> datetime | None:
        return _require_aware_datetime(value, field="since")

    @model_validator(mode="after")
    def _since_not_after_as_of(self) -> Self:
        if self.since is not None and self.as_of is not None and self.since > self.as_of:
            raise ValueError("since must be <= as_of")
        return self


class EventsSearchInput(_FrozenForbid):
    instrument_id: str | None = None
    event_types: tuple[USExternalEventType, ...] = ()
    start: date | None = None
    end: date | None = None
    as_of: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        return _require_aware_as_of(value)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _exact_dates(cls, value: object) -> object:
        return _require_exact_date_wire(value)

    @model_validator(mode="after")
    def _inclusive_range(self) -> Self:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must be >= start")
        return self


# ---------------------------------------------------------------------------
# Domain-mirroring output DTOs
# ---------------------------------------------------------------------------


class USCompanyProfileDTO(_FrozenForbid):
    instrument_id: str
    legal_name: str | None
    description: str | None
    sector: str | None
    industry: str | None
    country: str | None
    website: str | None
    employees: int | None
    market_cap: DecimalWire | None

    @classmethod
    def from_domain(cls, profile: USCompanyProfile) -> USCompanyProfileDTO:
        return cls.model_validate(profile, from_attributes=True)


class USFundamentalMetricsDTO(_FrozenForbid):
    trailing_pe: DecimalWire | None
    forward_pe: DecimalWire | None
    peg_ratio: DecimalWire | None
    price_to_book: DecimalWire | None
    price_to_sales: DecimalWire | None
    enterprise_to_ebitda: DecimalWire | None
    dividend_yield: DecimalWire | None
    beta: DecimalWire | None
    eps_ttm: DecimalWire | None
    eps_forward: DecimalWire | None
    book_value_per_share: DecimalWire | None
    revenue_per_share: DecimalWire | None
    revenue: DecimalWire | None
    gross_profit: DecimalWire | None
    ebitda: DecimalWire | None
    net_income: DecimalWire | None
    profit_margin: DecimalWire | None
    operating_margin: DecimalWire | None
    roe: DecimalWire | None
    roa: DecimalWire | None
    debt_to_equity: DecimalWire | None
    current_ratio: DecimalWire | None
    revenue_growth: DecimalWire | None
    eps_growth: DecimalWire | None
    estimate_revision: DecimalWire | None
    share_count: DecimalWire | None
    stock_based_compensation: DecimalWire | None
    capital_expenditure: DecimalWire | None
    free_cash_flow: DecimalWire | None
    net_cash_or_debt: DecimalWire | None
    period_end: date | None
    filed_at: datetime | None
    basis: USFundamentalBasis

    @classmethod
    def from_domain(cls, metrics: USFundamentalMetrics) -> USFundamentalMetricsDTO:
        return cls.model_validate(metrics, from_attributes=True)


class USCorporateActionDTO(_FrozenForbid):
    instrument_id: str
    action_type: USCorporateActionType
    effective_date: date | None
    declared_date: date | None
    paid_date: date | None
    amount: DecimalWire | None
    ratio: DecimalWire | None
    currency: str | None
    shares: DecimalWire | None
    description: str | None

    @classmethod
    def from_domain(cls, action: USCorporateAction) -> USCorporateActionDTO:
        return cls.model_validate(action, from_attributes=True)


class USFundamentalSnapshotDTO(_FrozenForbid):
    instrument_id: str
    as_of: datetime
    profile: USCompanyProfileDTO | None
    metrics: USFundamentalMetricsDTO | None
    corporate_actions: tuple[USCorporateActionDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]
    reported_metrics: USFundamentalMetricsDTO | None = None

    @classmethod
    def from_domain(cls, snapshot: USFundamentalSnapshot) -> USFundamentalSnapshotDTO:
        return cls(
            instrument_id=snapshot.instrument_id,
            as_of=snapshot.as_of,
            profile=(
                USCompanyProfileDTO.from_domain(snapshot.profile)
                if snapshot.profile is not None
                else None
            ),
            metrics=(
                USFundamentalMetricsDTO.from_domain(snapshot.metrics)
                if snapshot.metrics is not None
                else None
            ),
            corporate_actions=tuple(
                USCorporateActionDTO.from_domain(a) for a in snapshot.corporate_actions
            ),
            degraded=snapshot.degraded,
            warning_codes=snapshot.warning_codes,
            reported_metrics=(
                USFundamentalMetricsDTO.from_domain(snapshot.reported_metrics)
                if snapshot.reported_metrics is not None
                else None
            ),
        )


class USStatementPeriodDTO(_FrozenForbid):
    statement_type: USStatementType
    frequency: USStatementFrequency
    fiscal_year: int
    fiscal_period: str | None
    period_end: date
    filed_at: datetime | None
    currency: str | None
    line_items: tuple[tuple[str, DecimalWire | None], ...]
    period_start: date | None
    accession: str | None
    filing_form: str | None
    is_amendment: bool

    @classmethod
    def from_domain(cls, period: USStatementPeriod) -> USStatementPeriodDTO:
        # Preserve ordered unique keys; DecimalWire serializes values as strings.
        items: list[tuple[str, Decimal | None]] = list(period.line_items)
        return cls(
            statement_type=period.statement_type,
            frequency=period.frequency,
            fiscal_year=period.fiscal_year,
            fiscal_period=period.fiscal_period,
            period_end=period.period_end,
            filed_at=period.filed_at,
            currency=period.currency,
            line_items=tuple(items),
            period_start=period.period_start,
            accession=period.accession,
            filing_form=period.filing_form,
            is_amendment=period.is_amendment,
        )


class USFinancialStatementsDTO(_FrozenForbid):
    instrument_id: str
    as_of: datetime
    frequency: USStatementFrequency
    income: tuple[USStatementPeriodDTO, ...]
    balance_sheet: tuple[USStatementPeriodDTO, ...]
    cash_flow: tuple[USStatementPeriodDTO, ...]
    view: USStatementView
    quality_metrics: tuple[FinancialQualityMetricDTO, ...]

    @classmethod
    def from_domain(cls, statements: USFinancialStatements) -> USFinancialStatementsDTO:
        quality: list[FinancialQualityMetricDTO] = []
        by_period: dict[date, dict[str, Decimal | None]] = {}
        currencies: dict[date, str | None] = {}
        # A vintage view may contain multiple filings for one period end.  A
        # period-only quality metric would silently mix those filing versions,
        # so derived quality is intentionally emitted only for the deduplicated
        # latest view.  The vintage statement lines remain available verbatim.
        if statements.view is USStatementView.LATEST:
            for period in (*statements.income, *statements.balance_sheet, *statements.cash_flow):
                bucket = by_period.setdefault(period.period_end, {})
                for key, value in period.line_items:
                    if value is not None:
                        bucket[key] = value
                if period.currency is not None:
                    currencies[period.period_end] = period.currency
            for period_end in sorted(by_period, reverse=True):
                quality.extend(
                    derive_financial_quality_metrics(
                        period_end=period_end,
                        line_items=by_period[period_end],
                        currency=currencies.get(period_end),
                    )
                )
        return cls(
            instrument_id=statements.instrument_id,
            as_of=statements.as_of,
            frequency=statements.frequency,
            income=tuple(USStatementPeriodDTO.from_domain(p) for p in statements.income),
            balance_sheet=tuple(
                USStatementPeriodDTO.from_domain(p) for p in statements.balance_sheet
            ),
            cash_flow=tuple(USStatementPeriodDTO.from_domain(p) for p in statements.cash_flow),
            view=statements.view,
            quality_metrics=tuple(quality),
        )


class USFilingSectionDTO(_FrozenForbid):
    section_name: str
    document_url: str | None
    text: str | None
    algorithm_version: str

    @classmethod
    def from_domain(cls, section: USFilingSection) -> USFilingSectionDTO:
        return cls.model_validate(section, from_attributes=True)


class USFilingDTO(_FrozenForbid):
    instrument_id: str
    accession: str
    form: USFilingForm
    is_amendment: bool
    filed_date: date
    accepted_at: datetime | None
    period_of_report: date | None
    primary_document: str | None
    url: str | None
    items: tuple[str, ...]
    sections: tuple[USFilingSectionDTO, ...]

    @classmethod
    def from_domain(cls, filing: USFiling) -> USFilingDTO:
        return cls(
            instrument_id=filing.instrument_id,
            accession=filing.accession,
            form=filing.form,
            is_amendment=filing.is_amendment,
            filed_date=filing.filed_date,
            accepted_at=filing.accepted_at,
            period_of_report=filing.period_of_report,
            primary_document=filing.primary_document,
            url=filing.url,
            items=filing.items,
            sections=tuple(USFilingSectionDTO.from_domain(s) for s in filing.sections),
        )


class USInsiderTransactionDTO(_FrozenForbid):
    instrument_id: str
    owner_name: str
    relationship: str | None
    transaction_date: date | None
    filed_at: datetime | None
    accepted_at: datetime | None
    transaction_code: str | None
    acquired_disposed: USInsiderAcquiredDisposed | None
    shares: DecimalWire | None
    price: DecimalWire | None
    post_transaction_shares: DecimalWire | None
    is_direct: bool | None
    rule_10b5_1: bool | None

    @classmethod
    def from_domain(cls, transaction: USInsiderTransaction) -> USInsiderTransactionDTO:
        return cls.model_validate(transaction, from_attributes=True)


class USExternalEventDTO(_FrozenForbid):
    instrument_id: str
    event_type: USExternalEventType
    event_time: datetime
    visible_time: datetime
    title: str
    summary: str | None
    source_reference: str | None
    dedupe_key: str
    filing: USFilingDTO | None
    insider_transaction: USInsiderTransactionDTO | None
    corporate_action: USCorporateActionDTO | None
    news_article: USNewsArticleDTO | None

    @classmethod
    def from_domain(cls, event: USExternalEvent) -> USExternalEventDTO:
        return cls(
            instrument_id=event.instrument_id,
            event_type=event.event_type,
            event_time=event.event_time,
            visible_time=event.visible_time,
            title=event.title,
            summary=event.summary,
            source_reference=event.source_reference,
            dedupe_key=event.dedupe_key,
            filing=(USFilingDTO.from_domain(event.filing) if event.filing is not None else None),
            insider_transaction=(
                USInsiderTransactionDTO.from_domain(event.insider_transaction)
                if event.insider_transaction is not None
                else None
            ),
            corporate_action=(
                USCorporateActionDTO.from_domain(event.corporate_action)
                if event.corporate_action is not None
                else None
            ),
            news_article=(
                USNewsArticleDTO.model_validate(event.news_article)
                if event.news_article is not None
                else None
            ),
        )


class USCompanyUpdateDTO(_FrozenForbid):
    instrument_id: str
    as_of: datetime
    events: tuple[USExternalEventDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, update: USCompanyUpdate) -> USCompanyUpdateDTO:
        return cls(
            instrument_id=update.instrument_id,
            as_of=update.as_of,
            events=tuple(USExternalEventDTO.from_domain(e) for e in update.events),
            degraded=update.degraded,
            warning_codes=update.warning_codes,
        )
