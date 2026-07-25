"""SEC Company Facts adapter (Phase 1G G2b).

Direct HttpTransport only. Official endpoint:

- ``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json``

Also uses ``company_tickers.json`` via ``SECIdentityResolver``.
Implements CategoryProvider + USFundamentalProvider + USFinancialStatementsProvider.
Separate from ``sec_edgar.py`` (filings/insider).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
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
from domain.common.errors import DataContractError, ProviderNotConfigured
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic
from domain.us_research.enums import (
    USFundamentalBasis,
    USStatementFrequency,
    USStatementType,
    USStatementView,
)
from domain.us_research.models import (
    USCompanyProfile,
    USFinancialStatements,
    USFundamentalMetrics,
    USFundamentalSnapshot,
    USStatementPeriod,
)
from infrastructure.providers.us.sec_common import (
    COMPANYFACTS_PREFIX,
    JSON_CONTENT_TYPES,
    content_type_ok,
    filed_visibility_utc,
    loads_sec_json_strict,
    raise_for_sec_http_status,
    sec_contract,
)
from infrastructure.providers.us.sec_identity import SECIdentityResolver
from infrastructure.system.clock import SystemClock

_SUPPORTED: Final[frozenset[DataCategory]] = frozenset(
    {DataCategory.FUNDAMENTALS, DataCategory.FINANCIAL_STATEMENTS}
)
_WARN_PARTIAL: Final[str] = "SEC_FUNDAMENTALS_PARTIAL"

_ANNUAL_FORMS: Final[frozenset[str]] = frozenset({"10-K", "10-K/A"})
_QUARTERLY_FORMS: Final[frozenset[str]] = frozenset({"10-Q", "10-Q/A"})
_ALL_FORMS: Final[frozenset[str]] = _ANNUAL_FORMS | _QUARTERLY_FORMS

_QUARTER_DAYS_MIN: Final[int] = 60
_QUARTER_DAYS_MAX: Final[int] = 120
_ANNUAL_MAX: Final[int] = 5
_QUARTERLY_MAX: Final[int] = 8

# Unit families accepted per normalized line key.
_UNIT_USD: Final[str] = "USD"
_UNIT_SHARES: Final[str] = "shares"
_UNIT_USD_PER_SHARE: Final[str] = "USD/shares"

# Ordered versioned aliases: (taxonomy, tag) tried first-wins.
# Never expose taxonomy labels on the domain surface.
_INCOME_ALIASES: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    "revenue": (
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    ),
    "gross_profit": (("us-gaap", "GrossProfit"),),
    "operating_income": (("us-gaap", "OperatingIncomeLoss"),),
    "net_income": (
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
        ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),
    ),
    "eps_basic": (("us-gaap", "EarningsPerShareBasic"),),
    "eps_diluted": (("us-gaap", "EarningsPerShareDiluted"),),
    "research_and_development": (("us-gaap", "ResearchAndDevelopmentExpense"),),
    "selling_general_admin": (("us-gaap", "SellingGeneralAndAdministrativeExpense"),),
    "income_tax_expense": (("us-gaap", "IncomeTaxExpenseBenefit"),),
}

_BALANCE_ALIASES: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    "cash_and_equivalents": (
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "Cash"),
    ),
    "short_term_investments": (
        ("us-gaap", "ShortTermInvestments"),
        ("us-gaap", "MarketableSecuritiesCurrent"),
    ),
    "accounts_receivable": (
        ("us-gaap", "AccountsReceivableNetCurrent"),
        ("us-gaap", "AccountsReceivableNet"),
    ),
    "inventory": (("us-gaap", "InventoryNet"),),
    "current_assets": (("us-gaap", "AssetsCurrent"),),
    "total_assets": (("us-gaap", "Assets"),),
    "accounts_payable": (
        ("us-gaap", "AccountsPayableCurrent"),
        ("us-gaap", "AccountsPayable"),
    ),
    "current_liabilities": (("us-gaap", "LiabilitiesCurrent"),),
    "long_term_debt": (
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebt"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligations"),
    ),
    "total_liabilities": (("us-gaap", "Liabilities"),),
    "stockholders_equity": (
        ("us-gaap", "StockholdersEquity"),
        (
            "us-gaap",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    "shares_outstanding": (
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("us-gaap", "EntityCommonStockSharesOutstanding"),
    ),
}

_CASH_FLOW_ALIASES: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    "operating_cash_flow": (("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),),
    "capital_expenditure": (("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),),
    "investing_cash_flow": (("us-gaap", "NetCashProvidedByUsedInInvestingActivities"),),
    "financing_cash_flow": (("us-gaap", "NetCashProvidedByUsedInFinancingActivities"),),
    "stock_based_compensation": (("us-gaap", "ShareBasedCompensation"),),
    "dividends_paid": (
        ("us-gaap", "PaymentsOfDividends"),
        ("us-gaap", "PaymentsOfDividendsCommonStock"),
    ),
    "share_repurchases": (("us-gaap", "PaymentsForRepurchaseOfCommonStock"),),
    "depreciation_and_amortization": (
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "DepreciationAndAmortization"),
    ),
}

_INCOME_KEYS: Final[tuple[str, ...]] = tuple(_INCOME_ALIASES.keys())
_BALANCE_KEYS: Final[tuple[str, ...]] = tuple(_BALANCE_ALIASES.keys())
_CASH_FLOW_KEYS: Final[tuple[str, ...]] = tuple(_CASH_FLOW_ALIASES.keys())

# Duration line keys (income + cash flow) vs instant (balance sheet).
_DURATION_ALIASES: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    **_INCOME_ALIASES,
    **_CASH_FLOW_ALIASES,
}
_INSTANT_ALIASES: Final[Mapping[str, tuple[tuple[str, str], ...]]] = dict(_BALANCE_ALIASES)

_KEY_UNIT: Final[Mapping[str, str]] = {
    **{k: _UNIT_USD for k in _INCOME_KEYS if k not in {"eps_basic", "eps_diluted"}},
    "eps_basic": _UNIT_USD_PER_SHARE,
    "eps_diluted": _UNIT_USD_PER_SHARE,
    **{k: _UNIT_USD for k in _BALANCE_KEYS if k != "shares_outstanding"},
    "shares_outstanding": _UNIT_SHARES,
    **{k: _UNIT_USD for k in _CASH_FLOW_KEYS},
}


def _contract(message: str, *, operation: str, rule: str, **extra: object) -> DataContractError:
    return sec_contract(message, operation=operation, rule=rule, **extra)


def _to_decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    if type(raw) is Decimal:
        return raw if raw.is_finite() else None
    if type(raw) is int and not isinstance(raw, bool):
        return Decimal(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            value = Decimal(raw.strip())
        except (InvalidOperation, ValueError):
            return None
        return value if value.is_finite() else None
    return None


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _normalize_form(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip().upper()
    if text in _ALL_FORMS:
        return text
    # SEC may emit lowercase; re-check original strip map.
    stripped = raw.strip()
    if stripped in _ALL_FORMS:
        return stripped
    return None


def _period_days(start: date | None, end: date) -> int | None:
    if start is None:
        return None
    return (end - start).days


@dataclass(frozen=True, slots=True)
class _FactPoint:
    key: str
    value: Decimal
    accession: str
    end: date
    fy: int
    fp: str
    form: str
    filed: date
    start: date | None
    is_instant: bool


@dataclass(frozen=True, slots=True)
class _GroupKey:
    accession: str
    end: date
    fy: int
    fp: str
    filed: date
    form: str
    start: date | None


class SECCompanyFactsAdapter:
    """CategoryProvider for US fundamentals + statements via SEC Company Facts."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        sec_user_agent: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if transport is None:
            raise DataContractError(
                "transport is required",
                details={"field": "transport", "rule": "required"},
            )
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be a positive number",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        ua = sec_user_agent.strip() if isinstance(sec_user_agent, str) else ""
        self._sec_user_agent = ua or None
        self._timeout_seconds = float(timeout_seconds)
        self._identity: SECIdentityResolver | None = (
            SECIdentityResolver(
                transport,
                user_agent=self._sec_user_agent,
                timeout_seconds=self._timeout_seconds,
            )
            if self._sec_user_agent is not None
            else None
        )

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SEC_EDGAR

    @property
    def provider_name(self) -> str:
        return VendorId.SEC_EDGAR.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category in _SUPPORTED

    def is_configured(self) -> bool:
        return self._enabled and self._sec_user_agent is not None

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "SEC Company Facts adapter is not configured",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        return now

    def _require_us_equity(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.US:
            raise DataContractError(
                "instrument market must be US",
                details={"field": "instrument", "rule": "market"},
            )
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "SEC Company Facts supports US equity only",
                details={
                    "field": "instrument",
                    "rule": "asset_type",
                    "asset_type": instrument.asset_type.value,
                },
            )
        symbol = instrument.symbol.strip().upper()
        if not symbol:
            raise DataContractError(
                "instrument symbol must be non-blank",
                details={"field": "symbol", "rule": "non_blank"},
            )
        return symbol

    def _meta(
        self,
        *,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        warnings: tuple[str, ...] = (),
    ) -> ProviderResultMeta:
        try:
            session = infer_session_basic(Market.US, as_of, timezone="America/New_York")
        except DataContractError:
            session = TradingSession.UNKNOWN
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=warnings,
        )

    async def _resolve_cik(self, symbol: str) -> str:
        if self._identity is None:
            raise ProviderNotConfigured(
                "SEC Company Facts adapter is not configured",
                details={"vendor": self.vendor_id.value},
            )
        return await self._identity.resolve_cik(symbol)

    async def _fetch_companyfacts(self, cik10: str) -> dict[str, object]:
        assert self._sec_user_agent is not None
        url = f"{COMPANYFACTS_PREFIX}CIK{cik10}.json"
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params={},
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._sec_user_agent,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        raise_for_sec_http_status(response.status_code, operation="companyfacts")
        if not content_type_ok(response.headers, JSON_CONTENT_TYPES):
            raise _contract(
                "SEC Company Facts Content-Type is not acceptable",
                operation="companyfacts",
                rule="content_type",
            )
        payload = loads_sec_json_strict(response.body)
        if not isinstance(payload, dict):
            raise _contract(
                "companyfacts payload must be an object",
                operation="companyfacts",
                rule="contract_drift",
            )
        return payload

    def _concept_unit_series(
        self,
        facts_root: Mapping[str, object],
        *,
        taxonomy: str,
        tag: str,
        unit: str,
        operation: str = "companyfacts",
    ) -> list[object] | None:
        """Return unit series for a concept, or None if concept/unit absent.

        Raises when a present concept or its units/expected series is malformed.
        """
        tax = facts_root.get(taxonomy)
        if tax is None:
            return None
        if not isinstance(tax, dict):
            raise _contract(
                "companyfacts taxonomy must be an object",
                operation=operation,
                rule="contract_drift",
                taxonomy=taxonomy,
            )
        if tag not in tax:
            return None
        concept = tax[tag]
        if not isinstance(concept, dict):
            raise _contract(
                "companyfacts concept must be an object",
                operation=operation,
                rule="contract_drift",
                taxonomy=taxonomy,
                tag=tag,
            )
        units = concept.get("units")
        if not isinstance(units, dict):
            raise _contract(
                "companyfacts concept.units must be an object",
                operation=operation,
                rule="contract_drift",
                taxonomy=taxonomy,
                tag=tag,
            )
        if unit not in units:
            return None
        series = units[unit]
        if not isinstance(series, list):
            raise _contract(
                "companyfacts unit series must be a list",
                operation=operation,
                rule="contract_drift",
                taxonomy=taxonomy,
                tag=tag,
                unit=unit,
            )
        return series

    def _extract_points(
        self,
        facts_root: Mapping[str, object],
        *,
        aliases: Mapping[str, tuple[tuple[str, str], ...]],
        as_of: datetime,
        want_instant: bool,
        annual_only: bool | None,
    ) -> list[_FactPoint]:
        """Collect facts with first valid ordered alias **per group identity**.

        Group identity is (accession, end, fy, fp, filed, form). A primary tag
        with only older periods does not block a fallback alias for newer periods.
        """
        out: list[_FactPoint] = []
        for key, alias_list in aliases.items():
            unit = _KEY_UNIT[key]
            # First alias that yields a valid point for a group wins that group.
            by_group: dict[tuple[str, date, int, str, date, str], _FactPoint] = {}
            for taxonomy, tag in alias_list:
                series = self._concept_unit_series(
                    facts_root, taxonomy=taxonomy, tag=tag, unit=unit
                )
                if series is None:
                    continue
                for entry in series:
                    point = self._parse_entry(
                        entry,
                        key=key,
                        as_of=as_of,
                        want_instant=want_instant,
                        annual_only=annual_only,
                    )
                    if point is None:
                        continue
                    gid = (
                        point.accession,
                        point.end,
                        point.fy,
                        point.fp,
                        point.filed,
                        point.form,
                    )
                    if gid not in by_group:
                        by_group[gid] = point
            out.extend(by_group.values())
        return out

    def _parse_entry(
        self,
        entry: object,
        *,
        key: str,
        as_of: datetime,
        want_instant: bool,
        annual_only: bool | None,
    ) -> _FactPoint | None:
        if not isinstance(entry, dict):
            return None
        form = _normalize_form(entry.get("form"))
        if form is None:
            return None
        is_annual = form in _ANNUAL_FORMS
        is_quarterly = form in _QUARTERLY_FORMS
        if annual_only is True and not is_annual:
            return None
        if annual_only is False and not is_quarterly:
            return None
        end = _parse_iso_date(entry.get("end"))
        filed = _parse_iso_date(entry.get("filed"))
        accn = entry.get("accn")
        fy_raw = entry.get("fy")
        fp_raw = entry.get("fp")
        if end is None or filed is None:
            return None
        if not isinstance(accn, str) or not accn.strip():
            return None
        if type(fy_raw) is not int or isinstance(fy_raw, bool):
            # json may parse as Decimal/int via strict loader — accept int only
            if type(fy_raw) is Decimal and fy_raw == fy_raw.to_integral_value():
                fy = int(fy_raw)
            else:
                return None
        else:
            fy = fy_raw
        if not isinstance(fp_raw, str) or not fp_raw.strip():
            return None
        fp = fp_raw.strip().upper()
        if is_annual and fp != "FY":
            return None
        if is_quarterly and fp not in {"Q1", "Q2", "Q3", "Q4"}:
            return None
        visible = filed_visibility_utc(filed)
        if visible > as_of:
            return None
        value = _to_decimal(entry.get("val"))
        if value is None:
            return None
        start = _parse_iso_date(entry.get("start"))
        has_start = start is not None
        # Instant: no start (or empty). Duration: start required for quarterly filter.
        if want_instant:
            if has_start:
                return None
            return _FactPoint(
                key=key,
                value=value,
                accession=accn.strip(),
                end=end,
                fy=fy,
                fp=fp,
                form=form,
                filed=filed,
                start=None,
                is_instant=True,
            )
        # Duration facts.
        if not has_start:
            # Some share-like duration tags may omit start; reject for income/CF.
            return None
        if is_quarterly:
            days = _period_days(start, end)
            if days is None or days < _QUARTER_DAYS_MIN or days > _QUARTER_DAYS_MAX:
                return None  # exclude 6/9-month YTD
        return _FactPoint(
            key=key,
            value=value,
            accession=accn.strip(),
            end=end,
            fy=fy,
            fp=fp,
            form=form,
            filed=filed,
            start=start,
            is_instant=False,
        )

    def _group_points(self, points: Sequence[_FactPoint]) -> dict[_GroupKey, dict[str, Decimal]]:
        groups: dict[_GroupKey, dict[str, Decimal]] = {}
        for p in points:
            gk = _GroupKey(
                accession=p.accession,
                end=p.end,
                fy=p.fy,
                fp=p.fp,
                filed=p.filed,
                form=p.form,
                start=p.start,
            )
            bucket = groups.setdefault(gk, {})
            # First value for key in group wins (stable alias order already applied).
            if p.key not in bucket:
                bucket[p.key] = p.value
        return groups

    def _select_periods(
        self,
        groups: Mapping[_GroupKey, Mapping[str, Decimal]],
        *,
        view: USStatementView,
    ) -> list[tuple[_GroupKey, dict[str, Decimal]]]:
        """Select one latest visible group or retain bounded filing vintages."""
        if view is USStatementView.VINTAGES:
            rows = [(gk, dict(lines)) for gk, lines in groups.items()]
            rows.sort(
                key=lambda pair: (pair[0].end, pair[0].filed, pair[0].accession),
                reverse=True,
            )
            return rows
        best: dict[tuple[date, str], tuple[_GroupKey, dict[str, Decimal]]] = {}
        for gk, lines in groups.items():
            # SEC comparison facts may reuse the current filing's fiscal year for
            # a prior period.  End date + fiscal period identifies the economic
            # period; latest visible filing wins without calling it a restatement.
            period_id = (gk.end, gk.fp)
            prev = best.get(period_id)
            if prev is None:
                best[period_id] = (gk, dict(lines))
                continue
            prev_gk = prev[0]
            if (gk.filed, gk.accession) > (prev_gk.filed, prev_gk.accession):
                best[period_id] = (gk, dict(lines))
        rows = list(best.values())
        # Newest fiscal period first; late amendment of an older year must not
        # outrank a newer period_end (then filed, then accession for stability).
        rows.sort(
            key=lambda pair: (pair[0].end, pair[0].filed, pair[0].accession),
            reverse=True,
        )
        return rows

    def _line_items(
        self, keys: Sequence[str], lines: Mapping[str, Decimal]
    ) -> tuple[tuple[str, Decimal | None], ...]:
        return tuple((k, lines.get(k)) for k in keys)

    def _build_periods(
        self,
        *,
        statement_type: USStatementType,
        frequency: USStatementFrequency,
        keys: Sequence[str],
        duration_points: Sequence[_FactPoint],
        instant_points: Sequence[_FactPoint],
        limit: int,
        view: USStatementView,
    ) -> tuple[USStatementPeriod, ...]:
        # Statement-specific duration keys only.
        key_set = frozenset(keys)
        dur = [p for p in duration_points if p.key in key_set]
        inst = list(instant_points) if statement_type is USStatementType.BALANCE_SHEET else []
        points = inst if statement_type is USStatementType.BALANCE_SHEET else dur
        groups = self._group_points(points)
        # For income/CF, optionally attach nothing from instant.
        # For balance sheet, instants already grouped alone.
        # When income/CF groups exist, do not mix foreign accessions.
        rows = self._select_periods(groups, view=view)
        out: list[USStatementPeriod] = []
        for gk, lines in rows[:limit]:
            currency = "USD"
            out.append(
                USStatementPeriod(
                    statement_type=statement_type,
                    frequency=frequency,
                    fiscal_year=gk.fy,
                    fiscal_period=gk.fp,
                    period_end=gk.end,
                    filed_at=filed_visibility_utc(gk.filed),
                    currency=currency,
                    line_items=self._line_items(keys, lines),
                    period_start=gk.start,
                    accession=gk.accession,
                    filing_form=gk.form,
                    is_amendment=gk.form.endswith("/A"),
                )
            )
        return tuple(out)

    def _parse_entity_name(self, payload: Mapping[str, object]) -> str | None:
        name = payload.get("entityName")
        if isinstance(name, str) and name.strip():
            return name.strip()[:256]
        return None

    def _require_facts_root(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        """Return facts object, empty mapping if missing/empty, raise if malformed."""
        if "facts" not in payload:
            return {}
        facts = payload.get("facts")
        if facts is None:
            return {}
        if not isinstance(facts, dict):
            raise _contract(
                "companyfacts.facts must be an object",
                operation="companyfacts",
                rule="contract_drift",
            )
        return facts

    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        frequency: USStatementFrequency,
        limit: int,
        as_of: datetime,
        view: USStatementView = USStatementView.LATEST,
    ) -> ProviderSuccess[USFinancialStatements]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(frequency, USStatementFrequency):
            raise _contract(
                "frequency must be USStatementFrequency",
                operation="financial_statements",
                rule="type",
            )
        if not isinstance(view, USStatementView):
            raise _contract(
                "view must be USStatementView",
                operation="financial_statements",
                rule="type",
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise _contract(
                "limit must be an int in 1..100",
                operation="financial_statements",
                rule="range",
            )
        symbol = self._require_us_equity(instrument)
        cik10 = await self._resolve_cik(symbol)
        payload = await self._fetch_companyfacts(cik10)
        facts = self._require_facts_root(payload)
        annual_only = frequency is USStatementFrequency.ANNUAL
        cap = _ANNUAL_MAX if annual_only else _QUARTERLY_MAX
        effective_limit = min(limit, cap)
        duration = self._extract_points(
            facts,
            aliases=_DURATION_ALIASES,
            as_of=as_of,
            want_instant=False,
            annual_only=annual_only,
        )
        instant = self._extract_points(
            facts,
            aliases=_INSTANT_ALIASES,
            as_of=as_of,
            want_instant=True,
            annual_only=annual_only,
        )
        # Join instants into duration groups for balance sheet only via separate path.
        # For balance sheet, group instants; for income/CF group durations alone.
        # Additionally: balance sheet instants may share group keys with duration
        # facts from same accession/end/fy/fp/filed — build BS from instants only.
        freq = frequency
        income = self._build_periods(
            statement_type=USStatementType.INCOME,
            frequency=freq,
            keys=_INCOME_KEYS,
            duration_points=duration,
            instant_points=(),
            limit=effective_limit,
            view=view,
        )
        cash_flow = self._build_periods(
            statement_type=USStatementType.CASH_FLOW,
            frequency=freq,
            keys=_CASH_FLOW_KEYS,
            duration_points=duration,
            instant_points=(),
            limit=effective_limit,
            view=view,
        )
        balance = self._build_periods(
            statement_type=USStatementType.BALANCE_SHEET,
            frequency=freq,
            keys=_BALANCE_KEYS,
            duration_points=(),
            instant_points=instant,
            limit=effective_limit,
            view=view,
        )
        statements = USFinancialStatements(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            frequency=freq,
            income=income,
            balance_sheet=balance,
            cash_flow=cash_flow,
            view=view,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=statements,
            meta=self._meta(
                category=DataCategory.FINANCIAL_STATEMENTS,
                as_of=as_of,
                fetched_at=fetched_at,
            ),
        )

    def _metrics_from_annual_group(
        self,
        gk: _GroupKey,
        lines: Mapping[str, Decimal],
    ) -> USFundamentalMetrics:
        revenue = lines.get("revenue")
        gross_profit = lines.get("gross_profit")
        net_income = lines.get("net_income")
        share_count = lines.get("shares_outstanding")
        sbc = lines.get("stock_based_compensation")
        capex = lines.get("capital_expenditure")
        ocf = lines.get("operating_cash_flow")
        fcf: Decimal | None = None
        if ocf is not None and capex is not None:
            fcf = ocf - capex
        # long_term_debt alone omits current debt; do not invent net cash/debt.
        filed_at = filed_visibility_utc(gk.filed)
        return USFundamentalMetrics(
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
            revenue=revenue,
            gross_profit=gross_profit,
            ebitda=None,
            net_income=net_income,
            profit_margin=None,
            operating_margin=None,
            roe=None,
            roa=None,
            debt_to_equity=None,
            current_ratio=None,
            revenue_growth=None,
            eps_growth=None,
            estimate_revision=None,
            share_count=share_count,
            stock_based_compensation=sbc,
            capital_expenditure=capex,
            free_cash_flow=fcf,
            net_cash_or_debt=None,
            period_end=gk.end,
            filed_at=filed_at,
            basis=USFundamentalBasis.ANNUAL,
        )

    async def get_fundamental_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USFundamentalSnapshot]:
        self._require_configured()
        self._require_as_of(as_of)
        _ = self._require_us_equity(instrument)
        symbol = instrument.symbol.strip().upper()
        cik10 = await self._resolve_cik(symbol)
        payload = await self._fetch_companyfacts(cik10)
        facts = self._require_facts_root(payload)
        legal_name = self._parse_entity_name(payload)
        profile: USCompanyProfile | None = None
        if legal_name is not None:
            profile = USCompanyProfile(
                instrument_id=instrument.instrument_id,
                legal_name=legal_name,
                description=None,
                sector=None,
                industry=None,
                country=None,
                website=None,
                employees=None,
                market_cap=None,
            )
        duration = self._extract_points(
            facts,
            aliases=_DURATION_ALIASES,
            as_of=as_of,
            want_instant=False,
            annual_only=True,
        )
        instant = self._extract_points(
            facts,
            aliases=_INSTANT_ALIASES,
            as_of=as_of,
            want_instant=True,
            annual_only=True,
        )
        merged_groups: dict[_GroupKey, dict[str, Decimal]] = {}
        for p in list(duration) + list(instant):
            gk = _GroupKey(
                accession=p.accession,
                end=p.end,
                fy=p.fy,
                fp=p.fp,
                filed=p.filed,
                form=p.form,
                start=None,
            )
            bucket = merged_groups.setdefault(gk, {})
            if p.key not in bucket:
                bucket[p.key] = p.value
        rows = self._select_periods(merged_groups, view=USStatementView.LATEST)
        metrics: USFundamentalMetrics | None = None
        if rows:
            metrics = self._metrics_from_annual_group(rows[0][0], rows[0][1])
        # SEC snapshot is intentionally partial (no valuations / current profile).
        snapshot = USFundamentalSnapshot(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            profile=profile,
            metrics=None,
            corporate_actions=(),
            degraded=True,
            warning_codes=(_WARN_PARTIAL,),
            reported_metrics=metrics,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=snapshot,
            meta=self._meta(
                category=DataCategory.FUNDAMENTALS,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=(_WARN_PARTIAL,),
            ),
        )
