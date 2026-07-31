"""Provider-backed instrument discovery for US, Korean, and A-share symbols."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
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
    InvalidInstrument,
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
from infrastructure.providers.us.alpha_vantage_key_pool import (
    AlphaVantageKeyPool,
    classify_alpha_vantage_notice,
)

_YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q"
_TENCENT_HINT_URL = "https://smartbox.gtimg.cn/s3/"
_TENCENT_LINE_RE = re.compile(r'^v_(?P<symbol>[a-z]{2}\d{6})="(?P<body>.*)";?\s*$', re.M)
_TENCENT_HINT_RE = re.compile(r'^v_hint="(?P<body>(?:\\.|[^"])*)";?\s*$', re.S)


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
        "FUTURE": AssetType.FUTURE,
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


def _kr_symbol(raw: str, asset_type: AssetType) -> tuple[str, str] | None:
    upper = raw.upper()
    if asset_type is AssetType.INDEX:
        symbol = upper.removeprefix("^")
        if symbol not in {"KS11", "KQ11", "KS200"}:
            return None
        return symbol, "KOSDAQ" if symbol == "KQ11" else "KOSPI"
    match = re.fullmatch(r"(\d{6})\.(KS|KQ)", upper)
    if match is None or asset_type not in {AssetType.EQUITY, AssetType.ETF}:
        return None
    code, suffix = match.groups()
    return code, "KOSPI" if suffix == "KS" else "KOSDAQ"


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
        return market in {Market.US, Market.KR} and category is DataCategory.INSTRUMENT_MASTER

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
        if market not in {Market.US, Market.KR}:
            raise DataContractError(
                "Yahoo instrument directory supports US and KR only",
                details={"market": market.value, "operation": "instrument_lookup"},
            )
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
            provider_symbol = symbol.upper()
            name = _text(item.get("longname")) or _text(item.get("shortname")) or symbol
            if market is Market.KR:
                normalized = _kr_symbol(provider_symbol, asset_type)
                if normalized is None:
                    continue
                symbol, exchange = normalized
                timezone = "Asia/Seoul"
                currency = "KRW"
                country = "KR"
            else:
                symbol = provider_symbol
                exchange = _us_exchange(
                    _text(item.get("exchange")) or _text(item.get("exchDisp"))
                )
                timezone = _text(item.get("exchangeTimezoneName")) or "America/New_York"
                currency = _text(item.get("currency")) or "USD"
                country = "US"
            instrument = Instrument(
                instrument_id=build_instrument_id(asset_type, market, symbol),
                symbol=symbol,
                name=name,
                market=market,
                exchange=exchange,
                currency=currency,
                timezone=timezone,
                asset_type=asset_type,
                country=country,
            )
            candidates[instrument.instrument_id] = instrument
        if not candidates:
            raise NoMarketData(f"Yahoo instrument search returned no {market.value} candidates")
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
        api_keys: Sequence[str],
        clock: Clock,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._key_pool = AlphaVantageKeyPool(api_keys)
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
        return self._enabled and self._key_pool.is_configured()

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
        payload: Mapping[str, object] | None = None
        last_rate_limit: ProviderRateLimitError | None = None
        for key_index, api_key in self._key_pool.ordered_candidates():
            response = await self._transport.send(
                HttpRequest(
                    method="GET",
                    url=_ALPHA_VANTAGE_URL,
                    params={"function": "SYMBOL_SEARCH", "keywords": query, "apikey": api_key},
                    headers={"Accept": "application/json", "User-Agent": "TradingPartner/1.0"},
                    body=None,
                    timeout_seconds=self._timeout,
                )
            )
            try:
                _raise_http(response.status_code, vendor=self.vendor_id)
                candidate_payload = _loads(response.body, vendor=self.vendor_id)
                notice = _text(candidate_payload.get("Note")) or _text(
                    candidate_payload.get("Information")
                )
                if notice is not None:
                    notice_kind = classify_alpha_vantage_notice(notice)
                    if notice_kind == "rate_limit":
                        raise ProviderRateLimitError(
                            "Alpha Vantage instrument directory rate limited",
                            details={
                                "vendor": self.vendor_id.value,
                                "operation": "instrument_lookup",
                            },
                        )
                    if notice_kind == "api_key":
                        raise ProviderNotConfigured(
                            "Alpha Vantage API key invalid or missing",
                            details={
                                "vendor": self.vendor_id.value,
                                "operation": "instrument_lookup",
                            },
                        )
                    raise ProviderUnavailableError(
                        "Alpha Vantage instrument directory returned a notice",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "instrument_lookup",
                        },
                    )
            except ProviderRateLimitError as exc:
                last_rate_limit = exc
                continue
            self._key_pool.mark_success(key_index)
            payload = candidate_payload
            break
        if payload is None:
            assert last_rate_limit is not None
            raise ProviderRateLimitError(
                "Alpha Vantage instrument key pool exhausted by rate limits",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "instrument_lookup",
                    "error_type": "key_pool_exhausted",
                    "attempted_key_count": self._key_pool.size,
                },
            ) from None
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
        try:
            normalized = normalize_symbol_input(
                Market.A_SHARE, query, asset_type_hint=asset_type_hint
            )
        except InvalidInstrument:
            if not _is_name_like_a_share_query(query):
                raise
            candidates = await self._lookup_name_candidates(
                query=query,
                asset_type_hint=asset_type_hint,
            )
            if not candidates:
                raise NoMarketData(
                    "Tencent instrument search returned no exact A-share name"
                ) from None
            fetched_at = self._clock.now()
            return ProviderSuccess(
                value=candidates,
                meta=_meta(self.vendor_id, as_of=as_of, fetched_at=fetched_at),
            )
        if normalized.local_code is None:
            raise NoMarketData("A-share query does not identify one exchange")
        exchange = normalized.exchange_hint
        if exchange is None:
            exchange = {"5": "SSE", "1": "SZSE"}.get(normalized.local_code[0])
        if exchange is None:
            raise NoMarketData("A-share query does not identify one exchange")
        instrument = await self._fetch_validated_instrument(
            code=normalized.local_code,
            exchange=exchange,
            asset_type_hint=asset_type_hint,
        )
        fetched_at = self._clock.now()
        return ProviderSuccess(
            value=(instrument,),
            meta=_meta(self.vendor_id, as_of=as_of, fetched_at=fetched_at),
        )

    async def _lookup_name_candidates(
        self,
        *,
        query: str,
        asset_type_hint: AssetType | None,
    ) -> tuple[Instrument, ...]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_TENCENT_HINT_URL,
                params={"q": query.strip(), "t": "all"},
                headers={"Accept": "text/plain,*/*", "User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        _raise_http(response.status_code, vendor=self.vendor_id)
        text = _decode_tencent_text(response.body)
        match = _TENCENT_HINT_RE.fullmatch(text.strip())
        if match is None:
            raise NoMarketData("Tencent instrument search returned no A-share hints")
        try:
            decoded = json.loads(f'"{match.group("body")}"')
        except (TypeError, ValueError):
            raise DataContractError(
                "Tencent instrument search returned an invalid escaped payload",
                details={"vendor": self.vendor_id.value, "operation": "instrument_lookup"},
            ) from None
        if not isinstance(decoded, str):
            raise DataContractError(
                "Tencent instrument search returned an invalid hint payload",
                details={"vendor": self.vendor_id.value, "operation": "instrument_lookup"},
            )
        expected_name = query.strip().casefold()
        hinted: dict[tuple[str, str], None] = {}
        for item in decoded.split("^"):
            fields = item.split("~")
            if len(fields) < 3:
                continue
            prefix, code, name = fields[0].lower(), fields[1], fields[2].strip()
            exchange = {"sh": "SSE", "sz": "SZSE", "bj": "BSE"}.get(prefix)
            if (
                exchange is None
                or re.fullmatch(r"\d{6}", code) is None
                or name.casefold() != expected_name
            ):
                continue
            hinted[(exchange, code)] = None
        validated: list[Instrument] = []
        for exchange, code in hinted:
            instrument = await self._fetch_validated_instrument(
                code=code,
                exchange=exchange,
                asset_type_hint=asset_type_hint,
            )
            if instrument.name.casefold() == expected_name:
                validated.append(instrument)
        return tuple(sorted(validated, key=lambda item: item.instrument_id))

    async def _fetch_validated_instrument(
        self,
        *,
        code: str,
        exchange: str,
        asset_type_hint: AssetType | None,
    ) -> Instrument:
        prefix = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}[exchange]
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_TENCENT_QUOTE_URL,
                params={"q": f"{prefix}{code}"},
                headers={"Accept": "text/plain,*/*", "User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        _raise_http(response.status_code, vendor=self.vendor_id)
        text = _decode_tencent_text(response.body)
        match = _TENCENT_LINE_RE.search(text)
        if match is None:
            raise NoMarketData("Tencent returned no A-share instrument")
        fields = match.group("body").split("~")
        if len(fields) < 3 or fields[2] != code or not fields[1].strip():
            raise NoMarketData("Tencent did not validate the requested A-share instrument")
        inferred = (
            AssetType.ETF
            if code[:2] in {"15", "16", "18", "51", "56", "58"}
            else AssetType.EQUITY
        )
        if asset_type_hint is not None and asset_type_hint is not inferred:
            raise NoMarketData("Tencent instrument type does not match asset_type_hint")
        asset_type = asset_type_hint or inferred
        suffix = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}[exchange]
        symbol = f"{code}{suffix}"
        return Instrument(
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


def _decode_tencent_text(body: bytes) -> str:
    try:
        return body.decode("gbk")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="strict")


def _is_name_like_a_share_query(query: object) -> bool:
    return isinstance(query, str) and bool(query.strip()) and ":" not in query
