"""Alpha Vantage fallback for current US research facts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Final
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpTransport
from domain.common.enums import (
    AssetType,
    DataCategory,
    Market,
    TradingSession,
)
from domain.common.errors import DataContractError, NoMarketData
from domain.instruments.models import Instrument
from domain.us_context.enums import USNewsScope
from domain.us_context.models import USNewsArticle, USNewsFeed
from domain.us_research.enums import (
    USFundamentalBasis,
    USInsiderAcquiredDisposed,
    USStatementFrequency,
    USStatementType,
)
from domain.us_research.models import (
    USCompanyProfile,
    USFinancialStatements,
    USFundamentalMetrics,
    USFundamentalSnapshot,
    USInsiderTransaction,
    USStatementPeriod,
)
from infrastructure.providers.us.alpha_vantage import (
    AlphaVantageAdapter,
    _optional_decimal,
)

_NY: Final = ZoneInfo("America/New_York")
_SUPPORTED: Final = frozenset(
    {
        DataCategory.FUNDAMENTALS,
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.INSIDER_ACTIVITY,
        DataCategory.NEWS,
    }
)
_FUNCTIONS: Final = {
    USStatementType.INCOME: "INCOME_STATEMENT",
    USStatementType.BALANCE_SHEET: "BALANCE_SHEET",
    USStatementType.CASH_FLOW: "CASH_FLOW",
}
_LINE_KEYS: Final[Mapping[USStatementType, Mapping[str, str]]] = {
    USStatementType.INCOME: {
        "totalRevenue": "revenue",
        "grossProfit": "gross_profit",
        "operatingIncome": "operating_income",
        "netIncome": "net_income",
        "ebitda": "ebitda",
        "researchAndDevelopment": "research_and_development",
        "incomeTaxExpense": "income_tax_expense",
    },
    USStatementType.BALANCE_SHEET: {
        "cashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
        "shortTermInvestments": "short_term_investments",
        "currentNetReceivables": "accounts_receivable",
        "inventory": "inventory",
        "totalCurrentAssets": "current_assets",
        "totalAssets": "total_assets",
        "currentAccountsPayable": "accounts_payable",
        "totalCurrentLiabilities": "current_liabilities",
        "longTermDebt": "long_term_debt",
        "totalLiabilities": "total_liabilities",
        "totalShareholderEquity": "stockholders_equity",
        "commonStockSharesOutstanding": "shares_outstanding",
    },
    USStatementType.CASH_FLOW: {
        "operatingCashflow": "operating_cash_flow",
        "capitalExpenditures": "capital_expenditure",
        "cashflowFromInvestment": "investing_cash_flow",
        "cashflowFromFinancing": "financing_cash_flow",
        "dividendPayout": "dividends_paid",
        "paymentsForRepurchaseOfCommonStock": "share_repurchases",
        "stockBasedCompensation": "stock_based_compensation",
    },
}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return None if not value or value.casefold() in {"none", "null", "-"} else value


def _number(value: object, *, field: str) -> Decimal | None:
    text = _text(value)
    return _optional_decimal(text, field=field) if text is not None else None


def _day(value: object, *, field: str) -> date:
    text = _text(value)
    if text is None:
        raise DataContractError(f"{field} is required", details={"field": field})
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise DataContractError(f"{field} must be YYYY-MM-DD", details={"field": field}) from None


class AlphaVantageResearchAdapter(AlphaVantageAdapter):
    """Research-capable Alpha adapter; all endpoints are fallback quality."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_key: str | None = None,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        max_fresh_seconds: int = 30,
        max_delayed_seconds: int = 900,
    ) -> None:
        super().__init__(
            transport,
            api_key=api_key,
            clock=clock,
            enabled=enabled,
            timeout_seconds=timeout_seconds,
            max_fresh_seconds=max_fresh_seconds,
            max_delayed_seconds=max_delayed_seconds,
        )

    def supports(self, market: Market, category: DataCategory) -> bool:
        return super().supports(market, category) or (
            market is Market.US and category in _SUPPORTED
        )

    def _equity(self, instrument: Instrument) -> str:
        symbol = self._require_us_instrument(instrument)
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "Alpha research supports US equities only",
                details={"field": "instrument", "rule": "asset_type"},
            )
        return symbol

    def _current(self, as_of: datetime) -> datetime:
        self._require_configured()
        now = self._require_as_of(as_of)
        if as_of.astimezone(_NY).date() != now.astimezone(_NY).date():
            raise NoMarketData(
                "Alpha research cannot prove historical visibility",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "research",
                    "rule": "current_only",
                },
            )
        return now

    def _research_meta(
        self, category: DataCategory, as_of: datetime, fetched_at: datetime
    ) -> ProviderResultMeta:
        return self._meta(
            category=category,
            as_of=as_of,
            fetched_at=fetched_at,
            session=TradingSession.UNKNOWN,
            data_timestamp=None,
            adjustment=None,
        )

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        query: str | None,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[USNewsFeed]:
        self._require_configured()
        self._require_as_of(as_of)
        cutoff_day = min(end or as_of.date(), as_of.date())
        start_day = start or (cutoff_day - timedelta(days=30))
        if start_day > cutoff_day:
            return ProviderSuccess(
                USNewsFeed(
                    instrument.instrument_id if instrument else None,
                    query,
                    as_of,
                    (),
                    False,
                    (),
                ),
                self._research_meta(DataCategory.NEWS, as_of, self._clock.now()),
            )
        params = {
            "function": "NEWS_SENTIMENT",
            "time_from": datetime.combine(start_day, time.min).strftime("%Y%m%dT%H%M"),
            "time_to": datetime.combine(cutoff_day, time.max).strftime("%Y%m%dT%H%M"),
            "limit": str(limit),
            "sort": "LATEST",
        }
        symbol: str | None = None
        if instrument is not None:
            symbol = self._equity(instrument)
            params["tickers"] = symbol
        else:
            params["topics"] = "financial_markets,economy_macro,economy_monetary"
        payload, fetched_at = await self._fetch(params=params, operation="news")
        rows = payload.get("feed")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise DataContractError(
                "Alpha news feed has invalid shape",
                details={"vendor": self.vendor_id.value, "operation": "news"},
            )
        parsed: list[USNewsArticle] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise DataContractError(
                    "Alpha news row has invalid shape",
                    details={"vendor": self.vendor_id.value, "operation": "news"},
                )
            article = self._news_article(
                row,
                instrument_id=instrument.instrument_id if instrument else None,
                symbol=symbol,
            )
            if article is None or article.published_at > as_of:
                continue
            day = article.published_at.date()
            if day < start_day or day > cutoff_day:
                continue
            if query and instrument is None:
                haystack = f"{article.title} {article.summary or ''}".casefold()
                if query.casefold() not in haystack:
                    continue
            parsed.append(article)
        unique = {item.dedupe_key: item for item in parsed}
        articles = tuple(
            sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:limit]
        )
        feed = USNewsFeed(
            instrument.instrument_id if instrument else None,
            query,
            as_of,
            articles,
            False,
            (),
        )
        return ProviderSuccess(
            feed,
            self._research_meta(DataCategory.NEWS, as_of, fetched_at),
        )

    def _news_article(
        self,
        row: Mapping[str, object],
        *,
        instrument_id: str | None,
        symbol: str | None,
    ) -> USNewsArticle | None:
        title = _text(row.get("title"))
        raw_time = _text(row.get("time_published"))
        if title is None or raw_time is None:
            return None
        try:
            published_at = datetime.strptime(raw_time, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None
        relevance: Decimal | None = None
        ticker_rows = row.get("ticker_sentiment")
        if symbol is not None and isinstance(ticker_rows, list):
            for ticker_row in ticker_rows:
                if not isinstance(ticker_row, Mapping):
                    continue
                ticker = _text(ticker_row.get("ticker"))
                if ticker and ticker.casefold() == symbol.casefold():
                    relevance = _number(ticker_row.get("relevance_score"), field="relevance_score")
                    break
        sentiment = _number(row.get("overall_sentiment_score"), field="sentiment_score")
        url = _text(row.get("url"))
        digest = sha256(f"{title.casefold()}|{url or ''}".encode()).hexdigest()
        return USNewsArticle(
            article_id=f"alpha:{digest[:24]}",
            instrument_id=instrument_id,
            scope=USNewsScope.COMPANY if instrument_id else USNewsScope.GLOBAL,
            title=title[:500],
            summary=(text[:4_000] if (text := _text(row.get("summary"))) else None),
            publisher=(source[:256] if (source := _text(row.get("source"))) else None),
            url=url[:2_000] if url else None,
            published_at=published_at,
            vendor=self.vendor_id,
            source_sentiment=sentiment,
            relevance=relevance,
            dedupe_key=digest,
        )

    async def get_fundamental_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USFundamentalSnapshot]:
        symbol = self._equity(instrument)
        self._current(as_of)
        payload, fetched_at = await self._fetch(
            params={"function": "OVERVIEW", "symbol": symbol},
            operation="overview",
        )
        if not payload or _text(payload.get("Symbol")) is None:
            raise NoMarketData(
                "Alpha Vantage returned no company overview",
                details={"vendor": self.vendor_id.value, "operation": "overview"},
            )
        iid = instrument.instrument_id
        profile = USCompanyProfile(
            instrument_id=iid,
            legal_name=_text(payload.get("Name")),
            description=_text(payload.get("Description")),
            sector=_text(payload.get("Sector")),
            industry=_text(payload.get("Industry")),
            country=_text(payload.get("Country")),
            website=None,
            employees=None,
            market_cap=_number(payload.get("MarketCapitalization"), field="market_cap"),
        )
        metrics = USFundamentalMetrics(
            trailing_pe=_number(payload.get("PERatio"), field="trailing_pe"),
            forward_pe=_number(payload.get("ForwardPE"), field="forward_pe"),
            peg_ratio=_number(payload.get("PEGRatio"), field="peg_ratio"),
            price_to_book=_number(payload.get("PriceToBookRatio"), field="price_to_book"),
            price_to_sales=_number(payload.get("PriceToSalesRatioTTM"), field="price_to_sales"),
            enterprise_to_ebitda=_number(payload.get("EVToEBITDA"), field="enterprise_to_ebitda"),
            dividend_yield=_number(payload.get("DividendYield"), field="dividend_yield"),
            beta=_number(payload.get("Beta"), field="beta"),
            eps_ttm=_number(payload.get("EPS"), field="eps_ttm"),
            eps_forward=None,
            book_value_per_share=_number(payload.get("BookValue"), field="book_value_per_share"),
            revenue_per_share=_number(payload.get("RevenuePerShareTTM"), field="revenue_per_share"),
            revenue=_number(payload.get("RevenueTTM"), field="revenue"),
            gross_profit=_number(payload.get("GrossProfitTTM"), field="gross_profit"),
            ebitda=_number(payload.get("EBITDA"), field="ebitda"),
            net_income=None,
            profit_margin=_number(payload.get("ProfitMargin"), field="profit_margin"),
            operating_margin=_number(payload.get("OperatingMarginTTM"), field="operating_margin"),
            roe=_number(payload.get("ReturnOnEquityTTM"), field="roe"),
            roa=_number(payload.get("ReturnOnAssetsTTM"), field="roa"),
            debt_to_equity=None,
            current_ratio=None,
            revenue_growth=_number(
                payload.get("QuarterlyRevenueGrowthYOY"), field="revenue_growth"
            ),
            eps_growth=_number(payload.get("QuarterlyEarningsGrowthYOY"), field="eps_growth"),
            estimate_revision=None,
            share_count=_number(payload.get("SharesOutstanding"), field="share_count"),
            stock_based_compensation=None,
            capital_expenditure=None,
            free_cash_flow=None,
            net_cash_or_debt=None,
            period_end=None,
            filed_at=None,
            basis=USFundamentalBasis.CURRENT,
        )
        snapshot = USFundamentalSnapshot(
            instrument_id=iid,
            as_of=as_of,
            profile=profile,
            metrics=metrics,
            corporate_actions=(),
            degraded=True,
            warning_codes=("ALPHA_FUNDAMENTALS_FALLBACK",),
        )
        return ProviderSuccess(
            snapshot,
            self._research_meta(DataCategory.FUNDAMENTALS, as_of, fetched_at),
        )

    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        frequency: USStatementFrequency,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[USFinancialStatements]:
        symbol = self._equity(instrument)
        self._current(as_of)
        if not isinstance(frequency, USStatementFrequency):
            raise DataContractError("frequency is invalid", details={"field": "frequency"})
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError("limit must be positive", details={"field": "limit"})
        report_key = (
            "quarterlyReports" if frequency is USStatementFrequency.QUARTERLY else "annualReports"
        )
        effective_limit = min(limit, 8 if frequency is USStatementFrequency.QUARTERLY else 5)
        groups: dict[USStatementType, tuple[USStatementPeriod, ...]] = {}
        fetched_at: datetime | None = None
        for statement_type, function in _FUNCTIONS.items():
            payload, fetched = await self._fetch(
                params={"function": function, "symbol": symbol},
                operation=function.casefold(),
            )
            fetched_at = fetched
            reports = payload.get(report_key)
            if reports is None:
                groups[statement_type] = ()
                continue
            if not isinstance(reports, list):
                raise DataContractError(
                    "Alpha statement reports must be a list",
                    details={"field": report_key, "rule": "contract_drift"},
                )
            periods: list[USStatementPeriod] = []
            for report in reports:
                if len(periods) >= effective_limit:
                    break
                if not isinstance(report, Mapping):
                    raise DataContractError(
                        "Alpha statement report must be an object",
                        details={"field": report_key, "rule": "contract_drift"},
                    )
                period_end = _day(report.get("fiscalDateEnding"), field="period_end")
                items = tuple(
                    (normalized, _number(report.get(raw), field=normalized))
                    for raw, normalized in _LINE_KEYS[statement_type].items()
                )
                periods.append(
                    USStatementPeriod(
                        statement_type=statement_type,
                        frequency=frequency,
                        fiscal_year=period_end.year,
                        fiscal_period=None,
                        period_end=period_end,
                        filed_at=None,
                        currency=_text(report.get("reportedCurrency")),
                        line_items=items,
                    )
                )
            groups[statement_type] = tuple(periods)
        if not any(groups.values()):
            raise NoMarketData(
                "Alpha Vantage returned no financial statements",
                details={"vendor": self.vendor_id.value, "operation": "statements"},
            )
        assert fetched_at is not None
        statements = USFinancialStatements(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            frequency=frequency,
            income=groups[USStatementType.INCOME],
            balance_sheet=groups[USStatementType.BALANCE_SHEET],
            cash_flow=groups[USStatementType.CASH_FLOW],
        )
        return ProviderSuccess(
            statements,
            self._research_meta(DataCategory.FINANCIAL_STATEMENTS, as_of, fetched_at),
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
        symbol = self._equity(instrument)
        self._current(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError("limit must be positive", details={"field": "limit"})
        payload, fetched_at = await self._fetch(
            params={"function": "INSIDER_TRANSACTIONS", "symbol": symbol},
            operation="insider_transactions",
        )
        rows = payload.get("data")
        if rows is None:
            rows = payload.get("transactions")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise DataContractError(
                "Alpha insider data must be a list",
                details={"field": "data", "rule": "contract_drift"},
            )
        cutoff = min(end or as_of.astimezone(_NY).date(), as_of.astimezone(_NY).date())
        parsed: list[USInsiderTransaction] = []
        for row in rows:
            if len(parsed) >= limit:
                break
            if not isinstance(row, Mapping):
                raise DataContractError(
                    "Alpha insider row must be an object",
                    details={"field": "data", "rule": "contract_drift"},
                )
            transaction_date = _day(row.get("transaction_date"), field="transaction_date")
            if transaction_date > cutoff or (start is not None and transaction_date < start):
                continue
            direction = _text(row.get("acquisition_or_disposal"))
            owner_name = _text(row.get("executive"))
            if owner_name is None:
                raise DataContractError(
                    "Alpha insider row is missing executive",
                    details={"field": "executive", "rule": "required"},
                )
            acquired_disposed = None
            if direction is not None:
                acquired_disposed = (
                    USInsiderAcquiredDisposed.ACQUIRED
                    if direction.upper().startswith("A")
                    else USInsiderAcquiredDisposed.DISPOSED
                    if direction.upper().startswith("D")
                    else None
                )
            parsed.append(
                USInsiderTransaction(
                    instrument_id=instrument.instrument_id,
                    owner_name=owner_name,
                    relationship=_text(row.get("executive_title")),
                    transaction_date=transaction_date,
                    filed_at=None,
                    accepted_at=None,
                    transaction_code=None,
                    acquired_disposed=acquired_disposed,
                    shares=_number(row.get("shares"), field="shares"),
                    price=_number(row.get("share_price"), field="price"),
                    post_transaction_shares=None,
                    is_direct=None,
                    rule_10b5_1=None,
                )
            )
        return ProviderSuccess(
            tuple(parsed),
            self._research_meta(DataCategory.INSIDER_ACTIVITY, as_of, fetched_at),
        )
