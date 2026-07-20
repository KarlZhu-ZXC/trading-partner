"""Tencent A-share quote and forward-adjusted daily OHLCV adapter.

Supports ``MARKET_QUOTE`` plus Tencent's ``fqkline`` daily bars. Does not claim
order book or ticks.

Field map (0-based within body after ``~`` split), locked to live probe 2026-07-17:
  2 code; 3 last; 4 previous close; 5 open; 6 volume lots;
  30 timestamp YYYYMMDDHHMMSS; 31 change; 32 change_percent; 33 high; 34 low;
  35 composite last/volume_lots/exact_turnover_cny;
  38 turnover rate; 39 PE; 44/45 float/total market cap in 亿 CNY; 47/48 limit up/down.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.enums import BarInterval
from domain.a_share.models import AShareBar, AShareQuote
from domain.common.enums import (
    AdjustmentMethod,
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
from domain.market.freshness import classify_freshness
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    content_type_matches,
    decimal_from_text,
    decode_text,
    loads_json_decimal,
    lots_to_shares,
    parse_tencent_composite_turnover,
    parse_tencent_timestamp,
    require_a_share_instrument,
    require_decimal,
    require_int,
    tencent_symbol,
    yi_to_cny,
)
from infrastructure.system.clock import SystemClock

_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q"
_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_QUOTE_LINE_RE = re.compile(
    r'^v_(?P<sym>[a-z]{2}\d{6})="(?P<body>.*)";?\s*$',
    re.MULTILINE,
)
_ALLOWED_CONTENT = ("text/plain", "text/html", "application/octet-stream", "*/*")


class TencentAShareAdapter:
    """Tencent adapter for quotes and forward-adjusted daily bars."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        calendar: AShareTradingCalendar | None = None,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
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
        self._calendar = calendar
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._max_fresh_seconds = max_fresh_seconds
        self._max_delayed_seconds = max_delayed_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.TENCENT

    @property
    def provider_name(self) -> str:
        return VendorId.TENCENT.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in {
            DataCategory.MARKET_QUOTE,
            DataCategory.MARKET_OHLCV,
        }

    def is_configured(self) -> bool:
        return self._enabled

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[AShareQuote]:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Tencent A-share adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        symbol = tencent_symbol(code6, suffix)

        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_TENCENT_QUOTE_URL,
                params={"q": symbol},
                headers={
                    "Accept": "text/plain,*/*",
                    "User-Agent": self._user_agent,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_http_status(response.status_code)
        self._require_content_type(response.headers)

        try:
            text = decode_text(response.body, encodings=("gbk", "utf-8"))
            quote = self._parse_quote_text(
                text,
                instrument=instrument,
                symbol=symbol,
                as_of=as_of,
            )
        except DataContractError as exc:
            self._ensure_no_body_leak(exc)
            raise
        except NoMarketData as exc:
            self._ensure_no_body_leak(exc)
            raise

        if quote.quote_at > as_of:
            raise DataContractError(
                "quote_at must be <= requested as_of cutoff",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "as_of_cutoff",
                },
            )

        session = infer_session_basic(
            Market.A_SHARE, quote.quote_at, timezone="Asia/Shanghai"
        )
        if quote.session is not session:
            quote = AShareQuote(
                instrument_id=quote.instrument_id,
                quote_at=quote.quote_at,
                session=session,
                last=quote.last,
                open=quote.open,
                high=quote.high,
                low=quote.low,
                previous_close=quote.previous_close,
                change=quote.change,
                change_percent=quote.change_percent,
                volume_shares=quote.volume_shares,
                turnover_amount_cny=quote.turnover_amount_cny,
                turnover_rate=quote.turnover_rate,
                pe_ttm=quote.pe_ttm,
                pb=quote.pb,
                total_market_cap_cny=quote.total_market_cap_cny,
                float_market_cap_cny=quote.float_market_cap_cny,
                limit_up_price=quote.limit_up_price,
                limit_down_price=quote.limit_down_price,
            )

        delay = int((fetched_at - quote.quote_at).total_seconds())
        if delay < 0:
            delay = int((as_of - quote.quote_at).total_seconds())
            if delay < 0:
                delay = 0
        # Prefer delay vs fetched_at; also cap classification reference at as_of.
        ref_now = fetched_at if fetched_at <= as_of else as_of
        if quote.quote_at > ref_now:
            ref_now = fetched_at
        try:
            freshness = classify_freshness(
                now=ref_now if ref_now >= quote.quote_at else fetched_at,
                data_timestamp=quote.quote_at,
                session=quote.session,
                max_fresh_seconds=self._max_fresh_seconds,
                max_delayed_seconds=self._max_delayed_seconds,
                vendor_declared_delay_seconds=None,
            )
        except DataContractError:
            freshness = Freshness.UNKNOWN

        data_delay = max(0, int((fetched_at - quote.quote_at).total_seconds()))
        meta = ProviderResultMeta(
            vendor=self.vendor_id,
            category=DataCategory.MARKET_QUOTE,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            session=quote.session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=data_delay,
            warnings=(),
        )
        return ProviderSuccess(value=quote, meta=meta)

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
        """Return Tencent daily qfq bars; unsupported shapes fall through Router."""
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Tencent A-share adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        if type(start) is not date or type(end) is not date:
            raise DataContractError(
                "start and end must be exact dates",
                details={"field": "date_range", "rule": "type"},
            )
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if interval is not BarInterval.ONE_DAY:
            raise DataContractError(
                "Tencent OHLCV supports daily bars only",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "interval",
                    "rule": "unsupported_interval",
                },
            )
        if adjustment is not AdjustmentMethod.FORWARD_ADJUSTED:
            raise DataContractError(
                "Tencent OHLCV supports forward adjustment only",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "adjustment",
                    "rule": "unsupported_adjustment",
                },
            )
        if instrument.asset_type not in {AssetType.EQUITY, AssetType.ETF}:
            raise DataContractError(
                "Tencent daily bar volume is unsupported for this asset type",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "asset_type",
                    "rule": "volume_unit_unsupported",
                },
            )
        if self._calendar is None:
            raise ProviderNotConfigured(
                "Tencent OHLCV requires the A-share trading calendar",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )

        code6, suffix = require_a_share_instrument(instrument)
        symbol = tencent_symbol(code6, suffix)
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_TENCENT_KLINE_URL,
                params={
                    "param": ",".join(
                        (
                            symbol,
                            "day",
                            start.isoformat(),
                            end.isoformat(),
                            "2000",
                            "qfq",
                        )
                    )
                },
                headers={
                    "Accept": "application/json,*/*",
                    "User-Agent": self._user_agent,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_http_status(response.status_code)
        self._require_json_content_type(response.headers)
        payload = loads_json_decimal(response.body)
        bars = self._parse_bars_payload(
            payload,
            instrument=instrument,
            symbol=symbol,
            start=start,
            end=end,
            as_of=as_of,
        )
        if not bars:
            raise NoMarketData(
                "Tencent returned no daily bars",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        data_delay = max(0, int((fetched_at - bars[-1].end_at).total_seconds()))
        return ProviderSuccess(
            value=bars,
            meta=ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.MARKET_OHLCV,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                session=session,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=adjustment,
                data_delay_seconds=data_delay,
                warnings=(),
            ),
        )

    def _require_json_content_type(self, headers: Mapping[str, str]) -> None:
        if not content_type_matches(
            headers,
            allowed_substrings=(
                "application/json",
                "text/json",
                "text/plain",
                "text/html",
                "*/*",
            ),
        ):
            raise DataContractError(
                "Tencent bars response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "content_type",
                },
            )

    def _parse_bars_payload(
        self,
        payload: object,
        *,
        instrument: Instrument,
        symbol: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> tuple[AShareBar, ...]:
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise self._bars_contract_error()
        if payload["code"] != 0:
            raise NoMarketData(
                "Tencent returned no daily bars",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise self._bars_contract_error()
        instrument_data = data.get(symbol)
        if instrument_data is None:
            raise DataContractError(
                "Tencent bars response symbol does not match requested instrument",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "bars",
                    "rule": "identity_mismatch",
                },
            )
        if not isinstance(instrument_data, dict):
            raise self._bars_contract_error()
        rows = instrument_data.get("qfqday")
        if rows is None and instrument.asset_type is AssetType.ETF:
            # Tencent currently labels ETF qfq requests as `day`; accepting this
            # is deliberately asset-scoped and must not weaken the equity contract.
            rows = instrument_data.get("day")
        if not isinstance(rows, list):
            raise self._bars_contract_error()

        out: list[AShareBar] = []
        seen: set[datetime] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) < 6:
                raise self._bars_contract_error(index=index)
            try:
                bar_day = date.fromisoformat(str(row[0]))
            except ValueError:
                raise self._bars_contract_error(index=index) from None
            if bar_day < start or bar_day > end:
                continue
            windows = self._calendar.sessions_for(bar_day) if self._calendar else ()
            if not windows:
                raise DataContractError(
                    "Tencent bar date is not a trading day",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "non_trading_day",
                        "index": index,
                    },
                )
            start_at = windows[0].start_at
            end_at = windows[-1].end_at
            if end_at > as_of:
                continue
            if start_at in seen:
                raise DataContractError(
                    "Tencent bars must have unique dates",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "bars",
                        "rule": "unique_start",
                        "index": index,
                    },
                )
            seen.add(start_at)
            volume_lots = require_int(row[5], field=f"bars[{index}].volume_lots")
            volume = lots_to_shares(
                volume_lots,
                field=f"bars[{index}].volume_shares",
                asset_type=instrument.asset_type,
            )
            if volume is None:
                raise self._bars_contract_error(index=index)
            out.append(
                AShareBar(
                    start_at=start_at,
                    end_at=end_at,
                    interval=BarInterval.ONE_DAY,
                    open=require_decimal(row[1], field=f"bars[{index}].open"),
                    close=require_decimal(row[2], field=f"bars[{index}].close"),
                    high=require_decimal(row[3], field=f"bars[{index}].high"),
                    low=require_decimal(row[4], field=f"bars[{index}].low"),
                    volume_shares=volume,
                    turnover_amount_cny=None,
                    adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
                )
            )
        out.sort(key=lambda bar: bar.start_at)
        return tuple(out)

    def _bars_contract_error(self, *, index: int | None = None) -> DataContractError:
        details: dict[str, object] = {
            "vendor": self.vendor_id.value,
            "operation": "bars",
            "rule": "contract_drift",
        }
        if index is not None:
            details["index"] = index
        return DataContractError(
            "Tencent bars payload failed contract validation",
            details=details,
        )

    def _ensure_no_body_leak(self, exc: Exception) -> None:
        # Typed errors must never embed raw response bodies.
        blob = f"{getattr(exc, 'message', '')}{getattr(exc, 'details', {})}"
        if "v_sh" in blob or "v_sz" in blob:
            raise DataContractError(
                "provider error must not embed raw response body",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "secrecy",
                },
            ) from None

    def _require_content_type(self, headers: Mapping[str, str]) -> None:
        if not content_type_matches(headers, allowed_substrings=_ALLOWED_CONTENT):
            # Missing/wrong type is contract drift after status was accepted.
            if not headers.get("content-type") and not headers.get("Content-Type"):
                raise DataContractError(
                    "Tencent response missing Content-Type",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "quote",
                        "rule": "content_type",
                    },
                )
            raise DataContractError(
                "Tencent response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "content_type",
                },
            )

    def _raise_for_http_status(self, status_code: int) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Tencent rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Tencent access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Tencent HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _parse_quote_text(
        self,
        text: str,
        *,
        instrument: Instrument,
        symbol: str,
        as_of: datetime,
    ) -> AShareQuote:
        del as_of  # cutoff enforced by caller; never used as quote_at substitute
        stripped = text.strip()
        if not stripped or stripped in {'v_pv_none_match="1";'}:
            raise NoMarketData(
                "Tencent returned no quote data",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        match = _QUOTE_LINE_RE.search(stripped)
        if match is None:
            raise DataContractError(
                "Tencent quote payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "contract_drift",
                },
            )
        response_symbol = match.group("sym")
        if response_symbol != symbol:
            raise DataContractError(
                "Tencent response symbol does not match requested instrument",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "identity_mismatch",
                },
            )
        body = match.group("body")
        if not body or body.strip() == "":
            raise NoMarketData(
                "Tencent returned empty quote payload",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        fields = body.split("~")
        # Need through field 48 for limits; require at least 36 for core map.
        if len(fields) < 36:
            raise DataContractError(
                "Tencent quote payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "contract_drift",
                },
            )

        code_field = fields[2].strip() if len(fields) > 2 else ""
        expected_code = symbol[2:]  # strip sh/sz/bj
        if code_field != expected_code:
            raise DataContractError(
                "Tencent response code does not match requested instrument",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "identity_mismatch",
                },
            )

        last = require_decimal(fields[3], field="last")
        if last <= 0:
            raise NoMarketData(
                "Tencent returned non-positive last price",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )

        previous_close = decimal_from_text(fields[4], field="previous_close")
        open_ = decimal_from_text(fields[5], field="open")
        volume_lots = require_int(fields[6], field="volume_lots")
        volume_shares = lots_to_shares(
            volume_lots,
            field="volume_shares",
            asset_type=instrument.asset_type
            if isinstance(instrument.asset_type, AssetType)
            else AssetType.EQUITY,
        )

        # Field 30 is required exact timestamp; never fall back to as_of.
        if len(fields) <= 30 or not fields[30].strip():
            raise DataContractError(
                "Tencent quote timestamp missing",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "quote",
                    "rule": "contract_drift",
                },
            )
        quote_at = parse_tencent_timestamp(fields[30])

        change = decimal_from_text(fields[31], field="change") if len(fields) > 31 else None
        change_percent = (
            decimal_from_text(fields[32], field="change_percent")
            if len(fields) > 32
            else None
        )
        high = decimal_from_text(fields[33], field="high") if len(fields) > 33 else None
        low = decimal_from_text(fields[34], field="low") if len(fields) > 34 else None

        turnover_amount = None
        if len(fields) > 35:
            turnover_amount = parse_tencent_composite_turnover(
                fields[35], field="turnover_amount_cny"
            )

        turnover_rate = (
            decimal_from_text(fields[38], field="turnover_rate")
            if len(fields) > 38
            else None
        )
        pe_ttm = decimal_from_text(fields[39], field="pe_ttm") if len(fields) > 39 else None

        float_mkt = None
        if len(fields) > 44 and fields[44].strip():
            float_yi = require_decimal(fields[44], field="float_market_cap_yi")
            float_mkt = yi_to_cny(float_yi, field="float_market_cap_cny")
        total_mkt = None
        if len(fields) > 45 and fields[45].strip():
            total_yi = require_decimal(fields[45], field="total_market_cap_yi")
            total_mkt = yi_to_cny(total_yi, field="total_market_cap_cny")

        limit_up = (
            decimal_from_text(fields[47], field="limit_up_price")
            if len(fields) > 47
            else None
        )
        limit_down = (
            decimal_from_text(fields[48], field="limit_down_price")
            if len(fields) > 48
            else None
        )

        session = infer_session_basic(
            Market.A_SHARE, quote_at, timezone="Asia/Shanghai"
        )
        return AShareQuote(
            instrument_id=instrument.instrument_id,
            quote_at=quote_at,
            session=session if isinstance(session, TradingSession) else TradingSession.UNKNOWN,
            last=last,
            open=open_,
            high=high,
            low=low,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            volume_shares=volume_shares,
            turnover_amount_cny=turnover_amount,
            turnover_rate=turnover_rate,
            pe_ttm=pe_ttm,
            pb=None,
            total_market_cap_cny=total_mkt,
            float_market_cap_cny=float_mkt,
            limit_up_price=limit_up,
            limit_down_price=limit_down,
        )
