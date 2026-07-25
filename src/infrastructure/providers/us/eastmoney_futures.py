"""Daily-derived metal-futures bars from Eastmoney's global-futures surface."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
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
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries
from infrastructure.providers.a_share.eastmoney_gate import EastmoneyRequestGate
from infrastructure.system.clock import SystemClock

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_NY = ZoneInfo("America/New_York")
_SYMBOLS: dict[str, tuple[int, str]] = {
    "GC=F": (101, "GC00Y"),
    "MGC=F": (101, "MGC00Y"),
    "SI=F": (101, "SI00Y"),
    "HG=F": (101, "HG00Y"),
    "PL=F": (102, "PL00Y"),
    "PA=F": (102, "PA00Y"),
}
_SUPPORTED_INTERVALS = frozenset(
    {
        USBarInterval.ONE_DAY,
        USBarInterval.ONE_WEEK,
        USBarInterval.ONE_MONTH,
    }
)
_WARNINGS = (
    "FUTURES_CONTRACT_NOT_SPOT",
    "CONTINUOUS_FUTURES_ROLL_RISK",
    "BEST_EFFORT_PUBLIC_FEED_NO_SLA",
    "EASTMONEY_DAILY_DERIVED_BARS",
)


def _contract(message: str, *, rule: str, field: str | None = None) -> DataContractError:
    details: dict[str, object] = {
        "vendor": VendorId.EASTMONEY_FUTURES.value,
        "operation": "bars",
        "rule": rule,
    }
    if field is not None:
        details["field"] = field
    return DataContractError(message, details=details)


def _reject_constant(name: str) -> None:
    del name
    raise _contract("Eastmoney futures payload contains a non-finite number", rule="json")


def _loads(body: bytes) -> object:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise _contract("Eastmoney futures payload has invalid encoding", rule="encoding") from None
    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except DataContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise _contract("Eastmoney futures payload is not valid JSON", rule="json") from None


def _decimal(text: str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        raise _contract(
            "Eastmoney futures bar contains an invalid decimal",
            rule="decimal",
            field=field,
        ) from None
    if not parsed.is_finite():
        raise _contract(
            "Eastmoney futures bar contains an invalid decimal",
            rule="decimal",
            field=field,
        )
    return parsed


def _daily_timestamp(day: date) -> datetime:
    # The provider supplies only a trade date. Anchor it to the standard CME
    # Globex metal-futures trade-date boundary (17:00 New York) so an in-progress
    # current trade date is excluded before session end. The warning contract
    # explicitly discloses that this timestamp is derived, not provider-supplied.
    return datetime.combine(day, time(17, 0), tzinfo=_NY)


def _aggregate(bars: Iterable[MarketBar], interval: USBarInterval) -> tuple[MarketBar, ...]:
    values = tuple(bars)
    if interval is USBarInterval.ONE_DAY:
        return values

    grouped: dict[tuple[int, int], list[MarketBar]] = {}
    for bar in values:
        local_day = bar.timestamp.astimezone(_NY).date()
        if interval is USBarInterval.ONE_WEEK:
            iso = local_day.isocalendar()
            key = (iso.year, iso.week)
        else:
            key = (local_day.year, local_day.month)
        grouped.setdefault(key, []).append(bar)

    out: list[MarketBar] = []
    for rows in grouped.values():
        out.append(
            MarketBar(
                timestamp=rows[-1].timestamp,
                open=rows[0].open,
                high=max(item.high for item in rows),
                low=min(item.low for item in rows),
                close=rows[-1].close,
                volume=sum((item.volume for item in rows), start=Decimal(0)),
            )
        )
    return tuple(out)


class EastmoneyMetalFuturesAdapter:
    """Serve daily/weekly/monthly bars for the six seeded metal futures."""

    def __init__(
        self,
        transport: HttpTransport,
        gate: EastmoneyRequestGate,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
    ) -> None:
        if not isinstance(timeout_seconds, int | float) or isinstance(
            timeout_seconds, bool
        ) or timeout_seconds <= 0:
            raise DataContractError(
                "timeout_seconds must be positive",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        self._transport = transport
        self._gate = gate
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.EASTMONEY_FUTURES

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.MARKET_OHLCV

    def is_configured(self) -> bool:
        return self._enabled

    async def _send(self, request: HttpRequest) -> HttpResponse:
        async def _operation() -> HttpResponse:
            return await self._transport.send(request)

        return await self._gate.run(_operation)

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Eastmoney futures bars were rate limited",
                details={"vendor": VendorId.EASTMONEY_FUTURES.value, "operation": "bars"},
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Eastmoney futures bars HTTP failure",
                details={
                    "vendor": VendorId.EASTMONEY_FUTURES.value,
                    "operation": "bars",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    @staticmethod
    def _require_instrument(instrument: Instrument) -> tuple[int, str]:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.US or instrument.asset_type is not AssetType.FUTURE:
            raise NoMarketData(
                "Eastmoney metal-futures bars do not cover this instrument",
                details={"vendor": VendorId.EASTMONEY_FUTURES.value, "operation": "bars"},
            )
        mapping = _SYMBOLS.get(instrument.symbol)
        if mapping is None:
            raise NoMarketData(
                "Eastmoney metal-futures bars do not cover this symbol",
                details={"vendor": VendorId.EASTMONEY_FUTURES.value, "operation": "bars"},
            )
        return mapping

    @staticmethod
    def _parse_daily_bars(
        payload: object,
        *,
        expected_code: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> tuple[MarketBar, ...]:
        if not isinstance(payload, dict):
            raise _contract("Eastmoney futures payload changed shape", rule="wire_shape")
        data = payload.get("data")
        if data is None:
            return ()
        if not isinstance(data, dict) or data.get("code") != expected_code:
            raise _contract("Eastmoney futures payload identity mismatch", rule="identity")
        rows = data.get("klines")
        if not isinstance(rows, list):
            raise _contract("Eastmoney futures payload changed shape", rule="wire_shape")

        out: list[MarketBar] = []
        seen: set[date] = set()
        for index, raw in enumerate(rows):
            if not isinstance(raw, str):
                raise _contract(
                    "Eastmoney futures kline row must be a string",
                    rule="row_type",
                    field=f"klines[{index}]",
                )
            fields = raw.split(",")
            if len(fields) < 6:
                raise _contract(
                    "Eastmoney futures kline row changed shape",
                    rule="field_count",
                    field=f"klines[{index}]",
                )
            try:
                day = date.fromisoformat(fields[0])
            except ValueError:
                raise _contract(
                    "Eastmoney futures kline date is invalid",
                    rule="date",
                    field=f"klines[{index}]",
                ) from None
            timestamp = _daily_timestamp(day)
            if day < start or day > end or timestamp > as_of:
                continue
            if day in seen:
                raise _contract("Eastmoney futures kline dates must be unique", rule="unique")
            seen.add(day)
            volume = _decimal(fields[5], field=f"klines[{index}].volume")
            if volume < 0:
                raise _contract(
                    "Eastmoney futures volume must be nonnegative",
                    rule="nonnegative",
                    field=f"klines[{index}].volume",
                )
            out.append(
                MarketBar(
                    timestamp=timestamp,
                    open=_decimal(fields[1], field=f"klines[{index}].open"),
                    close=_decimal(fields[2], field=f"klines[{index}].close"),
                    high=_decimal(fields[3], field=f"klines[{index}].high"),
                    low=_decimal(fields[4], field=f"klines[{index}].low"),
                    volume=volume,
                )
            )
        out.sort(key=lambda item: item.timestamp)
        return tuple(out)

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
        if not self._enabled:
            raise ProviderNotConfigured(
                "Eastmoney futures adapter is disabled",
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
        if type(start) is not date or type(end) is not date or end < start:
            raise DataContractError(
                "start/end must be ordered date values",
                details={"field": "start", "rule": "date_range"},
            )
        if not isinstance(interval, USBarInterval):
            raise DataContractError(
                "interval must be USBarInterval",
                details={"field": "interval", "rule": "type"},
            )
        if interval not in _SUPPORTED_INTERVALS:
            raise NoMarketData(
                "Eastmoney fallback exposes no verified intraday OHLCV",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )
        if adjustment is not AdjustmentMethod.NONE:
            raise DataContractError(
                "metal futures bars require adjustment=none",
                details={"field": "adjustment", "rule": "futures_unadjusted_only"},
            )
        market_code, provider_code = self._require_instrument(instrument)
        effective_end = min(end, as_of.astimezone(_NY).date())
        if effective_end < start:
            raise NoMarketData(
                "Eastmoney futures bar window is empty after cutoff",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )

        response = await self._send(
            HttpRequest(
                method="GET",
                url=_KLINE_URL,
                params={
                    "secid": f"{market_code}.{provider_code}",
                    "klt": "101",
                    # Eastmoney requires this field; futures values remain raw.
                    "fqt": "1",
                    "beg": start.strftime("%Y%m%d"),
                    "end": effective_end.strftime("%Y%m%d"),
                    "lmt": "6600",
                    "iscca": "1",
                    "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64",
                    "ut": "f057cbcbce2a86e2866ab8877db1d059",
                    "forcect": "1",
                },
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "Referer": "https://quote.eastmoney.com/",
                    "User-Agent": self._user_agent,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_status(response.status_code)
        daily = self._parse_daily_bars(
            _loads(response.body),
            expected_code=provider_code,
            start=start,
            end=effective_end,
            as_of=as_of,
        )
        if not daily:
            raise NoMarketData(
                "Eastmoney returned no metal-futures bars",
                details={"vendor": self.vendor_id.value, "operation": "bars"},
            )
        bars = _aggregate(daily, interval)
        latest = bars[-1].timestamp
        delay = max(0, int((fetched_at - latest).total_seconds()))
        series = USBarSeries(
            instrument_id=instrument.instrument_id,
            interval=interval,
            adjustment=AdjustmentMethod.NONE,
            start=start,
            end=end,
            bars=bars,
        )
        return ProviderSuccess(
            value=series,
            meta=ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.MARKET_OHLCV,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=AdjustmentMethod.NONE,
                data_delay_seconds=delay,
                warnings=_WARNINGS,
            ),
        )
