"""Yahoo Finance direct-HTTP chart adapter (Phase 1F F2a).

Fixed host/path only: ``https://query1.finance.yahoo.com/v8/finance/chart/{symbol}``.
GET chart JSON — no yfinance package. Implements ``USQuoteProvider`` + ``USBarsProvider``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
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
    InvalidInstrument,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.common.time import require_aware_datetime
from domain.cross_asset.cme_identity import (
    is_legacy_us_continuous_proxy,
    parse_cme_contract_code,
    yahoo_symbol_for_cme_instrument,
)
from domain.instruments.models import Instrument
from domain.market.freshness import classify_freshness
from domain.market.models import MarketBar
from domain.market.session import infer_session_basic
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USQuote
from infrastructure.system.clock import SystemClock

_NY = ZoneInfo("America/New_York")
_CHART_HOST = "https://query1.finance.yahoo.com"
_CHART_PATH_PREFIX = "/v8/finance/chart/"
_ALLOWED_ASSETS = frozenset(
    {AssetType.EQUITY, AssetType.ETF, AssetType.INDEX, AssetType.FUTURE}
)
_CONTINUOUS_FUTURES_WARNING_CODES = (
    "FUTURES_CONTRACT_NOT_SPOT",
    "CONTINUOUS_FUTURES_ROLL_RISK",
)
_SPECIFIC_FUTURES_WARNING_CODES = (
    "FUTURES_CONTRACT_NOT_SPOT",
    "YAHOO_ACTIVE_CONTRACT_NO_EXPIRED_HISTORY",
)
_JSON_CONTENT = ("application/json", "text/json", "text/plain", "*/*")
# Latest daily bar more than this many natural days behind expected end/as_of → stale.
_MAX_DAILY_STALE_NATURAL_DAYS = 4
_SESSION_CLOSE = time(16, 0)
_CURRENT_QUOTE_CUTOFF_SECONDS = 300
_EXTENDED_HOURS_PRICE_WARNING = "EXTENDED_HOURS_PRICE"
_INTRADAY_QUOTE_RECOVERY_WARNING = "INTRADAY_QUOTE_RECOVERY"
_INTRADAY_QUOTE_UNAVAILABLE_WARNING = "INTRADAY_QUOTE_UNAVAILABLE"
_PREVIOUS_CLOSE_RECOVERY_WARNING = "PREVIOUS_CLOSE_REGULAR_SESSION_RECOVERY"

_INTERVAL_WIRE: Mapping[USBarInterval, str] = {
    USBarInterval.ONE_MINUTE: "1m",
    USBarInterval.FIVE_MINUTES: "5m",
    USBarInterval.FIFTEEN_MINUTES: "15m",
    USBarInterval.THIRTY_MINUTES: "30m",
    USBarInterval.SIXTY_MINUTES: "60m",
    USBarInterval.ONE_DAY: "1d",
    USBarInterval.ONE_WEEK: "1wk",
    USBarInterval.ONE_MONTH: "1mo",
}

_SUPPORTED_CATEGORIES = frozenset(
    {
        DataCategory.MARKET_QUOTE,
        DataCategory.MARKET_OHLCV,
    }
)


def _contract(
    message: str,
    *,
    operation: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    details: dict[str, object] = {
        "vendor": VendorId.YFINANCE.value,
        "operation": operation,
        "rule": rule,
    }
    details.update(extra)
    return DataContractError(message, details=details)


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise DataContractError(
                "JSON object contains duplicate keys",
                details={"field": "json", "rule": "duplicate_key"},
            )
        out[key] = value
    return out


def _reject_nonfinite_constant(name: str) -> None:
    raise DataContractError(
        "JSON non-finite constant is not allowed",
        details={"field": "json", "rule": "no_nan_infinity", "constant": name},
    )


def _loads_json_decimal(body: bytes) -> Any:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise DataContractError(
            "response body is not valid UTF-8",
            details={"field": "body", "rule": "encoding"},
        ) from None
    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_nonfinite_constant,
            object_pairs_hook=_reject_duplicate_object_pairs,
        )
    except DataContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise DataContractError(
            "response body is not valid JSON",
            details={"field": "body", "rule": "json"},
        ) from None


def _content_type_ok(headers: Mapping[str, str]) -> bool:
    raw = headers.get("content-type") or headers.get("Content-Type")
    if not isinstance(raw, str) or not raw.strip():
        return False
    lowered = raw.split(";", 1)[0].strip().casefold()
    return any(token in lowered for token in _JSON_CONTENT)


def _as_decimal(value: object, *, field: str) -> Decimal:
    if type(value) is Decimal:
        if not value.is_finite():
            raise DataContractError(
                f"{field} must be a finite Decimal",
                details={"field": field, "rule": "finite_decimal"},
            )
        return value
    if type(value) is int and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = Decimal(value.strip())
        except Exception:
            raise DataContractError(
                f"{field} is not a valid decimal",
                details={"field": field, "rule": "decimal_parse"},
            ) from None
        if not parsed.is_finite():
            raise DataContractError(
                f"{field} must be a finite Decimal",
                details={"field": field, "rule": "finite_decimal"},
            )
        return parsed
    raise DataContractError(
        f"{field} must be a decimal number",
        details={"field": field, "rule": "decimal_type", "type": type(value).__name__},
    )


def _optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _as_decimal(value, field=field)


def _ny_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=_NY)


def _ny_session_close(day: date) -> datetime:
    return datetime(
        day.year, day.month, day.day, _SESSION_CLOSE.hour, _SESSION_CLOSE.minute, tzinfo=_NY
    )


def _unix_to_aware(ts: object, *, field: str) -> datetime:
    if type(ts) is not int or isinstance(ts, bool):
        if type(ts) is Decimal:
            try:
                ts = int(ts)
            except Exception:
                raise DataContractError(
                    f"{field} must be a unix timestamp",
                    details={"field": field, "rule": "unix_int"},
                ) from None
        else:
            raise DataContractError(
                f"{field} must be a unix timestamp",
                details={"field": field, "rule": "unix_int"},
            )
    return datetime.fromtimestamp(int(ts), tz=_NY)


class YahooFinanceAdapter:
    """CategoryProvider for US quote + OHLCV via Yahoo chart HTTP only."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        max_fresh_seconds: int = 30,
        max_delayed_seconds: int = 900,
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
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._max_fresh_seconds = max_fresh_seconds
        self._max_delayed_seconds = max_delayed_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.YFINANCE

    @property
    def provider_name(self) -> str:
        return VendorId.YFINANCE.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        if category not in _SUPPORTED_CATEGORIES:
            return False
        # US equities/ETFs/indices/continuous futures, plus CME specific contracts.
        return market in {Market.US, Market.CME}

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Yahoo Finance adapter is disabled",
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

    def _require_chart_instrument(self, instrument: Instrument) -> str:
        """Return the Yahoo chart symbol for a supported instrument.

        - ``future:US:GC=F`` continuous proxies use the instrument symbol as-is.
        - ``future:CME:GCZ26`` specific contracts map to ``GCZ26.CMX`` and never
          fall back to ``GC=F``.
        """
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.asset_type not in _ALLOWED_ASSETS:
            raise DataContractError(
                "Yahoo Finance supports equity/ETF/index/future instruments",
                details={
                    "field": "instrument",
                    "rule": "asset_type",
                    "asset_type": instrument.asset_type.value,
                },
            )
        if instrument.market is Market.US:
            symbol = instrument.symbol.strip()
            if not symbol:
                raise DataContractError(
                    "instrument symbol must be non-blank",
                    details={"field": "symbol", "rule": "non_blank"},
                )
            return symbol
        if instrument.market is Market.CME and instrument.asset_type is AssetType.FUTURE:
            # Specific contracts only — never rewrite or fall back to GC=F.
            if is_legacy_us_continuous_proxy(instrument.instrument_id):
                raise DataContractError(
                    "legacy future:US:* proxy must not be treated as CME specific",
                    details={
                        "instrument_id": instrument.instrument_id,
                        "rule": "no_us_proxy_rewrite",
                    },
                )
            try:
                # Validate grammar (roots + month/year) before mapping.
                parse_cme_contract_code(instrument.symbol)
                return yahoo_symbol_for_cme_instrument(instrument.instrument_id)
            except InvalidInstrument as exc:
                raise DataContractError(
                    "Yahoo active-contract mapping requires validated CME contract code",
                    details={
                        "instrument_id": instrument.instrument_id,
                        "rule": "cme_contract_grammar",
                        "code": "INVALID_INSTRUMENT",
                    },
                ) from exc
        raise DataContractError(
            "Yahoo Finance instrument market must be US or CME futures",
            details={
                "field": "instrument",
                "rule": "market",
                "market": instrument.market.value,
            },
        )

    @staticmethod
    def _futures_warning_codes(instrument: Instrument) -> tuple[str, ...]:
        if instrument.asset_type is not AssetType.FUTURE:
            return ()
        if instrument.market is Market.CME:
            return _SPECIFIC_FUTURES_WARNING_CODES
        return _CONTINUOUS_FUTURES_WARNING_CODES

    def _chart_url(self, symbol: str) -> str:
        # URL-encode the path segment; keep host/path fixed.
        return f"{_CHART_HOST}{_CHART_PATH_PREFIX}{quote(symbol, safe='')}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
        }

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Yahoo Finance rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Yahoo Finance access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Yahoo Finance HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _require_json_content_type(
        self, headers: Mapping[str, str], *, operation: str
    ) -> None:
        if _content_type_ok(headers):
            return
        if not headers.get("content-type") and not headers.get("Content-Type"):
            raise _contract(
                "Yahoo Finance response missing Content-Type",
                operation=operation,
                rule="content_type",
            )
        raise _contract(
            "Yahoo Finance response Content-Type is not acceptable",
            operation=operation,
            rule="content_type",
        )

    async def _fetch_chart(
        self,
        symbol: str,
        *,
        params: Mapping[str, str],
        operation: str,
    ) -> tuple[object, datetime]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=self._chart_url(symbol),
                params=dict(params),
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_http_status(response.status_code, operation=operation)
        self._require_json_content_type(response.headers, operation=operation)
        try:
            payload = _loads_json_decimal(response.body)
        except DataContractError as exc:
            # Never attach raw body to details/message.
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule=str(exc.details.get("rule", "json")),
            ) from None
        return payload, fetched_at

    def _chart_result(self, payload: object, *, operation: str) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        error = chart.get("error")
        if error is not None:
            # Upstream error object — do not echo contents.
            raise NoMarketData(
                "Yahoo Finance returned a chart error",
                details={"vendor": self.vendor_id.value, "operation": operation},
            )
        results = chart.get("result")
        if results is None:
            raise NoMarketData(
                "Yahoo Finance returned no chart result",
                details={"vendor": self.vendor_id.value, "operation": operation},
            )
        if not isinstance(results, list) or not results:
            raise NoMarketData(
                "Yahoo Finance returned empty chart result",
                details={"vendor": self.vendor_id.value, "operation": operation},
            )
        first = results[0]
        if not isinstance(first, dict):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        return first

    def _session_from_meta(
        self, meta: Mapping[str, object], at: datetime
    ) -> TradingSession:
        periods = meta.get("currentTradingPeriod")
        if not isinstance(periods, dict):
            return infer_session_basic(Market.US, at, timezone="America/New_York")
        unix = int(at.timestamp())
        for key, session in (
            ("pre", TradingSession.PRE_MARKET),
            ("regular", TradingSession.REGULAR),
            ("post", TradingSession.POST_MARKET),
        ):
            block = periods.get(key)
            if not isinstance(block, dict):
                continue
            start = block.get("start")
            end = block.get("end")
            if type(start) is int and type(end) is int and start <= unix < end:
                return session
        return TradingSession.CLOSED

    def _meta(
        self,
        *,
        instrument_asset_type: AssetType,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        session: TradingSession,
        data_timestamp: datetime | None,
        adjustment: AdjustmentMethod | None,
        additional_warnings: tuple[str, ...] = (),
        instrument_market: Market = Market.US,
    ) -> ProviderResultMeta:
        if data_timestamp is not None:
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
        futures_codes: tuple[str, ...]
        if instrument_asset_type is AssetType.FUTURE:
            futures_codes = (
                _SPECIFIC_FUTURES_WARNING_CODES
                if instrument_market is Market.CME
                else _CONTINUOUS_FUTURES_WARNING_CODES
            )
        else:
            futures_codes = ()
        warnings = tuple(
            dict.fromkeys(
                (
                    *futures_codes,
                    *additional_warnings,
                )
            )
        )
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

    def _parse_ohlcv_arrays(
        self,
        result: Mapping[str, object],
        *,
        operation: str,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
        start: date | None = None,
        end: date | None = None,
    ) -> list[MarketBar]:
        timestamps = result.get("timestamp")
        if timestamps is None:
            return []
        if not isinstance(timestamps, list):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        indicators = result.get("indicators")
        if not isinstance(indicators, dict):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        quote_list = indicators.get("quote")
        if not isinstance(quote_list, list) or not quote_list:
            return []
        quote0 = quote_list[0]
        if not isinstance(quote0, dict):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        opens = quote0.get("open")
        highs = quote0.get("high")
        lows = quote0.get("low")
        closes = quote0.get("close")
        volumes = quote0.get("volume")
        if not all(isinstance(x, list) for x in (opens, highs, lows, closes, volumes)):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )
        assert isinstance(opens, list)
        assert isinstance(highs, list)
        assert isinstance(lows, list)
        assert isinstance(closes, list)
        assert isinstance(volumes, list)
        n = len(timestamps)
        if not all(len(x) == n for x in (opens, highs, lows, closes, volumes)):
            raise _contract(
                "Yahoo Finance chart OHLCV array length mismatch",
                operation=operation,
                rule="contract_drift",
            )

        adj_closes: list[object] | None = None
        if adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED:
            adj_list = indicators.get("adjclose")
            if not isinstance(adj_list, list) or not adj_list:
                raise _contract(
                    "Yahoo Finance adjclose required for split+dividend adjustment",
                    operation=operation,
                    rule="adjustment_unavailable",
                )
            adj0 = adj_list[0]
            if not isinstance(adj0, dict):
                raise _contract(
                    "Yahoo Finance adjclose required for split+dividend adjustment",
                    operation=operation,
                    rule="adjustment_unavailable",
                )
            raw_adj = adj0.get("adjclose")
            if not isinstance(raw_adj, list) or len(raw_adj) != n:
                raise _contract(
                    "Yahoo Finance adjclose required for split+dividend adjustment",
                    operation=operation,
                    rule="adjustment_unavailable",
                )
            adj_closes = raw_adj

        out: list[MarketBar] = []
        for idx in range(n):
            open_raw = opens[idx]
            high_raw = highs[idx]
            low_raw = lows[idx]
            close_raw = closes[idx]
            vol_raw = volumes[idx]
            # Yahoo pads gaps with null rows — skip rather than invent prices.
            if any(v is None for v in (open_raw, high_raw, low_raw, close_raw)):
                continue
            ts = _unix_to_aware(timestamps[idx], field=f"timestamp[{idx}]")
            if interval is USBarInterval.ONE_DAY:
                ts = _ny_session_close(ts.astimezone(_NY).date())
            if ts > as_of:
                continue
            local_day = ts.astimezone(_NY).date()
            if start is not None and local_day < start:
                continue
            if end is not None and local_day > end:
                continue

            open_ = _as_decimal(open_raw, field=f"bars[{idx}].open")
            high = _as_decimal(high_raw, field=f"bars[{idx}].high")
            low = _as_decimal(low_raw, field=f"bars[{idx}].low")
            close = _as_decimal(close_raw, field=f"bars[{idx}].close")
            volume = (
                Decimal(0)
                if vol_raw is None
                else _as_decimal(vol_raw, field=f"bars[{idx}].volume")
            )
            if volume < 0:
                raise _contract(
                    "bar volume must be nonnegative",
                    operation=operation,
                    rule="nonnegative",
                )

            if adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED:
                assert adj_closes is not None
                adj_raw = adj_closes[idx]
                if adj_raw is None:
                    raise _contract(
                        "Yahoo Finance adjclose missing for bar",
                        operation=operation,
                        rule="adjustment_unavailable",
                    )
                adj = _as_decimal(adj_raw, field=f"bars[{idx}].adjclose")
                if close == 0:
                    raise _contract(
                        "Yahoo Finance cannot scale bar with zero close",
                        operation=operation,
                        rule="adjustment_unavailable",
                    )
                factor = adj / close
                open_ = open_ * factor
                high = high * factor
                low = low * factor
                close = adj
            elif adjustment is AdjustmentMethod.NONE:
                pass
            else:
                # Unreachable when callers gate adjustment; defensive.
                raise _contract(
                    "unsupported adjustment for Yahoo Finance bars",
                    operation=operation,
                    rule="unsupported_adjustment",
                )

            out.append(
                MarketBar(
                    timestamp=ts,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
        out.sort(key=lambda b: b.timestamp)
        return out

    def _assert_daily_not_stale(
        self,
        bars: Sequence[MarketBar],
        *,
        expected_day: date,
        operation: str,
    ) -> None:
        if not bars:
            return
        latest_day = bars[-1].timestamp.astimezone(_NY).date()
        lag = (expected_day - latest_day).days
        if lag > _MAX_DAILY_STALE_NATURAL_DAYS:
            raise StaleMarketData(
                "Yahoo Finance latest daily bar is stale",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "stale_daily_bar",
                    "max_natural_days": _MAX_DAILY_STALE_NATURAL_DAYS,
                },
            )

    @staticmethod
    def _previous_session_close(
        bars: Sequence[MarketBar],
        *,
        quote_at: datetime,
        quote_session: TradingSession,
        asset_type: AssetType,
        regular_market_at: datetime | None,
        regular_market_price: Decimal | None,
    ) -> tuple[Decimal | None, bool]:
        """Return the regular-session baseline for the selected quote.

        Yahoo ``chartPreviousClose`` is the baseline immediately before the chart
        window, while ``regularMarketPreviousClose`` is relative to Yahoo's
        ``regularMarketPrice`` observation. Neither field is a stable definition
        of the previous close for an extended-hours quote, so both stay unused.

        Prefer a complete daily bar. Yahoo can temporarily publish the latest
        session's OHLC and volume with a null daily close; in pre/post market the
        timestamped ``regularMarketPrice`` then provides the completed regular
        close. The boolean reports that narrowly scoped recovery.
        """
        quote_day = quote_at.astimezone(_NY).date()
        same_day_close_allowed = (
            asset_type in {AssetType.EQUITY, AssetType.ETF, AssetType.INDEX}
            and quote_session is TradingSession.POST_MARKET
        )
        selected_bar: MarketBar | None = None
        for bar in reversed(bars):
            bar_day = bar.timestamp.astimezone(_NY).date()
            if bar_day < quote_day or (
                same_day_close_allowed
                and bar_day == quote_day
                and bar.timestamp <= quote_at
            ):
                selected_bar = bar
                break

        recoverable_session = quote_session in {
            TradingSession.PRE_MARKET,
            TradingSession.POST_MARKET,
        }
        equity_like = asset_type in {
            AssetType.EQUITY,
            AssetType.ETF,
            AssetType.INDEX,
        }
        if (
            equity_like
            and recoverable_session
            and regular_market_at is not None
            and regular_market_price is not None
            and regular_market_at <= quote_at
        ):
            regular_day = regular_market_at.astimezone(_NY).date()
            completed_for_quote = (
                quote_session is TradingSession.PRE_MARKET
                and regular_day < quote_day
            ) or (
                quote_session is TradingSession.POST_MARKET
                and regular_day == quote_day
            )
            selected_day = (
                selected_bar.timestamp.astimezone(_NY).date()
                if selected_bar is not None
                else None
            )
            if completed_for_quote and (
                selected_day is None or regular_day > selected_day
            ):
                return regular_market_price, True

        return (selected_bar.close if selected_bar is not None else None), False

    async def _latest_intraday_quote_bar(
        self,
        symbol: str,
        *,
        as_of: datetime,
    ) -> tuple[MarketBar, datetime, Mapping[str, object]]:
        """Return the latest minute bar at/before cutoff, including extended hours."""
        payload, fetched_at = await self._fetch_chart(
            symbol,
            params={
                "period1": str(int((as_of - timedelta(days=2)).timestamp())),
                "period2": str(int(as_of.timestamp()) + 1),
                "interval": "1m",
                "includePrePost": "true",
                "events": "div|split",
            },
            operation="quote_intraday",
        )
        result = self._chart_result(payload, operation="quote_intraday")
        meta = result.get("meta")
        if not isinstance(meta, dict):
            raise _contract(
                "Yahoo Finance intraday chart metadata failed contract validation",
                operation="quote_intraday",
                rule="contract_drift",
            )
        bars = self._parse_ohlcv_arrays(
            result,
            operation="quote_intraday",
            interval=USBarInterval.ONE_MINUTE,
            adjustment=AdjustmentMethod.NONE,
            as_of=as_of,
        )
        if not bars:
            raise NoMarketData(
                "Yahoo Finance returned no intraday quote bars",
                details={"vendor": self.vendor_id.value, "operation": "quote_intraday"},
            )
        return bars[-1], fetched_at, meta

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USQuote]:
        self._require_configured()
        now = self._require_as_of(as_of)
        symbol = self._require_chart_instrument(instrument)

        # Window ends exclusive of the calendar day after as_of (NY), inclusive wire.
        as_of_day = as_of.astimezone(_NY).date()
        period1 = int(_ny_midnight(as_of_day - timedelta(days=30)).timestamp())
        period2 = int(_ny_midnight(as_of_day + timedelta(days=1)).timestamp())
        payload, fetched_at = await self._fetch_chart(
            symbol,
            params={
                "period1": str(period1),
                "period2": str(period2),
                "interval": "1d",
                "includePrePost": "true",
                "events": "div|split",
            },
            operation="quote",
        )
        result = self._chart_result(payload, operation="quote")
        meta_raw = result.get("meta")
        if not isinstance(meta_raw, dict):
            raise _contract(
                "Yahoo Finance chart payload failed contract validation",
                operation="quote",
                rule="contract_drift",
            )

        bars = self._parse_ohlcv_arrays(
            result,
            operation="quote",
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=as_of,
        )

        regular_market_at: datetime | None = None
        rmt = meta_raw.get("regularMarketTime")
        if type(rmt) is int or type(rmt) is Decimal:
            candidate = _unix_to_aware(int(rmt), field="regularMarketTime")
            if candidate <= as_of:
                regular_market_at = candidate

        quote_at = regular_market_at

        last: Decimal | None = None
        open_: Decimal | None = None
        high: Decimal | None = None
        low: Decimal | None = None
        # Chart meta is a current snapshot even for a historical period query.
        # It is only legal when its own timestamp is at/before the requested
        # cutoff; otherwise using volume/52-week fields leaks future information
        # into a historical response.
        volume: Decimal | None = None
        week_low: Decimal | None = None
        week_high: Decimal | None = None

        regular_market_price: Decimal | None = None
        if regular_market_at is not None:
            volume = _optional_decimal(
                meta_raw.get("regularMarketVolume"), field="volume"
            )
            week_low = _optional_decimal(
                meta_raw.get("fiftyTwoWeekLow"), field="week_52_low"
            )
            week_high = _optional_decimal(
                meta_raw.get("fiftyTwoWeekHigh"), field="week_52_high"
            )
            regular_market_price = _optional_decimal(
                meta_raw.get("regularMarketPrice"), field="last"
            )
            last = regular_market_price
            open_ = _optional_decimal(
                meta_raw.get("regularMarketOpen")
                if meta_raw.get("regularMarketOpen") is not None
                else meta_raw.get("open"),
                field="open",
            )
            high = _optional_decimal(
                meta_raw.get("regularMarketDayHigh")
                if meta_raw.get("regularMarketDayHigh") is not None
                else meta_raw.get("dayHigh"),
                field="high",
            )
            low = _optional_decimal(
                meta_raw.get("regularMarketDayLow")
                if meta_raw.get("regularMarketDayLow") is not None
                else meta_raw.get("dayLow"),
                field="low",
            )

        quote_session = TradingSession.REGULAR

        if last is None and bars:
            bar = bars[-1]
            quote_at = bar.timestamp
            last = bar.close
            open_ = bar.open
            high = bar.high
            low = bar.low
            volume = bar.volume if volume is None else volume

        if last is None or quote_at is None:
            raise NoMarketData(
                "Yahoo Finance returned no quote data at or before as_of",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        if quote_at > as_of:
            raise _contract(
                "quote_at must be <= requested as_of cutoff",
                operation="quote",
                rule="as_of_cutoff",
            )

        meta_session = self._session_from_meta(meta_raw, as_of)
        additional_warnings: list[str] = []
        current_cutoff = 0 <= (now - as_of).total_seconds() <= _CURRENT_QUOTE_CUTOFF_SECONDS
        regular_age = (as_of - quote_at).total_seconds()
        if (
            current_cutoff
            and meta_session is not TradingSession.CLOSED
            and regular_age > self._max_delayed_seconds
        ):
            try:
                intraday_bar, intraday_fetched_at, intraday_meta = (
                    await self._latest_intraday_quote_bar(symbol, as_of=as_of)
                )
            except (
                DataContractError,
                NoMarketData,
                ProviderRateLimitError,
                ProviderUnavailableError,
            ):
                additional_warnings.append(_INTRADAY_QUOTE_UNAVAILABLE_WARNING)
            else:
                if intraday_bar.timestamp > quote_at:
                    quote_at = intraday_bar.timestamp
                    last = intraday_bar.close
                    fetched_at = intraday_fetched_at
                    quote_session = self._session_from_meta(intraday_meta, quote_at)
                    meta_session = quote_session
                    additional_warnings.append(
                        _EXTENDED_HOURS_PRICE_WARNING
                        if quote_session
                        in {TradingSession.PRE_MARKET, TradingSession.POST_MARKET}
                        else _INTRADAY_QUOTE_RECOVERY_WARNING
                    )

        previous_close, previous_close_recovered = self._previous_session_close(
            bars,
            quote_at=quote_at,
            quote_session=quote_session,
            asset_type=instrument.asset_type,
            regular_market_at=regular_market_at,
            regular_market_price=regular_market_price,
        )
        if previous_close_recovered:
            additional_warnings.append(_PREVIOUS_CLOSE_RECOVERY_WARNING)
        quote = USQuote(
            instrument_id=instrument.instrument_id,
            quote_at=quote_at,
            session=quote_session,
            last=last,
            open=open_,
            high=high,
            low=low,
            previous_close=previous_close,
            volume=volume,
            average_volume=None,
            market_cap=None,
            beta=None,
            week_52_low=week_low,
            week_52_high=week_high,
        )
        return ProviderSuccess(
            value=quote,
            meta=self._meta(
                instrument_asset_type=instrument.asset_type,
                category=DataCategory.MARKET_QUOTE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=meta_session,
                data_timestamp=quote_at,
                adjustment=None,
                additional_warnings=tuple(additional_warnings),
                instrument_market=instrument.market,
            ),
        )

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> ProviderSuccess[USBarSeries]:
        self._require_configured()
        self._require_as_of(as_of)
        symbol = self._require_chart_instrument(instrument)

        if type(start) is not date or type(end) is not date:
            raise DataContractError(
                "start and end must be date",
                details={"field": "start", "rule": "date_type"},
            )
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(interval, USBarInterval):
            raise DataContractError(
                "interval must be USBarInterval",
                details={"field": "interval", "rule": "type"},
            )
        if not isinstance(adjustment, AdjustmentMethod):
            raise DataContractError(
                "adjustment must be AdjustmentMethod",
                details={"field": "adjustment", "rule": "type"},
            )
        if adjustment is AdjustmentMethod.SPLIT_ADJUSTED:
            # Chart surface does not prove split-only vs split+dividend.
            raise _contract(
                "Yahoo Finance cannot prove split-only adjustment",
                operation="bars",
                rule="unsupported_adjustment",
            )
        if adjustment not in {
            AdjustmentMethod.NONE,
            AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        }:
            raise _contract(
                "adjustment method is not supported by Yahoo Finance OHLCV",
                operation="bars",
                rule="unsupported_adjustment",
            )

        wire_interval = _INTERVAL_WIRE[interval]
        # Upstream period2 is exclusive; wire end remains inclusive.
        period1 = int(_ny_midnight(start).timestamp())
        period2 = int(_ny_midnight(end + timedelta(days=1)).timestamp())
        # Cap period2 by as_of exclusive upper bound so we never solicit future rows.
        as_of_exclusive = int(
            _ny_midnight(as_of.astimezone(_NY).date() + timedelta(days=1)).timestamp()
        )
        if period2 > as_of_exclusive:
            period2 = as_of_exclusive
        if period2 <= period1:
            raise NoMarketData(
                "Yahoo Finance bar window is empty after as_of cutoff",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )

        payload, fetched_at = await self._fetch_chart(
            symbol,
            params={
                "period1": str(period1),
                "period2": str(period2),
                "interval": wire_interval,
                "includePrePost": "true",
                "events": "div|split",
            },
            operation="bars",
        )
        result = self._chart_result(payload, operation="bars")
        bars = self._parse_ohlcv_arrays(
            result,
            operation="bars",
            interval=interval,
            adjustment=adjustment,
            as_of=as_of,
            start=start,
            end=end,
        )
        if not bars:
            raise NoMarketData(
                "Yahoo Finance returned no bars",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )

        if interval is USBarInterval.ONE_DAY:
            expected_day = min(end, as_of.astimezone(_NY).date())
            self._assert_daily_not_stale(
                bars, expected_day=expected_day, operation="bars"
            )

        series = USBarSeries(
            instrument_id=instrument.instrument_id,
            interval=interval,
            adjustment=adjustment,
            start=start,
            end=end,
            bars=tuple(bars),
        )
        # Meta session describes the request/retrieval context, not the session
        # containing the last daily bar. On weekends the last bar is normally a
        # valid Friday close and should be explained as latest-known closed-session
        # data rather than a live-session stale anomaly.
        session = self._session_from_meta(result, fetched_at)
        return ProviderSuccess(
            value=series,
            meta=self._meta(
                instrument_asset_type=instrument.asset_type,
                category=DataCategory.MARKET_OHLCV,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                data_timestamp=bars[-1].timestamp,
                adjustment=adjustment,
                instrument_market=instrument.market,
            ),
        )
