"""Eastmoney A-share adapter (Phase 1E E2 + E3 + E4a capital + E4b limit/sentiment).

E2 capabilities: quote, OHLCV bars, order book, ticks, industry performance,
market board. All transport calls go through ``EastmoneyRequestGate``.

E3 capabilities: fundamentals, F10, financial-statement fallback, corporate
actions (unlock/dividend), report search, consensus, company/market news.

E4a capital methods include fund flow, northbound fallback, dragon tiger,
margin, block trades, and shareholder counts. Chip distribution remains
protocol-compatible but fails closed until an allowlisted upstream contract
is verified (no fabricated report names).

E4b: push2ex four limit pools (limit-up / broken / limit-down / previous
limit-up via live-verified getYesterdayZTPool), stockrank hot list
(``eastmoney_hot``), and instrument-scoped stockrank concept-hit counts
(``concept_heat``). Concept hits are not a global concept leaderboard.

Volume fields (quote f47, kline f56, book ladder, tick volume) are lots (手);
domain stores volume_shares = lots * 100 for EQUITY/ETF. INDEX does not invent
share volumes.

Market board is a multi-endpoint composition of allowlisted E2 endpoints only:
  - clist equity universe (breadth + turnover)
  - push2ex limit pools (limit-up / limit-down / broken)
  - clist industry board (industries rows)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from domain.a_share.current_clist_policy import require_current_clist_trade_date
from domain.a_share.enums import (
    BarInterval,
    FinancialStatementType,
    LimitPoolType,
    SentimentSourceType,
    TickDirection,
)
from domain.a_share.models import (
    AnalystReportItem,
    AShareBar,
    AShareQuote,
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    ConsensusEstimate,
    DividendRecord,
    DragonTigerRecord,
    DragonTigerSeat,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    FundFlowPoint,
    IndustryPerformanceRow,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    MarginRecord,
    MarketBoardSnapshot,
    NewsItem,
    NorthboundFlowPoint,
    OrderBookLevel,
    SentimentSignal,
    ShareholderCountRecord,
    TradeTick,
    UnlockRecord,
    validate_order_book_levels,
)
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    PartialDataError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.freshness import classify_freshness
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    SHANGHAI,
    combine_shanghai_date_time,
    content_type_matches,
    decimal_from_text,
    eastmoney_secid,
    first_day_of_month,
    instrument_id_from_code,
    int_from_text,
    loads_json_decimal,
    lots_to_shares,
    parse_shanghai_date,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_decimal,
    require_exact_date,
    require_int,
    sanitize_public_url,
    week_period_start,
)
from infrastructure.providers.a_share.chip_distribution import ChipInputBar, derive_tp_chip_v1
from infrastructure.providers.a_share.eastmoney_gate import EastmoneyRequestGate
from infrastructure.system.clock import SystemClock

_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_DETAILS_URL = "https://push2.eastmoney.com/api/qt/stock/details/get"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# Public push2ex path casing (allowlist match is case-insensitive).
_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_DT_POOL_URL = "https://push2ex.eastmoney.com/getTopicDTPool"
_ZB_POOL_URL = "https://push2ex.eastmoney.com/getTopicZBPool"
# Live-verified 2026-07-17 previous limit-up pool (getLastZTPool is live 404).
_YESTERDAY_ZT_POOL_URL = "https://push2ex.eastmoney.com/getYesterdayZTPool"
# E3 frozen hosts (§20).
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"
_NEWS_LIST_URL = "https://np-weblist.eastmoney.com/getFastNewsList"
# Capital fund-flow (live-verified stock/fflow paths under §20 host family).
_INTRADAY_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_DAILY_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
# Live-verified 2026-07-17 stockrank hot list (POST JSON, empty body ok).
_STOCKRANK_ALL_CURRENT_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
_STOCKRANK_CONCEPT_HEAT_URL = "https://emappdata.eastmoney.com/stockrank/getHotStockRankList"

_POOL_URL_BY_TYPE: Mapping[LimitPoolType, str] = {
    LimitPoolType.LIMIT_UP: _ZT_POOL_URL,
    LimitPoolType.BROKEN_LIMIT: _ZB_POOL_URL,
    LimitPoolType.LIMIT_DOWN: _DT_POOL_URL,
    LimitPoolType.PREVIOUS_LIMIT_UP: _YESTERDAY_ZT_POOL_URL,
}

_POOL_SORT_BY_TYPE: Mapping[LimitPoolType, str] = {
    LimitPoolType.LIMIT_UP: "fbt:asc",
    LimitPoolType.BROKEN_LIMIT: "fbt:asc",
    LimitPoolType.LIMIT_DOWN: "fund:asc",
    LimitPoolType.PREVIOUS_LIMIT_UP: "fbt:asc",
}

# Eastmoney ``m`` market field → instrument suffix (live-observed).
_EM_M_TO_SUFFIX: Mapping[int, str] = {
    0: "SZ",
    1: "SH",
}

# Northbound mutual-type → domain channel (northbound only; skip southbound).
_NORTHBOUND_MUTUAL_TYPES: Mapping[str, str] = {
    "001": "sh",
    "002": "sz",
    "005": "total",
}

# Mutual amounts from RPT_MUTUAL_DEAL_HISTORY are in 百万元 (million CNY).
_MILLION_CNY = Decimal("1000000")

# Full A-share equity universe on Eastmoney clist. Order frozen for request
# identity; do not silently drop or broaden boards.
#
# Live probing showed standalone ``m:0+t:81`` and ``m:0+t:7`` return a mixed
# instrument bag (~12,445 rows) that exceeds the hard ceiling. The frozen filter
# is exact:
#   m:0+t:6          — Shenzhen main (selected Eastmoney segment behavior also
#                      covers GEM / ChiNext membership in this board layout)
#   m:0+t:80         — Shenzhen SME board segment retained by Eastmoney fs
#   m:1+t:2          — Shanghai main
#   m:1+t:23         — STAR
#   m:0+t:81+s:2048  — BSE restricted by s:2048 (not bare t:81 / t:7)
#
# Forbidden segments (must never reappear standalone): m:0+t:81, m:0+t:7.
EASTMONEY_A_SHARE_EQUITY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
EASTMONEY_INDUSTRY_BOARD_FS = "m:90+t:2"

# Large page size keeps production latency reasonable (~1 request for full A-share).
_CLIST_EQUITY_PAGE_SIZE = 5000
_CLIST_EQUITY_MAX_TOTAL = 12_000
_CLIST_EQUITY_MAX_PAGES = 8
_CLIST_INDUSTRY_PAGE_SIZE = 500
_CLIST_INDUSTRY_MAX_TOTAL = 1_000
_CLIST_INDUSTRY_MAX_PAGES = 4

# Public static routing identifiers for push2ex pools — NOT credentials.
# Never log the full query string in errors.
_PUSH2EX_UT = "7eea3edcaed734bea9cbfc24409ed989"
_PUSH2EX_DPT = "wz.ztzt"
_PUSH2EX_POOL_PAGE_SIZE = "10000"

_QUOTE_FIELDS = (
    "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f71,"
    "f86,f116,f117,f162,f167,f168,f169,f170,"
    "f19,f20,f17,f18,f15,f16,f13,f14,f11,f12,"
    "f39,f40,f37,f38,f35,f36,f33,f34,f31,f32"
)

_KLT_BY_INTERVAL: Mapping[BarInterval, str] = {
    BarInterval.ONE_MINUTE: "1",
    BarInterval.FIVE_MINUTES: "5",
    BarInterval.FIFTEEN_MINUTES: "15",
    BarInterval.THIRTY_MINUTES: "30",
    BarInterval.SIXTY_MINUTES: "60",
    BarInterval.ONE_DAY: "101",
    BarInterval.ONE_WEEK: "102",
    BarInterval.ONE_MONTH: "103",
}

_FQT_BY_ADJUSTMENT: Mapping[AdjustmentMethod, str] = {
    AdjustmentMethod.NONE: "0",
    AdjustmentMethod.FORWARD_ADJUSTED: "1",
    AdjustmentMethod.BACKWARD_ADJUSTED: "2",
}

_SUPPORTED_CATEGORIES = frozenset(
    {
        DataCategory.MARKET_QUOTE,
        DataCategory.MARKET_OHLCV,
        DataCategory.MARKET_STRUCTURE,
        DataCategory.FUNDAMENTALS,
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.CORPORATE_ACTIONS,
        DataCategory.RESEARCH_REPORTS,
        DataCategory.NEWS,
        DataCategory.CAPITAL,
        DataCategory.LIMIT_UP,
        DataCategory.SENTIMENT,
    }
)

_STATEMENT_REPORT_NAMES: Mapping[FinancialStatementType, str] = {
    FinancialStatementType.BALANCE_SHEET: "RPT_DMSK_FN_BALANCE",
    FinancialStatementType.INCOME_STATEMENT: "RPT_DMSK_FN_INCOME",
    FinancialStatementType.CASH_FLOW: "RPT_DMSK_FN_CASHFLOW",
}

_F10_SECTION_REPORTS: Mapping[str, str] = {
    "company": "RPT_F10_ORG_INFO",
    "business": "RPT_F10_MAINOP",
    "holders": "RPT_F10_HOLDERNUM",
}

_JSON_CONTENT = ("application/json", "text/json", "text/plain")


class EastmoneyAShareAdapter:
    """CategoryProvider for quote + OHLCV + market structure operations."""

    def __init__(
        self,
        transport: HttpTransport,
        gate: EastmoneyRequestGate,
        *,
        calendar: AShareTradingCalendar,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        current_window_seconds: int = 300,
        max_fresh_seconds: int = 15,
        max_delayed_seconds: int = 120,
    ) -> None:
        if not isinstance(gate, EastmoneyRequestGate):
            raise DataContractError(
                "gate must be EastmoneyRequestGate",
                details={"field": "gate", "rule": "type"},
            )
        if calendar is None:
            raise DataContractError(
                "calendar is required for EastmoneyAShareAdapter",
                details={"field": "calendar", "rule": "required"},
            )
        for attr in ("is_trading_day", "previous_trading_day", "sessions_for"):
            if not callable(getattr(calendar, attr, None)):
                raise DataContractError(
                    "calendar must implement AShareTradingCalendar",
                    details={"field": "calendar", "rule": "protocol", "missing": attr},
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
        if (
            not isinstance(current_window_seconds, int)
            or isinstance(current_window_seconds, bool)
            or current_window_seconds < 0
        ):
            raise DataContractError(
                "current_window_seconds must be a nonnegative exact int",
                details={"field": "current_window_seconds", "rule": "nonnegative"},
            )
        if (
            not isinstance(max_fresh_seconds, int)
            or isinstance(max_fresh_seconds, bool)
            or max_fresh_seconds < 0
        ):
            raise DataContractError(
                "max_fresh_seconds must be a nonnegative int",
                details={"field": "max_fresh_seconds", "rule": "nonnegative"},
            )
        if (
            not isinstance(max_delayed_seconds, int)
            or isinstance(max_delayed_seconds, bool)
            or max_delayed_seconds < 0
        ):
            raise DataContractError(
                "max_delayed_seconds must be a nonnegative int",
                details={"field": "max_delayed_seconds", "rule": "nonnegative"},
            )
        if max_fresh_seconds > max_delayed_seconds:
            raise DataContractError(
                "max_fresh_seconds must be <= max_delayed_seconds",
                details={"field": "max_fresh_seconds", "rule": "fresh_le_delayed"},
            )
        self._transport = transport
        self._gate = gate
        self._calendar = calendar
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._current_window_seconds = current_window_seconds
        self._max_fresh_seconds = max_fresh_seconds
        self._max_delayed_seconds = max_delayed_seconds

    def _require_current_clist_trade_date(
        self, trade_date: date, *, operation: str, now: datetime
    ) -> date:
        return require_current_clist_trade_date(
            trade_date=trade_date,
            now=now,
            is_trading_day=self._calendar.is_trading_day,
            previous_trading_day=self._calendar.previous_trading_day,
            operation=operation,
        )

    def _require_current_only_as_of_and_trade_date(
        self, as_of: datetime, trade_date: date, *, operation: str
    ) -> datetime:
        """Reject before network unless as_of is current and trade_date is now.

        Hot-list / stockrank endpoints return a *current* cross-section. Sample
        the adapter clock once; require ``as_of`` within ``current_window_seconds``
        of that sample and ``trade_date`` equal to the Asia/Shanghai local date
        of the sample. Do not label current ranks as arbitrary historical dates.
        """
        now = self._require_as_of(as_of)
        if type(trade_date) is not date:
            raise DataContractError(
                "trade_date must be a date (not datetime)",
                details={
                    "field": "trade_date",
                    "rule": "exact_date_type",
                    "operation": operation,
                },
            )
        age = (now - as_of).total_seconds()
        if age < 0:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={
                    "field": "as_of",
                    "rule": "not_future",
                    "operation": operation,
                },
            )
        if age > self._current_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported current window for current-only cross-section data",
                details={
                    "operation": operation,
                    "rule": "current_window",
                    "window_seconds": self._current_window_seconds,
                },
            )
        local_day = now.astimezone(SHANGHAI).date()
        if trade_date != local_day:
            raise DataContractError(
                "trade_date must equal Asia/Shanghai local date of sampled now "
                "for current-only endpoints",
                details={
                    "field": "trade_date",
                    "rule": "current_only_local_date",
                    "operation": operation,
                    "requested": trade_date.isoformat(),
                    "supportable": local_day.isoformat(),
                },
            )
        return now

    def _require_live_current_clist_as_of(
        self, as_of: datetime, trade_date: date, *, operation: str
    ) -> datetime:
        """Defense-in-depth for current-only clist ops before gate/network.

        Rejects ``as_of`` older than ``current_window_seconds`` relative to the
        adapter clock, and rejects ``trade_date`` later than the Asia/Shanghai
        local date of ``as_of``. Details stay secret-free.
        """
        now = self._require_as_of(as_of)
        age = (now - as_of).total_seconds()
        if age > self._current_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported current window for current cross-section data",
                details={
                    "operation": operation,
                    "rule": "current_window",
                    "window_seconds": self._current_window_seconds,
                },
            )
        as_of_local_day = as_of.astimezone(SHANGHAI).date()
        if trade_date > as_of_local_day:
            raise DataContractError(
                "trade_date must not be later than the Asia/Shanghai local date of as_of",
                details={
                    "field": "trade_date",
                    "rule": "trade_date_not_after_as_of_local",
                    "operation": operation,
                },
            )
        return now

    def _require_current_only_as_of(self, as_of: datetime, *, operation: str) -> datetime:
        """Reject non-current requests for endpoints with no historical replay."""
        now = self._require_as_of(as_of)
        if (now - as_of).total_seconds() > self._current_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported current window",
                details={
                    "operation": operation,
                    "rule": "current_window",
                    "window_seconds": self._current_window_seconds,
                },
            )
        return now

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.EASTMONEY

    @property
    def provider_name(self) -> str:
        return VendorId.EASTMONEY.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in _SUPPORTED_CATEGORIES

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Eastmoney A-share adapter is disabled",
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

    async def _gated_send(self, request: HttpRequest) -> HttpResponse:
        async def _op() -> HttpResponse:
            return await self._transport.send(request)

        return await self._gate.run(_op)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
            "Referer": "https://quote.eastmoney.com/",
        }

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        # Rate-limit / blocked recognized before content-type checks.
        if status_code == 429:
            raise ProviderRateLimitError(
                "Eastmoney rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Eastmoney access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Eastmoney HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _require_json_content_type(self, headers: Mapping[str, str], *, operation: str) -> None:
        if content_type_matches(headers, allowed_substrings=_JSON_CONTENT):
            return
        if not headers.get("content-type") and not headers.get("Content-Type"):
            raise DataContractError(
                "Eastmoney response missing Content-Type",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "content_type",
                },
            )
        raise DataContractError(
            "Eastmoney response Content-Type is not acceptable",
            details={
                "vendor": self.vendor_id.value,
                "operation": operation,
                "rule": "content_type",
            },
        )

    def _meta(
        self,
        *,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        session: TradingSession,
        data_timestamp: datetime | None = None,
        adjustment: AdjustmentMethod | None = None,
        current_cross_section: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> ProviderResultMeta:
        if current_cross_section:
            # Current-only clist/pool rows have no trustworthy bar-close timestamp.
            # Never claim FRESH for unlabeled live cross-sections.
            data_delay = None
            freshness = Freshness.UNKNOWN
        elif data_timestamp is not None:
            data_delay = max(0, int((fetched_at - data_timestamp).total_seconds()))
            try:
                ref = fetched_at if fetched_at >= data_timestamp else as_of
                freshness = classify_freshness(
                    now=ref,
                    data_timestamp=data_timestamp,
                    session=session,
                    max_fresh_seconds=self._max_fresh_seconds,
                    max_delayed_seconds=self._max_delayed_seconds,
                    vendor_declared_delay_seconds=None,
                )
            except DataContractError:
                freshness = Freshness.UNKNOWN
        else:
            data_delay = None
            freshness = Freshness.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=adjustment,
            data_delay_seconds=data_delay,
            warnings=warnings,
        )

    def _volume_shares(self, lots_raw: object, *, field: str, asset_type: AssetType) -> int | None:
        lots = int_from_text(lots_raw, field=field)
        if lots is None:
            return None
        return lots_to_shares(lots, field=field, asset_type=asset_type)

    # --- quote ----------------------------------------------------------------

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[AShareQuote]:
        self._require_configured()
        self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_QUOTE_URL,
                params={
                    "secid": secid,
                    "fields": _QUOTE_FIELDS,
                    "fltt": "2",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        self._raise_for_http_status(response.status_code, operation="quote")
        self._require_json_content_type(response.headers, operation="quote")
        payload = loads_json_decimal(response.body)
        quote = self._parse_quote(payload, instrument=instrument, code6=code6, as_of=as_of)
        if quote.quote_at > as_of:
            raise DataContractError(
                "quote_at must be <= requested as_of cutoff",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "as_of_cutoff",
                },
            )
        session = infer_session_basic(Market.A_SHARE, quote.quote_at, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=quote,
            meta=self._meta(
                category=DataCategory.MARKET_QUOTE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                data_timestamp=quote.quote_at,
            ),
        )

    def _parse_quote(
        self,
        payload: object,
        *,
        instrument: Instrument,
        code6: str,
        as_of: datetime,
    ) -> AShareQuote:
        del as_of
        data = self._require_data_object(payload, operation="quote")
        if data is None:
            raise NoMarketData(
                "Eastmoney returned no quote data",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        # Identity when present.
        resp_code = data.get("f57")
        if resp_code is not None:
            code_text = str(resp_code).strip().zfill(6)
            if code_text != code6:
                raise DataContractError(
                    "Eastmoney quote code does not match requested instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "quote",
                        "rule": "identity_mismatch",
                    },
                )
        if data.get("f43") in (None, "-", ""):
            raise NoMarketData(
                "Eastmoney returned no quote data",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        last = require_decimal(data.get("f43"), field="last")
        if last <= 0:
            raise NoMarketData(
                "Eastmoney returned non-positive last",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        open_ = decimal_from_text(data.get("f46"), field="open")
        high = decimal_from_text(data.get("f44"), field="high")
        low = decimal_from_text(data.get("f45"), field="low")
        previous_close = decimal_from_text(data.get("f60"), field="previous_close")
        volume = self._volume_shares(
            data.get("f47"),
            field="volume_lots",
            asset_type=instrument.asset_type,
        )
        turnover = decimal_from_text(data.get("f48"), field="turnover_amount_cny")
        turnover_rate = decimal_from_text(data.get("f168"), field="turnover_rate")
        change = decimal_from_text(data.get("f169"), field="change")
        change_percent = decimal_from_text(data.get("f170"), field="change_percent")
        pe_ttm = decimal_from_text(data.get("f162"), field="pe_ttm")
        pb = decimal_from_text(data.get("f167"), field="pb")
        total_mkt = decimal_from_text(data.get("f116"), field="total_market_cap_cny")
        float_mkt = decimal_from_text(data.get("f117"), field="float_market_cap_cny")
        limit_up = decimal_from_text(data.get("f51"), field="limit_up_price")
        limit_down = decimal_from_text(data.get("f52"), field="limit_down_price")

        ts = int_from_text(data.get("f86"), field="quote_at")
        if ts is None or ts <= 0:
            raise DataContractError(
                "Eastmoney quote timestamp missing or invalid",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "contract_drift",
                },
            )
        quote_at = datetime.fromtimestamp(ts, tz=SHANGHAI)

        session = infer_session_basic(Market.A_SHARE, quote_at, timezone="Asia/Shanghai")
        return AShareQuote(
            instrument_id=instrument.instrument_id,
            quote_at=quote_at,
            session=session,
            last=last,
            open=open_,
            high=high,
            low=low,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            volume_shares=volume,
            turnover_amount_cny=turnover,
            turnover_rate=turnover_rate,
            pe_ttm=pe_ttm,
            pb=pb,
            total_market_cap_cny=total_mkt,
            float_market_cap_cny=float_mkt,
            limit_up_price=limit_up,
            limit_down_price=limit_down,
        )

    # --- bars -----------------------------------------------------------------

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: BarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AShareBar, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        start = require_exact_date(start, field="start")
        end = require_exact_date(end, field="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(interval, BarInterval):
            raise DataContractError(
                "interval must be BarInterval",
                details={"field": "interval", "rule": "type"},
            )
        if adjustment not in _FQT_BY_ADJUSTMENT:
            raise DataContractError(
                "adjustment method is not supported by Eastmoney OHLCV",
                details={
                    "field": "adjustment",
                    "rule": "unsupported_adjustment",
                    "vendor": self.vendor_id.value,
                },
            )
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        klt = _KLT_BY_INTERVAL[interval]
        fqt = _FQT_BY_ADJUSTMENT[adjustment]
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_KLINE_URL,
                params={
                    "secid": secid,
                    "klt": klt,
                    "fqt": fqt,
                    "beg": start.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        self._raise_for_http_status(response.status_code, operation="bars")
        self._require_json_content_type(response.headers, operation="bars")
        payload = loads_json_decimal(response.body)
        bars = self._parse_bars(
            payload,
            instrument=instrument,
            code6=code6,
            start=start,
            end=end,
            interval=interval,
            adjustment=adjustment,
            as_of=as_of,
        )
        if not bars:
            raise NoMarketData(
                "Eastmoney returned no bars",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=bars,
            meta=self._meta(
                category=DataCategory.MARKET_OHLCV,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                data_timestamp=bars[-1].end_at if bars else None,
                adjustment=adjustment,
            ),
        )

    def _parse_bars(
        self,
        payload: object,
        *,
        instrument: Instrument,
        code6: str,
        start: date,
        end: date,
        interval: BarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> tuple[AShareBar, ...]:
        data = self._require_data_object(payload, operation="bars")
        if data is None:
            return ()
        resp_code = data.get("code")
        if resp_code is not None and str(resp_code).strip().zfill(6) != code6:
            raise DataContractError(
                "Eastmoney bars code does not match requested instrument",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "identity_mismatch",
                },
            )
        klines = data.get("klines")
        if klines is None:
            return ()
        if not isinstance(klines, list):
            raise DataContractError(
                "Eastmoney bars payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "contract_drift",
                },
            )
        out: list[AShareBar] = []
        seen_starts: set[datetime] = set()
        for idx, row in enumerate(klines):
            if not isinstance(row, str):
                raise DataContractError(
                    "Eastmoney bars payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            parts = row.split(",")
            if len(parts) < 7:
                raise DataContractError(
                    "Eastmoney bars payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            stamp = parts[0].strip()
            open_ = require_decimal(parts[1], field=f"bars[{idx}].open")
            close = require_decimal(parts[2], field=f"bars[{idx}].close")
            high = require_decimal(parts[3], field=f"bars[{idx}].high")
            low = require_decimal(parts[4], field=f"bars[{idx}].low")
            volume_lots = require_int(parts[5], field=f"bars[{idx}].volume_lots")
            volume = lots_to_shares(
                volume_lots,
                field=f"bars[{idx}].volume_shares",
                asset_type=instrument.asset_type,
            )
            if volume is None:
                raise DataContractError(
                    "bar volume unit unsupported for instrument asset type",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "volume_unit_unsupported",
                        "index": idx,
                    },
                )
            amount = decimal_from_text(parts[6], field=f"bars[{idx}].turnover_amount_cny")

            start_at, end_at = self._bar_bounds(stamp, interval=interval)
            # Inclusive request window on calendar date of the bar period end.
            bar_day = end_at.astimezone(SHANGHAI).date()
            if bar_day < start or bar_day > end:
                continue
            if end_at > as_of:
                continue
            if start_at in seen_starts:
                raise DataContractError(
                    "bars must have unique start_at",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "unique_start",
                        "index": idx,
                    },
                )
            seen_starts.add(start_at)
            out.append(
                AShareBar(
                    start_at=start_at,
                    end_at=end_at,
                    interval=interval,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume_shares=volume,
                    turnover_amount_cny=amount,
                    adjustment=adjustment,
                )
            )
        out.sort(key=lambda b: b.start_at)
        return tuple(out)

    def _bar_bounds(self, stamp: str, *, interval: BarInterval) -> tuple[datetime, datetime]:
        if " " in stamp:
            day_s, time_s = stamp.split(" ", 1)
            day = parse_shanghai_date(day_s)
            # Intraday stamps must land on covered trading days.
            if not self._calendar.is_trading_day(day):
                raise DataContractError(
                    "intraday bar stamp is not an A-share trading day",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "non_trading_day",
                        "day": day.isoformat(),
                    },
                )
            start_at = combine_shanghai_date_time(day, time_s)
            minutes = {
                BarInterval.ONE_MINUTE: 1,
                BarInterval.FIVE_MINUTES: 5,
                BarInterval.FIFTEEN_MINUTES: 15,
                BarInterval.THIRTY_MINUTES: 30,
                BarInterval.SIXTY_MINUTES: 60,
            }.get(interval)
            if minutes is None:
                raise DataContractError(
                    "intraday stamp used with non-intraday interval",
                    details={"field": "interval", "rule": "intraday_mismatch"},
                )
            end_at = start_at + timedelta(minutes=minutes)
            return start_at, end_at

        day = parse_shanghai_date(stamp)
        if interval is BarInterval.ONE_DAY:
            return self._session_bounds_for_day(day)
        if interval is BarInterval.ONE_WEEK:
            return self._week_bounds(day)
        if interval is BarInterval.ONE_MONTH:
            return self._month_bounds(day)
        # Non-intraday stamp for intraday interval is contract drift.
        raise DataContractError(
            "bar stamp missing time for intraday interval",
            details={"field": "interval", "rule": "intraday_stamp"},
        )

    def _session_bounds_for_day(self, day: date) -> tuple[datetime, datetime]:
        """Session open/close for a trading day — fail closed, never invent 09:30–15:00."""
        windows = self._calendar.sessions_for(day)
        if not windows:
            raise DataContractError(
                "bar day is not an A-share trading day",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "non_trading_day",
                    "day": day.isoformat(),
                },
            )
        return windows[0].start_at, windows[-1].end_at

    def _week_bounds(self, period_end: date) -> tuple[datetime, datetime]:
        """Weekly stamp is the period end trading day from Eastmoney.

        Start: first session open of the first trading day on/after the ISO
        Monday of that week. End: last session close of ``period_end``.
        Rejects out-of-coverage or non-trading ``period_end`` (fail closed).
        """
        if not self._calendar.is_trading_day(period_end):
            raise DataContractError(
                "weekly bar period_end is not an A-share trading day",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "non_trading_period_end",
                    "day": period_end.isoformat(),
                },
            )
        week_start = week_period_start(period_end)
        cursor = week_start
        first_open: date | None = None
        while cursor <= period_end:
            # CalendarOutOfRange propagates (fail closed; no invented first_open).
            if self._calendar.is_trading_day(cursor):
                first_open = cursor
                break
            cursor += timedelta(days=1)
        if first_open is None:
            raise DataContractError(
                "weekly bar has no trading day in period",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "empty_week_period",
                    "period_end": period_end.isoformat(),
                },
            )
        start_at, _ = self._session_bounds_for_day(first_open)
        _, end_at = self._session_bounds_for_day(period_end)
        return start_at, end_at

    def _month_bounds(self, period_end: date) -> tuple[datetime, datetime]:
        """Monthly stamp is the period end trading day from Eastmoney.

        Start: first session open of the first trading day of that calendar month.
        End: last session close of ``period_end``.
        Rejects out-of-coverage or non-trading ``period_end`` (fail closed).
        """
        if not self._calendar.is_trading_day(period_end):
            raise DataContractError(
                "monthly bar period_end is not an A-share trading day",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "non_trading_period_end",
                    "day": period_end.isoformat(),
                },
            )
        month_start = first_day_of_month(period_end)
        cursor = month_start
        first_open: date | None = None
        while cursor <= period_end:
            # CalendarOutOfRange propagates (fail closed; no invented first_open).
            if self._calendar.is_trading_day(cursor):
                first_open = cursor
                break
            cursor += timedelta(days=1)
        if first_open is None:
            raise DataContractError(
                "monthly bar has no trading day in period",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "empty_month_period",
                    "period_end": period_end.isoformat(),
                },
            )
        start_at, _ = self._session_bounds_for_day(first_open)
        _, end_at = self._session_bounds_for_day(period_end)
        return start_at, end_at

    # --- order book -----------------------------------------------------------

    async def get_order_book(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[OrderBookLevel, ...]]:
        self._require_configured()
        self._require_current_only_as_of(as_of, operation="order_book")
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_QUOTE_URL,
                params={
                    "secid": secid,
                    "fields": _QUOTE_FIELDS,
                    "fltt": "2",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        self._raise_for_http_status(response.status_code, operation="order_book")
        self._require_json_content_type(response.headers, operation="order_book")
        payload = loads_json_decimal(response.body)
        levels = self._parse_order_book(payload, instrument=instrument, code6=code6)
        if not levels:
            raise NoMarketData(
                "Eastmoney returned no order book",
                details={"vendor": self.vendor_id.value, "operation": "order_book"},
            )
        validate_order_book_levels(levels)
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=levels,
            meta=self._meta(
                category=DataCategory.MARKET_STRUCTURE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    def _parse_order_book(
        self, payload: object, *, instrument: Instrument, code6: str
    ) -> tuple[OrderBookLevel, ...]:
        data = self._require_data_object(payload, operation="order_book")
        if data is None:
            return ()
        resp_code = data.get("f57")
        if resp_code is not None and str(resp_code).strip().zfill(6) != code6:
            raise DataContractError(
                "Eastmoney order book code does not match requested instrument",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "order_book",
                    "rule": "identity_mismatch",
                },
            )
        bid_pairs = (
            ("f19", "f20"),
            ("f17", "f18"),
            ("f15", "f16"),
            ("f13", "f14"),
            ("f11", "f12"),
        )
        ask_pairs = (
            ("f39", "f40"),
            ("f37", "f38"),
            ("f35", "f36"),
            ("f33", "f34"),
            ("f31", "f32"),
        )
        levels: list[OrderBookLevel] = []
        any_present = False
        for idx in range(5):
            bp_k, bv_k = bid_pairs[idx]
            ap_k, av_k = ask_pairs[idx]
            bid_price = decimal_from_text(data.get(bp_k), field=f"bid_price[{idx + 1}]")
            bid_vol = self._volume_shares(
                data.get(bv_k),
                field=f"bid_volume_lots[{idx + 1}]",
                asset_type=instrument.asset_type,
            )
            ask_price = decimal_from_text(data.get(ap_k), field=f"ask_price[{idx + 1}]")
            ask_vol = self._volume_shares(
                data.get(av_k),
                field=f"ask_volume_lots[{idx + 1}]",
                asset_type=instrument.asset_type,
            )
            if any(v is not None for v in (bid_price, bid_vol, ask_price, ask_vol)):
                any_present = True
            levels.append(
                OrderBookLevel(
                    level=idx + 1,
                    bid_price=bid_price,
                    bid_volume_shares=bid_vol,
                    ask_price=ask_price,
                    ask_volume_shares=ask_vol,
                )
            )
        if not any_present:
            return ()
        return tuple(levels)

    # --- ticks ----------------------------------------------------------------

    async def get_ticks(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[TradeTick, ...]]:
        self._require_configured()
        self._require_current_only_as_of(as_of, operation="ticks")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_DETAILS_URL,
                params={
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4",
                    "fields2": "f51,f52,f53,f54,f55",
                    "pos": "-20",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        self._raise_for_http_status(response.status_code, operation="ticks")
        self._require_json_content_type(response.headers, operation="ticks")
        payload = loads_json_decimal(response.body)
        ticks = self._parse_ticks(
            payload, instrument=instrument, code6=code6, as_of=as_of, limit=limit
        )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        latest = ticks[-1].occurred_at if ticks else None
        return ProviderSuccess(
            value=ticks,
            meta=self._meta(
                category=DataCategory.MARKET_STRUCTURE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                data_timestamp=latest,
            ),
        )

    def _parse_ticks(
        self,
        payload: object,
        *,
        instrument: Instrument,
        code6: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[TradeTick, ...]:
        data = self._require_data_object(payload, operation="ticks")
        if data is None:
            return ()
        resp_code = data.get("code") or data.get("f57")
        if resp_code is not None and str(resp_code).strip().zfill(6) != code6:
            raise DataContractError(
                "Eastmoney ticks code does not match requested instrument",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "ticks",
                    "rule": "identity_mismatch",
                },
            )
        details = data.get("details")
        if details is None:
            return ()
        if not isinstance(details, list):
            raise DataContractError(
                "Eastmoney ticks payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "ticks",
                    "rule": "contract_drift",
                },
            )
        day = as_of.astimezone(SHANGHAI).date()
        out: list[TradeTick] = []
        for idx, row in enumerate(details):
            if not isinstance(row, str):
                raise DataContractError(
                    "Eastmoney ticks payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "ticks",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            parts = row.split(",")
            if len(parts) < 3:
                raise DataContractError(
                    "Eastmoney ticks payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "ticks",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            occurred_at = combine_shanghai_date_time(day, parts[0])
            if occurred_at > as_of:
                continue
            price = require_decimal(parts[1], field=f"ticks[{idx}].price")
            volume_lots = require_int(parts[2], field=f"ticks[{idx}].volume_lots")
            volume = lots_to_shares(
                volume_lots,
                field=f"ticks[{idx}].volume_shares",
                asset_type=instrument.asset_type,
            )
            if volume is None:
                raise DataContractError(
                    "tick volume unit unsupported for instrument asset type",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "ticks",
                        "rule": "volume_unit_unsupported",
                        "index": idx,
                    },
                )
            direction = TickDirection.UNKNOWN
            if len(parts) >= 4:
                code = parts[3].strip()
                if code in {"1", "B", "b", "buy"}:
                    direction = TickDirection.BUY
                elif code in {"2", "S", "s", "sell"}:
                    direction = TickDirection.SELL
                elif code in {"0", "4", "N", "n"}:
                    direction = TickDirection.NEUTRAL
            out.append(
                TradeTick(
                    occurred_at=occurred_at,
                    price=price,
                    volume_shares=volume,
                    direction=direction,
                )
            )
        if len(out) > limit:
            out = out[-limit:]
        return tuple(out)

    # --- industry performance -------------------------------------------------

    async def get_industry_performance(
        self, *, trade_date: date, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[IndustryPerformanceRow, ...]]:
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        # Preflight before gate/network: live as_of + trade_date vs as_of local.
        now = self._require_live_current_clist_as_of(
            as_of, trade_date, operation="industry_performance"
        )
        # Preflight before any network: current clist only.
        self._require_current_clist_trade_date(
            trade_date, operation="industry_performance", now=now
        )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        # Standalone method returns caller limit of fid=f3 ranking; page size must
        # cover the requested top-N. If declared total exceeds one page we still
        # validate the page contract but only materialize ``limit`` rows.
        page_size = min(max(limit, 1), _CLIST_INDUSTRY_PAGE_SIZE)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_CLIST_URL,
                params=self._clist_params(
                    pn=1,
                    pz=page_size,
                    fs=EASTMONEY_INDUSTRY_BOARD_FS,
                    fields="f12,f14,f2,f3,f4,f5,f6,f104,f105,f106",
                    fid="f3",
                ),
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        self._raise_for_http_status(response.status_code, operation="industry_performance")
        self._require_json_content_type(response.headers, operation="industry_performance")
        payload = loads_json_decimal(response.body)
        data = self._require_data_object(payload, operation="industry_performance")
        if data is None:
            raise NoMarketData(
                "Eastmoney returned no industry performance",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "industry_performance",
                },
            )
        total = self._require_clist_total(data, operation="industry_performance")
        if total > page_size and limit > page_size:
            # Caller asked for more rows than one page — fail closed rather than
            # silently truncated ranking.
            raise DataContractError(
                "industry performance limit exceeds single-page fetch; "
                "raise page size or use market board full inventory",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "industry_performance",
                    "rule": "page_incomplete",
                    "total": total,
                    "page_size": page_size,
                    "limit": limit,
                },
            )
        if total > page_size and limit <= page_size:
            # Top-N within first sorted page is deterministic under fid=f3.
            pass
        elif total <= page_size:
            # Declared total fits the page: fetched count must match total.
            diff = data.get("diff")
            if not isinstance(diff, list):
                raise DataContractError(
                    "Eastmoney industry payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "industry_performance",
                        "rule": "contract_drift",
                    },
                )
            if len(diff) != total:
                raise DataContractError(
                    "industry page row count does not match declared total",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "industry_performance",
                        "rule": "total_mismatch",
                        "total": total,
                        "fetched": len(diff),
                    },
                )
        rows = self._rows_from_industry_diff(
            data.get("diff"), trade_date=trade_date, limit=limit, operation="industry_performance"
        )
        if not rows:
            raise NoMarketData(
                "Eastmoney returned no industry performance",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "industry_performance",
                },
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=rows,
            meta=self._meta(
                category=DataCategory.MARKET_STRUCTURE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                current_cross_section=True,
            ),
        )

    def _clist_params(
        self,
        *,
        pn: int,
        pz: int,
        fs: str,
        fields: str,
        fid: str = "f3",
    ) -> dict[str, str]:
        return {
            "pn": str(pn),
            "pz": str(pz),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

    def _require_clist_total(self, data: Mapping[str, Any], *, operation: str) -> int:
        if "total" not in data:
            raise DataContractError(
                "Eastmoney clist payload missing total",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        total = require_int(data.get("total"), field="clist.total")
        if total < 0:
            raise DataContractError(
                "Eastmoney clist total must be nonnegative",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        return total

    def _rows_from_industry_diff(
        self,
        diff: object,
        *,
        trade_date: date,
        limit: int | None,
        operation: str,
    ) -> tuple[IndustryPerformanceRow, ...]:
        if diff is None:
            return ()
        if not isinstance(diff, list):
            raise DataContractError(
                "Eastmoney industry payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        out: list[IndustryPerformanceRow] = []
        seen: set[str] = set()
        items = diff if limit is None else diff[:limit]
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise DataContractError(
                    "Eastmoney industry payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            code = item.get("f12")
            name = item.get("f14")
            if not isinstance(code, str) or not isinstance(name, str):
                raise DataContractError(
                    "Eastmoney industry payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            if code in seen:
                raise DataContractError(
                    "industry codes must be unique",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "unique_industry_code",
                        "index": idx,
                    },
                )
            seen.add(code)
            change_percent = require_decimal(
                item.get("f3"), field=f"industry[{idx}].change_percent"
            )
            advancing = int_from_text(item.get("f104"), field=f"industry[{idx}].advancing") or 0
            declining = int_from_text(item.get("f105"), field=f"industry[{idx}].declining") or 0
            unchanged = int_from_text(item.get("f106"), field=f"industry[{idx}].unchanged") or 0
            turnover = decimal_from_text(item.get("f6"), field=f"industry[{idx}].turnover")
            out.append(
                IndustryPerformanceRow(
                    industry_code=code,
                    industry_name=name,
                    trade_date=trade_date,
                    change_percent=change_percent,
                    advancing_count=advancing,
                    declining_count=declining,
                    unchanged_count=unchanged,
                    leading_instrument_id=None,
                    leading_change_percent=None,
                    turnover_amount_cny=turnover,
                )
            )
        return tuple(out)

    # --- market board (multi-endpoint composition) ----------------------------

    async def get_market_board(
        self, *, trade_date: date, as_of: datetime
    ) -> ProviderSuccess[MarketBoardSnapshot]:
        """Compose market board from allowlisted E2 endpoints only.

        Requests:
        1. clist full A-share equity universe (paginated to declared total)
        2. push2ex zt/dt/zb pools (limit counts — not percentage approximations)
        3. clist complete industry board inventory (paginated)

        Pool date and clist trade_date are the same supportable closed session.
        """
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        # Preflight before gate/network: live as_of + trade_date vs as_of local.
        now = self._require_live_current_clist_as_of(as_of, trade_date, operation="market_board")
        # Preflight before any network: reject pseudo-historical current-clist.
        self._require_current_clist_trade_date(trade_date, operation="market_board", now=now)
        date_param = trade_date.strftime("%Y%m%d")

        equity_rows = await self._fetch_clist_pages(
            operation="market_board",
            fs=EASTMONEY_A_SHARE_EQUITY_FS,
            fields="f12,f14,f2,f3,f6",
            page_size=_CLIST_EQUITY_PAGE_SIZE,
            max_total=_CLIST_EQUITY_MAX_TOTAL,
            max_pages=_CLIST_EQUITY_MAX_PAGES,
            code_field="f12",
        )
        if not equity_rows:
            raise NoMarketData(
                "Eastmoney returned no market board equity rows",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "market_board",
                },
            )

        zt_resp = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_ZT_POOL_URL,
                params=self._pool_params(date_param, sort="fbt:asc"),
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(zt_resp.status_code, operation="market_board")
        self._require_json_content_type(zt_resp.headers, operation="market_board")

        dt_resp = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_DT_POOL_URL,
                params=self._pool_params(date_param, sort="fund:asc"),
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(dt_resp.status_code, operation="market_board")
        self._require_json_content_type(dt_resp.headers, operation="market_board")

        zb_resp = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_ZB_POOL_URL,
                params=self._pool_params(date_param, sort="fbt:asc"),
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(zb_resp.status_code, operation="market_board")
        self._require_json_content_type(zb_resp.headers, operation="market_board")

        industry_diff = await self._fetch_clist_pages(
            operation="market_board",
            fs=EASTMONEY_INDUSTRY_BOARD_FS,
            fields="f12,f14,f2,f3,f4,f5,f6,f104,f105,f106",
            page_size=_CLIST_INDUSTRY_PAGE_SIZE,
            max_total=_CLIST_INDUSTRY_MAX_TOTAL,
            max_pages=_CLIST_INDUSTRY_MAX_PAGES,
            code_field="f12",
        )
        industries = self._rows_from_industry_diff(
            industry_diff,
            trade_date=trade_date,
            limit=None,
            operation="market_board",
        )

        fetched_at = self._clock.now()
        board = self._compose_market_board_from_rows(
            equity_rows=equity_rows,
            zt_body=zt_resp.body,
            dt_body=dt_resp.body,
            zb_body=zb_resp.body,
            industries=industries,
            trade_date=trade_date,
        )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=board,
            meta=self._meta(
                category=DataCategory.MARKET_STRUCTURE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                current_cross_section=True,
            ),
        )

    def _pool_params(self, date_param: str, *, sort: str) -> dict[str, str]:
        """Public push2ex pool routing/paging params (static ut is not a secret)."""
        return {
            "ut": _PUSH2EX_UT,
            "dpt": _PUSH2EX_DPT,
            "Pageindex": "0",
            "pagesize": _PUSH2EX_POOL_PAGE_SIZE,
            "sort": sort,
            "date": date_param,
        }

    async def _fetch_clist_pages(
        self,
        *,
        operation: str,
        fs: str,
        fields: str,
        page_size: int,
        max_total: int,
        max_pages: int,
        code_field: str,
    ) -> list[dict[str, Any]]:
        """Paginate clist until declared total is fetched (fail closed)."""
        aggregated: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        declared_total: int | None = None
        page = 1
        while page <= max_pages:
            response = await self._gated_send(
                HttpRequest(
                    method="GET",
                    url=_CLIST_URL,
                    params=self._clist_params(pn=page, pz=page_size, fs=fs, fields=fields),
                    headers=self._headers(),
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation=operation)
            self._require_json_content_type(response.headers, operation=operation)
            payload = loads_json_decimal(response.body)
            data = self._require_data_object(payload, operation=operation)
            if data is None:
                if page == 1:
                    return []
                raise DataContractError(
                    "Eastmoney clist returned null data on a subsequent page",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "page": page,
                    },
                )
            total = self._require_clist_total(data, operation=operation)
            if declared_total is None:
                declared_total = total
                if declared_total > max_total:
                    raise DataContractError(
                        "Eastmoney clist declared total exceeds hard maximum",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "max_total_exceeded",
                            "total": declared_total,
                            "max_total": max_total,
                        },
                    )
            elif total != declared_total:
                raise DataContractError(
                    "Eastmoney clist total changed across pages",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "total_changed",
                        "page": page,
                        "expected_total": declared_total,
                        "observed_total": total,
                    },
                )
            diff = data.get("diff")
            if diff is None:
                diff = []
            if not isinstance(diff, list):
                raise DataContractError(
                    "Eastmoney clist diff failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "page": page,
                    },
                )
            if page > 1 and not diff:
                raise DataContractError(
                    "Eastmoney clist returned empty page before completing total",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "empty_page",
                        "page": page,
                        "fetched": len(aggregated),
                        "total": declared_total,
                    },
                )
            for idx, item in enumerate(diff):
                if not isinstance(item, dict):
                    raise DataContractError(
                        "Eastmoney clist row failed contract validation",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "contract_drift",
                            "page": page,
                            "index": idx,
                        },
                    )
                code_raw = item.get(code_field)
                if not isinstance(code_raw, str) or not code_raw.strip():
                    raise DataContractError(
                        "Eastmoney clist row missing instrument code",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "contract_drift",
                            "page": page,
                            "index": idx,
                        },
                    )
                code = code_raw.strip()
                if code in seen_codes:
                    raise DataContractError(
                        "Eastmoney clist returned duplicate instrument code across pages",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "duplicate_code",
                            "page": page,
                            "index": idx,
                        },
                    )
                seen_codes.add(code)
                aggregated.append(dict(item))
            if declared_total == 0:
                # total=0 with a non-empty diff is a contract inconsistency.
                if diff:
                    raise DataContractError(
                        "Eastmoney clist declared total is 0 but diff is non-empty",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "total_zero_nonempty_diff",
                            "page": page,
                            "diff_len": len(diff),
                        },
                    )
                return []
            if len(aggregated) >= declared_total:
                break
            if len(diff) < page_size and len(aggregated) < declared_total:
                raise DataContractError(
                    "Eastmoney clist page truncated before declared total",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "truncated_page",
                        "page": page,
                        "fetched": len(aggregated),
                        "total": declared_total,
                    },
                )
            page += 1
        else:
            raise DataContractError(
                "Eastmoney clist exceeded hard maximum page count",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "max_pages_exceeded",
                    "max_pages": max_pages,
                    "fetched": len(aggregated),
                    "total": declared_total,
                },
            )

        if declared_total is None:
            raise DataContractError(
                "Eastmoney clist total was never observed",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        if len(aggregated) != declared_total:
            raise DataContractError(
                "Eastmoney clist fetched row count differs from declared total",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "total_mismatch",
                    "fetched": len(aggregated),
                    "total": declared_total,
                },
            )
        return aggregated

    def _compose_market_board_from_rows(
        self,
        *,
        equity_rows: list[dict[str, Any]],
        zt_body: bytes,
        dt_body: bytes,
        zb_body: bytes,
        industries: tuple[IndustryPerformanceRow, ...],
        trade_date: date,
    ) -> MarketBoardSnapshot:
        advancing = 0
        declining = 0
        unchanged = 0
        changes: list[Decimal] = []
        total_turnover = Decimal("0")
        has_turnover = False
        for idx, item in enumerate(equity_rows):
            chg = require_decimal(item.get("f3"), field=f"equity[{idx}].f3")
            changes.append(chg)
            if chg > 0:
                advancing += 1
            elif chg < 0:
                declining += 1
            else:
                unchanged += 1
            to = decimal_from_text(item.get("f6"), field=f"equity[{idx}].f6")
            if to is not None:
                total_turnover += to
                has_turnover = True

        limit_up = self._pool_count(zt_body, operation="market_board")
        limit_down = self._pool_count(dt_body, operation="market_board")
        broken = self._pool_count(zb_body, operation="market_board")

        med: Decimal | None = None
        if changes:
            ordered = sorted(changes)
            mid = len(ordered) // 2
            if len(ordered) % 2 == 1:
                med = ordered[mid]
            else:
                med = (ordered[mid - 1] + ordered[mid]) / Decimal(2)

        return MarketBoardSnapshot(
            trade_date=trade_date,
            advancing_count=advancing,
            declining_count=declining,
            unchanged_count=unchanged,
            limit_up_count=limit_up,
            limit_down_count=limit_down,
            broken_limit_count=broken,
            total_turnover_cny=total_turnover if has_turnover else None,
            median_change_percent=med,
            industries=industries,
        )

    def _pool_count(self, body: bytes, *, operation: str) -> int:
        """Count entries in a push2ex pool response (public envelope: rc + data)."""
        payload = loads_json_decimal(body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney pool payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        if "rc" not in payload or "data" not in payload:
            raise DataContractError(
                "Eastmoney pool payload missing required envelope fields",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        rc = payload.get("rc")
        if rc not in (0, "0", Decimal(0)):
            raise ProviderUnavailableError(
                "Eastmoney pool business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            # Explicit data:null → zero pool members (endpoint semantics).
            return 0
        if not isinstance(data, dict):
            raise DataContractError(
                "Eastmoney pool payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        # Prefer explicit total count when provided by endpoint.
        for key in ("tc", "total", "poolnum"):
            if key in data and data[key] is not None:
                return require_int(data[key], field=f"pool.{key}")
        pool = data.get("pool")
        if pool is None:
            return 0
        if not isinstance(pool, list):
            raise DataContractError(
                "Eastmoney pool payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        return len(pool)

    # --- shared payload helpers -----------------------------------------------

    def _require_data_object(self, payload: object, *, operation: str) -> dict[str, Any] | None:
        """Require frozen Eastmoney envelope: status fields + data shape.

        - Missing required envelope fields → contract_drift
        - Explicit ``data: null`` → no-data (return None)
        - Empty object ``{}`` is NOT legitimate no-data when it is the root
          (missing rc) or when data is ``{}`` without expected operation fields
          (callers decide emptiness of inner collections).
        """
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        if "rc" not in payload or "data" not in payload:
            raise DataContractError(
                "Eastmoney payload missing required envelope fields",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        rc = payload.get("rc")
        if rc not in (0, "0", Decimal(0)):
            raise ProviderUnavailableError(
                "Eastmoney business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            return None
        if not isinstance(data, dict):
            raise DataContractError(
                "Eastmoney payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        return dict(data)

    # --- E3 fundamentals / F10 / statements / corporate actions / reports / news ---

    async def _datacenter_get(
        self,
        *,
        report_name: str,
        filter_expr: str,
        columns: str,
        page_size: int,
        operation: str,
        sort_columns: str = "",
        sort_types: str = "",
    ) -> object:
        params: dict[str, str] = {
            "reportName": report_name,
            "columns": columns,
            "filter": filter_expr,
            "pageNumber": "1",
            "pageSize": str(page_size),
            "source": "WEB",
            "client": "WEB",
        }
        if sort_columns:
            params["sortColumns"] = sort_columns
            params["sortTypes"] = sort_types or "-1"
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_DATACENTER_URL,
                params=params,
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation=operation)
        self._require_json_content_type(response.headers, operation=operation)
        return loads_json_decimal(response.body)

    def _require_datacenter_rows(self, payload: object, *, operation: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney datacenter payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        # success/code envelope when present
        if "success" in payload and payload.get("success") not in (True, "true", 1, "1"):
            # Legitimate empty often still success=true with result=null
            pass
        if "result" not in payload:
            raise DataContractError(
                "Eastmoney datacenter payload missing result field",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        result = payload.get("result")
        if result is None:
            return []
        if not isinstance(result, dict):
            raise DataContractError(
                "Eastmoney datacenter result failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        data = result.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            raise DataContractError(
                "Eastmoney datacenter data must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(data):
            if not isinstance(row, dict):
                raise DataContractError(
                    "Eastmoney datacenter row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            rows.append(dict(row))
        return rows

    async def get_fundamentals(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundamentalMetric, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        payload = await self._datacenter_get(
            report_name="RPT_F10_FINANCE_MAINFINADATA",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=(
                "SECUCODE,SECURITY_CODE,REPORT_DATE,NOTICE_DATE,"
                "EPSJB,BPS,ROE_WEIGHT,MGJYXJJE,XSMLL,TOTALOPERATEREVE,"
                "PARENTNETPROFIT,KCFJCXSYJLR"
            ),
            page_size=4,
            operation="fundamentals",
            sort_columns="REPORT_DATE",
            sort_types="-1",
        )
        rows = self._require_datacenter_rows(payload, operation="fundamentals")
        metrics: list[FundamentalMetric] = []
        unknown_excluded = False
        field_map = (
            ("EPSJB", "eps", "CNY"),
            ("BPS", "bps", "CNY"),
            ("ROE_WEIGHT", "roe", "percent"),
            ("MGJYXJJE", "ocf_ps", "CNY"),
            ("XSMLL", "gross_margin", "percent"),
            ("TOTALOPERATEREVE", "operating_revenue", "CNY"),
            ("PARENTNETPROFIT", "net_profit_parent", "CNY"),
            ("KCFJCXSYJLR", "deducted_net_profit", "CNY"),
        )
        for row in rows:
            period_raw = row.get("REPORT_DATE")
            period_end = None
            if isinstance(period_raw, str) and period_raw.strip():
                period_end = parse_shanghai_date(period_raw)
            published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            for field, name, unit in field_map:
                if field not in row:
                    continue
                raw_val = row.get(field)
                if raw_val is None or raw_val == "-":
                    continue
                if isinstance(raw_val, str) and not raw_val.strip():
                    continue
                # Prefer Decimal; allow int/str labels only when non-numeric.
                value: Decimal | str | int | None
                try:
                    value = decimal_from_text(raw_val, field=name)
                except DataContractError:
                    if isinstance(raw_val, str):
                        value = raw_val.strip()[:200]
                    elif isinstance(raw_val, int) and not isinstance(raw_val, bool):
                        value = raw_val
                    else:
                        raise
                metrics.append(
                    FundamentalMetric(
                        name=name,
                        value=value,
                        unit=unit,
                        period_end=period_end,
                        published_at=published_at,
                    )
                )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not metrics:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "fundamentals",
                },
            )
        warnings: tuple[str, ...] = ()
        if unknown_excluded:
            warnings = ("PUBLICATION_TIME_UNKNOWN_EXCLUDED",)
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.FUNDAMENTALS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if warnings:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=warnings)
        return ProviderSuccess(value=tuple(metrics), meta=meta)

    async def get_f10_sections(
        self,
        instrument: Instrument,
        *,
        sections: tuple[str, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[F10Section, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(sections, tuple) or not sections:
            raise DataContractError(
                "sections must be a non-empty tuple",
                details={"field": "sections", "rule": "non_empty"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        out: list[F10Section] = []
        for section in sections:
            if not isinstance(section, str) or not section.strip():
                raise DataContractError(
                    "section name must be non-blank",
                    details={"field": "sections", "rule": "non_blank"},
                )
            key = section.strip().lower()
            report = _F10_SECTION_REPORTS.get(key)
            if report is None:
                # Unknown section key is contract error (closed allowlist).
                raise DataContractError(
                    "unsupported F10 section",
                    details={
                        "field": "sections",
                        "rule": "unknown_section",
                        "section": key,
                    },
                )
            payload = await self._datacenter_get(
                report_name=report,
                filter_expr=f'(SECUCODE="{secucode}")',
                columns="SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,ORG_NAME,MAIN_BUSINESS,ORG_PROFILE,HOLDER_NUM",
                page_size=1,
                operation="f10",
            )
            rows = self._require_datacenter_rows(payload, operation="f10")
            if not rows:
                continue
            row = rows[0]
            title = str(row.get("SECURITY_NAME_ABBR") or row.get("ORG_NAME") or key)[:500]
            # Build body from remaining text fields — untrusted data, never instructions.
            parts: list[str] = []
            for field in (
                "ORG_NAME",
                "ORG_PROFILE",
                "MAIN_BUSINESS",
                "HOLDER_NUM",
            ):
                val = row.get(field)
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    parts.append(f"{field}: {text}")
            body = "\n".join(parts)[:20_000]
            out.append(
                F10Section(
                    section=key[:100],
                    title=title,
                    body=body,
                    as_of=as_of,
                )
            )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        # Legitimate empty sections tuple is success for optional F10.
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(out),
            meta=self._meta(
                category=DataCategory.FUNDAMENTALS,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        statement_types: tuple[FinancialStatementType, ...],
        periods: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]:
        """Eastmoney datacenter fallback for financial statements."""
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(periods, int) or isinstance(periods, bool) or periods < 1 or periods > 40:
            raise DataContractError(
                "periods must be an int in 1..40",
                details={"field": "periods", "rule": "range"},
            )
        if not isinstance(statement_types, tuple) or not statement_types:
            raise DataContractError(
                "statement_types must be a non-empty tuple",
                details={"field": "statement_types", "rule": "non_empty"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        lines: list[FinancialStatementLine] = []
        unknown_excluded = False
        # Frozen item columns per statement type (subset locked by fixtures).
        item_columns: Mapping[FinancialStatementType, tuple[tuple[str, str], ...]] = {
            FinancialStatementType.BALANCE_SHEET: (
                ("TOTAL_ASSETS", "总资产"),
                ("TOTAL_LIABILITIES", "总负债"),
                ("TOTAL_EQUITY", "股东权益合计"),
            ),
            FinancialStatementType.INCOME_STATEMENT: (
                ("TOTAL_OPERATE_INCOME", "营业总收入"),
                ("OPERATE_PROFIT", "营业利润"),
                ("NETPROFIT", "净利润"),
            ),
            FinancialStatementType.CASH_FLOW: (
                ("NETCASH_OPERATE", "经营活动现金流净额"),
                ("NETCASH_INVEST", "投资活动现金流净额"),
                ("NETCASH_FINANCE", "筹资活动现金流净额"),
            ),
        }
        for stype in statement_types:
            if not isinstance(stype, FinancialStatementType):
                raise DataContractError(
                    "statement_types elements must be FinancialStatementType",
                    details={"field": "statement_types", "rule": "type"},
                )
            report = _STATEMENT_REPORT_NAMES[stype]
            cols = ",".join(
                ["SECUCODE", "SECURITY_CODE", "REPORT_DATE", "NOTICE_DATE"]
                + [c for c, _ in item_columns[stype]]
            )
            payload = await self._datacenter_get(
                report_name=report,
                filter_expr=f'(SECUCODE="{secucode}")',
                columns=cols,
                page_size=periods,
                operation="statements",
                sort_columns="REPORT_DATE",
                sort_types="-1",
            )
            rows = self._require_datacenter_rows(payload, operation="statements")
            for row in rows[:periods]:
                period_raw = row.get("REPORT_DATE")
                if not isinstance(period_raw, str) or not period_raw.strip():
                    raise DataContractError(
                        "Eastmoney statement missing REPORT_DATE",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                        },
                    )
                period_end = parse_shanghai_date(period_raw)
                published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
                keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
                if excluded:
                    unknown_excluded = True
                if not keep:
                    continue
                for code, name in item_columns[stype]:
                    if code not in row:
                        continue
                    value = decimal_from_text(row.get(code), field=code)
                    lines.append(
                        FinancialStatementLine(
                            statement_type=stype,
                            period_end=period_end,
                            published_at=published_at,
                            item_code=code[:64],
                            item_name=name[:200],
                            value=value,
                            unit="CNY",
                        )
                    )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not lines:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                },
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.FINANCIAL_STATEMENTS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(lines), meta=meta)

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        records: list[UnlockRecord | DividendRecord] = []
        unknown_excluded = False

        # Unlocks
        unlock_payload = await self._datacenter_get(
            report_name="RPT_SHAREFROZEN_DETAIL",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=(
                "SECUCODE,SECURITY_CODE,FREE_DATE,NOTICE_DATE,FREE_SHARES_TYPE,"
                "FREE_SHARES,LIFT_MARKET_CAP,ACTUAL_FREE_SHARES"
            ),
            page_size=50,
            operation="corporate_actions",
            sort_columns="FREE_DATE",
            sort_types="-1",
        )
        for row in self._require_datacenter_rows(unlock_payload, operation="corporate_actions"):
            unlock_raw = row.get("FREE_DATE")
            if not isinstance(unlock_raw, str) or not unlock_raw.strip():
                raise DataContractError(
                    "Eastmoney unlock missing FREE_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "corporate_actions",
                        "rule": "contract_drift",
                    },
                )
            unlock_date = parse_shanghai_date(unlock_raw)
            if start is not None and unlock_date < start:
                continue
            if end is not None and unlock_date > end:
                continue
            published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            unlock_type = row.get("FREE_SHARES_TYPE")
            if unlock_type is not None and not isinstance(unlock_type, str):
                unlock_type = None
            unlock_shares = int_from_text(row.get("FREE_SHARES"), field="unlock_shares")
            tradable = int_from_text(row.get("ACTUAL_FREE_SHARES"), field="tradable_shares")
            mkt = decimal_from_text(row.get("LIFT_MARKET_CAP"), field="market_value_cny")
            records.append(
                UnlockRecord(
                    unlock_date=unlock_date,
                    published_at=published_at,
                    unlock_type=unlock_type.strip()[:64] if unlock_type else None,
                    unlock_shares=unlock_shares,
                    tradable_shares=tradable,
                    market_value_cny=mkt,
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )

        # Dividends
        div_payload = await self._datacenter_get(
            report_name="RPT_SHAREBONUS_DET",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=(
                "SECUCODE,SECURITY_CODE,REPORT_DATE,NOTICE_DATE,PLAN_NOTICE_DATE,"
                "IMPL_PLAN_PROFILE,ASSIGN_PROGRESS,EX_DIVIDEND_DATE,"
                "CASH_DIVIDEND_RATIO,BONUS_SHARE_RATIO,TRANSFER_RATIO"
            ),
            page_size=50,
            operation="corporate_actions",
            sort_columns="NOTICE_DATE",
            sort_types="-1",
        )
        for row in self._require_datacenter_rows(div_payload, operation="corporate_actions"):
            report_raw = row.get("REPORT_DATE") or row.get("NOTICE_DATE")
            if not isinstance(report_raw, str) or not report_raw.strip():
                raise DataContractError(
                    "Eastmoney dividend missing REPORT_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "corporate_actions",
                        "rule": "contract_drift",
                    },
                )
            report_day = parse_shanghai_date(report_raw)
            fiscal_year = report_day.year
            published_at = parse_shanghai_datetime(
                row.get("NOTICE_DATE") or row.get("PLAN_NOTICE_DATE"),
                field="published_at",
            )
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            ex_raw = row.get("EX_DIVIDEND_DATE")
            ex_date = None
            if isinstance(ex_raw, str) and ex_raw.strip() and ex_raw.strip() != "-":
                ex_date = parse_shanghai_date(ex_raw)
            if start is not None and ex_date is not None and ex_date < start:
                continue
            if end is not None and ex_date is not None and ex_date > end:
                continue
            plan = row.get("ASSIGN_PROGRESS") or row.get("IMPL_PLAN_PROFILE") or "unknown"
            if not isinstance(plan, str) or not plan.strip():
                plan = "unknown"
            records.append(
                DividendRecord(
                    fiscal_year=fiscal_year,
                    plan_status=plan.strip()[:64],
                    ex_date=ex_date,
                    cash_per_share=decimal_from_text(
                        row.get("CASH_DIVIDEND_RATIO"), field="cash_per_share"
                    ),
                    bonus_shares_per_share=decimal_from_text(
                        row.get("BONUS_SHARE_RATIO"), field="bonus_shares_per_share"
                    ),
                    transfer_shares_per_share=decimal_from_text(
                        row.get("TRANSFER_RATIO"), field="transfer_shares_per_share"
                    ),
                    published_at=published_at,
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )

        def _actions_sort_key(
            item: UnlockRecord | DividendRecord,
        ) -> tuple[object, ...]:
            # Matches AShareSnapshotService corporate-actions order contract:
            # published_at desc (None last), Unlock before Dividend, then
            # stable identity fields.
            pub = (1, 0.0) if item.published_at is None else (0, -item.published_at.timestamp())
            if isinstance(item, UnlockRecord):
                return (
                    *pub,
                    0,
                    -item.unlock_date.toordinal(),
                    item.unlock_type or "",
                    item.unlock_shares if item.unlock_shares is not None else -1,
                )
            return (
                *pub,
                1,
                -item.fiscal_year,
                item.plan_status,
                item.ex_date.toordinal() if item.ex_date is not None else -1,
            )

        records.sort(key=_actions_sort_key)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        # Legitimate empty is success for corporate actions.
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.CORPORATE_ACTIONS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(records), meta=meta)

    # Bounded Eastmoney report page size and page-budget for exact offset + cutoff.
    _REPORT_PAGE_SIZE_MAX = 50
    _REPORT_MAX_PAGES = 8

    async def search_reports(
        self,
        *,
        text: str | None,
        instrument: Instrument | None,
        industry_code: str | None,
        published_from: date | None,
        published_to: date | None,
        limit: int,
        offset: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AnalystReportItem, ...]]:
        """Exact arbitrary-offset report search via adjacent page fetches.

        Offset semantics
        ----------------
        ``offset`` / ``limit`` apply to the **provider stream after publication
        cutoff** (and date-window filters), not to raw vendor rows. Rows with
        ``published_at > as_of`` or unknown publication time (historical) are
        dropped **before** the offset counter advances. Cross-page duplicate
        ``report_key`` values use first-seen deterministic dedupe (live pages
        can shift); the product service still rejects duplicate keys in the
        final adapter/cache payload.

        Page budget (fail-closed)
        -------------------------
        At most ``_REPORT_MAX_PAGES`` pages of size
        ``min(limit, _REPORT_PAGE_SIZE_MAX)`` are fetched. Before network, a
        raw window ``offset + limit`` that cannot fit that bounded capacity is
        rejected with ``DataContractError`` / ``page_budget_exceeded``. During
        fetch, if the page budget is exhausted while the last page was full
        and post-cutoff ``offset + limit`` was not satisfied, raise
        ``PartialDataError`` / ``page_budget_exhausted`` — never return a
        silently incomplete page.
        """
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise DataContractError(
                "offset must be a nonnegative int",
                details={"field": "offset", "rule": "nonnegative"},
            )
        if (
            published_from is not None
            and published_to is not None
            and published_to < published_from
        ):
            raise DataContractError(
                "published_to must be >= published_from",
                details={"field": "published_to", "rule": "range_order"},
            )

        page_size = min(max(limit, 1), self._REPORT_PAGE_SIZE_MAX)
        # Pages needed for offset+limit post-cutoff rows (raw capacity bound).
        needed = offset + limit
        max_capacity = self._REPORT_MAX_PAGES * page_size
        if needed > max_capacity:
            raise DataContractError(
                "requested offset+limit exceeds bounded report page capacity",
                details={
                    "field": "offset",
                    "rule": "page_budget_exceeded",
                    "offset": offset,
                    "limit": limit,
                    "max_capacity": max_capacity,
                    "max_pages": self._REPORT_MAX_PAGES,
                    "page_size": page_size,
                },
            )
        # Budget includes a small buffer for cutoff/dedupe losses, capped at max.
        page_budget = min(
            self._REPORT_MAX_PAGES,
            max(1, (needed + page_size - 1) // page_size + 2),
        )
        base_params: dict[str, str] = {
            "pageSize": str(page_size),
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "industryCode": (
                industry_code.strip()
                if isinstance(industry_code, str) and industry_code.strip()
                else ""
            ),
        }
        if instrument is not None:
            code6, _ = require_a_share_instrument(instrument)
            base_params["code"] = code6
        if isinstance(text, str) and text.strip():
            # Wire query may include q; never put raw free text into fingerprints.
            base_params["q"] = text.strip()[:200]
        if published_from is not None:
            base_params["beginTime"] = published_from.isoformat()
        if published_to is not None:
            base_params["endTime"] = published_to.isoformat()

        # Stream provider pages (gate-serial). Cutoff/dedupe first, then exact
        # offset skip on the kept stream, then take limit. Final page is sorted
        # deterministically for stable output.
        kept: list[AnalystReportItem] = []
        seen_keys: set[str] = set()
        unknown_excluded = False
        skipped = 0
        page_no = 1
        pages_fetched = 0
        last_page_full = False
        budget_exhausted = False
        while pages_fetched < page_budget and len(kept) < limit:
            params = dict(base_params)
            params["pageNo"] = str(page_no)
            response = await self._gated_send(
                HttpRequest(
                    method="GET",
                    url=_REPORT_LIST_URL,
                    params=params,
                    headers=self._headers(),
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation="reports")
            self._require_json_content_type(response.headers, operation="reports")
            payload = loads_json_decimal(response.body)
            if not isinstance(payload, dict):
                raise DataContractError(
                    "Eastmoney reports payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                    },
                )
            rows = payload.get("data")
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise DataContractError(
                    "Eastmoney reports data must be a list",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                    },
                )
            pages_fetched += 1
            if not rows:
                last_page_full = False
                break  # short/empty page — do not invent rows

            # Fail closed if envelope claims more rows than page_size.
            if len(rows) > page_size:
                raise DataContractError(
                    "Eastmoney reports page exceeds declared pageSize",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "envelope_drift",
                    },
                )

            for idx, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise DataContractError(
                        "Eastmoney report row failed contract validation",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "reports",
                            "rule": "contract_drift",
                            "index": idx,
                        },
                    )
                key = row.get("infoCode") or row.get("id") or row.get("encodeUrl")
                title = row.get("title") or row.get("titleNew")
                if key is None or (isinstance(key, str) and not str(key).strip()):
                    raise DataContractError(
                        "Eastmoney report missing key",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "reports",
                            "rule": "contract_drift",
                        },
                    )
                if not isinstance(title, str) or not title.strip():
                    raise DataContractError(
                        "Eastmoney report missing title",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "reports",
                            "rule": "contract_drift",
                        },
                    )
                report_key = str(key).strip()[:200]
                # Cross-page duplicate: first-seen deterministic dedupe (live
                # pages can shift). Never emit duplicate keys in the final
                # tuple; product service also rejects duplicates from cache.
                if report_key in seen_keys:
                    continue
                seen_keys.add(report_key)

                pub_raw = row.get("publishDate") or row.get("datetime") or row.get("pubDate")
                published_at = parse_shanghai_datetime(pub_raw, field="published_at")
                keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
                if excluded:
                    unknown_excluded = True
                if not keep or published_at is None:
                    if published_at is None:
                        unknown_excluded = True
                    continue
                pub_day = published_at.date()
                if published_from is not None and pub_day < published_from:
                    continue
                if published_to is not None and pub_day > published_to:
                    continue

                # Exact offset on post-cutoff kept stream (handles offset % page_size).
                if skipped < offset:
                    skipped += 1
                    continue
                if len(kept) >= limit:
                    break

                institution = row.get("orgSName") or row.get("orgName")
                if institution is not None and not isinstance(institution, str):
                    institution = None
                analysts_raw = row.get("researcher") or row.get("author")
                analysts: list[str] = []
                if isinstance(analysts_raw, str) and analysts_raw.strip():
                    analysts = [a.strip()[:200] for a in analysts_raw.split(",") if a.strip()]
                rating = row.get("emRatingName") or row.get("rating")
                if rating is not None and not isinstance(rating, str):
                    rating = None
                target = decimal_from_text(row.get("aimPriceT"), field="target_price")
                encode = row.get("encodeUrl") or row.get("infoCode")
                source_url = None
                if isinstance(encode, str) and encode.strip():
                    source_url = sanitize_public_url(
                        f"https://data.eastmoney.com/report/info/{encode.strip()}.html",
                        field="source_url",
                    )
                kept.append(
                    AnalystReportItem(
                        report_key=report_key,
                        title=title.strip()[:500],
                        institution=institution.strip()[:200] if institution else None,
                        analyst_names=tuple(analysts),
                        published_at=published_at,
                        rating=rating.strip()[:64] if rating else None,
                        target_price=target,
                        eps_forecasts=(),
                        source_url=source_url,
                        pdf_url=None,
                    )
                )

            # Short page ⇒ no further pages (legitimate end of stream).
            if len(rows) < page_size:
                last_page_full = False
                break
            last_page_full = True
            page_no += 1
            if pages_fetched >= page_budget and len(kept) < limit:
                budget_exhausted = True
                break

        # Fail closed: budget exhausted on a full final page without filling
        # the requested post-cutoff page. Do not silently return incomplete.
        if (budget_exhausted or (pages_fetched >= page_budget and last_page_full)) and len(
            kept
        ) < limit:
            raise PartialDataError(
                "report page budget exhausted before offset+limit satisfied",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "reports",
                    "rule": "page_budget_exhausted",
                    "pages_fetched": pages_fetched,
                    "page_budget": page_budget,
                    "kept": len(kept),
                    "limit": limit,
                    "offset": offset,
                    "skipped": skipped,
                },
            )

        # Deterministic output order (never invent rows).
        kept.sort(key=lambda r: (-r.published_at.timestamp(), r.report_key))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.RESEARCH_REPORTS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(kept), meta=meta)

    async def get_consensus(
        self, instrument: Instrument, *, as_of: datetime
    ) -> ProviderSuccess[tuple[ConsensusEstimate, ...]]:
        """Consensus via reportapi ranking fields when present (primary).

        At most one explicit year/EPS pair per row; malformed rows ignored
        deterministically; a single institution/report is never double-counted.
        """
        self._require_configured()
        self._require_as_of(as_of)
        code6, _ = require_a_share_instrument(instrument)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_REPORT_LIST_URL,
                params={
                    "pageNo": "1",
                    "pageSize": "20",
                    "code": code6,
                    "qType": "0",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="consensus")
        self._require_json_content_type(response.headers, operation="consensus")
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney consensus payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "contract_drift",
                },
            )
        rows = payload.get("data")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise DataContractError(
                "Eastmoney consensus data must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "contract_drift",
                },
            )
        # year -> list of (dedupe_key, eps)
        by_year: dict[int, list[tuple[str, Decimal]]] = {}
        seen_pair: set[tuple[int, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue  # ignore malformed rows deterministically
            year: int | None = None
            eps: Decimal | None = None
            # At most one explicit year/EPS pair per row (first match wins).
            for ykey, ekey in (
                ("predictYear", "predictThisYearEps"),
                ("year", "eps"),
                ("fiscal_year", "mean"),
            ):
                y = row.get(ykey)
                e = row.get(ekey)
                if y is None or e is None:
                    continue
                try:
                    year = int(y) if not isinstance(y, int) or isinstance(y, bool) else y
                except (TypeError, ValueError):
                    year = None
                    continue
                if year < 1990 or year > 2100:
                    year = None
                    continue
                eps = decimal_from_text(e, field="eps")
                if eps is None:
                    year = None
                    continue
                break  # one pair only
            if year is None or eps is None:
                continue
            # Dedup key: prefer report/info id, else institution name, else row hash-ish.
            inst = row.get("orgSName") or row.get("orgName") or row.get("orgCode")
            rkey = row.get("infoCode") or row.get("id") or row.get("encodeUrl")
            if isinstance(rkey, str) and rkey.strip():
                dedupe = f"r:{rkey.strip()}"
            elif isinstance(inst, str) and inst.strip():
                dedupe = f"i:{inst.strip()}"
            else:
                dedupe = f"e:{year}:{eps}"
            pair_key = (year, dedupe)
            if pair_key in seen_pair:
                continue
            seen_pair.add(pair_key)
            by_year.setdefault(year, []).append((dedupe, eps))
        estimates: list[ConsensusEstimate] = []
        for year in sorted(by_year):
            vals = [eps for _, eps in by_year[year]]
            mean = sum(vals) / Decimal(len(vals))
            estimates.append(
                ConsensusEstimate(
                    fiscal_year=year,
                    metric="eps",
                    mean=mean,
                    high=max(vals),
                    low=min(vals),
                    institution_count=len(vals),
                )
            )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not estimates:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                },
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(estimates),
            meta=self._meta(
                category=DataCategory.RESEARCH_REPORTS,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[NewsItem, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        require_aware_datetime(start, field_name="start")
        require_aware_datetime(end, field_name="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        params: dict[str, str] = {
            "client": "web",
            "biz": "web_news_col",
            "fastColumn": "102",
            "pageSize": str(limit),
            "pageNo": "1",
        }
        if instrument is not None:
            code6, suffix = require_a_share_instrument(instrument)
            params["code"] = f"{code6}.{suffix}"
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_NEWS_LIST_URL,
                params=params,
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="news")
        self._require_json_content_type(response.headers, operation="news")
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney news payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        data = payload.get("data")
        rows: list[Any]
        if data is None:
            rows = []
        elif isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            inner = data.get("list") or data.get("items") or data.get("news")
            if inner is None:
                rows = []
            elif isinstance(inner, list):
                rows = inner
            else:
                raise DataContractError(
                    "Eastmoney news list failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
        else:
            raise DataContractError(
                "Eastmoney news data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        items: list[NewsItem] = []
        for idx, row in enumerate(rows):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "Eastmoney news row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key = row.get("code") or row.get("id") or row.get("newsid") or row.get("art_code")
            title = row.get("title") or row.get("showTitle")
            if key is None or (isinstance(key, str) and not str(key).strip()):
                raise DataContractError(
                    "Eastmoney news missing key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
            if not isinstance(title, str) or not title.strip():
                raise DataContractError(
                    "Eastmoney news missing title",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
            pub_raw = (
                row.get("showTime") or row.get("publishTime") or row.get("time") or row.get("date")
            )
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            if published_at is None:
                continue
            if published_at < start or published_at > end or published_at > as_of:
                continue
            summary = row.get("summary") or row.get("digest") or row.get("content")
            if summary is not None and not isinstance(summary, str):
                summary = None
            if isinstance(summary, str):
                summary = summary[:4000]
            url_raw = row.get("url") or row.get("source_url")
            source_url = None
            if isinstance(url_raw, str) and url_raw.strip():
                source_url = sanitize_public_url(url_raw, field="source_url")
            else:
                source_url = sanitize_public_url(
                    f"https://finance.eastmoney.com/a/{str(key).strip()}.html",
                    field="source_url",
                )
            source_name = row.get("mediaName") or row.get("source") or "东方财富"
            if not isinstance(source_name, str) or not source_name.strip():
                source_name = "东方财富"
            items.append(
                NewsItem(
                    news_key=str(key).strip()[:200],
                    title=title.strip()[:500],
                    summary=summary,
                    published_at=published_at,
                    source_name=source_name.strip()[:200],
                    source_url=source_url,
                )
            )
        items.sort(key=lambda n: (-n.published_at.timestamp(), n.news_key))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(items),
            meta=self._meta(
                category=DataCategory.NEWS,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_announcements(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[Any, ...]]:
        # Announcements chain is cninfo → exchanges; Eastmoney does not own it.
        raise DataContractError(
            "eastmoney does not implement announcements in Phase 1E E3",
            details={
                "vendor": self.vendor_id.value,
                "operation": "announcements",
                "rule": "unsupported",
                "category": DataCategory.ANNOUNCEMENTS.value,
            },
        )

    # --- E4a capital / chips ---------------------------------------------------

    async def get_intraday_flow(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_INTRADAY_FLOW_URL,
                params={
                    "lmt": "0",
                    "klt": "1",
                    "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="intraday_flow")
        self._require_json_content_type(response.headers, operation="intraday_flow")
        payload = loads_json_decimal(response.body)
        points = self._parse_fflow_klines(
            payload,
            operation="intraday_flow",
            interval=BarInterval.ONE_MINUTE,
            as_of=as_of,
            start=None,
            end=None,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(points),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_daily_flow(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_DAILY_FLOW_URL,
                params={
                    "lmt": "0",
                    "klt": "101",
                    "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="daily_flow")
        self._require_json_content_type(response.headers, operation="daily_flow")
        payload = loads_json_decimal(response.body)
        points = self._parse_fflow_klines(
            payload,
            operation="daily_flow",
            interval=BarInterval.ONE_DAY,
            as_of=as_of,
            start=start,
            end=end,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(points),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    def _parse_fflow_klines(
        self,
        payload: object,
        *,
        operation: str,
        interval: BarInterval,
        as_of: datetime,
        start: date | None,
        end: date | None,
    ) -> list[FundFlowPoint]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney fund-flow payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, dict):
            raise DataContractError(
                "Eastmoney fund-flow data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        klines = data.get("klines")
        if klines is None:
            return []
        if not isinstance(klines, list):
            raise DataContractError(
                "Eastmoney fund-flow klines must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        points: list[FundFlowPoint] = []
        for idx, raw in enumerate(klines):
            if not isinstance(raw, str) or not raw.strip():
                raise DataContractError(
                    "Eastmoney fund-flow kline row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            parts = raw.split(",")
            if len(parts) < 6:
                raise DataContractError(
                    "Eastmoney fund-flow kline row missing fields",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            ts_raw = parts[0].strip()
            if interval is BarInterval.ONE_DAY:
                day = parse_shanghai_date(ts_raw)
                if start is not None and day < start:
                    continue
                if end is not None and day > end:
                    continue
                occurred_at: datetime = combine_shanghai_date_time(day, "15:00:00")
            else:
                parsed_at = parse_shanghai_datetime(ts_raw, field="occurred_at")
                if parsed_at is None:
                    raise DataContractError(
                        "Eastmoney fund-flow timestamp missing",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "contract_drift",
                            "index": idx,
                        },
                    )
                occurred_at = parsed_at
            if occurred_at > as_of:
                continue
            points.append(
                FundFlowPoint(
                    occurred_at=occurred_at,
                    interval=interval,
                    main_net_cny=decimal_from_text(parts[1], field="main_net_cny"),
                    super_large_net_cny=decimal_from_text(parts[2], field="super_large_net_cny"),
                    large_net_cny=decimal_from_text(parts[3], field="large_net_cny"),
                    medium_net_cny=decimal_from_text(parts[4], field="medium_net_cny"),
                    small_net_cny=decimal_from_text(parts[5], field="small_net_cny"),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
        points.sort(key=lambda p: p.occurred_at)
        return points

    async def get_northbound(
        self, *, start: date | None, end: date | None, as_of: datetime
    ) -> ProviderSuccess[tuple[NorthboundFlowPoint, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        filter_expr = ""
        if start is not None and end is not None:
            filter_expr = f"(TRADE_DATE>='{start.isoformat()}')(TRADE_DATE<='{end.isoformat()}')"
        elif start is not None:
            filter_expr = f"(TRADE_DATE>='{start.isoformat()}')"
        elif end is not None:
            filter_expr = f"(TRADE_DATE<='{end.isoformat()}')"
        payload = await self._datacenter_get(
            report_name="RPT_MUTUAL_DEAL_HISTORY",
            filter_expr=filter_expr,
            columns=("TRADE_DATE,MUTUAL_TYPE,BUY_AMT,SELL_AMT,NET_DEAL_AMT,FUND_INFLOW"),
            page_size=100,
            operation="northbound",
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        points: list[NorthboundFlowPoint] = []
        incomplete = False
        for row in self._require_datacenter_rows(payload, operation="northbound"):
            mutual = row.get("MUTUAL_TYPE")
            if not isinstance(mutual, str):
                mutual = str(mutual) if mutual is not None else ""
            channel = _NORTHBOUND_MUTUAL_TYPES.get(mutual.strip())
            if channel is None:
                continue
            trade_raw = row.get("TRADE_DATE")
            if not isinstance(trade_raw, str) or not trade_raw.strip():
                raise DataContractError(
                    "Eastmoney northbound missing TRADE_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                    },
                )
            trade_date = parse_shanghai_date(trade_raw)
            if start is not None and trade_date < start:
                continue
            if end is not None and trade_date > end:
                continue
            as_of_day = as_of.astimezone(SHANGHAI).date()
            if trade_date > as_of_day:
                continue
            buy = decimal_from_text(row.get("BUY_AMT"), field="buy_cny")
            sell = decimal_from_text(row.get("SELL_AMT"), field="sell_cny")
            net = decimal_from_text(row.get("NET_DEAL_AMT"), field="net_buy_cny")
            if buy is None and sell is None and net is None:
                incomplete = True
            # Vendor unit is 百万元 → CNY.
            buy_cny = buy * _MILLION_CNY if buy is not None else None
            sell_cny = sell * _MILLION_CNY if sell is not None else None
            net_cny = net * _MILLION_CNY if net is not None else None
            points.append(
                NorthboundFlowPoint(
                    trade_date=trade_date,
                    channel=channel,
                    net_buy_cny=net_cny,
                    buy_cny=buy_cny,
                    sell_cny=sell_cny,
                    disclosure_note=("Eastmoney mutual deal history; not HKEX official daily"),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        points.sort(key=lambda p: (p.trade_date, p.channel))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        warn_codes: list[str] = [
            "NORTHBOUND_DISCLOSURE_INCOMPLETE",
            "LOW_RELIABILITY_MARKET_SIGNAL",
        ]
        if incomplete:
            pass  # already flagged via disclosure_note + warnings
        meta = self._meta(
            category=DataCategory.CAPITAL,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        from dataclasses import replace as _replace

        meta = _replace(meta, warnings=tuple(warn_codes))
        _ = now
        return ProviderSuccess(value=tuple(points), meta=meta)

    async def get_dragon_tiger(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[DragonTigerRecord, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        trade_date = require_exact_date(trade_date, field="trade_date")
        as_of_day = as_of.astimezone(SHANGHAI).date()
        if trade_date > as_of_day:
            raise DataContractError(
                "trade_date must not be later than as_of local day",
                details={"field": "trade_date", "rule": "as_of_cutoff"},
            )
        day_s = trade_date.isoformat()
        filter_expr = f"(TRADE_DATE='{day_s}')"
        code6: str | None = None
        if instrument is not None:
            code6, _suffix = require_a_share_instrument(instrument)
            filter_expr = f"(TRADE_DATE='{day_s}')(SECURITY_CODE=\"{code6}\")"
        summary_payload = await self._datacenter_get(
            report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_expr=filter_expr,
            columns=(
                "TRADE_DATE,SECUCODE,SECURITY_CODE,EXPLANATION,EXPLAIN,"
                "BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_NET_AMT,"
                "SUM_BUY_AMT,SUM_SELL_AMT,NET_BS_AMT"
            ),
            page_size=50,
            operation="dragon_tiger",
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        rows = self._require_datacenter_rows(summary_payload, operation="dragon_tiger")
        records: list[DragonTigerRecord] = []
        for row in rows:
            sec = row.get("SECUCODE") or row.get("SECURITY_CODE")
            if not isinstance(sec, str) or not sec.strip():
                raise DataContractError(
                    "Eastmoney dragon tiger missing SECUCODE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "dragon_tiger",
                        "rule": "contract_drift",
                    },
                )
            sec_u = sec.strip().upper()
            if "." not in sec_u:
                # SECURITY_CODE alone — require instrument for identity.
                if instrument is None:
                    continue
                inst_id = instrument.instrument_id
            else:
                code_part, suffix_part = sec_u.rsplit(".", 1)
                inst_id = instrument_id_from_code(
                    code_part.zfill(6), suffix_part, asset=AssetType.EQUITY
                )
            if instrument is not None and inst_id != instrument.instrument_id:
                continue
            reason = row.get("EXPLANATION") or row.get("EXPLAIN") or "dragon_tiger"
            if not isinstance(reason, str) or not reason.strip():
                reason = "dragon_tiger"
            buy = require_decimal(
                row.get("BILLBOARD_BUY_AMT")
                if row.get("BILLBOARD_BUY_AMT") is not None
                else row.get("SUM_BUY_AMT"),
                field="buy_total_cny",
            )
            sell = require_decimal(
                row.get("BILLBOARD_SELL_AMT")
                if row.get("BILLBOARD_SELL_AMT") is not None
                else row.get("SUM_SELL_AMT"),
                field="sell_total_cny",
            )
            net = decimal_from_text(
                row.get("BILLBOARD_NET_AMT")
                if row.get("BILLBOARD_NET_AMT") is not None
                else row.get("NET_BS_AMT"),
                field="net_buy_cny",
            )
            if net is None:
                net = buy - sell
            seats = await self._fetch_dragon_seats(
                trade_date=trade_date,
                security_code=code6 or sec_u.split(".")[0].zfill(6),
            )
            records.append(
                DragonTigerRecord(
                    trade_date=trade_date,
                    instrument_id=inst_id,
                    reason=reason.strip()[:500],
                    buy_total_cny=buy,
                    sell_total_cny=sell,
                    net_buy_cny=net,
                    seats=tuple(seats),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
        records.sort(key=lambda r: (r.instrument_id, r.reason))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def _fetch_dragon_seats(
        self, *, trade_date: date, security_code: str
    ) -> list[DragonTigerSeat]:
        day_s = trade_date.isoformat()
        payload = await self._datacenter_get(
            report_name="RPT_OPERATEDEPT_TRADE",
            filter_expr=(f"(TRADE_DATE='{day_s}')(SECURITY_CODE=\"{security_code}\")"),
            columns=("RANK,TRADE_DIRECTION,OPERATEDEPT_NAME,BUY_AMT_REAL,SELL_AMT_REAL,NET"),
            page_size=40,
            operation="dragon_tiger",
            sort_columns="RANK",
            sort_types="1",
        )
        seats: list[DragonTigerSeat] = []
        for row in self._require_datacenter_rows(payload, operation="dragon_tiger"):
            rank = require_int(row.get("RANK"), field="rank")
            direction = row.get("TRADE_DIRECTION")
            direction_s = str(direction).strip() if direction is not None else "0"
            # 0 = buy ranking, 1 = sell ranking (Eastmoney convention).
            side = "sell" if direction_s in {"1", "卖", "sell"} else "buy"
            branch = row.get("OPERATEDEPT_NAME")
            if not isinstance(branch, str) or not branch.strip():
                continue
            if side == "buy":
                amount = require_decimal(row.get("BUY_AMT_REAL"), field="amount_cny")
            else:
                amount = require_decimal(row.get("SELL_AMT_REAL"), field="amount_cny")
            name_l = branch.strip()
            is_inst = "机构" in name_l or "专用" in name_l
            seats.append(
                DragonTigerSeat(
                    rank=rank,
                    side=side,
                    branch_name=name_l[:200],
                    amount_cny=amount,
                    is_institution=is_inst if is_inst else None,
                )
            )
        return seats

    async def get_margin(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[MarginRecord, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, _suffix = require_a_share_instrument(instrument)
        payload = await self._datacenter_get(
            report_name="RPTA_WEB_RZRQ_GGMX",
            filter_expr=f'(SCODE="{code6}")',
            columns="DATE,SCODE,RZYE,RZMRE,RZCHE,RQYE,RQYL,RQCHL",
            page_size=min(limit, 100),
            operation="margin",
            sort_columns="DATE",
            sort_types="-1",
        )
        as_of_day = as_of.astimezone(SHANGHAI).date()
        records: list[MarginRecord] = []
        for row in self._require_datacenter_rows(payload, operation="margin"):
            day_raw = row.get("DATE")
            if not isinstance(day_raw, str) or not day_raw.strip():
                raise DataContractError(
                    "Eastmoney margin missing DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "margin",
                        "rule": "contract_drift",
                    },
                )
            trade_date = parse_shanghai_date(day_raw)
            if trade_date > as_of_day:
                continue
            records.append(
                MarginRecord(
                    trade_date=trade_date,
                    financing_balance_cny=require_decimal(
                        row.get("RZYE"), field="financing_balance_cny"
                    ),
                    financing_buy_cny=require_decimal(row.get("RZMRE"), field="financing_buy_cny"),
                    financing_repayment_cny=require_decimal(
                        row.get("RZCHE"), field="financing_repayment_cny"
                    ),
                    securities_lending_balance_cny=decimal_from_text(
                        row.get("RQYE"), field="securities_lending_balance_cny"
                    ),
                    securities_lending_sell_shares=int_from_text(
                        row.get("RQYL"), field="securities_lending_sell_shares"
                    ),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
            if len(records) >= limit:
                break
        records.sort(key=lambda r: r.trade_date, reverse=True)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_block_trades(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[BlockTradeRecord, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, _suffix = require_a_share_instrument(instrument)
        payload = await self._datacenter_get(
            report_name="RPT_DATA_BLOCKTRADE",
            filter_expr=f'(SECURITY_CODE="{code6}")',
            columns=(
                "TRADE_DATE,DEAL_PRICE,DEAL_VOLUME,DEAL_AMT,PREMIUM_RATIO,BUYER_NAME,SELLER_NAME"
            ),
            page_size=min(limit, 100),
            operation="block_trades",
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        as_of_day = as_of.astimezone(SHANGHAI).date()
        records: list[BlockTradeRecord] = []
        for row in self._require_datacenter_rows(payload, operation="block_trades"):
            day_raw = row.get("TRADE_DATE")
            if not isinstance(day_raw, str) or not day_raw.strip():
                raise DataContractError(
                    "Eastmoney block trade missing TRADE_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "block_trades",
                        "rule": "contract_drift",
                    },
                )
            trade_date = parse_shanghai_date(day_raw)
            if trade_date > as_of_day:
                continue
            buyer = row.get("BUYER_NAME")
            seller = row.get("SELLER_NAME")
            records.append(
                BlockTradeRecord(
                    trade_date=trade_date,
                    price=require_decimal(row.get("DEAL_PRICE"), field="price"),
                    volume_shares=require_int(row.get("DEAL_VOLUME"), field="volume_shares"),
                    amount_cny=require_decimal(row.get("DEAL_AMT"), field="amount_cny"),
                    premium_percent=decimal_from_text(
                        row.get("PREMIUM_RATIO"), field="premium_percent"
                    ),
                    buyer_branch=(
                        buyer.strip()[:200] if isinstance(buyer, str) and buyer.strip() else None
                    ),
                    seller_branch=(
                        seller.strip()[:200] if isinstance(seller, str) and seller.strip() else None
                    ),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
            if len(records) >= limit:
                break
        records.sort(
            key=lambda r: (
                -r.trade_date.toordinal(),
                r.price,
                r.volume_shares,
                r.amount_cny,
                r.buyer_branch or "",
                r.seller_branch or "",
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_shareholder_counts(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[ShareholderCountRecord, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        payload = await self._datacenter_get(
            report_name="RPT_F10_EH_HOLDERNUM",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=("END_DATE,NOTICE_DATE,HOLDER_TOTAL_NUM,TOTAL_NUM_RATIO,AVG_FREE_SHARES"),
            page_size=min(limit, 100),
            operation="shareholder_counts",
            sort_columns="END_DATE",
            sort_types="-1",
        )
        records: list[ShareholderCountRecord] = []
        unknown_excluded = False
        for row in self._require_datacenter_rows(payload, operation="shareholder_counts"):
            end_raw = row.get("END_DATE")
            if not isinstance(end_raw, str) or not end_raw.strip():
                raise DataContractError(
                    "Eastmoney shareholder missing END_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "shareholder_counts",
                        "rule": "contract_drift",
                    },
                )
            period_end = parse_shanghai_date(end_raw)
            published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            records.append(
                ShareholderCountRecord(
                    period_end=period_end,
                    published_at=published_at,
                    shareholder_count=require_int(
                        row.get("HOLDER_TOTAL_NUM"), field="shareholder_count"
                    ),
                    change_percent=decimal_from_text(
                        row.get("TOTAL_NUM_RATIO"), field="change_percent"
                    ),
                    average_holding_shares=decimal_from_text(
                        row.get("AVG_FREE_SHARES"), field="average_holding_shares"
                    ),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
            if len(records) >= limit:
                break
        records.sort(
            key=lambda r: (
                -r.period_end.toordinal(),
                0 if r.published_at is not None else 1,
                -(r.published_at.timestamp()) if r.published_at is not None else 0.0,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.CAPITAL,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(records), meta=meta)

    async def get_chip_distribution(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[ChipDistributionSnapshot]:
        self._require_configured()
        sampled_now = self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "chip distribution supports equity only",
                details={"field": "instrument", "rule": "asset_support"},
            )
        local_day = as_of.astimezone(SHANGHAI).date()
        sessions = self._calendar.sessions_for(local_day)
        end_day = (
            local_day
            if sessions and as_of >= sessions[-1].end_at
            else self._calendar.previous_trading_day(local_day)
        )
        reverse_days: list[date] = []
        day = end_day
        while len(reverse_days) < 120:
            reverse_days.append(day)
            if len(reverse_days) < 120:
                day = self._calendar.previous_trading_day(day)
        expected_days = tuple(reversed(reverse_days))
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_KLINE_URL,
                params={
                    "secid": eastmoney_secid(code6, suffix),
                    "klt": "101",
                    "fqt": "1",
                    "beg": expected_days[0].strftime("%Y%m%d"),
                    "end": end_day.strftime("%Y%m%d"),
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="chip_distribution")
        self._require_json_content_type(response.headers, operation="chip_distribution")
        data = self._require_data_object(
            loads_json_decimal(response.body), operation="chip_distribution"
        )
        if data is None:
            raise DataContractError(
                "chip input is missing required sessions",
                details={"field": "data", "rule": "missing_session"},
            )
        response_code = data.get("code")
        response_market = data.get("market")
        expected_market = 1 if suffix == "SH" else 0
        if (
            response_code != code6
            or type(response_market) is not int
            or response_market != expected_market
        ):
            raise DataContractError(
                "chip input identity does not match requested instrument",
                details={"field": "data", "rule": "identity_mismatch"},
            )
        rows = data.get("klines")
        if not isinstance(rows, list) or len(rows) != 120:
            raise DataContractError(
                "chip rows must contain 120 sessions",
                details={"field": "klines", "rule": "exact_120"},
            )
        dates: list[date] = []
        inputs: list[ChipInputBar] = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, str):
                raise DataContractError(
                    "chip kline row must be string",
                    details={"field": "klines", "index": index, "rule": "type"},
                )
            parts = raw.split(",")
            if len(parts) != 11:
                raise DataContractError(
                    "chip kline row width mismatch",
                    details={"field": "klines", "index": index, "rule": "width"},
                )
            date_text = parts[0].strip()
            try:
                parsed_date = date.fromisoformat(date_text)
            except ValueError as exc:
                raise DataContractError(
                    "chip kline date invalid",
                    details={"field": "klines", "index": index, "rule": "date"},
                ) from exc
            if parsed_date.isoformat() != date_text:
                raise DataContractError(
                    "chip kline date invalid",
                    details={"field": "klines", "index": index, "rule": "date"},
                )
            dates.append(parsed_date)
            open_price = require_decimal(parts[1], field=f"chip[{index}].open")
            close_price = require_decimal(parts[2], field=f"chip[{index}].close")
            high_price = require_decimal(parts[3], field=f"chip[{index}].high")
            low_price = require_decimal(parts[4], field=f"chip[{index}].low")
            volume = require_decimal(parts[5], field=f"chip[{index}].volume")
            amount = require_decimal(parts[6], field=f"chip[{index}].amount")
            require_decimal(parts[7], field=f"chip[{index}].amplitude")
            require_decimal(parts[8], field=f"chip[{index}].pct_change")
            require_decimal(parts[9], field=f"chip[{index}].change")
            turnover_percent = require_decimal(
                parts[10], field=f"chip[{index}].turnover_percent"
            )
            if (
                min(open_price, close_price, high_price, low_price) <= 0
                or high_price < max(open_price, close_price, low_price)
                or low_price > min(open_price, close_price, high_price)
                or volume < 0
                or amount < 0
                or turnover_percent < 0
            ):
                raise DataContractError(
                    "chip kline numeric range mismatch",
                    details={"field": "klines", "index": index, "rule": "range"},
                )
            inputs.append(
                ChipInputBar(
                    low=low_price,
                    high=high_price,
                    close=close_price,
                    turnover_percent=turnover_percent,
                )
            )
        if tuple(dates) != expected_days:
            raise DataContractError(
                "chip sessions mismatch", details={"field": "klines", "rule": "session_sequence"}
            )
        result = derive_tp_chip_v1(inputs)
        snapshot_at = self._calendar.sessions_for(end_day)[-1].end_at
        fetched_at = sampled_now
        from dataclasses import replace

        meta = self._meta(
            category=DataCategory.CAPITAL,
            as_of=as_of,
            fetched_at=fetched_at,
            session=infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai"),
            data_timestamp=snapshot_at,
            adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        )
        meta = replace(meta, warnings=("DERIVED_CHIP_DISTRIBUTION",))
        return ProviderSuccess(
            value=ChipDistributionSnapshot(
                as_of=snapshot_at,
                bins=tuple(
                    ChipDistributionBin(
                        price_low=result.edges[i],
                        price_high=result.edges[i + 1],
                        holding_ratio=result.weights[i],
                    )
                    for i in range(len(result.weights))
                ),
                profit_ratio=result.profit_ratio,
                average_cost=result.average_cost,
                concentration_90=result.concentration_90,
                concentration_70=result.concentration_70,
                source_vendor=VendorId.EASTMONEY,
                reliability=ReliabilityLevel.LOW,
                is_authoritative=False,
                calculation_method="turnover_decay_uniform_range",
                algorithm_version="tp_chip_v1",
                lookback_sessions=120,
                input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
                bar_trade_date=end_day,
            ),
            meta=meta,
        )

    # --- E4b limit pools + sentiment -----------------------------------------

    async def get_limit_pools(
        self,
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[LimitUpContext]:
        """Fetch requested push2ex limit pools and compose LimitUpContext.

        Live-verified endpoints (2026-07-17): getTopicZTPool, getTopicZBPool,
        getTopicDTPool, getYesterdayZTPool. Counts/ladder are derived only from
        observed pool rows — no prediction or invented promotion rates.
        """
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        now = self._require_live_current_clist_as_of(as_of, trade_date, operation="limit_pools")
        self._require_current_clist_trade_date(trade_date, operation="limit_pools", now=now)
        ordered_pools = self._require_limit_pools(pools)
        date_param = trade_date.strftime("%Y%m%d")

        entries: list[LimitPoolEntry] = []
        counts: dict[LimitPoolType, int] = {
            LimitPoolType.LIMIT_UP: 0,
            LimitPoolType.LIMIT_DOWN: 0,
            LimitPoolType.BROKEN_LIMIT: 0,
            LimitPoolType.PREVIOUS_LIMIT_UP: 0,
        }
        for pool_type in ordered_pools:
            url = _POOL_URL_BY_TYPE[pool_type]
            sort = _POOL_SORT_BY_TYPE[pool_type]
            response = await self._gated_send(
                HttpRequest(
                    method="GET",
                    url=url,
                    params=self._pool_params(date_param, sort=sort),
                    headers=self._headers(),
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation="limit_pools")
            self._require_json_content_type(response.headers, operation="limit_pools")
            pool_entries = self._parse_limit_pool_body(
                response.body,
                pool_type=pool_type,
                trade_date=trade_date,
            )
            counts[pool_type] = len(pool_entries)
            entries.extend(pool_entries)

        # Deterministic order: pool enum order already applied; within pool by
        # instrument_id ascending (stable unique key with pool_type).
        entries.sort(
            key=lambda e: (
                list(LimitPoolType).index(e.pool_type),
                e.instrument_id,
            )
        )
        limit_up_count = counts[LimitPoolType.LIMIT_UP]
        limit_down_count = counts[LimitPoolType.LIMIT_DOWN]
        broken_limit_count = counts[LimitPoolType.BROKEN_LIMIT]
        # Only compute rates from pools that were actually requested.
        broken_rate: Decimal | None = None
        if LimitPoolType.LIMIT_UP in ordered_pools and LimitPoolType.BROKEN_LIMIT in ordered_pools:
            denom = limit_up_count + broken_limit_count
            if denom > 0:
                broken_rate = (Decimal(broken_limit_count) / Decimal(denom)).quantize(
                    Decimal("0.0001")
                )

        ladder, max_consecutive = self._build_limit_ladder(
            tuple(e for e in entries if e.pool_type is LimitPoolType.LIMIT_UP)
        )
        # promotion_rate requires prior-day→today identity match; without a
        # verified joined contract leave None (do not invent).
        context = LimitUpContext(
            trade_date=trade_date,
            entries=tuple(entries),
            limit_up_count=limit_up_count if LimitPoolType.LIMIT_UP in ordered_pools else 0,
            limit_down_count=limit_down_count if LimitPoolType.LIMIT_DOWN in ordered_pools else 0,
            broken_limit_count=broken_limit_count
            if LimitPoolType.BROKEN_LIMIT in ordered_pools
            else 0,
            broken_rate=broken_rate,
            max_consecutive_count=max_consecutive,
            promotion_rate=None,
            ladder=ladder,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=context,
            meta=self._meta(
                category=DataCategory.LIMIT_UP,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                current_cross_section=True,
            ),
        )

    def _require_limit_pools(self, pools: tuple[LimitPoolType, ...]) -> tuple[LimitPoolType, ...]:
        if not isinstance(pools, tuple):
            raise DataContractError(
                "pools must be a tuple of LimitPoolType",
                details={"field": "pools", "rule": "type"},
            )
        if not pools:
            raise DataContractError(
                "pools must not be empty at the adapter boundary",
                details={"field": "pools", "rule": "non_empty"},
            )
        seen: set[LimitPoolType] = set()
        ordered: list[LimitPoolType] = []
        for pool in pools:
            if not isinstance(pool, LimitPoolType):
                raise DataContractError(
                    "pools elements must be LimitPoolType",
                    details={"field": "pools", "rule": "type"},
                )
            if pool in seen:
                raise DataContractError(
                    "pools must not contain duplicates",
                    details={"field": "pools", "rule": "unique"},
                )
            seen.add(pool)
            ordered.append(pool)
        # Preserve caller order (service already normalizes to enum order).
        return tuple(ordered)

    def _parse_limit_pool_body(
        self,
        body: bytes,
        *,
        pool_type: LimitPoolType,
        trade_date: date,
    ) -> list[LimitPoolEntry]:
        payload = loads_json_decimal(body)
        data = self._require_data_object(payload, operation="limit_pools")
        if data is None:
            return []
        pool = data.get("pool")
        if pool is None:
            pool_list: list[object] = []
        elif not isinstance(pool, list):
            raise DataContractError(
                "Eastmoney limit pool list failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                },
            )
        else:
            pool_list = pool
        # Envelope integrity: qdate must equal requested trade_date (no relabeling).
        # Live probe 2026-07-17: date=20260715 and date=20260716 both returned
        # qdate=20260717 — date param is ignored; mismatch must reject.
        self._require_limit_pool_envelope(data, trade_date=trade_date, pool_len=len(pool_list))
        out: list[LimitPoolEntry] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(pool_list):
            if not isinstance(raw, dict):
                raise DataContractError(
                    "Eastmoney limit pool row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            entry = self._limit_pool_entry_from_row(
                raw, pool_type=pool_type, trade_date=trade_date, index=idx
            )
            if entry.instrument_id in seen_ids:
                raise DataContractError(
                    "Eastmoney limit pool returned duplicate instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen_ids.add(entry.instrument_id)
            out.append(entry)
        return out

    def _require_limit_pool_envelope(
        self,
        data: Mapping[str, Any],
        *,
        trade_date: date,
        pool_len: int,
    ) -> None:
        """Validate data.qdate and data.tc before emitting any pool rows."""
        expected_qdate = int(trade_date.strftime("%Y%m%d"))
        if "qdate" not in data:
            raise DataContractError(
                "Eastmoney limit pool missing data.qdate",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "qdate",
                    "rule": "qdate_required",
                },
            )
        qdate = require_int(data.get("qdate"), field="data.qdate")
        if qdate != expected_qdate:
            raise DataContractError(
                "Eastmoney limit pool qdate does not match requested trade_date; "
                "refusing to relabel current cross-section as historical",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "qdate",
                    "rule": "qdate_trade_date",
                    "requested": trade_date.isoformat(),
                    "observed": str(qdate),
                },
            )
        if "tc" not in data:
            raise DataContractError(
                "Eastmoney limit pool missing data.tc",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "tc",
                    "rule": "tc_required",
                },
            )
        tc = require_int(data.get("tc"), field="data.tc")
        if tc < 0:
            raise DataContractError(
                "Eastmoney limit pool tc must be nonnegative",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "tc",
                    "rule": "nonnegative",
                },
            )
        if tc != pool_len:
            raise DataContractError(
                "Eastmoney limit pool tc does not match returned pool length; "
                "refusing silent truncation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "tc",
                    "rule": "pool_completeness",
                    "declared": tc,
                    "returned": pool_len,
                },
            )

    def _limit_pool_entry_from_row(
        self,
        row: Mapping[str, Any],
        *,
        pool_type: LimitPoolType,
        trade_date: date,
        index: int,
    ) -> LimitPoolEntry:
        code_raw = row.get("c")
        if not isinstance(code_raw, str) or not code_raw.strip().isdigit():
            raise DataContractError(
                "Eastmoney limit pool row missing code",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        code6 = code_raw.strip().zfill(6)
        m_raw = row.get("m")
        m_int = require_int(m_raw, field=f"pool[{index}].m") if m_raw is not None else None
        if m_int is None or m_int not in _EM_M_TO_SUFFIX:
            # Fail closed rather than guess SH/SZ from code ranges.
            raise DataContractError(
                "Eastmoney limit pool row missing market m",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        suffix = _EM_M_TO_SUFFIX[m_int]
        instrument_id = instrument_id_from_code(code6, suffix, asset=AssetType.EQUITY)
        name_raw = row.get("n")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise DataContractError(
                "Eastmoney limit pool row missing name",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        # Live-observed: p is price * 100 (integer cents).
        price_raw = row.get("p")
        if price_raw is None:
            raise DataContractError(
                "Eastmoney limit pool row missing price p",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        last = require_decimal(price_raw, field=f"pool[{index}].p") / Decimal(100)
        change_percent = require_decimal(row.get("zdp"), field=f"pool[{index}].zdp")
        consecutive = None
        if "lbc" in row and row.get("lbc") is not None:
            consecutive = require_int(row.get("lbc"), field=f"pool[{index}].lbc")
            if consecutive < 0:
                raise DataContractError(
                    "consecutive_limit_count must be nonnegative",
                    details={"field": "lbc", "rule": "nonnegative", "index": index},
                )
        days_and_boards = None
        zttj = row.get("zttj")
        if isinstance(zttj, dict):
            days = zttj.get("days")
            ct = zttj.get("ct")
            if days is not None and ct is not None:
                d_i = require_int(days, field=f"pool[{index}].zttj.days")
                c_i = require_int(ct, field=f"pool[{index}].zttj.ct")
                days_and_boards = f"{d_i}天{c_i}板"
        first_seal = self._pool_time_field(
            row.get("fbt"), trade_date=trade_date, field=f"pool[{index}].fbt"
        )
        last_seal = self._pool_time_field(
            row.get("lbt"), trade_date=trade_date, field=f"pool[{index}].lbt"
        )
        seal_amount = decimal_from_text(row.get("fund"), field=f"pool[{index}].fund")
        broken_count = None
        if "zbc" in row and row.get("zbc") is not None:
            broken_count = require_int(row.get("zbc"), field=f"pool[{index}].zbc")
            if broken_count < 0:
                raise DataContractError(
                    "broken_count must be nonnegative",
                    details={"field": "zbc", "rule": "nonnegative", "index": index},
                )
        industry = None
        hybk = row.get("hybk")
        if isinstance(hybk, str) and hybk.strip():
            industry = hybk.strip()
        return LimitPoolEntry(
            pool_type=pool_type,
            trade_date=trade_date,
            instrument_id=instrument_id,
            name=name_raw.strip(),
            last=last,
            change_percent=change_percent,
            consecutive_limit_count=consecutive,
            days_and_boards=days_and_boards,
            first_seal_at=first_seal,
            last_seal_at=last_seal,
            seal_amount_cny=seal_amount,
            broken_count=broken_count,
            industry=industry,
            reason_tags=(),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
        )

    def _pool_time_field(self, raw: object, *, trade_date: date, field: str) -> datetime | None:
        if raw is None:
            return None
        # Live-observed: integer HHMMSS without leading zero (92500 → 09:25:00).
        if isinstance(raw, Decimal):
            if raw != raw.to_integral_value():
                raise DataContractError(
                    f"{field} must be an integer time code",
                    details={"field": field, "rule": "time_format"},
                )
            value = int(raw)
        elif isinstance(raw, int) and not isinstance(raw, bool):
            value = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            value = int(raw.strip())
        else:
            raise DataContractError(
                f"{field} failed contract validation",
                details={"field": field, "rule": "contract_drift"},
            )
        if value < 0:
            raise DataContractError(
                f"{field} must be nonnegative",
                details={"field": field, "rule": "nonnegative"},
            )
        text = f"{value:06d}"
        return combine_shanghai_date_time(trade_date, text)

    def _build_limit_ladder(
        self, limit_up_entries: tuple[LimitPoolEntry, ...]
    ) -> tuple[tuple[LimitUpLadderRung, ...], int | None]:
        by_count: dict[int, list[str]] = {}
        for entry in limit_up_entries:
            if entry.consecutive_limit_count is None:
                continue
            by_count.setdefault(entry.consecutive_limit_count, []).append(entry.instrument_id)
        if not by_count:
            return (), None
        rungs: list[LimitUpLadderRung] = []
        for count in sorted(by_count):
            ids = tuple(sorted(set(by_count[count])))
            rungs.append(
                LimitUpLadderRung(
                    consecutive_limit_count=count,
                    instrument_count=len(ids),
                    instrument_ids=ids,
                )
            )
        return tuple(rungs), max(by_count)

    async def get_sentiment_signals(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        sources: tuple[SentimentSourceType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[SentimentSignal, ...]]:
        """Eastmoney current stockrank facts.

        Live-verified 2026-07-17: POST emappdata.../stockrank/getAllCurrentList.
        Current-only: ranks are a live cross-section and must not be labeled as
        an arbitrary historical trade_date. ``concept_heat`` is separately
        fetched for one required instrument from getHotStockRankList and returns
        concept hit counts, not a global concept ranking.
        """
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        # Sample adapter clock once; reject stale/historical before network.
        now = self._require_current_only_as_of_and_trade_date(
            as_of, trade_date, operation="sentiment"
        )
        ordered = self._require_sentiment_sources(sources)
        if instrument is not None:
            require_a_share_instrument(instrument)

        signals: list[SentimentSignal] = []
        for source in ordered:
            if source is SentimentSourceType.EASTMONEY_HOT:
                signals.extend(
                    await self._fetch_eastmoney_hot(instrument=instrument, trade_date=trade_date)
                )
            elif source is SentimentSourceType.CONCEPT_HEAT:
                if instrument is None or instrument.asset_type is AssetType.OPTION:
                    raise DataContractError(
                        "concept heat requires non-option instrument",
                        details={"field": "instrument", "rule": "asset_support"},
                    )
                signals.extend(
                    await self._fetch_concept_heat(
                        instrument=instrument, trade_date=trade_date, sampled_now=now, as_of=as_of
                    )
                )
            else:
                # ths_hot / interactive_qa / news are other vendors/categories.
                raise DataContractError(
                    "Eastmoney does not implement this sentiment source",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "source": source.value,
                        "rule": "unsupported",
                    },
                )

        # Deterministic order: rank asc then instrument_id.
        signals.sort(
            key=lambda s: (
                s.rank if s.rank is not None else 10**9,
                s.instrument_id or "",
            )
        )
        fetched_at = now
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(signals),
            meta=self._meta(
                category=DataCategory.SENTIMENT,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                current_cross_section=True,
                warnings=("LOW_RELIABILITY_MARKET_SIGNAL",),
            ),
        )

    async def _fetch_concept_heat(
        self, *, instrument: Instrument, trade_date: date, sampled_now: datetime, as_of: datetime
    ) -> list[SentimentSignal]:
        code6, suffix = require_a_share_instrument(instrument)
        source_code = f"{suffix}{code6}"
        body = ('{"srcSecurityCode":"' + source_code + '"}').encode("utf-8")
        response = await self._gated_send(
            HttpRequest(
                method="POST",
                url=_STOCKRANK_CONCEPT_HEAT_URL,
                params={},
                headers={**self._headers(), "Content-Type": "application/json"},
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="sentiment")
        self._require_json_content_type(response.headers, operation="sentiment")
        payload = loads_json_decimal(response.body)
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {"globalId", "message", "status", "code", "data", "stack"}
            or payload.get("globalId") is not None
            or type(payload.get("status")) is not int
            or type(payload.get("code")) is not int
            or payload.get("stack") is not None
        ):
            raise DataContractError("concept heat envelope mismatch", details={"rule": "envelope"})
        if payload["status"] != 0 or payload["code"] != 0:
            raise ProviderUnavailableError(
                "Eastmoney concept heat business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "error_type": "business_status",
                },
            )
        if payload.get("message") != "OK":
            raise DataContractError("concept heat envelope mismatch", details={"rule": "envelope"})
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise DataContractError(
                "concept heat data must be list", details={"field": "data", "rule": "type"}
            )
        parsed: list[tuple[str, str, int, datetime]] = []
        names: set[str] = set()
        ids: set[str] = set()
        previous: int | None = None
        calc_time: datetime | None = None
        import re

        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise DataContractError(
                    "concept heat row must object", details={"index": index, "rule": "type"}
                )
            name, cid, security, time_raw, hit, flag = (
                row.get("conceptName"),
                row.get("conceptId"),
                row.get("srcSecurityCode"),
                row.get("calcTime"),
                row.get("hitCount"),
                row.get("flag"),
            )
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(cid, str)
                or re.fullmatch(r"BK[0-9]+", cid) is None
                or security != source_code
                or type(hit) is not int
                or hit < 0
                or type(flag) is not int
                or flag != 0
                or not isinstance(time_raw, str)
            ):
                raise DataContractError(
                    "concept heat row mismatch", details={"index": index, "rule": "row"}
                )
            try:
                observed = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
            except ValueError as exc:
                raise DataContractError(
                    "concept heat calcTime invalid", details={"index": index, "rule": "calc_time"}
                ) from exc
            if (
                observed.date() != trade_date
                or observed > as_of
                or observed > sampled_now
                or (sampled_now - observed).total_seconds() > self._max_delayed_seconds
                or (cid in ids)
                or (name in names)
                or (previous is not None and hit > previous)
            ):
                raise DataContractError(
                    "concept heat row invariant mismatch",
                    details={"index": index, "rule": "freshness_identity_order"},
                )
            if calc_time is not None and observed != calc_time:
                raise DataContractError(
                    "concept heat calcTime must be shared",
                    details={"index": index, "rule": "shared_time"},
                )
            calc_time = observed
            previous = hit
            ids.add(cid)
            names.add(name)
            parsed.append((cid, name, hit, observed))
        parsed.sort(key=lambda row: (-row[2], row[0]))
        return [
            SentimentSignal(
                source_type=SentimentSourceType.CONCEPT_HEAT,
                trade_date=trade_date,
                instrument_id=None,
                rank=index + 1,
                rank_change=None,
                heat_value=Decimal(hit),
                concept_tags=(name,),
                label=name,
                source_vendor=VendorId.EASTMONEY,
                reliability=ReliabilityLevel.LOW,
                is_authoritative=False,
                source_item_id=cid,
                observed_at=observed,
            )
            for index, (cid, name, hit, observed) in enumerate(parsed)
        ]

    def _require_sentiment_sources(
        self, sources: tuple[SentimentSourceType, ...]
    ) -> tuple[SentimentSourceType, ...]:
        if not isinstance(sources, tuple):
            raise DataContractError(
                "sources must be a tuple of SentimentSourceType",
                details={"field": "sources", "rule": "type"},
            )
        if not sources:
            raise DataContractError(
                "sources must not be empty at the adapter boundary",
                details={"field": "sources", "rule": "non_empty"},
            )
        seen: set[SentimentSourceType] = set()
        out: list[SentimentSourceType] = []
        for source in sources:
            if not isinstance(source, SentimentSourceType):
                raise DataContractError(
                    "sources elements must be SentimentSourceType",
                    details={"field": "sources", "rule": "type"},
                )
            if source in seen:
                raise DataContractError(
                    "sources must not contain duplicates",
                    details={"field": "sources", "rule": "unique"},
                )
            seen.add(source)
            out.append(source)
        return tuple(out)

    async def _fetch_eastmoney_hot(
        self,
        *,
        instrument: Instrument | None,
        trade_date: date,
    ) -> list[SentimentSignal]:
        # Empty JSON object is live-verified success body (2026-07-17).
        response = await self._gated_send(
            HttpRequest(
                method="POST",
                url=_STOCKRANK_ALL_CURRENT_URL,
                params={},
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                body=b"{}",
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="sentiment")
        self._require_json_content_type(response.headers, operation="sentiment")
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney stockrank payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        # Live envelope: status/code 0 + data list of {sc, rk, rc, hisRc}.
        status = payload.get("status")
        code = payload.get("code")
        if status not in (0, "0", Decimal(0)) or code not in (0, "0", Decimal(0)):
            raise ProviderUnavailableError(
                "Eastmoney stockrank business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            raise DataContractError(
                "Eastmoney stockrank data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        wanted: str | None = None
        if instrument is not None:
            wanted = instrument.instrument_id
        signals: list[SentimentSignal] = []
        seen_ids: set[str] = set()
        for idx, row in enumerate(data):
            if not isinstance(row, dict):
                raise DataContractError(
                    "Eastmoney stockrank row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            sc = row.get("sc")
            if not isinstance(sc, str) or len(sc) < 8:
                raise DataContractError(
                    "Eastmoney stockrank row missing sc",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            sc_u = sc.strip().upper()
            # Live shape: SH600664 / SZ002185
            if sc_u.startswith("SH") and sc_u[2:].isdigit():
                instrument_id = instrument_id_from_code(
                    sc_u[2:].zfill(6), "SH", asset=AssetType.EQUITY
                )
            elif sc_u.startswith("SZ") and sc_u[2:].isdigit():
                instrument_id = instrument_id_from_code(
                    sc_u[2:].zfill(6), "SZ", asset=AssetType.EQUITY
                )
            elif sc_u.startswith("BJ") and sc_u[2:].isdigit():
                instrument_id = instrument_id_from_code(
                    sc_u[2:].zfill(6), "BJ", asset=AssetType.EQUITY
                )
            else:
                raise DataContractError(
                    "Eastmoney stockrank sc failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            if instrument_id in seen_ids:
                raise DataContractError(
                    "Eastmoney stockrank returned duplicate instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen_ids.add(instrument_id)
            if wanted is not None and instrument_id != wanted:
                continue
            rank = require_int(row.get("rk"), field=f"stockrank[{idx}].rk")
            if rank < 0:
                raise DataContractError(
                    "rank must be nonnegative",
                    details={"field": "rk", "rule": "nonnegative", "index": idx},
                )
            rank_change = None
            if "rc" in row and row.get("rc") is not None:
                rank_change = require_int(row.get("rc"), field=f"stockrank[{idx}].rc")
            signals.append(
                SentimentSignal(
                    source_type=SentimentSourceType.EASTMONEY_HOT,
                    trade_date=trade_date,
                    instrument_id=instrument_id,
                    rank=rank,
                    rank_change=rank_change,
                    heat_value=None,
                    concept_tags=(),
                    label=None,
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        return signals
