"""Quote, bars, order-book, and ticks Eastmoney endpoint implementation."""

# Mixin attributes are supplied by EastmoneyAShareAdapter.
# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from infrastructure.providers.a_share.eastmoney.common import (
    _DETAILS_URL,
    _FQT_BY_ADJUSTMENT,
    _KLINE_URL,
    _KLT_BY_INTERVAL,
    _QUOTE_FIELDS,
    _QUOTE_URL,
    SHANGHAI,
    AdjustmentMethod,
    AShareBar,
    AShareQuote,
    AssetType,
    BarInterval,
    DataCategory,
    DataContractError,
    HttpRequest,
    Instrument,
    Market,
    NoMarketData,
    OrderBookLevel,
    ProviderSuccess,
    TickDirection,
    TradeTick,
    combine_shanghai_date_time,
    date,
    datetime,
    decimal_from_text,
    eastmoney_secid,
    first_day_of_month,
    infer_session_basic,
    int_from_text,
    loads_json_decimal,
    lots_to_shares,
    parse_shanghai_date,
    require_a_share_instrument,
    require_decimal,
    require_exact_date,
    require_int,
    timedelta,
    validate_order_book_levels,
    week_period_start,
)


class EastmoneyQuoteBarsMixin:
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
