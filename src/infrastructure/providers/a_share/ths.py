"""THS (同花顺) A-share adapter (Phase 1E E3 + E4b).

E3: consensus / worth page fallback via public no-Cookie host family
``basic.10jqka.com.cn/new/*/worth.html``.

E4b (live-verified 2026-07-17):
  - Limit-up reason tags: ``data.10jqka.com.cn/dataapi/limit_up/limit_up_pool``
  - Hot list (ths_hot): ``dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock``

Does not claim exchange authority for limit pools; reason tags are editorial
enrichment only. THS does not implement ``concept_heat``; the product routes
instrument-scoped concept-hit requests to the Eastmoney provider instead.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from html import unescape
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.enums import LimitPoolType, SentimentSourceType
from domain.a_share.models import (
    AnalystReportItem,
    ConsensusEstimate,
    LimitPoolEntry,
    LimitUpContext,
    SentimentSignal,
)
from domain.common.enums import (
    AssetType,
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
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    content_type_matches,
    decimal_from_text,
    decode_text,
    instrument_id_from_code,
    loads_json_decimal,
    require_a_share_instrument,
    require_decimal,
    require_exact_date,
    require_int,
    require_nonnegative_exact_int,
)
from infrastructure.system.clock import SystemClock

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DEFAULT_CURRENT_WINDOW_SECONDS = 300

_WORTH_URL_TMPL = "https://basic.10jqka.com.cn/new/{code}/worth.html"
# Live-verified 2026-07-17 public limit-up pool (reason_type enrichment).
_LIMIT_UP_POOL_URL = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
# Live-verified 2026-07-17 public hot list (matches design §20 path family).
_HOT_LIST_URL = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
)
_HTML_CONTENT = ("text/html", "application/xhtml", "text/plain")
_JSON_CONTENT = ("application/json", "text/json", "text/plain")
_EPS_ROW_RE = re.compile(
    r"data-year=[\"'](?P<year>\d{4})[\"'][^>]*>.*?"
    r"class=[\"'][^\"]*eps[^\"]*[\"'][^>]*>\s*(?P<eps>[-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)
_SIMPLE_EPS_RE = re.compile(
    r"(?P<year>20\d{2})\s*年?\s*(?:EPS|每股收益)[:：]?\s*(?P<eps>[-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_JSON_EPS_RE = re.compile(
    r"[\"']year[\"']\s*:\s*[\"']?(?P<year>20\d{2})[\"']?[^}]{0,80}"
    r"[\"'](?:eps|value|mean)[\"']\s*:\s*[\"']?(?P<eps>[-+]?\d+(?:\.\d+)?)[\"']?",
    re.IGNORECASE,
)

# Live-observed THS market_id → exchange suffix.
_THS_MARKET_TO_SUFFIX: dict[int, str] = {
    17: "SH",
    33: "SZ",
    151: "BJ",
}

_SUPPORTED_CATEGORIES = frozenset(
    {
        DataCategory.RESEARCH_REPORTS,
        DataCategory.LIMIT_UP,
        DataCategory.SENTIMENT,
    }
)


class ThsAShareAdapter:
    """CategoryProvider: consensus fallback + limit reason tags + hot list."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        current_window_seconds: int = _DEFAULT_CURRENT_WINDOW_SECONDS,
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
        return VendorId.THS

    @property
    def provider_name(self) -> str:
        return VendorId.THS.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in _SUPPORTED_CATEGORIES

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "THS A-share adapter is disabled",
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

    def _require_current_only_as_of_and_trade_date(
        self, as_of: datetime, trade_date: date, *, operation: str
    ) -> datetime:
        """Reject before network unless as_of is current and trade_date is now.

        THS hot-list and limit-reason endpoints are current-only cross-sections.
        Sample the adapter clock once; require ``as_of`` within
        ``current_window_seconds`` of that sample and ``trade_date`` equal to
        the Asia/Shanghai local date of the sample.
        """
        now = self._require_as_of(as_of)
        if type(trade_date) is not date:
            raise DataContractError(
                "trade_date must be a date (not datetime)",
                details={
                    "field": "trade_date",
                    "rule": "exact_date_type",
                    "operation": operation,
                },
            )
        age = (now - as_of).total_seconds()
        if age > self._current_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported current window for current-only "
                "cross-section data",
                details={
                    "operation": operation,
                    "rule": "current_window",
                    "window_seconds": self._current_window_seconds,
                },
            )
        local_day = now.astimezone(_SHANGHAI).date()
        if trade_date != local_day:
            raise DataContractError(
                "trade_date must equal Asia/Shanghai local date of sampled now "
                "for current-only endpoints",
                details={
                    "field": "trade_date",
                    "rule": "current_only_local_date",
                    "operation": operation,
                    "requested": trade_date.isoformat(),
                    "supportable": local_day.isoformat(),
                },
            )
        return now

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "THS rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "THS access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "THS HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _meta(
        self,
        *,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        role: SourceRole = SourceRole.FALLBACK,
        warnings: tuple[str, ...] = (),
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=role,
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

    def _headers_json(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
            "Referer": "https://data.10jqka.com.cn/",
        }

    async def search_reports(
        self,
        *,
        text: str | None,
        instrument: Instrument | None,
        industry_code: str | None,
        published_from: object,
        published_to: object,
        limit: int,
        offset: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AnalystReportItem, ...]]:
        # THS public no-Cookie endpoints only support consensus worth page,
        # not free-text report search. Explicit unsupported (not fake empty).
        raise DataContractError(
            "THS does not implement report search in Phase 1E E3",
            details={
                "vendor": self.vendor_id.value,
                "operation": "reports",
                "rule": "unsupported",
            },
        )

    async def get_consensus(
        self, instrument: Instrument, *, as_of: datetime
    ) -> ProviderSuccess[tuple[ConsensusEstimate, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        code6, _suffix = require_a_share_instrument(instrument)
        url = _WORTH_URL_TMPL.format(code=code6)
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params={},
                headers={
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://basic.10jqka.com.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="consensus")
        if not content_type_matches(response.headers, allowed_substrings=_HTML_CONTENT):
            raise DataContractError(
                "THS response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "content_type",
                },
            )
        text = decode_text(response.body, encodings=("utf-8", "gbk"))
        estimates = self._parse_worth_html(text)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not estimates:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                },
            )
        return ProviderSuccess(
            value=tuple(estimates),
            meta=self._meta(
                category=DataCategory.RESEARCH_REPORTS,
                as_of=as_of,
                fetched_at=fetched_at,
            ),
        )

    def _parse_worth_html(self, html: str) -> list[ConsensusEstimate]:
        if not html or not html.strip():
            raise DataContractError(
                "THS worth page empty",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "contract_drift",
                },
            )
        # Strip scripts/styles — never execute; treat as untrusted data only.
        cleaned = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = unescape(cleaned)
        found: dict[int, Decimal] = {}
        for pattern in (_EPS_ROW_RE, _SIMPLE_EPS_RE, _JSON_EPS_RE):
            for match in pattern.finditer(cleaned):
                year = int(match.group("year"))
                eps = decimal_from_text(match.group("eps"), field="eps")
                if eps is None:
                    continue
                found[year] = eps
        if not found:
            # Explicit marker used by fixtures for legitimate empty consensus.
            if "NO_CONSENSUS_DATA" in cleaned or "暂无盈利预测" in cleaned:
                return []
            raise DataContractError(
                "THS worth page failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "contract_drift",
                },
            )
        out: list[ConsensusEstimate] = []
        for year in sorted(found):
            out.append(
                ConsensusEstimate(
                    fiscal_year=year,
                    metric="eps",
                    mean=found[year],
                    high=None,
                    low=None,
                    institution_count=None,
                )
            )
        return out

    # --- E4b limit-up reason enrichment --------------------------------------

    async def get_limit_pools(
        self,
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[LimitUpContext]:
        """THS public limit-up pool for editorial reason_tags only.

        Only ``LIMIT_UP`` is supported. Never claims exchange authority; other
        pools are explicit unsupported. Eastmoney remains the factual primary.
        Current-only: reject stale/historical as_of/trade_date before network.
        """
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        self._require_current_only_as_of_and_trade_date(
            as_of, trade_date, operation="limit_pools"
        )
        if not isinstance(pools, tuple) or not pools:
            raise DataContractError(
                "pools must be a non-empty tuple",
                details={"field": "pools", "rule": "non_empty"},
            )
        seen: set[LimitPoolType] = set()
        for pool in pools:
            if not isinstance(pool, LimitPoolType):
                raise DataContractError(
                    "pools elements must be LimitPoolType",
                    details={"field": "pools", "rule": "type"},
                )
            if pool in seen:
                raise DataContractError(
                    "pools must not contain duplicates",
                    details={"field": "pools", "rule": "unique"},
                )
            seen.add(pool)
            if pool is not LimitPoolType.LIMIT_UP:
                raise DataContractError(
                    "THS limit enrichment only supports LIMIT_UP pool",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "pool": pool.value,
                        "rule": "unsupported",
                    },
                )

        params = {
            "page": "1",
            "limit": "200",
            "filter": "HS,GEM2STAR",
            "order_field": "330324",
            "order_type": "0",
            "date": trade_date.strftime("%Y%m%d"),
        }
        # Field list is part of the live-verified public API query contract
        # (not a secret). Includes reason_type and identity fields.
        params["field"] = (
            "199112,10,9001,330323,330324,330325,9002,330329,"
            "133971,133970,1968584,3475914,9003,9004"
        )
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_LIMIT_UP_POOL_URL,
                params=params,
                headers=self._headers_json(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="limit_pools")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "THS limit-up pool Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        entries = self._parse_limit_up_pool(payload, trade_date=trade_date)
        # Summary counts for THS enrichment are non-authoritative; zeros for
        # down/broken so context invariants hold. Service merges tags only.
        context = LimitUpContext(
            trade_date=trade_date,
            entries=tuple(entries),
            limit_up_count=len(entries),
            limit_down_count=0,
            broken_limit_count=0,
            broken_rate=None,
            max_consecutive_count=None,
            promotion_rate=None,
            ladder=(),
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=context,
            meta=self._meta(
                category=DataCategory.LIMIT_UP,
                as_of=as_of,
                fetched_at=fetched_at,
                role=SourceRole.FALLBACK,
            ),
        )

    def _parse_limit_up_pool(
        self, payload: object, *, trade_date: date
    ) -> list[LimitPoolEntry]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "THS limit-up pool payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                },
            )
        status = payload.get("status_code")
        if status not in (0, "0", Decimal(0)):
            raise ProviderUnavailableError(
                "THS limit-up pool business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, dict):
            raise DataContractError(
                "THS limit-up pool data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                },
            )
        info = data.get("info")
        if info is None:
            return []
        if not isinstance(info, list):
            raise DataContractError(
                "THS limit-up pool info failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                },
            )
        out: list[LimitPoolEntry] = []
        seen: set[str] = set()
        for idx, row in enumerate(info):
            if not isinstance(row, dict):
                raise DataContractError(
                    "THS limit-up pool row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            entry = self._limit_entry_from_row(row, trade_date=trade_date, index=idx)
            if entry.instrument_id in seen:
                raise DataContractError(
                    "THS limit-up pool returned duplicate instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen.add(entry.instrument_id)
            out.append(entry)
        out.sort(key=lambda e: e.instrument_id)
        return out

    def _limit_entry_from_row(
        self, row: dict[str, object], *, trade_date: date, index: int
    ) -> LimitPoolEntry:
        code_raw = row.get("code")
        if not isinstance(code_raw, str) or not code_raw.strip().isdigit():
            raise DataContractError(
                "THS limit-up pool row missing code",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        code6 = code_raw.strip().zfill(6)
        market_id = row.get("market_id")
        mid = require_int(market_id, field=f"info[{index}].market_id")
        suffix = _THS_MARKET_TO_SUFFIX.get(mid)
        if suffix is None:
            # market_type fallback (live: HS/GEM2STAR) is not exchange-unique.
            raise DataContractError(
                "THS limit-up pool market_id is not mapped",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        instrument_id = instrument_id_from_code(code6, suffix, asset=AssetType.EQUITY)
        name_raw = row.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise DataContractError(
                "THS limit-up pool row missing name",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        latest = row.get("latest")
        if latest is None:
            raise DataContractError(
                "THS limit-up pool row missing latest",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        last = require_decimal(latest, field=f"info[{index}].latest")
        change_rate = row.get("change_rate")
        if change_rate is None:
            raise DataContractError(
                "THS limit-up pool row missing change_rate",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        change_percent = require_decimal(
            change_rate, field=f"info[{index}].change_rate"
        )
        # Editorial reason tags — source_vendor remains THS; not exchange facts.
        reason_tags: tuple[str, ...] = ()
        reason_type = row.get("reason_type")
        if isinstance(reason_type, str) and reason_type.strip():
            parts = [
                p.strip()
                for p in reason_type.replace("，", "+").split("+")
                if p.strip()
            ]
            # Prefix for attribution; never claim exchange authority.
            reason_tags = tuple(f"ths:{p}" for p in parts)
        days_and_boards = None
        high_days = row.get("high_days")
        if isinstance(high_days, str) and high_days.strip():
            days_and_boards = high_days.strip()
        return LimitPoolEntry(
            pool_type=LimitPoolType.LIMIT_UP,
            trade_date=trade_date,
            instrument_id=instrument_id,
            name=name_raw.strip(),
            last=last,
            change_percent=change_percent,
            consecutive_limit_count=None,
            days_and_boards=days_and_boards,
            first_seal_at=None,
            last_seal_at=None,
            seal_amount_cny=None,
            broken_count=None,
            industry=None,
            reason_tags=reason_tags,
            source_vendor=VendorId.THS,
            reliability=ReliabilityLevel.LOW,
        )

    # --- E4b sentiment hot list ----------------------------------------------

    async def get_sentiment_signals(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        sources: tuple[SentimentSourceType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[SentimentSignal, ...]]:
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        # Hot list is current-only; sample clock once and reject before network.
        # THS has no verified concept-hit endpoint; product routing assigns that
        # source to Eastmoney rather than deriving it from THS concept tags.
        self._require_current_only_as_of_and_trade_date(
            as_of, trade_date, operation="sentiment"
        )
        if not isinstance(sources, tuple) or not sources:
            raise DataContractError(
                "sources must be a non-empty tuple",
                details={"field": "sources", "rule": "non_empty"},
            )
        if instrument is not None:
            require_a_share_instrument(instrument)

        signals: list[SentimentSignal] = []
        seen_src: set[SentimentSourceType] = set()
        for source in sources:
            if not isinstance(source, SentimentSourceType):
                raise DataContractError(
                    "sources elements must be SentimentSourceType",
                    details={"field": "sources", "rule": "type"},
                )
            if source in seen_src:
                raise DataContractError(
                    "sources must not contain duplicates",
                    details={"field": "sources", "rule": "unique"},
                )
            seen_src.add(source)
            if source is SentimentSourceType.THS_HOT:
                signals.extend(
                    await self._fetch_ths_hot(
                        instrument=instrument, trade_date=trade_date
                    )
                )
            elif source is SentimentSourceType.CONCEPT_HEAT:
                # No live-verified dedicated concept-heat endpoint (2026-07-17).
                # Hot-list concept_tag is stock-attached only — not a concept rank.
                raise ProviderUnavailableError(
                    "THS concept heat upstream contract is unverified",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "source": source.value,
                        "error_type": "upstream_contract_unverified",
                    },
                    retryable=False,
                )
            else:
                raise DataContractError(
                    "THS does not implement this sentiment source",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "source": source.value,
                        "rule": "unsupported",
                    },
                )
        signals.sort(
            key=lambda s: (
                s.rank if s.rank is not None else 10**9,
                s.instrument_id or "",
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(signals),
            meta=self._meta(
                category=DataCategory.SENTIMENT,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=("LOW_RELIABILITY_MARKET_SIGNAL",),
                role=SourceRole.PRIMARY,
            ),
        )

    async def _fetch_ths_hot(
        self,
        *,
        instrument: Instrument | None,
        trade_date: date,
    ) -> list[SentimentSignal]:
        params = {
            "stock_type": "a",
            "type": "hour",
            "list_type": "normal",
        }
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_HOT_LIST_URL,
                params=params,
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://dq.10jqka.com.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="sentiment")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "THS hot-list Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "THS hot-list payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        status = payload.get("status_code")
        if status not in (0, "0", Decimal(0)):
            raise ProviderUnavailableError(
                "THS hot-list business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, dict):
            raise DataContractError(
                "THS hot-list data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        stock_list = data.get("stock_list")
        if stock_list is None:
            return []
        if not isinstance(stock_list, list):
            raise DataContractError(
                "THS hot-list stock_list failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        wanted = instrument.instrument_id if instrument is not None else None
        signals: list[SentimentSignal] = []
        seen: set[str] = set()
        for idx, row in enumerate(stock_list):
            if not isinstance(row, dict):
                raise DataContractError(
                    "THS hot-list row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            code_raw = row.get("code")
            if not isinstance(code_raw, str) or not code_raw.strip().isdigit():
                raise DataContractError(
                    "THS hot-list row missing code",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            code6 = code_raw.strip().zfill(6)
            market = require_int(row.get("market"), field=f"stock_list[{idx}].market")
            suffix = _THS_MARKET_TO_SUFFIX.get(market)
            if suffix is None:
                raise DataContractError(
                    "THS hot-list market is not mapped",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            instrument_id = instrument_id_from_code(
                code6, suffix, asset=AssetType.EQUITY
            )
            if instrument_id in seen:
                raise DataContractError(
                    "THS hot-list returned duplicate instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen.add(instrument_id)
            if wanted is not None and instrument_id != wanted:
                continue
            order = row.get("order")
            rank = require_int(order, field=f"stock_list[{idx}].order")
            if rank < 0:
                raise DataContractError(
                    "rank must be nonnegative",
                    details={"field": "order", "rule": "nonnegative", "index": idx},
                )
            rank_change = None
            if "hot_rank_chg" in row and row.get("hot_rank_chg") is not None:
                rank_change = require_int(
                    row.get("hot_rank_chg"), field=f"stock_list[{idx}].hot_rank_chg"
                )
            heat = decimal_from_text(row.get("rate"), field=f"stock_list[{idx}].rate")
            concept_tags: list[str] = []
            tag = row.get("tag")
            if isinstance(tag, dict):
                concepts = tag.get("concept_tag")
                if isinstance(concepts, list):
                    for c in concepts:
                        if isinstance(c, str) and c.strip():
                            concept_tags.append(c.strip())
            label = None
            name = row.get("name")
            if isinstance(name, str) and name.strip():
                label = name.strip()
            signals.append(
                SentimentSignal(
                    source_type=SentimentSourceType.THS_HOT,
                    trade_date=trade_date,
                    instrument_id=instrument_id,
                    rank=rank,
                    rank_change=rank_change,
                    heat_value=heat,
                    concept_tags=tuple(concept_tags),
                    label=label,
                    source_vendor=VendorId.THS,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        return signals
