"""SSE / SZSE A-share announcement + dragon-tiger fallback adapters (Phase 1E).

Corporate actions remain Eastmoney-owned. These adapters support
``ANNOUNCEMENTS`` and E4a ``CAPITAL`` dragon-tiger fallback only.

Paths frozen to §20:
- SSE: ``query.sse.com.cn/infodisplay/`` family (incl. showTradePublicFile.do)
- SZSE: ``www.szse.cn/api/disc/announcement/annList``;
  ``www.szse.cn/api/report/ShowReport/data`` for dragon-tiger fallback
"""

from __future__ import annotations

from datetime import date, datetime

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.models import (
    AnnouncementItem,
    DividendRecord,
    DragonTigerRecord,
    DragonTigerSeat,
    UnlockRecord,
)
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    content_type_matches,
    decimal_from_text,
    instrument_id_from_code,
    loads_json_decimal,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_decimal,
    require_exact_date,
    require_int,
    require_nonnegative_exact_int,
    sanitize_public_url,
)
from infrastructure.providers.common.adapter_guards import require_as_of
from infrastructure.system.clock import SystemClock

_SSE_ANNOUNCE_URL = (
    "https://query.sse.com.cn/infodisplay/queryLatestBulletinNew.do"
)
_SSE_DRAGON_URL = (
    "https://query.sse.com.cn/infodisplay/showTradePublicFile.do"
)
_SZSE_ANNOUNCE_URL = "https://www.szse.cn/api/disc/announcement/annList"
_SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport/data"
_JSON_CONTENT = ("application/json", "text/json", "text/plain", "text/javascript")


class _ExchangeAnnouncementBase:
    """Shared announcement-fallback behaviour for SSE/SZSE."""

    vendor: VendorId
    _supported_suffixes: frozenset[str]
    _url: str

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        current_window_seconds: int = 300,
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
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._current_window_seconds = require_nonnegative_exact_int(
            current_window_seconds, field="current_window_seconds"
        )

    @property
    def vendor_id(self) -> VendorId:
        return self.vendor

    @property
    def provider_name(self) -> str:
        return self.vendor.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in {
            DataCategory.ANNOUNCEMENTS,
            DataCategory.CAPITAL,
        }

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                f"{self.vendor.value} A-share adapter is disabled",
                details={"vendor": self.vendor.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        return require_as_of(as_of=as_of, clock_now=self._clock.now())

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                f"{self.vendor.value} rate limited",
                details={
                    "vendor": self.vendor.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                f"{self.vendor.value} access blocked",
                details={
                    "vendor": self.vendor.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                f"{self.vendor.value} HTTP failure",
                details={
                    "vendor": self.vendor.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _meta(
        self,
        *,
        as_of: datetime,
        fetched_at: datetime,
        warnings: tuple[str, ...] = (),
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor,
            category=DataCategory.ANNOUNCEMENTS,
            role=SourceRole.FALLBACK,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.UNKNOWN,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=warnings,
        )

    def _require_exchange(self, suffix: str) -> None:
        if suffix not in self._supported_suffixes:
            raise DataContractError(
                f"{self.vendor.value} does not cover this exchange board",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "exchange_mismatch",
                },
            )

    async def get_announcements(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[AnnouncementItem, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        self._require_exchange(suffix)
        request = self._build_request(code6=code6, limit=limit)
        response = await self._transport.send(request)
        self._raise_for_http_status(response.status_code, operation="announcements")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                f"{self.vendor.value} response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        items, unknown_excluded = self._parse(
            payload, limit=limit, as_of=as_of, now=now, code6=code6
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        warnings: tuple[str, ...] = ()
        if unknown_excluded:
            warnings = ("PUBLICATION_TIME_UNKNOWN_EXCLUDED",)
        return ProviderSuccess(
            value=tuple(items),
            meta=self._meta(as_of=as_of, fetched_at=fetched_at, warnings=warnings),
        )

    def _build_request(self, *, code6: str, limit: int) -> HttpRequest:
        raise NotImplementedError

    def _parse(
        self,
        payload: object,
        *,
        limit: int,
        as_of: datetime,
        now: datetime,
        code6: str,
    ) -> tuple[list[AnnouncementItem], bool]:
        raise NotImplementedError

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
        raise DataContractError(
            f"{self.vendor.value} does not implement corporate actions",
            details={
                "vendor": self.vendor.value,
                "operation": "corporate_actions",
                "rule": "unsupported",
                "category": DataCategory.CORPORATE_ACTIONS.value,
            },
        )

    def _capital_meta(
        self, *, as_of: datetime, fetched_at: datetime
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor,
            category=DataCategory.CAPITAL,
            role=SourceRole.FALLBACK,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.UNKNOWN,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=(),
        )

    async def get_dragon_tiger(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[DragonTigerRecord, ...]]:
        """Dragon-tiger fallback; fixture-locked offline contracts."""
        self._require_configured()
        self._require_as_of(as_of)
        trade_date = require_exact_date(trade_date, field="trade_date")
        if instrument is not None:
            code6, suffix = require_a_share_instrument(instrument)
            self._require_exchange(suffix)
        else:
            code6, suffix = None, None
        records = await self._fetch_dragon_tiger(
            instrument=instrument,
            code6=code6,
            trade_date=trade_date,
            as_of=as_of,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._capital_meta(as_of=as_of, fetched_at=fetched_at),
        )

    async def _fetch_dragon_tiger(
        self,
        *,
        instrument: Instrument | None,
        code6: str | None,
        trade_date: date,
        as_of: datetime,
    ) -> list[DragonTigerRecord]:
        raise NotImplementedError


class SseAShareDisclosureAdapter(_ExchangeAnnouncementBase):
    """SSE official disclosure announcement + dragon-tiger fallback."""

    vendor = VendorId.SSE
    _supported_suffixes = frozenset({"SH"})
    _url = _SSE_ANNOUNCE_URL

    async def _fetch_dragon_tiger(
        self,
        *,
        instrument: Instrument | None,
        code6: str | None,
        trade_date: date,
        as_of: datetime,
    ) -> list[DragonTigerRecord]:
        # Fixture-locked JSON under showTradePublicFile.do family.
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_SSE_DRAGON_URL,
                params={
                    "isPagination": "true",
                    "pageHelp.pageSize": "50",
                    "pageHelp.pageNo": "1",
                    "tradeDate": trade_date.isoformat(),
                },
                headers={
                    "Accept": "application/json,text/javascript,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://www.sse.com.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="dragon_tiger")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "SSE dragon tiger Content-Type is not acceptable",
                details={
                    "vendor": self.vendor.value,
                    "operation": "dragon_tiger",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        return _parse_exchange_dragon_json(
            payload,
            vendor=self.vendor,
            trade_date=trade_date,
            instrument=instrument,
            code6=code6,
            default_suffix="SH",
        )

    def _build_request(self, *, code6: str, limit: int) -> HttpRequest:
        return HttpRequest(
            method="GET",
            url=self._url,
            params={
                "isPagination": "true",
                "productId": code6,
                "keyWord": "",
                "securityType": "0101",
                "reportType2": "",
                "reportType": "ALL",
                "beginDate": "",
                "endDate": "",
                "pageHelp.pageSize": str(limit),
                "pageHelp.pageCount": "50",
                "pageHelp.pageNo": "1",
                "pageHelp.beginPage": "1",
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": "1",
            },
            headers={
                "Accept": "application/json,text/javascript,*/*",
                "User-Agent": self._user_agent,
                "Referer": "https://www.sse.com.cn/",
            },
            body=None,
            timeout_seconds=self._timeout_seconds,
        )

    def _parse(
        self,
        payload: object,
        *,
        limit: int,
        as_of: datetime,
        now: datetime,
        code6: str,
    ) -> tuple[list[AnnouncementItem], bool]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "SSE announcements payload failed contract validation",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        rows = payload.get("result")
        if rows is None:
            page = payload.get("pageHelp")
            if isinstance(page, dict):
                rows = page.get("data")
        if rows is None:
            rows = payload.get("data")
        if rows is None:
            raise DataContractError(
                "SSE announcements payload missing result list",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        if not isinstance(rows, list):
            raise DataContractError(
                "SSE announcements result must be a list",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        items: list[AnnouncementItem] = []
        unknown_excluded = False
        for idx, row in enumerate(rows):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "SSE announcement row failed contract validation",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key = row.get("BULLETIN_ID") or row.get("id") or row.get("URL")
            title = row.get("TITLE") or row.get("title")
            if not isinstance(key, str) or not key.strip():
                raise DataContractError(
                    "SSE announcement missing key",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            if not isinstance(title, str) or not title.strip():
                raise DataContractError(
                    "SSE announcement missing title",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            pub_raw = row.get("SSEDATE") or row.get("publishDate") or row.get("BULLETIN_DATE")
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            keep, excluded = publication_cutoff_keep(
                published_at,
                as_of=as_of,
                now=now,
                current_window_seconds=self._current_window_seconds,
            )
            if excluded:
                unknown_excluded = True
            if not keep or published_at is None:
                if published_at is None:
                    unknown_excluded = True
                continue
            url_raw = row.get("URL") or row.get("url")
            if isinstance(url_raw, str) and url_raw.strip():
                if url_raw.startswith("http"):
                    source_url = sanitize_public_url(url_raw, field="source_url")
                else:
                    path = url_raw if url_raw.startswith("/") else f"/{url_raw}"
                    source_url = sanitize_public_url(
                        f"https://www.sse.com.cn{path}",
                        field="source_url",
                    )
            else:
                source_url = sanitize_public_url(
                    f"https://www.sse.com.cn/disclosure/listedinfo/announcement/c/{key.strip()}.pdf",
                    field="source_url",
                )
            if source_url is None:
                raise DataContractError(
                    "SSE announcement source_url invalid",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            items.append(
                AnnouncementItem(
                    announcement_key=key.strip()[:200],
                    title=title.strip()[:500],
                    published_at=published_at,
                    category=None,
                    source_url=source_url,
                    pdf_url=source_url if source_url.lower().endswith(".pdf") else None,
                )
            )
        items.sort(key=lambda a: (-a.published_at.timestamp(), a.announcement_key))
        return items, unknown_excluded


class SzseAShareDisclosureAdapter(_ExchangeAnnouncementBase):
    """SZSE official disclosure announcement fallback.

    BSE (``.BJ``) is **not** claimed here — BSE announcements rely on CNINFO.
    """

    vendor = VendorId.SZSE
    _supported_suffixes = frozenset({"SZ"})
    _url = _SZSE_ANNOUNCE_URL

    def _build_request(self, *, code6: str, limit: int) -> HttpRequest:
        body = (
            f'{{"seDate":["",""],"stock":["{code6}"],"channelCode":["listedNotice_disc"],'
            f'"pageSize":{limit},"pageNum":1}}'
        ).encode()
        return HttpRequest(
            method="POST",
            url=self._url,
            params={},
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Content-Type": "application/json",
                "User-Agent": self._user_agent,
                "Referer": "https://www.szse.cn/",
            },
            body=body,
            timeout_seconds=self._timeout_seconds,
        )

    def _parse(
        self,
        payload: object,
        *,
        limit: int,
        as_of: datetime,
        now: datetime,
        code6: str,
    ) -> tuple[list[AnnouncementItem], bool]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "SZSE announcements payload failed contract validation",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        data = payload.get("data")
        if data is None:
            raise DataContractError(
                "SZSE announcements payload missing data",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        if not isinstance(data, list):
            raise DataContractError(
                "SZSE announcements data must be a list",
                details={
                    "vendor": self.vendor.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        items: list[AnnouncementItem] = []
        unknown_excluded = False
        for idx, row in enumerate(data):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "SZSE announcement row failed contract validation",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key = row.get("id") or row.get("attachPath") or row.get("title")
            title = row.get("title")
            if not isinstance(key, (str, int)) or (
                isinstance(key, str) and not key.strip()
            ):
                raise DataContractError(
                    "SZSE announcement missing key",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            if not isinstance(title, str) or not title.strip():
                raise DataContractError(
                    "SZSE announcement missing title",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            key_s = str(key).strip()
            pub_raw = row.get("publishTime") or row.get("pubTime") or row.get("seDate")
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            keep, excluded = publication_cutoff_keep(
                published_at,
                as_of=as_of,
                now=now,
                current_window_seconds=self._current_window_seconds,
            )
            if excluded:
                unknown_excluded = True
            if not keep or published_at is None:
                if published_at is None:
                    unknown_excluded = True
                continue
            attach = row.get("attachPath") or row.get("url")
            if isinstance(attach, str) and attach.strip():
                if attach.startswith("http"):
                    source_url = sanitize_public_url(attach, field="source_url")
                else:
                    path = attach if attach.startswith("/") else f"/{attach}"
                    source_url = sanitize_public_url(
                        f"https://disc.static.szse.cn/download{path}",
                        field="source_url",
                    )
            else:
                source_url = sanitize_public_url(
                    f"https://www.szse.cn/disclosure/listed/bulletinDetail/index.html?id={key_s}",
                    field="source_url",
                )
            if source_url is None:
                raise DataContractError(
                    "SZSE announcement source_url invalid",
                    details={
                        "vendor": self.vendor.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            items.append(
                AnnouncementItem(
                    announcement_key=key_s[:200],
                    title=title.strip()[:500],
                    published_at=published_at,
                    category=None,
                    source_url=source_url,
                    pdf_url=source_url if source_url.lower().endswith(".pdf") else None,
                )
            )
        items.sort(key=lambda a: (-a.published_at.timestamp(), a.announcement_key))
        return items, unknown_excluded

    async def _fetch_dragon_tiger(
        self,
        *,
        instrument: Instrument | None,
        code6: str | None,
        trade_date: date,
        as_of: datetime,
    ) -> list[DragonTigerRecord]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_SZSE_REPORT_URL,
                params={
                    "SHOWTYPE": "JSON",
                    "CATALOGID": "1834_xxpl",
                    "TABKEY": "tab1",
                    "txtStart": trade_date.isoformat(),
                    "txtEnd": trade_date.isoformat(),
                    "random": "0.1",
                },
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://www.szse.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="dragon_tiger")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "SZSE dragon tiger Content-Type is not acceptable",
                details={
                    "vendor": self.vendor.value,
                    "operation": "dragon_tiger",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        return _parse_exchange_dragon_json(
            payload,
            vendor=self.vendor,
            trade_date=trade_date,
            instrument=instrument,
            code6=code6,
            default_suffix="SZ",
        )


def _parse_exchange_dragon_json(
    payload: object,
    *,
    vendor: VendorId,
    trade_date: date,
    instrument: Instrument | None,
    code6: str | None,
    default_suffix: str,
) -> list[DragonTigerRecord]:
    """Parse fixture-locked dragon-tiger JSON shared by SSE/SZSE fallbacks."""
    if isinstance(payload, list) and payload:
        # SZSE sometimes returns a list of report tables.
        first = payload[0]
        if isinstance(first, dict) and "data" in first:
            payload = first
    if not isinstance(payload, dict):
        raise DataContractError(
            f"{vendor.value} dragon tiger payload failed contract validation",
            details={
                "vendor": vendor.value,
                "operation": "dragon_tiger",
                "rule": "contract_drift",
            },
        )
    rows = payload.get("records") or payload.get("data") or payload.get("result")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise DataContractError(
            f"{vendor.value} dragon tiger rows must be a list",
            details={
                "vendor": vendor.value,
                "operation": "dragon_tiger",
                "rule": "contract_drift",
            },
        )
    records: list[DragonTigerRecord] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DataContractError(
                f"{vendor.value} dragon tiger row failed contract validation",
                details={
                    "vendor": vendor.value,
                    "operation": "dragon_tiger",
                    "rule": "contract_drift",
                    "index": idx,
                },
            )
        sec = row.get("SECURITY_CODE") or row.get("code") or row.get("zqdm")
        if sec is None and code6 is not None:
            sec = code6
        if not isinstance(sec, str) and not isinstance(sec, int):
            continue
        sec_s = str(sec).strip().zfill(6)
        if code6 is not None and sec_s != code6:
            continue
        if instrument is not None:
            inst_id = instrument.instrument_id
        else:
            inst_id = instrument_id_from_code(sec_s, default_suffix)
        reason = row.get("reason") or row.get("EXPLANATION") or row.get("yy") or "dragon_tiger"
        if not isinstance(reason, str) or not reason.strip():
            reason = "dragon_tiger"
        buy = require_decimal(
            row.get("buy_total_cny") or row.get("BUY") or row.get("mrje"),
            field="buy_total_cny",
        )
        sell = require_decimal(
            row.get("sell_total_cny") or row.get("SELL") or row.get("mcje"),
            field="sell_total_cny",
        )
        net_raw = row.get("net_buy_cny") or row.get("NET")
        net = decimal_from_text(net_raw, field="net_buy_cny")
        if net is None:
            net = buy - sell
        seats_raw = row.get("seats")
        seats: list[DragonTigerSeat] = []
        if isinstance(seats_raw, list):
            for sidx, seat in enumerate(seats_raw):
                if not isinstance(seat, dict):
                    raise DataContractError(
                        f"{vendor.value} dragon tiger seat failed contract",
                        details={
                            "vendor": vendor.value,
                            "operation": "dragon_tiger",
                            "rule": "contract_drift",
                            "index": sidx,
                        },
                    )
                seats.append(
                    DragonTigerSeat(
                        rank=require_int(seat.get("rank"), field="rank"),
                        side=str(seat.get("side") or "buy"),
                        branch_name=str(seat.get("branch_name") or "unknown")[:200],
                        amount_cny=require_decimal(
                            seat.get("amount_cny"), field="amount_cny"
                        ),
                        is_institution=(
                            bool(seat.get("is_institution"))
                            if seat.get("is_institution") is not None
                            else None
                        ),
                    )
                )
        records.append(
            DragonTigerRecord(
                trade_date=trade_date,
                instrument_id=inst_id,
                reason=reason.strip()[:500],
                buy_total_cny=buy,
                sell_total_cny=sell,
                net_buy_cny=net,
                seats=tuple(seats),
                source_vendor=vendor,
                reliability=ReliabilityLevel.MEDIUM,
                is_authoritative=False,
            )
        )
    records.sort(key=lambda r: (r.instrument_id, r.reason))
    return records
