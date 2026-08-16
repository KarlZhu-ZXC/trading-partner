"""CLS (财联社) A-share news adapter (Phase 1E E3).

Primary market-news source via frozen path ``www.cls.cn/v1/roll/get_roll_list``.
Company-specific news remains Eastmoney-owned as fallback/composition.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.models import NewsItem
from domain.common.enums import (
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
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    content_type_matches,
    loads_json_decimal,
    parse_shanghai_datetime,
    sanitize_public_url,
)
from infrastructure.providers.common.adapter_guards import require_as_of
from infrastructure.system.clock import SystemClock

_ROLL_URL = "https://www.cls.cn/v1/roll/get_roll_list"
_JSON_CONTENT = ("application/json", "text/json", "text/plain")


class CLSAShareAdapter:
    """CategoryProvider implementing AShareNewsProvider (market news primary)."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
    ) -> None:
        if transport is None:
            raise DataContractError(
                "transport is required",
                details={"field": "transport", "rule": "required"},
            )
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

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.CLS

    @property
    def provider_name(self) -> str:
        return VendorId.CLS.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category is DataCategory.NEWS

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "CLS A-share adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        return require_as_of(as_of=as_of, clock_now=self._clock.now())

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "CLS rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "CLS access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "CLS HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _meta(
        self, *, as_of: datetime, fetched_at: datetime
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=DataCategory.NEWS,
            role=SourceRole.PRIMARY,
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

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[NewsItem, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        require_aware_datetime(start, field_name="start")
        require_aware_datetime(end, field_name="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        # CLS roll list is market-scope only — cannot filter by instrument.
        # Return typed unsupported/no-data before network so chains fall through
        # to Eastmoney company news. Never label market headlines as company-specific.
        if instrument is not None:
            raise NoMarketData(
                "CLS market roll news does not support instrument-scoped queries",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "instrument_unsupported",
                    "category": DataCategory.NEWS.value,
                },
            )
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_ROLL_URL,
                params={
                    "app": "CailianpressWeb",
                    "os": "web",
                    "sv": "8.4.6",
                    "refresh_type": "1",
                    "rn": str(limit),
                    "last_time": "",
                },
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://www.cls.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="news")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "CLS response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        items = self._parse(payload, start=start, end=end, limit=limit, as_of=as_of)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(items),
            meta=self._meta(as_of=as_of, fetched_at=fetched_at),
        )

    def _parse(
        self,
        payload: object,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        as_of: datetime,
    ) -> list[NewsItem]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "CLS news payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        # errno=0 success; data.roll_data list.
        errno = payload.get("errno")
        if errno is not None and errno not in (0, "0"):
            raise ProviderUnavailableError(
                "CLS business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, dict):
            raise DataContractError(
                "CLS news data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        rows = data.get("roll_data") or data.get("list") or data.get("news")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise DataContractError(
                "CLS news list failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        items: list[NewsItem] = []
        for idx, row in enumerate(rows):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "CLS news row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key = row.get("id") or row.get("news_id") or row.get("ctime")
            title = row.get("title") or row.get("brief")
            if key is None or (isinstance(key, str) and not key.strip()):
                raise DataContractError(
                    "CLS news missing key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
            if not isinstance(title, str) or not title.strip():
                raise DataContractError(
                    "CLS news missing title",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
            pub_raw = row.get("ctime") or row.get("time") or row.get("modified_time")
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            if published_at is None:
                # News always requires published_at; unknown → exclude (not invent).
                continue
            # Inclusive window + as_of cutoff.
            if published_at < start or published_at > end or published_at > as_of:
                continue
            summary = row.get("brief") or row.get("content") or row.get("summary")
            if summary is not None and not isinstance(summary, str):
                summary = None
            if isinstance(summary, str):
                summary = summary[:4000]
            share = row.get("shareurl") or row.get("url") or row.get("share_url")
            source_url = None
            if isinstance(share, str) and share.strip():
                source_url = sanitize_public_url(share, field="source_url")
            else:
                source_url = sanitize_public_url(
                    f"https://www.cls.cn/detail/{str(key).strip()}",
                    field="source_url",
                )
            # Provider content is untrusted data, never instructions.
            items.append(
                NewsItem(
                    news_key=str(key).strip()[:200],
                    title=title.strip()[:500],
                    summary=summary,
                    published_at=published_at,
                    source_name="财联社",
                    source_url=source_url,
                )
            )
        items.sort(key=lambda n: (-n.published_at.timestamp(), n.news_key))
        return items
