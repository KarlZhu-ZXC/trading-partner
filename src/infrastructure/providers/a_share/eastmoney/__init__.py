"""Stable Eastmoney A-share adapter façade."""

from __future__ import annotations

from infrastructure.providers.a_share.eastmoney.capital import EastmoneyCapitalMixin
from infrastructure.providers.a_share.eastmoney.common import (
    _CLIST_EQUITY_MAX_PAGES,
    _CLIST_EQUITY_MAX_TOTAL,
    _CLIST_EQUITY_PAGE_SIZE,
    _CLIST_INDUSTRY_MAX_PAGES,
    _CLIST_INDUSTRY_MAX_TOTAL,
    _CLIST_INDUSTRY_PAGE_SIZE,
    _CLIST_URL,
    _DATACENTER_URL,
    _DT_POOL_URL,
    _PUSH2EX_DPT,
    _PUSH2EX_POOL_PAGE_SIZE,
    _PUSH2EX_UT,
    _SUPPORTED_CATEGORIES,
    _ZB_POOL_URL,
    _ZT_POOL_URL,
    EASTMONEY_A_SHARE_EQUITY_FS,
    EASTMONEY_INDUSTRY_BOARD_FS,
    SHANGHAI,
    AdjustmentMethod,
    Any,
    AShareTradingCalendar,
    CacheDisposition,
    Clock,
    DataCategory,
    DataContractError,
    Decimal,
    EastmoneyHttpClient,
    EastmoneyRequestGate,
    Freshness,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    IndustryPerformanceRow,
    Mapping,
    Market,
    MarketBoardSnapshot,
    NoMarketData,
    ProviderNotConfigured,
    ProviderResultMeta,
    ProviderSuccess,
    ProviderUnavailableError,
    SourceRole,
    StaleMarketData,
    SystemClock,
    TradingSession,
    VendorId,
    classify_freshness,
    date,
    datetime,
    decimal_from_text,
    infer_session_basic,
    int_from_text,
    loads_json_decimal,
    require_aware_datetime,
    require_current_clist_trade_date,
    require_decimal,
    require_exact_date,
    require_int,
)
from infrastructure.providers.a_share.eastmoney.fundamentals import (
    EastmoneyFundamentalsMixin,
)
from infrastructure.providers.a_share.eastmoney.quote_bars import EastmoneyQuoteBarsMixin
from infrastructure.providers.a_share.eastmoney.sentiment import EastmoneySentimentMixin


class EastmoneyAShareAdapter(
    EastmoneyQuoteBarsMixin,
    EastmoneyFundamentalsMixin,
    EastmoneyCapitalMixin,
    EastmoneySentimentMixin,
):
    """CategoryProvider façade retaining the original public adapter type."""

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
        self._client = EastmoneyHttpClient(
            transport,
            gate,
            user_agent=user_agent,
        )
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
        return await self._client.send(request)

    def _headers(self) -> dict[str, str]:
        return self._client.headers()

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        self._client.require_success(status_code, operation=operation)

    def _require_json_content_type(self, headers: Mapping[str, str], *, operation: str) -> None:
        self._client.require_json_content_type(headers, operation=operation)

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
