"""Provider-backed instrument discovery for US and A-share symbols."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime

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
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument
from domain.instruments.normalize import normalize_symbol_input

_YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q"
_TENCENT_LINE_RE = re.compile(r'^v_(?P<symbol>[a-z]{2}\d{6})="(?P<body>.*)";?\s*$', re.M)


def _loads(body: bytes, *, vendor: VendorId) -> Mapping[str, object]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        raise DataContractError(
            "instrument directory returned invalid JSON",
            details={"vendor": vendor.value, "operation": "instrument_lookup"},
        ) from None
    if not isinstance(value, Mapping):
        raise DataContractError(
            "instrument directory returned invalid payload shape",
            details={"vendor": vendor.value, "operation": "instrument_lookup"},
        )
    return value


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _raise_http(status: int, *, vendor: VendorId) -> None:
    details = {"vendor": vendor.value, "operation": "instrument_lookup", "status": status}
    if status in {401, 403}:
        raise ProviderAuthenticationError(
            "instrument directory rejected credentials", details=details
        )
    if status == 429:
        raise ProviderRateLimitError("instrument directory rate limited", details=details)
    if status < 200 or status >= 300:
        raise ProviderUnavailableError("instrument directory request failed", details=details)


def _meta(vendor: VendorId, *, as_of: datetime, fetched_at: datetime) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=vendor,
        category=DataCategory.INSTRUMENT_MASTER,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=fetched_at,
        freshness=Freshness.FRESH,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.BYPASS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )


def _us_asset_type(raw: str | None) -> AssetType | None:
    if raw is None:
        return None
    normalized = raw.upper().replace(" ", "_")
    return {
        "EQUITY": AssetType.EQUITY,
        "COMMON_STOCK": AssetType.EQUITY,
        "ETF": AssetType.ETF,
        "EXCHANGE_TRADED_FUND": AssetType.ETF,
        "INDEX": AssetType.INDEX,
        "OPTION": AssetType.OPTION,
    }.get(normalized)


def _us_exchange(raw: str | None) -> str:
    if raw is None:
        return "UNKNOWN"
    upper = raw.upper()
    return {
        "NMS": "NASDAQ",
        "NGM": "NASDAQ",
        "NCM": "NASDAQ",
        "NAS": "NASDAQ",
        "NYQ": "NYSE",
        "PCX": "ARCA",
        "ASE": "AMEX",
    }.get(upper, upper)


class YahooInstrumentDirectoryAdapter:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.YFINANCE

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.INSTRUMENT_MASTER

    def is_configured(self) -> bool:
        return self._enabled

    async def lookup(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Instrument, ...]]:
        if not self.is_configured():
            raise ProviderNotConfigured("Yahoo instrument directory is disabled")
        require_aware_datetime(as_of, field_name="as_of")
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_YAHOO_SEARCH_URL,
                params={
                    "q": query,
                    "quotesCount": "10",
                    "newsCount": "0",
                    "enableFuzzyQuery": "false",
                },
                headers={"Accept": "application/json", "User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        _raise_http(response.status_code, vendor=self.vendor_id)
        payload = _loads(response.body, vendor=self.vendor_id)
        raw_quotes = payload.get("quotes")
        if not isinstance(raw_quotes, list):
            raise DataContractError(
                "Yahoo instrument search omitted quotes",
                details={"vendor": self.vendor_id.value, "operation": "instrument_lookup"},
            )
        candidates: dict[str, Instrument] = {}
        for item in raw_quotes:
            if not isinstance(item, Mapping):
                continue
            symbol = _text(item.get("symbol"))
            asset_type = _us_asset_type(_text(item.get("quoteType")))
            if symbol is None or asset_type is None:
                continue
            if asset_type_hint is not None and asset_type is not asset_type_hint:
                continue
            symbol = symbol.upper()
            name = _text(item.get("longname")) or _text(item.get("shortname")) or symbol
            exchange = _us_exchange(_text(item.get("exchange")) or _text(item.get("exchDisp")))
            timezone = _text(item.get("exchangeTimezoneName")) or "America/New_York"
            instrument = Instrument(
                instrument_id=build_instrument_id(asset_type, Market.US, symbol),
                symbol=symbol,
                name=name,
                market=Market.US,
                exchange=exchange,
                currency=_text(item.get("currency")) or "USD",
                timezone=timezone,
                asset_type=asset_type,
                country="US",
            )
            candidates[instrument.instrument_id] = instrument
        if not candidates:
            raise NoMarketData("Yahoo instrument search returned no candidates")
        fetched_at = self._clock.now()
        return ProviderSuccess(
            value=tuple(candidates[key] for key in sorted(candidates)),
            meta=_meta(self.vendor_id, as_of=as_of, fetched_at=fetched_at),
        )


class AlphaVantageInstrumentDirectoryAdapter:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_key: str | None,
        clock: Clock,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._clock = clock
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.ALPHA_VANTAGE

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.INSTRUMENT_MASTER

    def is_configured(self) -> bool:
        return self._enabled and self._api_key is not None

    async def lookup(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Instrument, ...]]:
        if not self.is_configured():
            raise ProviderNotConfigured("Alpha Vantage instrument directory is not configured")
        require_aware_datetime(as_of, field_name="as_of")
        assert self._api_key is not None
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_ALPHA_VANTAGE_URL,
                params={"function": "SYMBOL_SEARCH", "keywords": query, "apikey": self._api_key},
                headers={"Accept": "application/json", "User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        _raise_http(response.status_code, vendor=self.vendor_id)
        payload = _loads(response.body, vendor=self.vendor_id)
        note = _text(payload.get("Note")) or _text(payload.get("Information"))
        if note is not None:
            raise ProviderRateLimitError(
                "Alpha Vantage instrument directory is temporarily unavailable",
                details={"vendor": self.vendor_id.value, "operation": "instrument_lookup"},
            )
        raw_matches = payload.get("bestMatches")
        if not isinstance(raw_matches, list):
            raise DataContractError(
                "Alpha Vantage instrument search omitted bestMatches",
                details={"vendor": self.vendor_id.value, "operation": "instrument_lookup"},
            )
        candidates: dict[str, Instrument] = {}
        for item in raw_matches:
            if not isinstance(item, Mapping):
                continue
            region = (_text(item.get("4. region")) or "").casefold()
            if region not in {"united states", "us", "usa"}:
                continue
            symbol = _text(item.get("1. symbol"))
            asset_type = _us_asset_type(_text(item.get("3. type")))
            if symbol is None or asset_type is None:
                continue
            if asset_type_hint is not None and asset_type is not asset_type_hint:
                continue
            symbol = symbol.upper()
            instrument = Instrument(
                instrument_id=build_instrument_id(asset_type, Market.US, symbol),
                symbol=symbol,
                name=_text(item.get("2. name")) or symbol,
                market=Market.US,
                exchange="UNKNOWN",
                currency=_text(item.get("8. currency")) or "USD",
                timezone="America/New_York",
                asset_type=asset_type,
                country="US",
            )
            candidates[instrument.instrument_id] = instrument
        if not candidates:
            raise NoMarketData("Alpha Vantage instrument search returned no US candidates")
        fetched_at = self._clock.now()
        return ProviderSuccess(
            value=tuple(candidates[key] for key in sorted(candidates)),
            meta=_meta(self.vendor_id, as_of=as_of, fetched_at=fetched_at),
        )


class TencentInstrumentDirectoryAdapter:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.TENCENT

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category is DataCategory.INSTRUMENT_MASTER

    def is_configured(self) -> bool:
        return self._enabled

    async def lookup(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Instrument, ...]]:
        if not self.is_configured():
            raise ProviderNotConfigured("Tencent instrument directory is disabled")
        require_aware_datetime(as_of, field_name="as_of")
        normalized = normalize_symbol_input(Market.A_SHARE, query, asset_type_hint=asset_type_hint)
        if normalized.local_code is None:
            raise NoMarketData("A-share query does not identify one exchange")
        exchange = normalized.exchange_hint
        if exchange is None:
            exchange = {"5": "SSE", "1": "SZSE"}.get(normalized.local_code[0])
        if exchange is None:
            raise NoMarketData("A-share query does not identify one exchange")
        prefix = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}[exchange]
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_TENCENT_QUOTE_URL,
                params={"q": f"{prefix}{normalized.local_code}"},
                headers={"Accept": "text/plain,*/*", "User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        _raise_http(response.status_code, vendor=self.vendor_id)
        try:
            text = response.body.decode("gbk")
        except UnicodeDecodeError:
            text = response.body.decode("utf-8", errors="strict")
        match = _TENCENT_LINE_RE.search(text)
        if match is None:
            raise NoMarketData("Tencent returned no A-share instrument")
        fields = match.group("body").split("~")
        if len(fields) < 3 or fields[2] != normalized.local_code or not fields[1].strip():
            raise NoMarketData("Tencent did not validate the requested A-share instrument")
        code = normalized.local_code
        inferred = (
            AssetType.ETF
            if code[:2] in {"15", "16", "18", "51", "56", "58"}
            else AssetType.EQUITY
        )
        asset_type = asset_type_hint or inferred
        suffix = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}[exchange]
        symbol = f"{code}{suffix}"
        instrument = Instrument(
            instrument_id=build_instrument_id(asset_type, Market.A_SHARE, symbol),
            symbol=symbol,
            name=fields[1].strip(),
            market=Market.A_SHARE,
            exchange=exchange,
            currency="CNY",
            timezone="Asia/Shanghai",
            asset_type=asset_type,
            country="CN",
        )
        fetched_at = self._clock.now()
        return ProviderSuccess(
            value=(instrument,),
            meta=_meta(self.vendor_id, as_of=as_of, fetched_at=fetched_at),
        )
