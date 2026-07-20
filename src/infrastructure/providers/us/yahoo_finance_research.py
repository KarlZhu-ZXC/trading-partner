"""Yahoo Finance current fundamentals and dated corporate actions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Final
from urllib.parse import quote
from zoneinfo import ZoneInfo

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
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic
from domain.us_context.enums import USNewsScope
from domain.us_context.models import USNewsArticle, USNewsFeed
from domain.us_market.models import USBreadthSnapshot
from domain.us_research.enums import USCorporateActionType, USFundamentalBasis
from domain.us_research.models import (
    USCompanyProfile,
    USCorporateAction,
    USFundamentalMetrics,
    USFundamentalSnapshot,
)
from infrastructure.providers.us.yahoo_finance import YahooFinanceAdapter
from infrastructure.providers.us.yfinance_breadth_client import (
    YahooBreadthClient,
    YFinanceBreadthClient,
)
from infrastructure.providers.us.yfinance_fundamentals_client import (
    YahooFundamentalsClient,
    YFinanceFundamentalsClient,
)
from infrastructure.system.clock import SystemClock

_NY: Final = ZoneInfo("America/New_York")
_CHART_PREFIX: Final[str] = "https://query1.finance.yahoo.com/v8/finance/chart/"
_SEARCH_URL: Final[str] = "https://query1.finance.yahoo.com/v1/finance/search"
_SUPPORTED: Final = frozenset(
    {
        DataCategory.FUNDAMENTALS,
        DataCategory.CORPORATE_ACTIONS,
        DataCategory.NEWS,
        DataCategory.MARKET_BREADTH,
    }
)


def _contract(message: str, *, operation: str, rule: str) -> DataContractError:
    return DataContractError(
        message,
        details={"vendor": VendorId.YFINANCE.value, "operation": operation, "rule": rule},
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _contract(
                "Yahoo JSON contains duplicate keys", operation="decode", rule="duplicate_key"
            )
        result[key] = value
    return result


def _constant(_name: str) -> None:
    raise _contract("Yahoo JSON contains a non-finite value", operation="decode", rule="nonfinite")


def _loads(body: bytes) -> object:
    try:
        return json.loads(
            body.decode("utf-8"),
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_constant,
            object_pairs_hook=_pairs,
        )
    except DataContractError:
        raise
    except (UnicodeDecodeError, ValueError, TypeError):
        raise _contract(
            "Yahoo response is not valid JSON", operation="decode", rule="json"
        ) from None


def _decimal(value: object, *, field: str) -> Decimal | None:
    if isinstance(value, Mapping) and "raw" in value:
        value = value["raw"]
    if value is None:
        return None
    if type(value) is Decimal:
        return value if value.is_finite() else None
    if type(value) is int:
        return Decimal(value)
    if type(value) is float:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation:
            raise _contract(
                "Yahoo numeric field is invalid", operation="decode", rule=field
            ) from None
        return parsed if parsed.is_finite() else None
    raise _contract("Yahoo numeric field has invalid type", operation="decode", rule=field)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value: object, *, field: str) -> int | None:
    raw = _decimal(value, field=field)
    if raw is None:
        return None
    if raw != raw.to_integral_value() or raw < 0:
        raise _contract("Yahoo integer field is invalid", operation="decode", rule=field)
    return int(raw)


def _split_ratio(item: Mapping[str, object]) -> Decimal | None:
    numerator = _decimal(item.get("numerator"), field="split_numerator")
    denominator = _decimal(item.get("denominator"), field="split_denominator")
    if numerator is not None and denominator not in {None, Decimal(0)}:
        return numerator / denominator
    raw = item.get("splitRatio")
    if isinstance(raw, str) and ":" in raw:
        left, right = raw.split(":", 1)
        numerator = _decimal(left, field="split_ratio")
        denominator = _decimal(right, field="split_ratio")
        if numerator is not None and denominator not in {None, Decimal(0)}:
            return numerator / denominator
    return _decimal(raw, field="split_ratio")


class YahooFinanceResearchAdapter(YahooFinanceAdapter):
    """US equity research adapter; current facts never masquerade as history."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        breadth_timeout_seconds: float = 30.0,
        user_agent: str = "TradingPartner/1.0",
        max_fresh_seconds: int = 30,
        max_delayed_seconds: int = 900,
        fundamentals_client: YahooFundamentalsClient | None = None,
        breadth_client: YahooBreadthClient | None = None,
    ) -> None:
        super().__init__(
            transport,
            clock=clock,
            enabled=enabled,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
            max_fresh_seconds=max_fresh_seconds,
            max_delayed_seconds=max_delayed_seconds,
        )
        if transport is None:
            raise DataContractError("transport is required", details={"field": "transport"})
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be positive", details={"field": "timeout_seconds"}
            )
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise DataContractError("user_agent is required", details={"field": "user_agent"})
        self._transport = transport
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout = float(timeout_seconds)
        if breadth_timeout_seconds <= 0:
            raise DataContractError(
                "breadth_timeout_seconds must be positive",
                details={"field": "breadth_timeout_seconds"},
            )
        self._breadth_timeout = float(breadth_timeout_seconds)
        self._user_agent = user_agent.strip()
        self._fundamentals_client = fundamentals_client or YFinanceFundamentalsClient()
        self._breadth_client = breadth_client or YFinanceBreadthClient()

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.YFINANCE

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return super().supports(market, category) or (
            market is Market.US and category in _SUPPORTED
        )

    def is_configured(self) -> bool:
        return self._enabled

    def _instrument(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument", details={"field": "instrument"}
            )
        if instrument.market is not Market.US or instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "Yahoo research supports US equities only", details={"field": "instrument"}
            )
        return quote(instrument.symbol.upper(), safe=".-^")

    def _as_of(self, as_of: datetime, *, current_only: bool) -> datetime:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError("as_of must not be in the future", details={"field": "as_of"})
        if current_only and as_of.astimezone(_NY).date() != now.astimezone(_NY).date():
            raise NoMarketData(
                "Yahoo current fundamentals are unavailable for historical as_of",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "fundamentals",
                    "rule": "current_only",
                },
            )
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Yahoo research adapter is disabled", details={"vendor": self.vendor_id.value}
            )
        return now

    async def _get(
        self, url: str, params: Mapping[str, str], *, operation: str
    ) -> tuple[object, datetime]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params=params,
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                },
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        fetched_at = self._clock.now()
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Yahoo Finance rate limited",
                details={"vendor": self.vendor_id.value, "operation": operation},
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError(
                "Yahoo Finance HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "status_class": f"{response.status_code // 100}xx",
                },
            )
        content_type = response.headers.get("content-type") or response.headers.get("Content-Type")
        if not isinstance(content_type, str) or "json" not in content_type.casefold():
            raise _contract(
                "Yahoo response Content-Type is invalid", operation=operation, rule="content_type"
            )
        return _loads(response.body), fetched_at

    def _research_meta(
        self,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        warnings: tuple[str, ...] = (),
    ) -> ProviderResultMeta:
        try:
            session = infer_session_basic(Market.US, as_of, timezone="America/New_York")
        except DataContractError:
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

    async def get_market_breadth(self, *, as_of: datetime) -> ProviderSuccess[USBreadthSnapshot]:
        """Return current Yahoo aggregate breadth; never backfill historical requests."""
        self._as_of(as_of, current_only=True)
        snapshot = await self._breadth_client.get_current(timeout_seconds=self._breadth_timeout)
        warnings = ["YAHOO_BREADTH_UNOFFICIAL_UNIVERSE"]
        if len(snapshot.sector_rotation) < 11:
            warnings.append("US_SECTOR_ROTATION_PARTIAL")
        fetched_at = self._clock.now()
        meta = self._research_meta(
            DataCategory.MARKET_BREADTH,
            as_of,
            fetched_at,
            tuple(warnings),
        )
        return ProviderSuccess(value=snapshot, meta=meta)

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
        self._as_of(as_of, current_only=False)
        if instrument is not None:
            search = self._instrument(instrument)
            instrument_id = instrument.instrument_id
            scope = USNewsScope.COMPANY
        else:
            search = query.strip() if isinstance(query, str) else ""
            instrument_id = None
            scope = USNewsScope.GLOBAL
        if not search:
            raise DataContractError("query is required", details={"field": "query"})
        payload, fetched_at = await self._get(
            _SEARCH_URL,
            {
                "q": search,
                "quotesCount": "0",
                "newsCount": str(limit),
                "enableFuzzyQuery": "false",
            },
            operation="news",
        )
        if not isinstance(payload, Mapping):
            raise _contract(
                "Yahoo search payload has invalid shape",
                operation="news",
                rule="contract_drift",
            )
        rows = payload.get("news")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise _contract(
                "Yahoo search news has invalid shape",
                operation="news",
                rule="contract_drift",
            )
        articles: list[USNewsArticle] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise _contract(
                    "Yahoo news row has invalid shape",
                    operation="news",
                    rule="contract_drift",
                )
            article = self._news_article(row, instrument_id=instrument_id, scope=scope)
            if article is None or article.published_at > as_of:
                continue
            day = article.published_at.date()
            if (start is not None and day < start) or (end is not None and day > end):
                continue
            articles.append(article)
        unique = {item.dedupe_key: item for item in articles}
        ordered = tuple(
            sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:limit]
        )
        feed = USNewsFeed(instrument_id, query, as_of, ordered, False, ())
        return ProviderSuccess(
            feed,
            self._research_meta(DataCategory.NEWS, as_of, fetched_at),
        )

    def _news_article(
        self,
        row: Mapping[str, object],
        *,
        instrument_id: str | None,
        scope: USNewsScope,
    ) -> USNewsArticle | None:
        content = row.get("content")
        root = content if isinstance(content, Mapping) else row
        title = _text(root.get("title"))
        if title is None:
            return None
        raw_time = root.get("pubDate")
        published_at: datetime | None = None
        if isinstance(raw_time, str):
            try:
                published_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            timestamp = row.get("providerPublishTime")
            if type(timestamp) is int:
                published_at = datetime.fromtimestamp(timestamp, tz=_NY)
        if published_at is None or published_at.tzinfo is None:
            return None
        provider = root.get("provider")
        publisher = (
            _text(provider.get("displayName"))
            if isinstance(provider, Mapping)
            else _text(root.get("publisher"))
        )
        url_value = root.get("canonicalUrl") or root.get("clickThroughUrl")
        url = _text(url_value.get("url")) if isinstance(url_value, Mapping) else None
        url = url or _text(root.get("link"))
        seed = f"{title.casefold()}|{url or ''}"
        digest = sha256(seed.encode("utf-8")).hexdigest()
        raw_id = _text(root.get("id") or row.get("uuid"))
        return USNewsArticle(
            article_id=f"yahoo:{(raw_id or digest[:24])[:240]}",
            instrument_id=instrument_id,
            scope=scope,
            title=title[:500],
            summary=(text[:4_000] if (text := _text(root.get("summary"))) else None),
            publisher=publisher[:256] if publisher else None,
            url=url[:2_000] if url else None,
            published_at=published_at,
            vendor=self.vendor_id,
            source_sentiment=None,
            relevance=None,
            dedupe_key=digest,
        )

    async def get_fundamental_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USFundamentalSnapshot]:
        symbol = self._instrument(instrument)
        self._as_of(as_of, current_only=True)
        root = await self._fundamentals_client.get_info(symbol, timeout_seconds=self._timeout)
        fetched_at = self._clock.now()
        total_cash = _decimal(root.get("totalCash"), field="total_cash")
        total_debt = _decimal(root.get("totalDebt"), field="total_debt")
        net_cash_or_debt = (
            total_cash - total_debt if total_cash is not None and total_debt is not None else None
        )
        instrument_id = instrument.instrument_id
        profile_values = (
            _text(root.get("longName") or root.get("shortName")),
            _text(root.get("longBusinessSummary")),
            _text(root.get("sector")),
            _text(root.get("industry")),
            _text(root.get("country")),
            _text(root.get("website")),
            _integer(root.get("fullTimeEmployees"), field="employees"),
            _decimal(root.get("marketCap"), field="market_cap"),
        )
        profile = (
            USCompanyProfile(instrument_id, *profile_values)
            if any(value is not None for value in profile_values)
            else None
        )
        metrics = USFundamentalMetrics(
            trailing_pe=_decimal(root.get("trailingPE"), field="trailing_pe"),
            forward_pe=_decimal(root.get("forwardPE"), field="forward_pe"),
            peg_ratio=_decimal(
                root.get("trailingPegRatio") or root.get("pegRatio"), field="peg_ratio"
            ),
            price_to_book=_decimal(root.get("priceToBook"), field="price_to_book"),
            price_to_sales=_decimal(
                root.get("priceToSalesTrailing12Months"), field="price_to_sales"
            ),
            enterprise_to_ebitda=_decimal(
                root.get("enterpriseToEbitda"), field="enterprise_to_ebitda"
            ),
            dividend_yield=_decimal(root.get("dividendYield"), field="dividend_yield"),
            beta=_decimal(root.get("beta"), field="beta"),
            eps_ttm=_decimal(root.get("trailingEps"), field="eps_ttm"),
            eps_forward=_decimal(root.get("forwardEps"), field="eps_forward"),
            book_value_per_share=_decimal(root.get("bookValue"), field="book_value_per_share"),
            revenue_per_share=_decimal(root.get("revenuePerShare"), field="revenue_per_share"),
            revenue=_decimal(root.get("totalRevenue"), field="revenue"),
            gross_profit=_decimal(root.get("grossProfits"), field="gross_profit"),
            ebitda=_decimal(root.get("ebitda"), field="ebitda"),
            net_income=_decimal(root.get("netIncomeToCommon"), field="net_income"),
            profit_margin=_decimal(root.get("profitMargins"), field="profit_margin"),
            operating_margin=_decimal(root.get("operatingMargins"), field="operating_margin"),
            roe=_decimal(root.get("returnOnEquity"), field="roe"),
            roa=_decimal(root.get("returnOnAssets"), field="roa"),
            debt_to_equity=_decimal(root.get("debtToEquity"), field="debt_to_equity"),
            current_ratio=_decimal(root.get("currentRatio"), field="current_ratio"),
            revenue_growth=_decimal(root.get("revenueGrowth"), field="revenue_growth"),
            eps_growth=_decimal(root.get("earningsGrowth"), field="eps_growth"),
            estimate_revision=None,
            share_count=_decimal(root.get("sharesOutstanding"), field="share_count"),
            stock_based_compensation=None,
            capital_expenditure=None,
            free_cash_flow=_decimal(root.get("freeCashflow"), field="free_cash_flow"),
            net_cash_or_debt=net_cash_or_debt,
            period_end=None,
            filed_at=None,
            basis=USFundamentalBasis.CURRENT,
        )
        meaningful = sum(
            value is not None
            for value in (
                metrics.trailing_pe,
                metrics.revenue,
                metrics.ebitda,
                metrics.free_cash_flow,
                metrics.share_count,
            )
        )
        degraded = profile is None or meaningful < 2
        warnings = ("YAHOO_FUNDAMENTALS_PARTIAL",) if degraded else ()
        snapshot = USFundamentalSnapshot(
            instrument_id, as_of, profile, metrics, (), degraded, warnings
        )
        return ProviderSuccess(
            snapshot,
            self._research_meta(DataCategory.FUNDAMENTALS, as_of, fetched_at, warnings),
        )

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USCorporateAction, ...]]:
        symbol = self._instrument(instrument)
        self._as_of(as_of, current_only=False)
        cutoff = min(end or as_of.astimezone(_NY).date(), as_of.astimezone(_NY).date())
        lower = start or date(1970, 1, 1)
        if lower > cutoff:
            return ProviderSuccess(
                (),
                self._research_meta(DataCategory.CORPORATE_ACTIONS, as_of, self._clock.now()),
            )
        period1 = int(datetime(lower.year, lower.month, lower.day, tzinfo=_NY).timestamp())
        after = cutoff + timedelta(days=1)
        period2 = int(datetime(after.year, after.month, after.day, tzinfo=_NY).timestamp())
        payload, fetched_at = await self._get(
            f"{_CHART_PREFIX}{symbol}",
            {
                "period1": str(period1),
                "period2": str(period2),
                "interval": "1d",
                "events": "div,splits",
            },
            operation="corporate_actions",
        )
        result = self._research_chart_result(payload)
        events = result.get("events")
        if events is None:
            actions: tuple[USCorporateAction, ...] = ()
        elif not isinstance(events, Mapping):
            raise _contract(
                "Yahoo events has invalid shape",
                operation="corporate_actions",
                rule="contract_drift",
            )
        else:
            parsed: list[USCorporateAction] = []
            for name, action_type in (
                ("dividends", USCorporateActionType.DIVIDEND),
                ("splits", USCorporateActionType.SPLIT),
            ):
                group = events.get(name)
                if group is None:
                    continue
                if not isinstance(group, Mapping):
                    raise _contract(
                        "Yahoo event group has invalid shape",
                        operation="corporate_actions",
                        rule="contract_drift",
                    )
                for item in group.values():
                    if not isinstance(item, Mapping):
                        raise _contract(
                            "Yahoo event has invalid shape",
                            operation="corporate_actions",
                            rule="contract_drift",
                        )
                    timestamp = item.get("date")
                    if type(timestamp) is not int:
                        raise _contract(
                            "Yahoo event date is invalid",
                            operation="corporate_actions",
                            rule="event_date",
                        )
                    day = datetime.fromtimestamp(timestamp, tz=_NY).date()
                    if day < lower or day > cutoff:
                        continue
                    ratio = None
                    if action_type is USCorporateActionType.SPLIT:
                        ratio = _split_ratio(item)
                    parsed.append(
                        USCorporateAction(
                            instrument_id=instrument.instrument_id,
                            action_type=action_type,
                            effective_date=day,
                            declared_date=None,
                            paid_date=None,
                            amount=_decimal(item.get("amount"), field="dividend_amount")
                            if action_type is USCorporateActionType.DIVIDEND
                            else None,
                            ratio=ratio,
                            currency=instrument.currency
                            if action_type is USCorporateActionType.DIVIDEND
                            else None,
                            shares=None,
                            description=_text(item.get("splitRatio"))
                            if action_type is USCorporateActionType.SPLIT
                            else None,
                        )
                    )
            unique = {(a.action_type.value, a.effective_date, a.amount, a.ratio): a for a in parsed}
            actions = tuple(
                sorted(
                    unique.values(),
                    key=lambda a: (a.effective_date or date.min, a.action_type.value),
                    reverse=True,
                )
            )
        return ProviderSuccess(
            actions,
            self._research_meta(DataCategory.CORPORATE_ACTIONS, as_of, fetched_at),
        )

    def _research_chart_result(self, payload: object) -> Mapping[str, object]:
        if not isinstance(payload, Mapping) or not isinstance(payload.get("chart"), Mapping):
            raise _contract(
                "Yahoo chart payload has invalid shape",
                operation="corporate_actions",
                rule="contract_drift",
            )
        chart = payload["chart"]
        assert isinstance(chart, Mapping)
        if chart.get("error") is not None:
            raise NoMarketData(
                "Yahoo returned no corporate actions",
                details={"vendor": self.vendor_id.value, "operation": "corporate_actions"},
            )
        results = chart.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise NoMarketData(
                "Yahoo returned no corporate actions",
                details={"vendor": self.vendor_id.value, "operation": "corporate_actions"},
            )
        return results[0]
