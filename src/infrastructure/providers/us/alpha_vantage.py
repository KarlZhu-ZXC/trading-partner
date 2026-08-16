"""Alpha Vantage HTTP fallback adapter (Phase 1F F2b).

Fixed official endpoint: ``https://www.alphavantage.co/query`` (GET only).
Implements ``USQuoteProvider`` + ``USBarsProvider`` for US equity/ETF/index.
API key is a request param only — never errors, logs, details, or cache keys.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
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
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.freshness import classify_freshness
from domain.market.models import MarketBar
from domain.market.session import infer_session_basic
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USQuote
from infrastructure.providers.common.adapter_guards import require_as_of
from infrastructure.providers.us.alpha_vantage_key_pool import (
    AlphaVantageKeyPool,
    classify_alpha_vantage_notice,
)
from infrastructure.system.clock import SystemClock

_NY = ZoneInfo("America/New_York")
_QUERY_URL = "https://www.alphavantage.co/query"
_ALLOWED_ASSETS = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_JSON_CONTENT = ("application/json", "text/json", "text/plain", "*/*")
_MAX_DAILY_STALE_NATURAL_DAYS = 4
_SESSION_CLOSE = time(16, 0)
_COMPACT_LOOKBACK_DAYS = 100
_SUPPORTED_CATEGORIES = frozenset({DataCategory.MARKET_QUOTE, DataCategory.MARKET_OHLCV})

_GQ_OPEN, _GQ_HIGH, _GQ_LOW = "02. open", "03. high", "04. low"
_GQ_PRICE, _GQ_VOLUME = "05. price", "06. volume"
_GQ_LATEST_DAY, _GQ_PREV_CLOSE = "07. latest trading day", "08. previous close"
_TS_KEY = "Time Series (Daily)"
_BAR_OPEN, _BAR_HIGH, _BAR_LOW = "1. open", "2. high", "3. low"
_BAR_CLOSE, _BAR_ADJ_CLOSE, _BAR_VOLUME = "4. close", "5. adjusted close", "6. volume"


def _contract(message: str, *, operation: str, rule: str, **extra: object) -> DataContractError:
    details: dict[str, object] = {
        "vendor": VendorId.ALPHA_VANTAGE.value,
        "operation": operation,
        "rule": rule,
    }
    details.update(extra)
    return DataContractError(message, details=details)


def _reject_duplicate_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
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
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _as_decimal(value, field=field)


def _ny_session_close(day: date) -> datetime:
    return datetime(
        day.year, day.month, day.day, _SESSION_CLOSE.hour, _SESSION_CLOSE.minute, tzinfo=_NY
    )


def _parse_ymd(value: object, *, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(
            f"{field} must be a YYYY-MM-DD date string",
            details={"field": field, "rule": "date_string"},
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise DataContractError(
            f"{field} must be a YYYY-MM-DD date string",
            details={"field": field, "rule": "date_string"},
        ) from None


def _positive_number(value: object, *, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise DataContractError(
            f"{field} must be a positive number",
            details={"field": field, "rule": "positive"},
        )
    return float(value)


def _nonneg_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataContractError(
            f"{field} must be a nonnegative int",
            details={"field": field, "rule": "nonnegative"},
        )
    return value


class AlphaVantageAdapter:
    """CategoryProvider for US quote + daily OHLCV via Alpha Vantage HTTP only."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_keys: Sequence[str] = (),
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        max_fresh_seconds: int = 30,
        max_delayed_seconds: int = 900,
    ) -> None:
        self._timeout_seconds = _positive_number(timeout_seconds, field="timeout_seconds")
        self._max_fresh_seconds = _nonneg_int(max_fresh_seconds, field="max_fresh_seconds")
        self._max_delayed_seconds = _nonneg_int(max_delayed_seconds, field="max_delayed_seconds")
        if self._max_fresh_seconds > self._max_delayed_seconds:
            raise DataContractError(
                "max_fresh_seconds must be <= max_delayed_seconds",
                details={"field": "max_fresh_seconds", "rule": "fresh_le_delayed"},
            )
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        # Keys are request params only — never copy into details/messages.
        self._key_pool = AlphaVantageKeyPool(api_keys)

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.ALPHA_VANTAGE

    @property
    def provider_name(self) -> str:
        return VendorId.ALPHA_VANTAGE.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category in _SUPPORTED_CATEGORIES

    def is_configured(self) -> bool:
        return self._enabled and self._key_pool.is_configured()

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Alpha Vantage adapter is not configured",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        return require_as_of(as_of=as_of, clock_now=self._clock.now())

    def _require_us_instrument(self, instrument: Instrument) -> str:
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
        if instrument.asset_type not in _ALLOWED_ASSETS:
            raise DataContractError(
                "Alpha Vantage supports equity/ETF/index only",
                details={
                    "field": "instrument",
                    "rule": "asset_type",
                    "asset_type": instrument.asset_type.value,
                },
            )
        symbol = instrument.symbol.strip()
        if not symbol:
            raise DataContractError(
                "instrument symbol must be non-blank",
                details={"field": "symbol", "rule": "non_blank"},
            )
        return symbol

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Alpha Vantage rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Alpha Vantage access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Alpha Vantage HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _raise_for_payload_notices(self, payload: Mapping[str, object], *, operation: str) -> None:
        """Map Note / Information / Error Message without echoing payload or key."""
        err = payload.get("Error Message")
        if isinstance(err, str) and err.strip():
            raise NoMarketData(
                "Alpha Vantage returned an error message",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "api_error_message",
                },
            )
        notice: str | None = None
        for key in ("Note", "Information"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                notice = raw
                break
        if notice is None:
            return
        notice_kind = classify_alpha_vantage_notice(notice)
        if notice_kind == "rate_limit":
            raise ProviderRateLimitError(
                "Alpha Vantage rate limit or entitlement notice",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                },
            )
        if notice_kind == "api_key":
            raise ProviderNotConfigured(
                "Alpha Vantage API key invalid or missing",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "api_key",
                },
            )
        raise ProviderUnavailableError(
            "Alpha Vantage returned an informational notice",
            details={
                "vendor": self.vendor_id.value,
                "operation": operation,
                "error_type": "notice",
            },
        )

    async def _fetch(
        self, *, params: Mapping[str, str], operation: str
    ) -> tuple[dict[str, object], datetime]:
        last_rate_limit: ProviderRateLimitError | None = None
        for key_index, api_key in self._key_pool.ordered_candidates():
            wire = dict(params)
            wire["apikey"] = api_key  # request param only
            response = await self._transport.send(
                HttpRequest(
                    method="GET",
                    url=_QUERY_URL,
                    params=wire,
                    headers={"Accept": "application/json,text/plain,*/*"},
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            fetched_at = self._clock.now()
            require_aware_datetime(fetched_at, field_name="fetched_at")
            try:
                self._raise_for_http_status(response.status_code, operation=operation)
                if not _content_type_ok(response.headers):
                    rule = "content_type"
                    if not response.headers.get("content-type") and not response.headers.get(
                        "Content-Type"
                    ):
                        raise _contract(
                            "Alpha Vantage response missing Content-Type",
                            operation=operation,
                            rule=rule,
                        )
                    raise _contract(
                        "Alpha Vantage response Content-Type is not acceptable",
                        operation=operation,
                        rule=rule,
                    )
                try:
                    payload = _loads_json_decimal(response.body)
                except DataContractError as exc:
                    raise _contract(
                        "Alpha Vantage payload failed contract validation",
                        operation=operation,
                        rule=str(exc.details.get("rule", "json")),
                    ) from None
                if not isinstance(payload, dict):
                    raise _contract(
                        "Alpha Vantage payload failed contract validation",
                        operation=operation,
                        rule="contract_drift",
                    )
                self._raise_for_payload_notices(payload, operation=operation)
            except ProviderRateLimitError as exc:
                last_rate_limit = exc
                continue
            self._key_pool.mark_success(key_index)
            return payload, fetched_at

        assert last_rate_limit is not None
        raise ProviderRateLimitError(
            "Alpha Vantage key pool exhausted by rate limits",
            details={
                "vendor": self.vendor_id.value,
                "operation": operation,
                "error_type": "key_pool_exhausted",
                "attempted_key_count": self._key_pool.size,
            },
        ) from None

    def _meta(
        self,
        *,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        session: TradingSession,
        data_timestamp: datetime | None,
        adjustment: AdjustmentMethod | None,
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
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=SourceRole.FALLBACK,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=adjustment,
            data_delay_seconds=data_delay,
            warnings=(),
        )

    def _assert_daily_not_stale(
        self, bars: Sequence[MarketBar], *, expected_day: date, operation: str
    ) -> None:
        if not bars:
            return
        latest_day = bars[-1].timestamp.astimezone(_NY).date()
        if (expected_day - latest_day).days > _MAX_DAILY_STALE_NATURAL_DAYS:
            raise StaleMarketData(
                "Alpha Vantage latest daily bar is stale",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "stale_daily_bar",
                    "max_natural_days": _MAX_DAILY_STALE_NATURAL_DAYS,
                },
            )

    def _parse_daily_series(
        self,
        payload: Mapping[str, object],
        *,
        operation: str,
        adjustment: AdjustmentMethod,
        as_of: datetime,
        start: date,
        end: date,
    ) -> list[MarketBar]:
        series = payload.get(_TS_KEY)
        if series is None:
            raise NoMarketData(
                "Alpha Vantage returned no daily time series",
                details={"vendor": self.vendor_id.value, "operation": operation},
            )
        if not isinstance(series, dict):
            raise _contract(
                "Alpha Vantage payload failed contract validation",
                operation=operation,
                rule="contract_drift",
            )

        out: list[MarketBar] = []
        for day_key, row in series.items():
            if not isinstance(day_key, str) or not isinstance(row, dict):
                raise _contract(
                    "Alpha Vantage payload failed contract validation",
                    operation=operation,
                    rule="contract_drift",
                )
            day = _parse_ymd(day_key, field="time_series.date")
            if day < start or day > end:
                continue
            ts = _ny_session_close(day)
            if ts > as_of:
                continue

            open_ = _as_decimal(row.get(_BAR_OPEN), field=f"bars[{day_key}].open")
            high = _as_decimal(row.get(_BAR_HIGH), field=f"bars[{day_key}].high")
            low = _as_decimal(row.get(_BAR_LOW), field=f"bars[{day_key}].low")
            close = _as_decimal(row.get(_BAR_CLOSE), field=f"bars[{day_key}].close")
            volume = _as_decimal(row.get(_BAR_VOLUME), field=f"bars[{day_key}].volume")
            if volume < 0:
                raise _contract(
                    "bar volume must be nonnegative",
                    operation=operation,
                    rule="nonnegative",
                )

            if adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED:
                adj_raw = row.get(_BAR_ADJ_CLOSE)
                if adj_raw is None or (isinstance(adj_raw, str) and not adj_raw.strip()):
                    raise _contract(
                        "Alpha Vantage adjusted close required for split+dividend adjustment",
                        operation=operation,
                        rule="adjustment_unavailable",
                    )
                adj = _as_decimal(adj_raw, field=f"bars[{day_key}].adj_close")
                if close == 0:
                    raise _contract(
                        "Alpha Vantage cannot scale bar with zero close",
                        operation=operation,
                        rule="adjustment_unavailable",
                    )
                factor = adj / close
                open_, high, low, close = open_ * factor, high * factor, low * factor, adj
            elif adjustment is not AdjustmentMethod.NONE:
                raise _contract(
                    "unsupported adjustment for Alpha Vantage bars",
                    operation=operation,
                    rule="unsupported_adjustment",
                )

            out.append(
                MarketBar(timestamp=ts, open=open_, high=high, low=low, close=close, volume=volume)
            )
        out.sort(key=lambda b: b.timestamp)
        return out

    async def get_quote(self, instrument: Instrument, as_of: datetime) -> ProviderSuccess[USQuote]:
        self._require_configured()
        self._require_as_of(as_of)
        symbol = self._require_us_instrument(instrument)

        payload, fetched_at = await self._fetch(
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "datatype": "json"},
            operation="quote",
        )
        gq = payload.get("Global Quote")
        if not isinstance(gq, dict) or not gq or not gq.get(_GQ_PRICE):
            if gq is not None and not isinstance(gq, dict):
                raise _contract(
                    "Alpha Vantage payload failed contract validation",
                    operation="quote",
                    rule="contract_drift",
                )
            raise NoMarketData(
                "Alpha Vantage returned no global quote",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )

        # No reliable intraday timestamp/session — use NY session close on the
        # latest trading day only; never claim realtime; enforce as_of cutoff.
        latest_day = _parse_ymd(gq.get(_GQ_LATEST_DAY), field="latest_trading_day")
        quote_at = _ny_session_close(latest_day)
        if quote_at > as_of:
            raise NoMarketData(
                "Alpha Vantage global quote is after as_of cutoff",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )

        last = _as_decimal(gq.get(_GQ_PRICE), field="last")
        volume = _optional_decimal(gq.get(_GQ_VOLUME), field="volume")
        if volume is not None and volume < 0:
            raise _contract(
                "quote volume must be nonnegative", operation="quote", rule="nonnegative"
            )

        session = infer_session_basic(Market.US, quote_at, timezone="America/New_York")
        quote = USQuote(
            instrument_id=instrument.instrument_id,
            quote_at=quote_at,
            session=session,
            last=last,
            open=_optional_decimal(gq.get(_GQ_OPEN), field="open"),
            high=_optional_decimal(gq.get(_GQ_HIGH), field="high"),
            low=_optional_decimal(gq.get(_GQ_LOW), field="low"),
            previous_close=_optional_decimal(gq.get(_GQ_PREV_CLOSE), field="previous_close"),
            volume=volume,
            average_volume=None,
            market_cap=None,
            beta=None,
            week_52_low=None,
            week_52_high=None,
        )
        return ProviderSuccess(
            value=quote,
            meta=self._meta(
                category=DataCategory.MARKET_QUOTE,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                data_timestamp=quote_at,
                adjustment=None,
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
        symbol = self._require_us_instrument(instrument)

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
        if interval is not USBarInterval.ONE_DAY:
            raise _contract(
                "Alpha Vantage bars support daily interval only",
                operation="bars",
                rule="unsupported_interval",
            )
        if not isinstance(adjustment, AdjustmentMethod):
            raise DataContractError(
                "adjustment must be AdjustmentMethod",
                details={"field": "adjustment", "rule": "type"},
            )
        if adjustment is AdjustmentMethod.SPLIT_ADJUSTED:
            raise _contract(
                "Alpha Vantage cannot prove split-only adjustment",
                operation="bars",
                rule="unsupported_adjustment",
            )
        if adjustment not in {
            AdjustmentMethod.NONE,
            AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        }:
            raise _contract(
                "adjustment method is not supported by Alpha Vantage OHLCV",
                operation="bars",
                rule="unsupported_adjustment",
            )

        today = self._clock.now().astimezone(_NY).date()
        outputsize = "compact" if (today - start).days < _COMPACT_LOOKBACK_DAYS else "full"
        payload, fetched_at = await self._fetch(
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": outputsize,
                "datatype": "json",
            },
            operation="bars",
        )
        bars = self._parse_daily_series(
            payload,
            operation="bars",
            adjustment=adjustment,
            as_of=as_of,
            start=start,
            end=end,
        )
        if not bars:
            raise NoMarketData(
                "Alpha Vantage returned no bars",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )
        self._assert_daily_not_stale(
            bars,
            expected_day=min(end, as_of.astimezone(_NY).date()),
            operation="bars",
        )
        series = USBarSeries(
            instrument_id=instrument.instrument_id,
            interval=interval,
            adjustment=adjustment,
            start=start,
            end=end,
            bars=tuple(bars),
        )
        session = infer_session_basic(Market.US, bars[-1].timestamp, timezone="America/New_York")
        return ProviderSuccess(
            value=series,
            meta=self._meta(
                category=DataCategory.MARKET_OHLCV,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                data_timestamp=bars[-1].timestamp,
                adjustment=adjustment,
            ),
        )
