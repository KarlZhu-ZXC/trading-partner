"""Current-only Moomoo OpenD US overnight quote adapter.

The adapter reads OpenD's dedicated ``overnight_*`` snapshot fields only during
the US 20:00-04:00 ET venue window.  It never relabels the ordinary ``last_price``
or a pre/post-market field as an overnight observation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from domain.common.enums import (
    AssetType,
    CacheDisposition,
    DataCategory,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.freshness import classify_freshness
from domain.market.session import is_us_overnight_session
from domain.us_market.models import USCommunityHeatSnapshot, USQuote
from infrastructure.providers.moomoo_rate_limiter import (
    MoomooOpenDOperation,
    OpenDRequestLimiter,
)
from infrastructure.system.clock import SystemClock

from .moomoo_community import MoomooCommunityHeatAdapter

_NY = ZoneInfo("America/New_York")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,31}$")
_OVERNIGHT_START = time(20, 0)
_OVERNIGHT_END = time(4, 0)
_WARNINGS = (
    "MOOMOO_OVERNIGHT_PRICE",
    "MOOMOO_OVERNIGHT_OBSERVED_AT_SNAPSHOT_TIME",
    "MOOMOO_OVERNIGHT_LIQUIDITY_RISK",
    "MOOMOO_OVERNIGHT_VENUE_UNDISCLOSED",
)


class _SnapshotContext(Protocol):
    def get_market_state(self, codes: list[str]) -> tuple[bool, object]: ...

    def get_market_snapshot(self, codes: list[str]) -> tuple[bool, object]: ...

    def close(self) -> object: ...


ContextFactory = Callable[[str, int], _SnapshotContext]


class _SdkSnapshotContext:
    def __init__(self, context: Any, ret_ok: object) -> None:
        self._context = context
        self._ret_ok = ret_ok

    def get_market_snapshot(self, codes: list[str]) -> tuple[bool, object]:
        ret, value = self._context.get_market_snapshot(codes)
        return ret == self._ret_ok, value

    def get_market_state(self, codes: list[str]) -> tuple[bool, object]:
        ret, value = self._context.get_market_state(codes)
        return ret == self._ret_ok, value

    def close(self) -> object:
        return self._context.close()


def _default_context_factory(host: str, port: int) -> _SnapshotContext:
    try:
        import moomoo
    except ImportError as exc:
        raise ProviderNotConfigured("Moomoo SDK is unavailable") from exc
    moomoo.SysConfig.enable_console_log(False)
    return _SdkSnapshotContext(
        moomoo.OpenQuoteContext(host=host, port=port),
        moomoo.RET_OK,
    )


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _records(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return list(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        rows = to_dict(orient="records")
        if isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows):
            return list(rows)
    raise DataContractError(
        "Moomoo overnight snapshot payload is invalid",
        details={"vendor": VendorId.MOOMOO.value, "operation": "overnight_quote"},
    )


def _decimal(value: object, *, field: str, positive: bool = False) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        return None
    return parsed


def _overnight_session_date(value: datetime) -> date | None:
    local = value.astimezone(_NY)
    weekday = local.weekday()
    local_time = local.time().replace(tzinfo=None)
    if local_time >= _OVERNIGHT_START and weekday in {0, 1, 2, 3, 6}:
        return local.date() + timedelta(days=1)
    if local_time < _OVERNIGHT_END and weekday in {0, 1, 2, 3, 4}:
        return local.date()
    return None


def _snapshot_time(value: object, *, as_of: datetime) -> datetime:
    if not isinstance(value, str):
        raise NoMarketData(
            "Moomoo overnight snapshot omitted update_time",
            details={"vendor": VendorId.MOOMOO.value, "operation": "overnight_quote"},
        )
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        raise DataContractError(
            "Moomoo overnight update_time is invalid",
            details={"vendor": VendorId.MOOMOO.value, "operation": "overnight_quote"},
        ) from None
    candidates: tuple[datetime, ...]
    if naive.tzinfo is not None:
        candidates = (naive.astimezone(_NY),)
    else:
        candidates = tuple(
            {
                naive.replace(tzinfo=_NY, fold=0),
                naive.replace(tzinfo=_NY, fold=1),
            }
        )
    eligible = [candidate for candidate in candidates if candidate <= as_of]
    if not eligible:
        raise NoMarketData(
            "Moomoo overnight snapshot is newer than the requested cutoff",
            details={"vendor": VendorId.MOOMOO.value, "operation": "overnight_quote"},
        )
    return max(eligible)


class MoomooOvernightQuoteAdapter:
    """Serve current US equity/ETF overnight prices from dedicated OpenD fields."""

    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        port: int,
        clock: Clock | None = None,
        current_window_seconds: int = 300,
        max_fresh_seconds: int = 30,
        max_delayed_seconds: int = 900,
        context_factory: ContextFactory | None = None,
        opend_rate_limiter: OpenDRequestLimiter | None = None,
    ) -> None:
        if not _is_loopback(host):
            raise DataContractError("Moomoo overnight host must be loopback")
        if type(port) is not int or not 1 <= port <= 65535:
            raise DataContractError("Moomoo overnight port is invalid")
        if min(current_window_seconds, max_fresh_seconds, max_delayed_seconds) < 0:
            raise DataContractError("Moomoo overnight freshness limits must be nonnegative")
        if max_fresh_seconds > max_delayed_seconds:
            raise DataContractError("max_fresh_seconds must be <= max_delayed_seconds")
        self._enabled = bool(enabled)
        self._host = host
        self._port = port
        self._clock = clock or SystemClock()
        self._current_window_seconds = current_window_seconds
        self._max_fresh_seconds = max_fresh_seconds
        self._max_delayed_seconds = max_delayed_seconds
        self._context_factory = context_factory or _default_context_factory
        self._opend_rate_limiter = opend_rate_limiter

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.MOOMOO

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.MARKET_QUOTE

    def is_configured(self) -> bool:
        return self._enabled

    async def get_quote(self, instrument: Instrument, as_of: datetime) -> ProviderSuccess[USQuote]:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo overnight quote provider is disabled")
        if instrument.market is not Market.US or instrument.asset_type not in {
            AssetType.EQUITY,
            AssetType.ETF,
        }:
            raise NoMarketData(
                "Moomoo overnight quote supports US equities and ETFs only",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        if as_of > now or (now - as_of).total_seconds() > self._current_window_seconds:
            raise NoMarketData(
                "Moomoo overnight quote is current-only",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        if not is_us_overnight_session(as_of):
            raise NoMarketData(
                "The requested cutoff is outside the US overnight session",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        if not _SYMBOL.fullmatch(instrument.symbol):
            raise NoMarketData(
                "Instrument has no supported Moomoo US symbol",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        return await asyncio.to_thread(self._read, instrument, as_of)

    def _read(self, instrument: Instrument, as_of: datetime) -> ProviderSuccess[USQuote]:
        try:
            context = self._context_factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError(
                "Moomoo OpenD overnight context creation failed",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            ) from None
        try:
            if self._opend_rate_limiter is not None:
                self._opend_rate_limiter.wait(MoomooOpenDOperation.MARKET_STATE)
            state_ok, state_payload = context.get_market_state([f"US.{instrument.symbol}"])
            if not state_ok:
                raise ProviderUnavailableError(
                    "Moomoo overnight market-state request failed",
                    details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
                )
            state_rows = _records(state_payload)
            state = state_rows[0].get("market_state") if len(state_rows) == 1 else None
            if str(state).upper() != "OVERNIGHT":
                raise NoMarketData(
                    "Moomoo does not report this instrument in the overnight session",
                    details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
                )
            if self._opend_rate_limiter is not None:
                self._opend_rate_limiter.wait(MoomooOpenDOperation.MARKET_SNAPSHOT)
            ok, payload = context.get_market_snapshot([f"US.{instrument.symbol}"])
        finally:
            context.close()
        if not ok:
            raise ProviderUnavailableError(
                "Moomoo overnight snapshot request failed",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        rows = _records(payload)
        if len(rows) != 1:
            raise NoMarketData(
                "Moomoo returned no unique overnight snapshot",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        row = rows[0]
        quote_at = _snapshot_time(row.get("update_time"), as_of=as_of)
        if _overnight_session_date(quote_at) != _overnight_session_date(as_of):
            raise NoMarketData(
                "Moomoo overnight snapshot does not belong to the current session",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        last = _decimal(row.get("overnight_price"), field="overnight_price", positive=True)
        if last is None:
            raise NoMarketData(
                "Moomoo returned no verified overnight price for this instrument",
                details={"vendor": self.vendor_id.value, "operation": "overnight_quote"},
            )
        high = _decimal(row.get("overnight_high_price"), field="overnight_high_price")
        low = _decimal(row.get("overnight_low_price"), field="overnight_low_price")
        if (high is not None and high < last) or (low is not None and low > last):
            raise DataContractError(
                "Moomoo overnight price is outside its session range",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "overnight_quote",
                    "rule": "last_within_range",
                },
            )
        fetched_at = self._clock.now()
        freshness = classify_freshness(
            now=fetched_at,
            data_timestamp=quote_at,
            session=TradingSession.OVERNIGHT,
            max_fresh_seconds=self._max_fresh_seconds,
            max_delayed_seconds=self._max_delayed_seconds,
            vendor_declared_delay_seconds=None,
        )
        delay = max(0, int((fetched_at - quote_at).total_seconds()))
        return ProviderSuccess(
            USQuote(
                instrument_id=instrument.instrument_id,
                quote_at=quote_at,
                session=TradingSession.OVERNIGHT,
                last=last,
                open=None,
                high=high,
                low=low,
                previous_close=_decimal(
                    row.get("prev_close_price"), field="prev_close_price", positive=True
                ),
                volume=_decimal(row.get("overnight_volume"), field="overnight_volume"),
                average_volume=None,
                market_cap=None,
                beta=None,
                week_52_low=None,
                week_52_high=None,
            ),
            ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.MARKET_QUOTE,
                role=SourceRole.PRIMARY,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=freshness,
                session=TradingSession.OVERNIGHT,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=None,
                data_delay_seconds=delay,
                warnings=_WARNINGS,
            ),
        )


class MoomooOpenDMarketAdapter:
    """One registry identity exposing distinct local OpenD market categories."""

    def __init__(
        self,
        *,
        community: MoomooCommunityHeatAdapter,
        overnight: MoomooOvernightQuoteAdapter,
    ) -> None:
        self._community = community
        self._overnight = overnight

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.MOOMOO

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return self._community.supports(market, category) or self._overnight.supports(
            market, category
        )

    def is_configured(self) -> bool:
        return self._community.is_configured() or self._overnight.is_configured()

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USQuote]:
        return await self._overnight.get_quote(instrument, as_of)

    async def get_community_heat(
        self, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[USCommunityHeatSnapshot]:
        return await self._community.get_community_heat(limit=limit, as_of=as_of)
