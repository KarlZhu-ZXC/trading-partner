"""Timestamped best-effort quotes for selected COMEX metal futures via Sina.

The public Sina wire is current-only and has no contractual delay SLA.  It is
therefore a quote fallback, never a historical source and never evidence that a
continuous future is an OTC spot metal price.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
from domain.us_market.models import USQuote
from infrastructure.system.clock import SystemClock

_HOST = "https://hq.sinajs.cn/list"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOLS = {
    "GC=F": "GC",
    "SI=F": "SI",
    "HG=F": "HG",
}
_WARNINGS = (
    "FUTURES_CONTRACT_NOT_SPOT",
    "CONTINUOUS_FUTURES_ROLL_RISK",
    "BEST_EFFORT_PUBLIC_FEED_NO_SLA",
    "FUTURES_SESSION_UNKNOWN",
)


def _contract(message: str, *, rule: str) -> DataContractError:
    return DataContractError(
        message,
        details={
            "vendor": VendorId.SINA_FUTURES.value,
            "operation": "quote",
            "rule": rule,
        },
    )


def _decimal(value: str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, ValueError):
        raise _contract("Sina futures quote contains an invalid decimal", rule=field) from None
    if not parsed.is_finite() or parsed < 0:
        raise _contract("Sina futures quote contains an invalid decimal", rule=field)
    return parsed


def _optional_decimal(value: str, *, field: str) -> Decimal | None:
    if not value.strip():
        return None
    return _decimal(value, field=field)


class SinaMetalFuturesAdapter:
    """Serve GC/SI/HG continuous-futures quotes from the Sina public feed."""

    def __init__(
        self,
        transport: HttpTransport,
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
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SINA_FUTURES

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.MARKET_QUOTE

    def is_configured(self) -> bool:
        return self._enabled

    def _require_instrument(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.US or instrument.asset_type is not AssetType.FUTURE:
            raise NoMarketData(
                "Sina metal-futures quote does not cover this instrument",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        code = _SYMBOLS.get(instrument.symbol)
        if code is None:
            raise NoMarketData(
                "Sina metal-futures quote does not cover this symbol",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        return code

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Sina futures quote was rate limited",
                details={"vendor": VendorId.SINA_FUTURES.value, "operation": "quote"},
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Sina futures quote HTTP failure",
                details={
                    "vendor": VendorId.SINA_FUTURES.value,
                    "operation": "quote",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USQuote]:
        if not self._enabled:
            raise ProviderNotConfigured(
                "Sina futures adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )
        require_aware_datetime(as_of, field_name="as_of")
        code = self._require_instrument(instrument)
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_HOST,
                params={"list": f"hf_{code}"},
                headers={
                    "Accept": "*/*",
                    "Referer": "https://finance.sina.com.cn/",
                    "User-Agent": self._user_agent,
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_status(response.status_code)
        try:
            text = response.body.decode("gb18030")
        except UnicodeDecodeError:
            raise _contract("Sina futures quote has invalid encoding", rule="encoding") from None

        pattern = rf'\s*var\s+hq_str_hf_{code}="([^"]*)";?\s*'
        match = re.fullmatch(pattern, text)
        if match is None:
            raise _contract("Sina futures quote payload changed shape", rule="wire_shape")
        payload = match.group(1)
        if not payload:
            raise NoMarketData(
                "Sina returned no metal-futures quote",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )
        fields = payload.split(",")
        if len(fields) != 15:
            raise _contract("Sina futures quote payload changed shape", rule="field_count")

        try:
            quote_at = datetime.strptime(
                f"{fields[12].strip()} {fields[6].strip()}", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=_SHANGHAI)
        except ValueError:
            raise _contract("Sina futures quote timestamp is invalid", rule="timestamp") from None
        if quote_at > as_of:
            raise NoMarketData(
                "Sina current quote is after the requested cutoff",
                details={"vendor": self.vendor_id.value, "operation": "quote"},
            )

        last = _decimal(fields[0], field="last")
        high = _optional_decimal(fields[4], field="high")
        low = _optional_decimal(fields[5], field="low")
        open_ = _optional_decimal(fields[8], field="open")
        if high is not None and low is not None and high < low:
            raise _contract("Sina futures quote OHLC is invalid", rule="ohlc")
        if (
            open_ is not None
            and high is not None
            and low is not None
            and (high < max(open_, last, low) or low > min(open_, last, high))
        ):
            raise _contract("Sina futures quote OHLC is invalid", rule="ohlc")

        delay = max(0, int((fetched_at - quote_at).total_seconds()))
        quote = USQuote(
            instrument_id=instrument.instrument_id,
            quote_at=quote_at,
            session=TradingSession.UNKNOWN,
            last=last,
            open=open_,
            high=high,
            low=low,
            # Wire field 7 is previous settlement, not previous close.
            previous_close=None,
            volume=None,
            average_volume=None,
            market_cap=None,
            beta=None,
            week_52_low=None,
            week_52_high=None,
        )
        return ProviderSuccess(
            value=quote,
            meta=ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.MARKET_QUOTE,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=None,
                data_delay_seconds=delay,
                warnings=_WARNINGS,
            ),
        )
