"""Frozen US research domain models (Phase 1G G1).

All models are ``@dataclass(frozen=True, slots=True)``. Numerics are
``Decimal`` (no float). Datetimes are timezone-aware. Nested sequences are
immutable tuples. Unavailable values are ``None`` — never coerced zeros.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.us_context.models import USNewsArticle
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

_EQUITY_ONLY = frozenset({AssetType.EQUITY})

_NAME_MAX = 256
_DESC_MAX = 8_000
_URL_MAX = 2_000
_KEY_MAX = 128
_CURRENCY_MAX = 16
_ACCESSION_MAX = 32
_SECTION_MAX = 128
_CODE_MAX = 32
_TITLE_MAX = 500
_SUMMARY_MAX = 4_000
_DEDUPE_MAX = 256
_VERSION_MAX = 64
_FISCAL_PERIOD_MAX = 16

_QUARTERLY_PERIOD_MAX = 8
_ANNUAL_PERIOD_MAX = 5


def _reject_float(value: object, *, field: str) -> None:
    if isinstance(value, float):
        raise DataContractError(
            f"{field} must not be float; use Decimal",
            details={"field": field, "rule": "no_float"},
        )


def _require_decimal(value: object, *, field: str) -> Decimal:
    _reject_float(value, field=field)
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={"field": field, "rule": "decimal_type", "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field, "rule": "finite_decimal"},
        )
    return value


def _require_optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _require_decimal(value, field=field)


def _require_optional_nonnegative_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    number = _require_decimal(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field, "rule": "nonnegative"},
        )
    return number


def _require_int(value: object, *, field: str) -> int:
    _reject_float(value, field=field)
    if type(value) is not int:
        raise DataContractError(
            f"{field} must be an int",
            details={"field": field, "rule": "int_type", "type": type(value).__name__},
        )
    return value


def _require_optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    number = _require_int(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field, "rule": "nonnegative"},
        )
    return number


def _require_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise DataContractError(
            f"{field} must be a bool",
            details={"field": field, "rule": "bool_type", "type": type(value).__name__},
        )
    return value


def _require_optional_bool(value: object, *, field: str) -> bool | None:
    if value is None:
        return None
    return _require_bool(value, field=field)


def _require_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date",
            details={"field": field, "rule": "date_type", "type": type(value).__name__},
        )
    return value


def _require_optional_date(value: object, *, field: str) -> date | None:
    if value is None:
        return None
    return _require_date(value, field=field)


def _require_str(value: object, *, field: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string",
            details={"field": field, "rule": "str_type", "type": type(value).__name__},
        )
    if not value or not value.strip():
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field, "rule": "non_blank"},
        )
    if len(value) > max_len:
        raise DataContractError(
            f"{field} exceeds max length",
            details={"field": field, "rule": "max_length", "max": max_len},
        )
    return value


def _require_optional_str(value: object, *, field: str, max_len: int) -> str | None:
    if value is None:
        return None
    return _require_str(value, field=field, max_len=max_len)


def _require_tuple(value: object, *, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(
            f"{field} must be a tuple",
            details={"field": field, "rule": "tuple_type", "type": type(value).__name__},
        )
    return value


def _require_enum[T](value: object, enum_type: type[T], *, field: str) -> T:
    if not isinstance(value, enum_type):
        raise DataContractError(
            f"{field} must be a {enum_type.__name__}",
            details={
                "field": field,
                "rule": "enum_type",
                "type": type(value).__name__,
                "expected": enum_type.__name__,
            },
        )
    return value


def _require_us_equity_instrument_id(value: object, *, field: str) -> str:
    text = _require_str(value, field=field, max_len=128)
    try:
        asset_type, market, _symbol = parse_instrument_id(text)
    except DataContractError as exc:
        raise DataContractError(
            f"{field} must be a well-formed instrument_id",
            details={"field": field, "rule": "instrument_id_syntax"},
        ) from exc
    if market is not Market.US:
        raise DataContractError(
            f"{field} must use Market.US",
            details={"field": field, "rule": "us_market", "market": market.value},
        )
    if asset_type not in _EQUITY_ONLY:
        raise DataContractError(
            f"{field} asset type must be equity for US research",
            details={
                "field": field,
                "rule": "us_equity",
                "asset_type": asset_type.value,
            },
        )
    return text


def _require_warning_codes(value: object, *, field: str) -> tuple[str, ...]:
    codes = _require_tuple(value, field=field)
    for idx, code in enumerate(codes):
        if not isinstance(code, str) or not code.strip():
            raise DataContractError(
                f"{field} items must be non-blank strings",
                details={"field": f"{field}[{idx}]", "rule": "non_blank"},
            )
    if len(set(codes)) != len(codes):
        raise DataContractError(
            f"{field} must be unique",
            details={"field": field, "rule": "unique"},
        )
    return codes  # type: ignore[return-value]


def _require_line_items(value: object, *, field: str) -> tuple[tuple[str, Decimal | None], ...]:
    items = _require_tuple(value, field=field)
    seen: set[str] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, tuple) or len(item) != 2:
            raise DataContractError(
                f"{field} items must be (key, Decimal|None) pairs",
                details={"field": f"{field}[{idx}]", "rule": "pair"},
            )
        key, amount = item
        key_text = _require_str(key, field=f"{field}[{idx}].key", max_len=_KEY_MAX)
        if key_text in seen:
            raise DataContractError(
                f"{field} keys must be unique",
                details={"field": f"{field}[{idx}].key", "rule": "unique"},
            )
        seen.add(key_text)
        _require_optional_decimal(amount, field=f"{field}[{idx}].value")
    return items  # type: ignore[return-value]


def _period_cap(frequency: USStatementFrequency) -> int:
    if frequency is USStatementFrequency.QUARTERLY:
        return _QUARTERLY_PERIOD_MAX
    return _ANNUAL_PERIOD_MAX


# ---------------------------------------------------------------------------
# Leaf facts used by composite snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class USCorporateAction:
    instrument_id: str
    action_type: USCorporateActionType
    effective_date: date | None
    declared_date: date | None
    paid_date: date | None
    amount: Decimal | None
    ratio: Decimal | None
    currency: str | None
    shares: Decimal | None
    description: str | None

    def __post_init__(self) -> None:
        _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        _require_enum(self.action_type, USCorporateActionType, field="action_type")
        _require_optional_date(self.effective_date, field="effective_date")
        _require_optional_date(self.declared_date, field="declared_date")
        _require_optional_date(self.paid_date, field="paid_date")
        _require_optional_decimal(self.amount, field="amount")
        _require_optional_decimal(self.ratio, field="ratio")
        _require_optional_str(self.currency, field="currency", max_len=_CURRENCY_MAX)
        _require_optional_nonnegative_decimal(self.shares, field="shares")
        _require_optional_str(self.description, field="description", max_len=_TITLE_MAX)


@dataclass(frozen=True, slots=True)
class USCompanyProfile:
    instrument_id: str
    legal_name: str | None
    description: str | None
    sector: str | None
    industry: str | None
    country: str | None
    website: str | None
    employees: int | None
    market_cap: Decimal | None

    def __post_init__(self) -> None:
        _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        _require_optional_str(self.legal_name, field="legal_name", max_len=_NAME_MAX)
        _require_optional_str(self.description, field="description", max_len=_DESC_MAX)
        _require_optional_str(self.sector, field="sector", max_len=_NAME_MAX)
        _require_optional_str(self.industry, field="industry", max_len=_NAME_MAX)
        _require_optional_str(self.country, field="country", max_len=_NAME_MAX)
        _require_optional_str(self.website, field="website", max_len=_URL_MAX)
        _require_optional_nonnegative_int(self.employees, field="employees")
        _require_optional_nonnegative_decimal(self.market_cap, field="market_cap")


@dataclass(frozen=True, slots=True)
class USFundamentalMetrics:
    trailing_pe: Decimal | None
    forward_pe: Decimal | None
    peg_ratio: Decimal | None
    price_to_book: Decimal | None
    price_to_sales: Decimal | None
    enterprise_to_ebitda: Decimal | None
    dividend_yield: Decimal | None
    beta: Decimal | None
    eps_ttm: Decimal | None
    eps_forward: Decimal | None
    book_value_per_share: Decimal | None
    revenue_per_share: Decimal | None
    revenue: Decimal | None
    gross_profit: Decimal | None
    ebitda: Decimal | None
    net_income: Decimal | None
    profit_margin: Decimal | None
    operating_margin: Decimal | None
    roe: Decimal | None
    roa: Decimal | None
    debt_to_equity: Decimal | None
    current_ratio: Decimal | None
    revenue_growth: Decimal | None
    eps_growth: Decimal | None
    estimate_revision: Decimal | None
    share_count: Decimal | None
    stock_based_compensation: Decimal | None
    capital_expenditure: Decimal | None
    free_cash_flow: Decimal | None
    net_cash_or_debt: Decimal | None
    period_end: date | None
    filed_at: datetime | None
    basis: USFundamentalBasis

    def __post_init__(self) -> None:
        for name in (
            "trailing_pe",
            "forward_pe",
            "peg_ratio",
            "price_to_book",
            "price_to_sales",
            "enterprise_to_ebitda",
            "dividend_yield",
            "beta",
            "eps_ttm",
            "eps_forward",
            "book_value_per_share",
            "revenue_per_share",
            "revenue",
            "gross_profit",
            "ebitda",
            "net_income",
            "profit_margin",
            "operating_margin",
            "roe",
            "roa",
            "debt_to_equity",
            "current_ratio",
            "revenue_growth",
            "eps_growth",
            "estimate_revision",
            "share_count",
            "stock_based_compensation",
            "capital_expenditure",
            "free_cash_flow",
            "net_cash_or_debt",
        ):
            _require_optional_decimal(getattr(self, name), field=name)
        _require_optional_date(self.period_end, field="period_end")
        if self.filed_at is not None:
            require_aware_datetime(self.filed_at, field_name="filed_at")
        _require_enum(self.basis, USFundamentalBasis, field="basis")


@dataclass(frozen=True, slots=True)
class USFundamentalSnapshot:
    instrument_id: str
    as_of: datetime
    profile: USCompanyProfile | None
    metrics: USFundamentalMetrics | None
    corporate_actions: tuple[USCorporateAction, ...]
    degraded: bool
    warning_codes: tuple[str, ...]
    reported_metrics: USFundamentalMetrics | None = None

    def __post_init__(self) -> None:
        instrument_id = _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        require_aware_datetime(self.as_of, field_name="as_of")
        _require_bool(self.degraded, field="degraded")
        _require_warning_codes(self.warning_codes, field="warning_codes")
        if self.profile is not None:
            if not isinstance(self.profile, USCompanyProfile):
                raise DataContractError(
                    "profile must be a USCompanyProfile",
                    details={"field": "profile", "rule": "type"},
                )
            if self.profile.instrument_id != instrument_id:
                raise DataContractError(
                    "profile.instrument_id must match snapshot instrument_id",
                    details={"field": "profile.instrument_id", "rule": "instrument_match"},
                )
        if self.metrics is not None and not isinstance(self.metrics, USFundamentalMetrics):
            raise DataContractError(
                "metrics must be a USFundamentalMetrics",
                details={"field": "metrics", "rule": "type"},
            )
        if self.reported_metrics is not None and not isinstance(
            self.reported_metrics, USFundamentalMetrics
        ):
            raise DataContractError(
                "reported_metrics must be a USFundamentalMetrics",
                details={"field": "reported_metrics", "rule": "type"},
            )
        if (
            self.metrics is not None
            and self.metrics.filed_at is not None
            and self.metrics.filed_at > self.as_of
        ):
            raise DataContractError(
                "metrics.filed_at must be <= as_of",
                details={"field": "metrics.filed_at", "rule": "not_after_as_of"},
            )
        if (
            self.reported_metrics is not None
            and self.reported_metrics.filed_at is not None
            and self.reported_metrics.filed_at > self.as_of
        ):
            raise DataContractError(
                "reported_metrics.filed_at must be <= as_of",
                details={"field": "reported_metrics.filed_at", "rule": "not_after_as_of"},
            )
        actions = _require_tuple(self.corporate_actions, field="corporate_actions")
        for idx, action in enumerate(actions):
            if not isinstance(action, USCorporateAction):
                raise DataContractError(
                    "corporate_actions items must be USCorporateAction",
                    details={"field": f"corporate_actions[{idx}]", "rule": "type"},
                )
            if action.instrument_id != instrument_id:
                raise DataContractError(
                    "corporate_actions instrument_id must match snapshot",
                    details={
                        "field": f"corporate_actions[{idx}].instrument_id",
                        "rule": "instrument_match",
                    },
                )


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class USStatementPeriod:
    statement_type: USStatementType
    frequency: USStatementFrequency
    fiscal_year: int
    fiscal_period: str | None
    period_end: date
    filed_at: datetime | None
    currency: str | None
    line_items: tuple[tuple[str, Decimal | None], ...]
    period_start: date | None = None
    accession: str | None = None
    filing_form: str | None = None
    is_amendment: bool = False

    def __post_init__(self) -> None:
        _require_enum(self.statement_type, USStatementType, field="statement_type")
        _require_enum(self.frequency, USStatementFrequency, field="frequency")
        year = _require_int(self.fiscal_year, field="fiscal_year")
        if year < 1900 or year > 2100:
            raise DataContractError(
                "fiscal_year out of range",
                details={"field": "fiscal_year", "rule": "year_range"},
            )
        _require_optional_str(self.fiscal_period, field="fiscal_period", max_len=_FISCAL_PERIOD_MAX)
        _require_date(self.period_end, field="period_end")
        if self.period_start is not None:
            _require_date(self.period_start, field="period_start")
            if self.period_start > self.period_end:
                raise DataContractError(
                    "period_start must be <= period_end",
                    details={"field": "period_start", "rule": "range"},
                )
        if self.filed_at is not None:
            require_aware_datetime(self.filed_at, field_name="filed_at")
        _require_optional_str(self.currency, field="currency", max_len=_CURRENCY_MAX)
        _require_optional_str(self.accession, field="accession", max_len=_ACCESSION_MAX)
        _require_optional_str(self.filing_form, field="filing_form", max_len=16)
        if type(self.is_amendment) is not bool:
            raise DataContractError(
                "is_amendment must be an exact bool",
                details={"field": "is_amendment", "rule": "type"},
            )
        _require_line_items(self.line_items, field="line_items")


@dataclass(frozen=True, slots=True)
class USFinancialStatements:
    instrument_id: str
    as_of: datetime
    frequency: USStatementFrequency
    income: tuple[USStatementPeriod, ...]
    balance_sheet: tuple[USStatementPeriod, ...]
    cash_flow: tuple[USStatementPeriod, ...]
    view: USStatementView = USStatementView.LATEST

    def __post_init__(self) -> None:
        _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        require_aware_datetime(self.as_of, field_name="as_of")
        frequency = _require_enum(self.frequency, USStatementFrequency, field="frequency")
        _require_enum(self.view, USStatementView, field="view")
        cap = _period_cap(frequency)
        for field_name, statement_type, periods in (
            ("income", USStatementType.INCOME, self.income),
            ("balance_sheet", USStatementType.BALANCE_SHEET, self.balance_sheet),
            ("cash_flow", USStatementType.CASH_FLOW, self.cash_flow),
        ):
            seq = _require_tuple(periods, field=field_name)
            if len(seq) > cap:
                raise DataContractError(
                    f"{field_name} exceeds max periods for frequency",
                    details={
                        "field": field_name,
                        "rule": "period_cap",
                        "max": cap,
                        "count": len(seq),
                    },
                )
            for idx, period in enumerate(seq):
                if not isinstance(period, USStatementPeriod):
                    raise DataContractError(
                        f"{field_name} items must be USStatementPeriod",
                        details={"field": f"{field_name}[{idx}]", "rule": "type"},
                    )
                if period.statement_type is not statement_type:
                    raise DataContractError(
                        f"{field_name} period statement_type mismatch",
                        details={
                            "field": f"{field_name}[{idx}].statement_type",
                            "rule": "statement_type",
                        },
                    )
                if period.frequency is not frequency:
                    raise DataContractError(
                        f"{field_name} period frequency mismatch",
                        details={
                            "field": f"{field_name}[{idx}].frequency",
                            "rule": "frequency",
                        },
                    )
                if period.filed_at is not None and period.filed_at > self.as_of:
                    raise DataContractError(
                        f"{field_name} filed_at must be <= as_of",
                        details={
                            "field": f"{field_name}[{idx}].filed_at",
                            "rule": "not_after_as_of",
                        },
                    )


# ---------------------------------------------------------------------------
# Filings / insider
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class USFilingSection:
    section_name: str
    document_url: str | None
    text: str | None
    algorithm_version: str

    def __post_init__(self) -> None:
        _require_str(self.section_name, field="section_name", max_len=_SECTION_MAX)
        _require_optional_str(self.document_url, field="document_url", max_len=_URL_MAX)
        _require_optional_str(self.text, field="text", max_len=_DESC_MAX)
        _require_str(self.algorithm_version, field="algorithm_version", max_len=_VERSION_MAX)


@dataclass(frozen=True, slots=True)
class USFiling:
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
    sections: tuple[USFilingSection, ...]

    def __post_init__(self) -> None:
        _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        _require_str(self.accession, field="accession", max_len=_ACCESSION_MAX)
        _require_enum(self.form, USFilingForm, field="form")
        _require_bool(self.is_amendment, field="is_amendment")
        _require_date(self.filed_date, field="filed_date")
        if self.accepted_at is not None:
            require_aware_datetime(self.accepted_at, field_name="accepted_at")
        _require_optional_date(self.period_of_report, field="period_of_report")
        _require_optional_str(self.primary_document, field="primary_document", max_len=_NAME_MAX)
        _require_optional_str(self.url, field="url", max_len=_URL_MAX)
        items = _require_tuple(self.items, field="items")
        for idx, item in enumerate(items):
            _require_str(item, field=f"items[{idx}]", max_len=_CODE_MAX)
        sections = _require_tuple(self.sections, field="sections")
        for idx, section in enumerate(sections):
            if not isinstance(section, USFilingSection):
                raise DataContractError(
                    "sections items must be USFilingSection",
                    details={"field": f"sections[{idx}]", "rule": "type"},
                )


@dataclass(frozen=True, slots=True)
class USInsiderTransaction:
    instrument_id: str
    owner_name: str
    relationship: str | None
    transaction_date: date | None
    filed_at: datetime | None
    accepted_at: datetime | None
    transaction_code: str | None
    acquired_disposed: USInsiderAcquiredDisposed | None
    shares: Decimal | None
    price: Decimal | None
    post_transaction_shares: Decimal | None
    is_direct: bool | None
    rule_10b5_1: bool | None

    def __post_init__(self) -> None:
        _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        _require_str(self.owner_name, field="owner_name", max_len=_NAME_MAX)
        _require_optional_str(self.relationship, field="relationship", max_len=_NAME_MAX)
        _require_optional_date(self.transaction_date, field="transaction_date")
        if self.filed_at is not None:
            require_aware_datetime(self.filed_at, field_name="filed_at")
        if self.accepted_at is not None:
            require_aware_datetime(self.accepted_at, field_name="accepted_at")
        _require_optional_str(self.transaction_code, field="transaction_code", max_len=_CODE_MAX)
        if self.acquired_disposed is not None:
            _require_enum(
                self.acquired_disposed,
                USInsiderAcquiredDisposed,
                field="acquired_disposed",
            )
        _require_optional_nonnegative_decimal(self.shares, field="shares")
        _require_optional_nonnegative_decimal(self.price, field="price")
        _require_optional_nonnegative_decimal(
            self.post_transaction_shares, field="post_transaction_shares"
        )
        _require_optional_bool(self.is_direct, field="is_direct")
        _require_optional_bool(self.rule_10b5_1, field="rule_10b5_1")


# ---------------------------------------------------------------------------
# Company updates / external events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class USExternalEvent:
    instrument_id: str
    event_type: USExternalEventType
    event_time: datetime
    visible_time: datetime
    title: str
    summary: str | None
    source_reference: str | None
    dedupe_key: str
    filing: USFiling | None
    insider_transaction: USInsiderTransaction | None
    corporate_action: USCorporateAction | None
    news_article: USNewsArticle | None = None

    def __post_init__(self) -> None:
        instrument_id = _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        event_type = _require_enum(self.event_type, USExternalEventType, field="event_type")
        require_aware_datetime(self.event_time, field_name="event_time")
        require_aware_datetime(self.visible_time, field_name="visible_time")
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_optional_str(self.summary, field="summary", max_len=_SUMMARY_MAX)
        _require_optional_str(self.source_reference, field="source_reference", max_len=_URL_MAX)
        _require_str(self.dedupe_key, field="dedupe_key", max_len=_DEDUPE_MAX)

        payload_count = sum(
            payload is not None
            for payload in (
                self.filing,
                self.insider_transaction,
                self.corporate_action,
                self.news_article,
            )
        )
        if payload_count != 1:
            raise DataContractError(
                "event must contain exactly one typed payload",
                details={"field": "event", "rule": "exactly_one_payload"},
            )

        if self.filing is not None:
            if not isinstance(self.filing, USFiling):
                raise DataContractError(
                    "filing must be a USFiling",
                    details={"field": "filing", "rule": "type"},
                )
            if self.filing.instrument_id != instrument_id:
                raise DataContractError(
                    "filing.instrument_id must match event instrument_id",
                    details={"field": "filing.instrument_id", "rule": "instrument_match"},
                )
            if event_type is not USExternalEventType.FILING:
                raise DataContractError(
                    "filing payload requires event_type=filing",
                    details={"field": "filing", "rule": "event_type"},
                )
        if self.insider_transaction is not None:
            if not isinstance(self.insider_transaction, USInsiderTransaction):
                raise DataContractError(
                    "insider_transaction must be a USInsiderTransaction",
                    details={"field": "insider_transaction", "rule": "type"},
                )
            if self.insider_transaction.instrument_id != instrument_id:
                raise DataContractError(
                    "insider_transaction.instrument_id must match event",
                    details={
                        "field": "insider_transaction.instrument_id",
                        "rule": "instrument_match",
                    },
                )
            if event_type is not USExternalEventType.INSIDER_TRANSACTION:
                raise DataContractError(
                    "insider_transaction payload requires matching event_type",
                    details={"field": "insider_transaction", "rule": "event_type"},
                )
        if self.corporate_action is not None:
            if not isinstance(self.corporate_action, USCorporateAction):
                raise DataContractError(
                    "corporate_action must be a USCorporateAction",
                    details={"field": "corporate_action", "rule": "type"},
                )
            if self.corporate_action.instrument_id != instrument_id:
                raise DataContractError(
                    "corporate_action.instrument_id must match event",
                    details={
                        "field": "corporate_action.instrument_id",
                        "rule": "instrument_match",
                    },
                )
            if event_type is not USExternalEventType.CORPORATE_ACTION:
                raise DataContractError(
                    "corporate_action payload requires matching event_type",
                    details={"field": "corporate_action", "rule": "event_type"},
                )
        if self.news_article is not None:
            if not isinstance(self.news_article, USNewsArticle):
                raise DataContractError(
                    "news_article must be a USNewsArticle",
                    details={"field": "news_article", "rule": "type"},
                )
            if self.news_article.instrument_id != instrument_id:
                raise DataContractError(
                    "news_article.instrument_id must match event",
                    details={
                        "field": "news_article.instrument_id",
                        "rule": "instrument_match",
                    },
                )
            if event_type is not USExternalEventType.NEWS:
                raise DataContractError(
                    "news_article payload requires event_type=news",
                    details={"field": "news_article", "rule": "event_type"},
                )


@dataclass(frozen=True, slots=True)
class USCompanyUpdate:
    instrument_id: str
    as_of: datetime
    events: tuple[USExternalEvent, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        instrument_id = _require_us_equity_instrument_id(self.instrument_id, field="instrument_id")
        require_aware_datetime(self.as_of, field_name="as_of")
        _require_bool(self.degraded, field="degraded")
        _require_warning_codes(self.warning_codes, field="warning_codes")
        events = _require_tuple(self.events, field="events")
        seen_keys: set[str] = set()
        for idx, event in enumerate(events):
            if not isinstance(event, USExternalEvent):
                raise DataContractError(
                    "events items must be USExternalEvent",
                    details={"field": f"events[{idx}]", "rule": "type"},
                )
            if event.instrument_id != instrument_id:
                raise DataContractError(
                    "events instrument_id must match update instrument_id",
                    details={
                        "field": f"events[{idx}].instrument_id",
                        "rule": "instrument_match",
                    },
                )
            if event.visible_time > self.as_of:
                raise DataContractError(
                    "events visible_time must be <= as_of",
                    details={
                        "field": f"events[{idx}].visible_time",
                        "rule": "not_after_as_of",
                    },
                )
            if event.dedupe_key in seen_keys:
                raise DataContractError(
                    "events dedupe_key must be unique",
                    details={"field": f"events[{idx}].dedupe_key", "rule": "unique"},
                )
            seen_keys.add(event.dedupe_key)
