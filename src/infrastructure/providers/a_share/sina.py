"""Sina A-share adapter (Phase 1E E3 + E4a daily-flow + E4c ETF options).

Primary owner of financial statements via the frozen host/path family:
``quotes.sina.cn/.../CompanyFinanceService.getFinanceReport2022``.

E4a: daily fund-flow fallback only at the live-verified exact endpoint
``vip.stock.finance.sina.com.cn/.../MoneyFlow.ssl_qsfx_zjlrqs`` (allowlisted).
Does not claim intraday/northbound/margin/etc.

E4c: ETF option chain/quotes/Greeks via live-verified exact endpoints
``stock.finance.sina.com.cn/.../StockOptionService.getStockName``,
``.../getRemainderDay``, and ``hq.sinajs.cn/list`` (allowlisted). Field maps
locked to official Sina ``optionCommon20191223.js`` (CON_OP / CON_SO).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.enums import BarInterval, FinancialStatementType, OptionType
from domain.a_share.models import (
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    FinancialStatementLine,
    FundFlowPoint,
    OptionGreeks,
)
from domain.common.enums import (
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
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument
from domain.market.freshness import classify_freshness
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    SHANGHAI,
    combine_shanghai_date_time,
    content_type_matches,
    decimal_from_text,
    decode_text,
    int_from_text,
    loads_json_decimal,
    loads_json_decimal_declared,
    parse_shanghai_date,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_decimal,
    require_nonnegative_exact_int,
)
from infrastructure.system.clock import SystemClock

# Frozen §20 host/path (allowlist is case-insensitive).
_STATEMENTS_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/"
    "CompanyFinanceService.getFinanceReport2022"
)
# Live-verified 2026-07-17 capital daily-flow fallback (exact host/path only).
# Content-Type is application/json; charset=gbk; r1/r2/r3 nets may be absent.
_DAILY_FLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
    "json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"
)
# Live-verified 2026-07-17 ETF option metadata (exact host/path only).
_OPTION_STOCK_NAME_URL = (
    "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
    "StockOptionService.getStockName"
)
_OPTION_REMAINDER_URL = (
    "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
    "StockOptionService.getRemainderDay"
)
_HQ_LIST_URL = "https://hq.sinajs.cn/list"
_SINA_REFERER = "https://finance.sina.com.cn/"

_SOURCE_BY_TYPE: Mapping[FinancialStatementType, str] = {
    FinancialStatementType.BALANCE_SHEET: "gjzb_bs",
    FinancialStatementType.INCOME_STATEMENT: "gjzb_is",
    FinancialStatementType.CASH_FLOW: "gjzb_cf",
}

_PAPER_PREFIX: Mapping[str, str] = {"SH": "sh", "SZ": "sz", "BJ": "bj"}

_JSON_CONTENT = ("application/json", "text/json", "text/plain", "text/javascript")
_HQ_CONTENT = (
    "application/javascript",
    "text/javascript",
    "text/plain",
    "application/octet-stream",
)
_SUPPORTED = frozenset(
    {
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.CAPITAL,
        DataCategory.OPTIONS,
    }
)

# Frozen supported ETF option underlyings (pre-network reject otherwise).
# (exchange Chinese name, cate code used by StockOptionService).
_ETF_OPTION_MAP: Mapping[str, tuple[str, str]] = {
    "510050.SH": ("上交所", "50ETF"),
    "510300.SH": ("上交所", "300ETF"),
    "510500.SH": ("上交所", "500ETF"),
    "588000.SH": ("上交所", "科创50"),
    "588080.SH": ("上交所", "科创板50"),
    "159901.SZ": ("深交所", "159901"),
    "159915.SZ": ("深交所", "159915"),
    "159919.SZ": ("深交所", "159919"),
    "159922.SZ": ("深交所", "159922"),
}

# Fail-closed line grammar: each nonblank line must be exactly one assignment.
_HQ_VAR_LINE_RE = re.compile(
    r'^var hq_str_(?P<sym>[A-Za-z0-9_]+)="(?P<body>[^"\\\r\n]*)";$'
)
_OP_LIST_TOKEN_RE = re.compile(r"^CON_OP_(\d+)$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
# Trading code: stock6 + C/P + YYMM + alphabetic adjustment marker + digits.
_SO_TRADING_CODE_RE = re.compile(
    r"^(?P<stock>\d{6})(?P<side>[CP])(?P<yymm>\d{4})(?P<adj>[A-Za-z])(?P<digits>\d+)$"
)

# Deterministic HQ batching for CON_OP/CON_SO list calls (eliminates URL-length caps).
_HQ_MAX_SYMBOLS: int = 80

# CON_OP field indices (official optionCommon20191223.js).
_OP_LAST = 2
_OP_OI = 5
_OP_STRIKE = 7
_OP_ASK5_PRICE = 12  # ask5..ask1 price/qty pairs through index 21
_OP_BID1_PRICE = 22  # bid1..bid5 price/qty pairs through index 31
_OP_QUOTE_AT = 32
_OP_VOLUME = 41
_OP_MIN_FIELDS = 45

# CON_SO field indices.
_SO_DELTA = 5
_SO_GAMMA = 6
_SO_THETA = 7
_SO_VEGA = 8
_SO_IV = 9
_SO_TRADING_CODE = 12
_SO_STRIKE = 13
_SO_THEORETICAL = 15
_SO_MIN_FIELDS = 17


class _EtfOptionKey(NamedTuple):
    exchange: str
    cate: str
    stock_id: str
    board_prefix: str  # "sh" or "sz"
    underlying_instrument_id: str


class SinaAShareAdapter:
    """CategoryProvider: statements, daily-flow capital fallback, ETF options."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        current_window_seconds: int = 300,
        max_fresh_seconds: int = 15,
        max_delayed_seconds: int = 120,
    ) -> None:
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
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._current_window_seconds = require_nonnegative_exact_int(
            current_window_seconds, field="current_window_seconds"
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
        self._max_fresh_seconds = max_fresh_seconds
        self._max_delayed_seconds = max_delayed_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SINA

    @property
    def provider_name(self) -> str:
        return VendorId.SINA.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in _SUPPORTED

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Sina A-share adapter is disabled",
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

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Sina rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Sina access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Sina HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _require_json_content(self, headers: Mapping[str, str], *, operation: str) -> None:
        if content_type_matches(headers, allowed_substrings=_JSON_CONTENT):
            return
        raise DataContractError(
            "Sina response Content-Type is not acceptable",
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
        warnings: tuple[str, ...] = (),
        freshness: Freshness = Freshness.UNKNOWN,
        session: TradingSession | None = None,
        data_delay_seconds: int | None = None,
    ) -> ProviderResultMeta:
        if session is None:
            session = infer_session_basic(
                Market.A_SHARE, as_of, timezone="Asia/Shanghai"
            )
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
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
            adjustment=None,
            data_delay_seconds=data_delay_seconds,
            warnings=warnings,
        )

    def _require_current_only_as_of(self, as_of: datetime, *, operation: str) -> datetime:
        """Sample clock once; reject future or stale as_of before network."""
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={
                    "field": "as_of",
                    "rule": "not_future",
                    "operation": operation,
                },
            )
        age = (now - as_of).total_seconds()
        if age > self._current_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported current window",
                details={
                    "operation": operation,
                    "rule": "current_window",
                    "window_seconds": self._current_window_seconds,
                },
            )
        return now

    def _paper_code(self, code6: str, suffix: str) -> str:
        prefix = _PAPER_PREFIX.get(suffix)
        if prefix is None:
            raise DataContractError(
                "unsupported A-share exchange suffix for Sina",
                details={"field": "symbol", "rule": "exchange_suffix"},
            )
        return f"{prefix}{code6}"

    async def get_daily_flow(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
        """Daily fund-flow fallback only (not intraday/northbound/etc.)."""
        self._require_configured()
        self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        daima = self._paper_code(code6, suffix)
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_DAILY_FLOW_URL,
                params={
                    "page": "1",
                    "num": "60",
                    "sort": "opendate",
                    "asc": "0",
                    "daima": daima,
                },
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://finance.sina.com.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="daily_flow")
        self._require_json_content(response.headers, operation="daily_flow")
        try:
            payload = loads_json_decimal_declared(response.body, response.headers)
        except DataContractError as exc:
            self._ensure_no_body_leak(exc)
            raise
        if not isinstance(payload, list):
            # Error envelopes (e.g. code=11 / __ERROR Service not valid) and
            # any non-list shape are contract drift — never invent rows.
            raise DataContractError(
                "Sina daily flow payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "daily_flow",
                    "rule": "contract_drift",
                },
            )
        points: list[FundFlowPoint] = []
        for idx, row in enumerate(payload):
            if not isinstance(row, dict):
                raise DataContractError(
                    "Sina daily flow row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "daily_flow",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            day_raw = row.get("opendate")
            if not isinstance(day_raw, str) or not day_raw.strip():
                raise DataContractError(
                    "Sina daily flow missing opendate",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "daily_flow",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            day = parse_shanghai_date(day_raw)
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            occurred_at = combine_shanghai_date_time(day, "15:00:00")
            if occurred_at > as_of:
                continue
            # Live shape: netamount + r0_net required when present; r1/r2/r3
            # nets may be absent → keep None (never coerce missing to zero).
            points.append(
                FundFlowPoint(
                    occurred_at=occurred_at,
                    interval=BarInterval.ONE_DAY,
                    main_net_cny=decimal_from_text(
                        row.get("netamount"), field="main_net_cny"
                    ),
                    super_large_net_cny=decimal_from_text(
                        row.get("r0_net"), field="super_large_net_cny"
                    ),
                    large_net_cny=decimal_from_text(
                        row.get("r1_net"), field="large_net_cny"
                    ),
                    medium_net_cny=decimal_from_text(
                        row.get("r2_net"), field="medium_net_cny"
                    ),
                    small_net_cny=decimal_from_text(
                        row.get("r3_net"), field="small_net_cny"
                    ),
                    source_vendor=VendorId.SINA,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        points.sort(key=lambda p: p.occurred_at)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(points),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=("LOW_RELIABILITY_MARKET_SIGNAL",),
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
        self._require_configured()
        now = self._require_as_of(as_of)
        if (
            not isinstance(periods, int)
            or isinstance(periods, bool)
            or periods < 1
            or periods > 40
        ):
            raise DataContractError(
                "periods must be an int in 1..40",
                details={"field": "periods", "rule": "range"},
            )
        if not isinstance(statement_types, tuple) or not statement_types:
            raise DataContractError(
                "statement_types must be a non-empty tuple",
                details={"field": "statement_types", "rule": "non_empty"},
            )
        seen: set[FinancialStatementType] = set()
        for st in statement_types:
            if not isinstance(st, FinancialStatementType):
                raise DataContractError(
                    "statement_types elements must be FinancialStatementType",
                    details={"field": "statement_types", "rule": "type"},
                )
            if st in seen:
                raise DataContractError(
                    "statement_types must not contain duplicates",
                    details={"field": "statement_types", "rule": "unique"},
                )
            seen.add(st)

        code6, suffix = require_a_share_instrument(instrument)
        paper = self._paper_code(code6, suffix)
        lines: list[FinancialStatementLine] = []
        unknown_excluded = False
        operation = "statements"

        for stype in statement_types:
            source = _SOURCE_BY_TYPE[stype]
            response = await self._transport.send(
                HttpRequest(
                    method="GET",
                    url=_STATEMENTS_URL,
                    params={
                        "paperCode": paper,
                        "source": source,
                        "type": "0",
                        "page": "1",
                        "num": str(periods),
                    },
                    headers={
                        "Accept": "application/json,text/plain,*/*",
                        "User-Agent": self._user_agent,
                        "Referer": "https://finance.sina.com.cn/",
                    },
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation=operation)
            self._require_json_content(response.headers, operation=operation)
            try:
                payload = loads_json_decimal(response.body)
                type_lines, excluded = self._parse_statement_payload(
                    payload,
                    statement_type=stype,
                    periods=periods,
                    as_of=as_of,
                    now=now,
                )
            except DataContractError as exc:
                self._ensure_no_body_leak(exc)
                raise
            lines.extend(type_lines)
            unknown_excluded = unknown_excluded or excluded

        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not lines:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                },
            )
        warnings: tuple[str, ...] = ()
        if unknown_excluded:
            warnings = ("PUBLICATION_TIME_UNKNOWN_EXCLUDED",)
        return ProviderSuccess(
            value=tuple(lines),
            meta=self._meta(
                category=DataCategory.FINANCIAL_STATEMENTS,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=warnings,
            ),
        )

    def _parse_statement_payload(
        self,
        payload: object,
        *,
        statement_type: FinancialStatementType,
        periods: int,
        as_of: datetime,
        now: datetime,
    ) -> tuple[list[FinancialStatementLine], bool]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Sina statements payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        # Frozen envelope: result.data.report is a list of period objects.
        result = payload.get("result")
        if result is None and payload.get("data") is None:
            # Explicit empty success path handled by caller as NoMarketData.
            return [], False
        if not isinstance(result, dict):
            # Some Sina builds nest under data only.
            data_root = payload.get("data")
            if not isinstance(data_root, dict):
                raise DataContractError(
                    "Sina statements payload missing result object",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                    },
                )
            result = data_root
        data = result.get("data") if "data" in result else result
        if data is None:
            return [], False
        if not isinstance(data, dict):
            raise DataContractError(
                "Sina statements data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        reports = data.get("report")
        if reports is None:
            reports = data.get("report_list")
        if reports is None:
            return [], False
        if not isinstance(reports, list):
            raise DataContractError(
                "Sina statements report list failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        if len(reports) == 0:
            return [], False

        lines: list[FinancialStatementLine] = []
        unknown_excluded = False
        for idx, report in enumerate(reports[:periods]):
            if not isinstance(report, dict):
                raise DataContractError(
                    "Sina statement period failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            period_raw = report.get("report_date") or report.get("rDate") or report.get(
                "enddate"
            )
            if period_raw is None:
                raise DataContractError(
                    "Sina statement missing report_date",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            if isinstance(period_raw, str):
                period_end = parse_shanghai_date(period_raw)
            else:
                raise DataContractError(
                    "Sina statement report_date type invalid",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            pub_raw = (
                report.get("publish_date")
                or report.get("notice_date")
                or report.get("announcement_date")
            )
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            keep, excluded = publication_cutoff_keep(
                published_at,
                as_of=as_of,
                now=now,
                current_window_seconds=self._current_window_seconds,
            )
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            items = report.get("data") or report.get("items") or report.get("list")
            if items is None:
                # Period-level field map form: every non-meta key is a line item.
                meta_keys = {
                    "report_date",
                    "rDate",
                    "enddate",
                    "publish_date",
                    "notice_date",
                    "announcement_date",
                    "data",
                    "items",
                    "list",
                }
                items = [
                    {"item_code": k, "item_name": k, "item_value": v}
                    for k, v in report.items()
                    if k not in meta_keys
                ]
            if not isinstance(items, list):
                raise DataContractError(
                    "Sina statement items failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            for jdx, item in enumerate(items):
                if not isinstance(item, dict):
                    raise DataContractError(
                        "Sina statement line failed contract validation",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                            "index": jdx,
                        },
                    )
                code = item.get("item_code") or item.get("code") or item.get("field")
                name = item.get("item_name") or item.get("name") or item.get("title")
                if not isinstance(code, str) or not code.strip():
                    raise DataContractError(
                        "Sina statement line missing item_code",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                        },
                    )
                if not isinstance(name, str) or not name.strip():
                    name = code
                value = decimal_from_text(
                    item.get("item_value")
                    if "item_value" in item
                    else item.get("value"),
                    field="value",
                )
                unit = item.get("unit")
                if unit is None or (isinstance(unit, str) and not unit.strip()):
                    unit = "CNY"
                if not isinstance(unit, str):
                    raise DataContractError(
                        "Sina statement unit must be string",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                        },
                    )
                lines.append(
                    FinancialStatementLine(
                        statement_type=statement_type,
                        period_end=period_end,
                        published_at=published_at,
                        item_code=code.strip()[:64],
                        item_name=name.strip()[:200],
                        value=value,
                        unit=unit.strip()[:50],
                    )
                )
        return lines, unknown_excluded

    @staticmethod
    def _ensure_no_body_leak(exc: DataContractError) -> None:
        # Guard against accidental payload embedding in details.
        for key, value in exc.details.items():
            if key in {"body", "raw", "payload", "response"}:
                raise DataContractError(
                    "error details must not embed raw provider payload",
                    details={"field": "details", "rule": "no_body_leak"},
                ) from exc
            if isinstance(value, (bytes, bytearray)):
                raise DataContractError(
                    "error details must not embed raw provider payload",
                    details={"field": "details", "rule": "no_body_leak"},
                ) from exc

    # ------------------------------------------------------------------
    # E4c ETF options
    # ------------------------------------------------------------------

    async def get_option_snapshot(
        self,
        underlying: Instrument,
        *,
        expiry: date | None,
        strike_center: Decimal | None,
        strike_count_each_side: int,
        as_of: datetime,
    ) -> ProviderSuccess[EtfOptionSnapshot]:
        """Current ETF option chain/quotes/Greeks (no historical replay)."""
        self._require_configured()
        now = self._require_current_only_as_of(as_of, operation="options")
        key = self._require_supported_etf_underlying(underlying)
        if expiry is not None and type(expiry) is not date:
            raise DataContractError(
                "expiry must be a date (not datetime)",
                details={"field": "expiry", "rule": "exact_date_type"},
            )
        center = self._validate_strike_center(strike_center)
        each_side = self._validate_strike_count_each_side(strike_count_each_side)
        # Nonexpired selection uses sampled clock local date (not request as_of)
        # so a midnight-crossing as_of window cannot select an already-expired month.
        now_local = now.astimezone(SHANGHAI).date()

        try:
            months = await self._fetch_contract_months(key)
            month, expire_day, stock_id, _cate_id, other_symbol = (
                await self._resolve_expiry_month(
                    key,
                    months=months,
                    expiry=expiry,
                    now_local=now_local,
                )
            )
            yymm = month[2:4] + month[5:7]
            call_ids, put_ids = await self._fetch_chain_ids(
                stock_id=stock_id, yymm=yymm
            )
            all_ids = call_ids + put_ids
            id_to_type: dict[str, OptionType] = {
                cid: OptionType.CALL for cid in call_ids
            }
            id_to_type.update({pid: OptionType.PUT for pid in put_ids})
            spot_symbol = other_symbol
            con_op_syms = [f"CON_OP_{cid}" for cid in all_ids]
            quote_symbols = [*con_op_syms, spot_symbol]
            hq_map = await self._fetch_hq_list(quote_symbols, operation="options_quotes")

            if center is None:
                spot = self._parse_underlying_spot(
                    hq_map[spot_symbol], symbol=spot_symbol
                )
                if spot is None or spot <= 0:
                    raise DataContractError(
                        "underlying spot must be a positive Decimal when "
                        "strike_center is not supplied",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "options",
                            "rule": "spot_required",
                        },
                    )
                effective_center = spot
            else:
                effective_center = center

            parsed: dict[str, tuple[EtfOptionQuote, Decimal]] = {}
            for cid in all_ids:
                sym = f"CON_OP_{cid}"
                quote, strike = self._parse_con_op(
                    hq_map[sym],
                    contract_id=cid,
                    option_type=id_to_type[cid],
                    underlying_instrument_id=key.underlying_instrument_id,
                    expiry=expire_day,
                    as_of=as_of,
                )
                parsed[cid] = (quote, strike)

            selected_ids = self._select_strike_window(
                parsed,
                id_to_type=id_to_type,
                center=effective_center,
                each_side=each_side,
            )
            quotes = self._emit_sorted_quotes(selected_ids, parsed)
            self._require_single_local_quote_date(quotes, as_of=as_of)

            so_syms = [f"CON_SO_{cid}" for cid in selected_ids]
            so_map = await self._fetch_hq_list(so_syms, operation="options_greeks")

            ordered_greeks_list: list[OptionGreeks] = []
            for quote in quotes:
                cid = quote.contract.instrument_id.rsplit(":", 1)[-1]
                _q, strike = parsed[cid]
                greek = self._parse_con_so(
                    so_map[f"CON_SO_{cid}"],
                    contract_id=cid,
                    quote=quote,
                    strike=strike,
                    stock_id=stock_id,
                    as_of=as_of,
                )
                ordered_greeks_list.append(greek)
            ordered_greeks = tuple(ordered_greeks_list)

            snapshot = EtfOptionSnapshot(
                underlying_instrument_id=key.underlying_instrument_id,
                expiry=expire_day,
                quotes=quotes,
                greeks=ordered_greeks,
            )
        except DataContractError as exc:
            self._ensure_no_body_leak(exc)
            raise

        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        freshness, session, data_delay = self._classify_option_freshness(
            quotes=snapshot.quotes,
            as_of=as_of,
            fetched_at=fetched_at,
            now=now,
        )
        return ProviderSuccess(
            value=snapshot,
            meta=self._meta(
                category=DataCategory.OPTIONS,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=freshness,
                session=session,
                data_delay_seconds=data_delay,
            ),
        )

    def _require_supported_etf_underlying(self, underlying: Instrument) -> _EtfOptionKey:
        if not isinstance(underlying, Instrument):
            raise DataContractError(
                "underlying must be Instrument",
                details={"field": "underlying", "rule": "type"},
            )
        if underlying.market is not Market.A_SHARE:
            raise DataContractError(
                "underlying market must be A_SHARE",
                details={"field": "underlying", "rule": "market"},
            )
        if underlying.asset_type is not AssetType.ETF:
            raise DataContractError(
                "options underlying must be an ETF",
                details={"field": "underlying", "rule": "asset_type"},
            )
        code6, suffix = require_a_share_instrument(underlying)
        symbol_key = f"{code6}.{suffix}"
        mapping = _ETF_OPTION_MAP.get(symbol_key)
        if mapping is None:
            raise DataContractError(
                "ETF underlying is not on the frozen options support list",
                details={
                    "field": "underlying",
                    "rule": "unsupported_etf",
                    "symbol": symbol_key,
                },
            )
        exchange, cate = mapping
        board = "sh" if suffix == "SH" else "sz"
        underlying_id = build_instrument_id(AssetType.ETF, Market.A_SHARE, symbol_key)
        if underlying.instrument_id != underlying_id:
            # Accept only canonical etf:A_SHARE:CODE.EX form.
            raise DataContractError(
                "underlying instrument_id must be canonical ETF form",
                details={
                    "field": "underlying",
                    "rule": "instrument_id",
                    "expected": underlying_id,
                },
            )
        return _EtfOptionKey(
            exchange=exchange,
            cate=cate,
            stock_id=code6,
            board_prefix=board,
            underlying_instrument_id=underlying_id,
        )

    @staticmethod
    def _validate_strike_center(strike_center: Decimal | None) -> Decimal | None:
        if strike_center is None:
            return None
        if type(strike_center) is not Decimal:
            raise DataContractError(
                "strike_center must be Decimal or None",
                details={
                    "field": "strike_center",
                    "rule": "decimal_type",
                    "type": type(strike_center).__name__,
                },
            )
        if not strike_center.is_finite() or strike_center <= 0:
            raise DataContractError(
                "strike_center must be a finite Decimal > 0",
                details={"field": "strike_center", "rule": "positive_finite"},
            )
        return strike_center

    @staticmethod
    def _validate_strike_count_each_side(value: object) -> int:
        if type(value) is not int or isinstance(value, bool):
            raise DataContractError(
                "strike_count_each_side must be an exact int",
                details={
                    "field": "strike_count_each_side",
                    "rule": "int_type",
                    "type": type(value).__name__,
                },
            )
        if value < 0 or value > 20:
            raise DataContractError(
                "strike_count_each_side must be in 0..20",
                details={"field": "strike_count_each_side", "rule": "range"},
            )
        return value

    async def _fetch_contract_months(self, key: _EtfOptionKey) -> tuple[str, ...]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_OPTION_STOCK_NAME_URL,
                params={"exchange": key.exchange, "cate": key.cate},
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": _SINA_REFERER,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="options_stock_name")
        self._require_json_content(response.headers, operation="options_stock_name")
        payload = loads_json_decimal(response.body)
        data = self._option_data_object(payload, operation="options_stock_name")
        cate_list = data.get("cateList")
        if not isinstance(cate_list, list):
            raise DataContractError(
                "Sina option stock-name cateList must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_stock_name",
                    "rule": "contract_drift",
                },
            )
        cate_entries: list[str] = []
        for idx, item in enumerate(cate_list):
            if type(item) is not str or not item.strip():
                raise DataContractError(
                    "Sina option stock-name cateList entries must be nonblank strings",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options_stock_name",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            cate_entries.append(item.strip())
        # Live surface may duplicate the first entry; require requested cate appears.
        if key.cate not in cate_entries:
            raise DataContractError(
                "Sina option stock-name cateList missing requested cate",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_stock_name",
                    "rule": "cate_missing",
                },
            )
        months_raw = data.get("contractMonth")
        if not isinstance(months_raw, list) or not months_raw:
            raise NoMarketData(
                "provider returned no option contract months",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_stock_name",
                },
            )
        months: list[str] = []
        seen: set[str] = set()
        for idx, item in enumerate(months_raw):
            if type(item) is not str or not _MONTH_RE.fullmatch(item.strip()):
                raise DataContractError(
                    "Sina option contractMonth entry invalid",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options_stock_name",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            month = item.strip()
            if month in seen:
                continue
            seen.add(month)
            months.append(month)
        if not months:
            raise NoMarketData(
                "provider returned no option contract months",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_stock_name",
                },
            )
        return tuple(months)

    async def _resolve_expiry_month(
        self,
        key: _EtfOptionKey,
        *,
        months: tuple[str, ...],
        expiry: date | None,
        now_local: date,
    ) -> tuple[str, date, str, str, str]:
        if expiry is not None:
            month_key = f"{expiry.year:04d}-{expiry.month:02d}"
            if month_key not in months:
                raise NoMarketData(
                    "requested option expiry month is not available",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "month_unavailable",
                    },
                )
            rem = await self._fetch_remainder(key, month=month_key)
            if rem[1] != expiry:
                raise DataContractError(
                    "requested expiry must equal getRemainderDay.expireDay exactly",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "expiry_mismatch",
                    },
                )
            if rem[1] < now_local:
                raise NoMarketData(
                    "requested option expiry is already expired",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "expired",
                    },
                )
            return rem

        # Nearest nonexpired available month (scan response-order months by date).
        ordered = sorted(months)
        best: tuple[str, date, str, str, str] | None = None
        for month in ordered:
            rem = await self._fetch_remainder(key, month=month)
            if rem[1] < now_local:
                continue
            if best is None or rem[1] < best[1]:
                best = rem
        if best is None:
            raise NoMarketData(
                "no nonexpired option contract month available",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "no_nonexpired_month",
                },
            )
        return best

    async def _fetch_remainder(
        self, key: _EtfOptionKey, *, month: str
    ) -> tuple[str, date, str, str, str]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_OPTION_REMAINDER_URL,
                params={
                    "exchange": key.exchange,
                    "cate": key.cate,
                    "date": month,
                },
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": _SINA_REFERER,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="options_remainder")
        self._require_json_content(response.headers, operation="options_remainder")
        payload = loads_json_decimal(response.body)
        data = self._option_data_object(payload, operation="options_remainder")
        expire_raw = data.get("expireDay")
        if type(expire_raw) is not str or not expire_raw.strip():
            raise DataContractError(
                "Sina option remainder missing expireDay",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "contract_drift",
                },
            )
        expire_day = parse_shanghai_date(expire_raw)
        month_year = int(month[0:4])
        month_num = int(month[5:7])
        if expire_day.year != month_year or expire_day.month != month_num:
            raise DataContractError(
                "Sina option remainder expireDay month must equal requested month",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "expire_day_month",
                },
            )
        remainder_days = data.get("remainderDays")
        if type(remainder_days) is not int or isinstance(remainder_days, bool):
            raise DataContractError(
                "Sina option remainderDays must be an exact int",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "remainder_days_type",
                },
            )
        stock_id_raw = data.get("stockId")
        cate_id_raw = data.get("cateId")
        other = data.get("other")
        if type(stock_id_raw) is not str or not stock_id_raw.strip():
            raise DataContractError(
                "Sina option remainder missing stockId",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "contract_drift",
                },
            )
        if type(cate_id_raw) is not str or not cate_id_raw.strip():
            raise DataContractError(
                "Sina option remainder missing cateId",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "contract_drift",
                },
            )
        if type(other) is not dict:
            raise DataContractError(
                "Sina option remainder missing other object",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "contract_drift",
                },
            )
        other_type = other.get("type")
        if type(other_type) is not int or isinstance(other_type, bool) or other_type != 10:
            raise DataContractError(
                "Sina option remainder other.type must be exact numeric 10",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "other_type",
                },
            )
        name_raw = other.get("name")
        if type(name_raw) is not str or not name_raw.strip():
            raise DataContractError(
                "Sina option remainder missing other.name",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "other_name",
                },
            )
        symbol_raw = other.get("symbol")
        if type(symbol_raw) is not str or not symbol_raw.strip():
            raise DataContractError(
                "Sina option remainder missing other.symbol",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "contract_drift",
                },
            )
        url_raw = other.get("url")
        if type(url_raw) is not str or not url_raw.strip():
            raise DataContractError(
                "Sina option remainder missing other.url",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "other_url",
                },
            )
        stock_id = stock_id_raw.strip()
        cate_id = cate_id_raw.strip()
        symbol = symbol_raw.strip()
        expected_symbol = f"s_{key.board_prefix}{key.stock_id}"
        if stock_id != key.stock_id:
            raise DataContractError(
                "Sina option remainder stockId does not match underlying",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "stock_id_mismatch",
                },
            )
        yymm = month[2:4] + month[5:7]
        expected_cate_prefix = f"{key.stock_id}C{yymm}"
        if not cate_id.startswith(expected_cate_prefix):
            raise DataContractError(
                "Sina option remainder cateId prefix must be stockId+C+YYMM",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "cate_id_prefix",
                },
            )
        if symbol != expected_symbol:
            raise DataContractError(
                "Sina option remainder other.symbol does not match underlying",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_remainder",
                    "rule": "other_symbol_mismatch",
                },
            )
        return month, expire_day, stock_id, cate_id, symbol

    def _option_data_object(self, payload: object, *, operation: str) -> dict[str, object]:
        if type(payload) is not dict:
            raise DataContractError(
                "Sina option payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        result = payload.get("result")
        if type(result) is not dict:
            raise DataContractError(
                "Sina option envelope result must be an object",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "envelope_result",
                },
            )
        status = result.get("status")
        if type(status) is not dict:
            raise DataContractError(
                "Sina option envelope status must be an object",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "envelope_status",
                },
            )
        code = status.get("code")
        if type(code) is not int or isinstance(code, bool) or code != 0:
            raise DataContractError(
                "Sina option result.status.code must be exact 0",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "status_code",
                },
            )
        data = result.get("data")
        if type(data) is not dict:
            raise DataContractError(
                "Sina option result.data must be an exact object",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "envelope_data",
                },
            )
        return data

    async def _fetch_chain_ids(
        self, *, stock_id: str, yymm: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        up_sym = f"OP_UP_{stock_id}{yymm}"
        down_sym = f"OP_DOWN_{stock_id}{yymm}"
        hq_map = await self._fetch_hq_list(
            [up_sym, down_sym], operation="options_chain"
        )
        call_ids = self._parse_op_list(hq_map[up_sym], side="call")
        put_ids = self._parse_op_list(hq_map[down_sym], side="put")
        if not call_ids or not put_ids:
            raise NoMarketData(
                "provider returned incomplete option chain lists",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_chain",
                    "rule": "chain_incomplete",
                },
            )
        call_set = set(call_ids)
        put_set = set(put_ids)
        if len(call_set) != len(call_ids) or len(put_set) != len(put_ids):
            raise DataContractError(
                "option chain list contains duplicate contract ids",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_chain",
                    "rule": "unique",
                },
            )
        if call_set & put_set:
            raise DataContractError(
                "option call and put chain lists must not overlap",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_chain",
                    "rule": "call_put_overlap",
                },
            )
        return call_ids, put_ids

    def _parse_op_list(self, body: str, *, side: str) -> tuple[str, ...]:
        text = body.strip()
        if not text:
            return ()
        ids: list[str] = []
        for token in text.split(","):
            token = token.strip()
            if not token:
                continue
            match = _OP_LIST_TOKEN_RE.fullmatch(token)
            if match is None:
                raise DataContractError(
                    f"option {side} list token failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options_chain",
                        "rule": "contract_drift",
                    },
                )
            ids.append(match.group(1))
        return tuple(ids)

    async def _fetch_hq_list(
        self, symbols: list[str], *, operation: str
    ) -> dict[str, str]:
        if not symbols:
            raise DataContractError(
                "hq list symbols must be non-empty",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "non_empty",
                },
            )
        if len(set(symbols)) != len(symbols):
            raise DataContractError(
                "hq list request symbols must be unique",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "unique",
                },
            )
        # Deterministic frozen batch size for all CON_OP/CON_SO (and chain) list calls.
        aggregated: dict[str, str] = {}
        for start in range(0, len(symbols), _HQ_MAX_SYMBOLS):
            batch = symbols[start : start + _HQ_MAX_SYMBOLS]
            batch_map = await self._fetch_hq_list_batch(batch, operation=operation)
            self._require_exact_hq_symbols(batch_map, batch, operation=operation)
            for sym, body in batch_map.items():
                if sym in aggregated:
                    raise DataContractError(
                        "Sina hq aggregated response contains duplicate variables",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "duplicate_var",
                        },
                    )
                aggregated[sym] = body
        return aggregated

    async def _fetch_hq_list_batch(
        self, symbols: list[str], *, operation: str
    ) -> dict[str, str]:
        # Sina list param is comma-joined symbols (path remains exact /list).
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_HQ_LIST_URL,
                params={"list": ",".join(symbols)},
                headers={
                    "Accept": "application/javascript,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": _SINA_REFERER,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation=operation)
        if not content_type_matches(response.headers, allowed_substrings=_HQ_CONTENT):
            raise DataContractError(
                "Sina hq response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "content_type",
                },
            )
        text = decode_text(response.body, encodings=("gb18030", "gbk", "utf-8"))
        return self._parse_hq_var_assignments(text, operation=operation)

    def _parse_hq_var_assignments(
        self, text: str, *, operation: str
    ) -> dict[str, str]:
        """Fail-closed: every nonblank line must match one safe var assignment. Never eval."""
        found: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = _HQ_VAR_LINE_RE.fullmatch(stripped)
            if match is None:
                raise DataContractError(
                    "Sina hq response line failed safe assignment grammar",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "hq_line_grammar",
                    },
                )
            sym = match.group("sym")
            body = match.group("body")
            if sym in found:
                raise DataContractError(
                    "Sina hq response contains duplicate variables",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "duplicate_var",
                    },
                )
            found[sym] = body
        return found

    def _require_exact_hq_symbols(
        self,
        hq_map: Mapping[str, str],
        symbols: list[str],
        *,
        operation: str,
    ) -> None:
        requested = set(symbols)
        got = set(hq_map)
        if got != requested:
            raise DataContractError(
                "Sina hq response variables must exactly match requested symbols",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "exact_symbols",
                    "missing_count": len(requested - got),
                    "extra_count": len(got - requested),
                },
            )

    def _parse_underlying_spot(self, body: str, *, symbol: str) -> Decimal | None:
        # s_sh/s_sz format: name,price,change,pct,... — position 1 is live spot.
        fields = body.split(",")
        if len(fields) < 2:
            raise DataContractError(
                "underlying spot line failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "contract_drift",
                    "symbol": symbol,
                },
            )
        return decimal_from_text(fields[1], field="underlying_spot")

    def _parse_con_op(
        self,
        body: str,
        *,
        contract_id: str,
        option_type: OptionType,
        underlying_instrument_id: str,
        expiry: date,
        as_of: datetime,
    ) -> tuple[EtfOptionQuote, Decimal]:
        fields = body.split(",")
        if len(fields) < _OP_MIN_FIELDS:
            raise DataContractError(
                "CON_OP field count failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "contract_drift",
                },
            )
        if not contract_id.isdigit():
            raise DataContractError(
                "option contract id must be numeric Sina id",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "contract_id",
                },
            )
        strike = require_decimal(fields[_OP_STRIKE], field="strike")
        if strike <= 0:
            raise DataContractError(
                "option strike must be positive",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "strike_positive",
                },
            )
        last = decimal_from_text(fields[_OP_LAST], field="last")
        if last is not None and last < 0:
            raise DataContractError(
                "option last must be nonnegative when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "nonnegative",
                },
            )
        quote_at = parse_shanghai_datetime(fields[_OP_QUOTE_AT], field="quote_at")
        if quote_at is None:
            raise DataContractError(
                "CON_OP quote datetime is required",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "quote_at_required",
                },
            )
        if quote_at > as_of:
            raise DataContractError(
                "quote_at must be <= requested as_of cutoff",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "as_of_cutoff",
                },
            )
        volume = int_from_text(fields[_OP_VOLUME], field="volume_contracts")
        open_interest = int_from_text(fields[_OP_OI], field="open_interest")
        if volume is not None and volume < 0:
            raise DataContractError(
                "volume_contracts must be nonnegative",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "nonnegative",
                },
            )
        if open_interest is not None and open_interest < 0:
            raise DataContractError(
                "open_interest must be nonnegative",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "nonnegative",
                },
            )
        bid_prices, bid_volumes = self._parse_depth_levels(
            fields, start=_OP_BID1_PRICE, best_first=True, side="bid"
        )
        ask_prices, ask_volumes = self._parse_depth_levels(
            fields, start=_OP_ASK5_PRICE, best_first=False, side="ask"
        )
        instrument_id = build_instrument_id(
            AssetType.OPTION, Market.A_SHARE, contract_id
        )
        contract = EtfOptionContract(
            instrument_id=instrument_id,
            underlying_instrument_id=underlying_instrument_id,
            option_type=option_type,
            expiry=expiry,
            strike=strike,
            multiplier=None,
        )
        quote = EtfOptionQuote(
            contract=contract,
            quote_at=quote_at,
            last=last,
            bid_prices=bid_prices,
            bid_volumes=bid_volumes,
            ask_prices=ask_prices,
            ask_volumes=ask_volumes,
            volume_contracts=volume,
            open_interest=open_interest,
        )
        return quote, strike

    def _parse_depth_levels(
        self,
        fields: list[str],
        *,
        start: int,
        best_first: bool,
        side: str,
    ) -> tuple[tuple[Decimal, ...], tuple[int, ...]]:
        """Parse five levels; emit best-first contiguous present levels."""
        raw_levels: list[tuple[Decimal | None, int | None]] = []
        if best_first:
            # bid1..bid5: consecutive pairs from start.
            for i in range(5):
                p_idx = start + i * 2
                q_idx = p_idx + 1
                raw_levels.append(
                    (
                        decimal_from_text(fields[p_idx], field=f"{side}_price"),
                        int_from_text(fields[q_idx], field=f"{side}_volume"),
                    )
                )
        else:
            # ask5..ask1 layout: reverse to best-first ask1..ask5.
            for i in range(4, -1, -1):
                p_idx = start + i * 2
                q_idx = p_idx + 1
                raw_levels.append(
                    (
                        decimal_from_text(fields[p_idx], field=f"{side}_price"),
                        int_from_text(fields[q_idx], field=f"{side}_volume"),
                    )
                )
        prices: list[Decimal] = []
        volumes: list[int] = []
        seen_absent = False
        for price, qty in raw_levels:
            if (price is not None and price < 0) or (qty is not None and qty < 0):
                raise DataContractError(
                    f"option {side} level values must be nonnegative",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "nonnegative",
                    },
                )
            # Explicit numeric zero price is the authoritative absent-level
            # sentinel. Sina CON_OP may leave a stale nonzero quantity residue
            # (live: ask2 price 0.0000 with qty 124); omit the level and keep
            # contiguous-depth enforcement for remaining levels.
            if price is not None and price == 0:
                seen_absent = True
                continue
            price_absent = price is None
            qty_absent = qty is None or qty == 0
            if price_absent and qty_absent:
                seen_absent = True
                continue
            if price_absent or qty_absent:
                # Missing price + nonzero qty still fails (unless price was
                # explicit numeric zero, handled above). Positive price with
                # zero/missing qty still fails.
                raise DataContractError(
                    f"option {side} level price/volume pairing inconsistent",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "depth_pair",
                    },
                )
            if seen_absent:
                raise DataContractError(
                    f"option {side} depth has data after absent level",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "depth_gap",
                    },
                )
            assert price is not None and qty is not None
            prices.append(price)
            volumes.append(qty)
        return tuple(prices), tuple(volumes)

    def _select_strike_window(
        self,
        parsed: Mapping[str, tuple[EtfOptionQuote, Decimal]],
        *,
        id_to_type: Mapping[str, OptionType],
        center: Decimal,
        each_side: int,
    ) -> tuple[str, ...]:
        by_strike: dict[Decimal, dict[OptionType, str]] = {}
        for cid, (_quote, strike) in parsed.items():
            otype = id_to_type[cid]
            bucket = by_strike.setdefault(strike, {})
            if otype in bucket:
                raise DataContractError(
                    "duplicate option type at the same strike",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "unique_strike_type",
                    },
                )
            bucket[otype] = cid
        strikes = sorted(by_strike.keys())
        if not strikes:
            raise NoMarketData(
                "provider returned no option contracts",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                },
            )
        # Anchor = closest strike; ties choose the higher strike.
        anchor = min(
            strikes,
            key=lambda s: (abs(s - center), -s),
        )
        anchor_idx = strikes.index(anchor)
        lo = max(0, anchor_idx - each_side)
        hi = min(len(strikes) - 1, anchor_idx + each_side)
        selected_strikes = strikes[lo : hi + 1]
        selected_ids: list[str] = []
        for strike in selected_strikes:
            bucket = by_strike[strike]
            if OptionType.CALL not in bucket or OptionType.PUT not in bucket:
                raise DataContractError(
                    "missing call/put counterpart at selected strike",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "missing_counterpart",
                    },
                )
            selected_ids.append(bucket[OptionType.CALL])
            selected_ids.append(bucket[OptionType.PUT])
        return tuple(selected_ids)

    def _emit_sorted_quotes(
        self,
        selected_ids: tuple[str, ...],
        parsed: Mapping[str, tuple[EtfOptionQuote, Decimal]],
    ) -> tuple[EtfOptionQuote, ...]:
        quotes = [parsed[cid][0] for cid in selected_ids]
        quotes.sort(
            key=lambda q: (
                q.contract.strike,
                0 if q.contract.option_type is OptionType.CALL else 1,
                q.contract.instrument_id,
            )
        )
        return tuple(quotes)

    def _parse_con_so(
        self,
        body: str,
        *,
        contract_id: str,
        quote: EtfOptionQuote,
        strike: Decimal,
        stock_id: str,
        as_of: datetime,
    ) -> OptionGreeks:
        fields = body.split(",")
        if len(fields) < _SO_MIN_FIELDS:
            raise DataContractError(
                "CON_SO field count failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "contract_drift",
                },
            )
        trading_code = fields[_SO_TRADING_CODE].strip()
        self._validate_so_trading_code(
            trading_code,
            quote=quote,
            stock_id=stock_id,
        )
        # Numeric Sina id remains canonical identity; strike pos13 must match CON_OP.
        so_strike = decimal_from_text(fields[_SO_STRIKE], field="so_strike")
        if so_strike is None or so_strike != strike:
            raise DataContractError(
                "CON_SO strike inconsistent with CON_OP",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "strike_mismatch",
                },
            )
        instrument_id = build_instrument_id(
            AssetType.OPTION, Market.A_SHARE, contract_id
        )
        if instrument_id != quote.contract.instrument_id:
            raise DataContractError(
                "CON_SO contract id must match emitted quote",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "identity",
                },
            )
        delta = decimal_from_text(fields[_SO_DELTA], field="delta")
        gamma = decimal_from_text(fields[_SO_GAMMA], field="gamma")
        theta = decimal_from_text(fields[_SO_THETA], field="theta")
        vega = decimal_from_text(fields[_SO_VEGA], field="vega")
        iv = decimal_from_text(fields[_SO_IV], field="implied_volatility")
        theoretical = decimal_from_text(
            fields[_SO_THEORETICAL], field="theoretical_value"
        )
        self._validate_source_greek_ranges(
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            implied_volatility=iv,
            theoretical_value=theoretical,
        )
        return OptionGreeks(
            contract_instrument_id=instrument_id,
            as_of=as_of,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            implied_volatility=iv,
            theoretical_value=theoretical,
            source_provided=True,
        )

    def _validate_so_trading_code(
        self,
        trading_code: str,
        *,
        quote: EtfOptionQuote,
        stock_id: str,
    ) -> None:
        match = _SO_TRADING_CODE_RE.fullmatch(trading_code)
        if match is None:
            raise DataContractError(
                "CON_SO trading code failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "trading_code",
                },
            )
        if match.group("stock") != stock_id:
            raise DataContractError(
                "CON_SO trading code underlying does not match stockId",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "trading_code_stock",
                },
            )
        expected_side = "C" if quote.contract.option_type is OptionType.CALL else "P"
        if match.group("side") != expected_side:
            raise DataContractError(
                "CON_SO trading code side does not match quote option_type",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "trading_code_side",
                },
            )
        expiry = quote.contract.expiry
        expected_yymm = f"{expiry.year % 100:02d}{expiry.month:02d}"
        if match.group("yymm") != expected_yymm:
            raise DataContractError(
                "CON_SO trading code YYMM does not match quote expiry",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "trading_code_yymm",
                },
            )
        # Alphabetic adjustment marker + digits already enforced by regex.

    def _validate_source_greek_ranges(
        self,
        *,
        delta: Decimal | None,
        gamma: Decimal | None,
        theta: Decimal | None,
        vega: Decimal | None,
        implied_volatility: Decimal | None,
        theoretical_value: Decimal | None,
    ) -> None:
        if delta is not None and (not delta.is_finite() or delta < -1 or delta > 1):
            raise DataContractError(
                "source delta must be in [-1, 1] when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "delta_range",
                },
            )
        if gamma is not None and (not gamma.is_finite() or gamma < 0):
            raise DataContractError(
                "source gamma must be nonnegative when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "nonnegative",
                },
            )
        if vega is not None and (not vega.is_finite() or vega < 0):
            raise DataContractError(
                "source vega must be nonnegative when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "nonnegative",
                },
            )
        if implied_volatility is not None and (
            not implied_volatility.is_finite() or implied_volatility < 0
        ):
            raise DataContractError(
                "source implied_volatility must be nonnegative when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "nonnegative",
                },
            )
        if theoretical_value is not None and (
            not theoretical_value.is_finite() or theoretical_value < 0
        ):
            raise DataContractError(
                "source theoretical_value must be nonnegative when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "nonnegative",
                },
            )
        if theta is not None and not theta.is_finite():
            raise DataContractError(
                "source theta must be finite when present",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options_greeks",
                    "rule": "finite",
                },
            )

    def _require_single_local_quote_date(
        self,
        quotes: tuple[EtfOptionQuote, ...],
        *,
        as_of: datetime,
    ) -> None:
        if not quotes:
            return
        local_dates: set[date] = set()
        for quote in quotes:
            if quote.quote_at > as_of:
                raise DataContractError(
                    "quote_at must be <= as_of",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "options",
                        "rule": "as_of_cutoff",
                    },
                )
            local_dates.add(quote.quote_at.astimezone(SHANGHAI).date())
        if len(local_dates) != 1:
            raise DataContractError(
                "option snapshot quotes must share one local quote date",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "mixed_quote_dates",
                },
            )

    def _classify_option_freshness(
        self,
        *,
        quotes: tuple[EtfOptionQuote, ...],
        as_of: datetime,
        fetched_at: datetime,
        now: datetime,
    ) -> tuple[Freshness, TradingSession, int | None]:
        if not quotes:
            session = infer_session_basic(
                Market.A_SHARE, as_of, timezone="Asia/Shanghai"
            )
            return Freshness.UNKNOWN, session, None
        # Use newest quote_at for classification; do not hard-reject a valid
        # latest closed-session quote solely by wall-clock age.
        newest = max(q.quote_at for q in quotes)
        if newest > as_of:
            raise DataContractError(
                "quote_at must be <= as_of",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "options",
                    "rule": "as_of_cutoff",
                },
            )
        session = infer_session_basic(
            Market.A_SHARE, newest, timezone="Asia/Shanghai"
        )
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        ref_now = fetched_at if fetched_at >= newest else now
        try:
            freshness = classify_freshness(
                now=ref_now if ref_now >= newest else now,
                data_timestamp=newest,
                session=session,
                max_fresh_seconds=self._max_fresh_seconds,
                max_delayed_seconds=self._max_delayed_seconds,
                vendor_declared_delay_seconds=None,
            )
        except DataContractError:
            freshness = Freshness.UNKNOWN
        data_delay = max(0, int((fetched_at - newest).total_seconds()))
        return freshness, session, data_delay
