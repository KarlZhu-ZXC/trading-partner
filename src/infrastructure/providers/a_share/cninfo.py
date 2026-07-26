"""Cninfo A-share disclosure + interactive QA adapter (Phase 1E E3 + E4b).

Primary owner of announcements via
``www.cninfo.com.cn/new/hisAnnouncement/query``.

E4b interactive QA via live-verified 2026-07-17
``irm.cninfo.com.cn/newircs/index/search`` (POST JSON).

Phase 3B company operating metrics: list publication-cutoff-safe announcements,
download only official ``static.cninfo.com.cn/finalpage/*.PDF`` bodies through
the shared transport, extract text with pypdf, and parse with a versioned
generic Chinese text parser. Raw PDF bytes/text never leave this adapter.

Corporate actions are **not** claimed (Eastmoney-owned). The protocol method
exists only so ``isinstance(..., AShareDisclosureProvider)`` can narrow; it
raises a typed unsupported contract error.

OrgId resolution uses the official static CNINFO inventory (vendored map).
Runtime never synthesizes orgId from code.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.util import find_spec
from urllib.parse import urlencode, urlsplit

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.enums import CompanyDocumentParseStatus, CompanyDocumentType
from domain.a_share.models import (
    AnnouncementItem,
    CompanyOperatingMetricObservation,
    CompanyOperatingMetricsSnapshot,
    DividendRecord,
    DocumentParseReceipt,
    InteractiveQAItem,
    UnlockRecord,
)
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
from domain.common.values import parse_instrument_id
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    content_type_matches,
    loads_json_decimal,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_nonnegative_exact_int,
    sanitize_public_url,
)
from infrastructure.providers.a_share.cninfo_org_map import (
    load_cninfo_org_map,
    require_org_id,
)
from infrastructure.providers.a_share.company_operating_parser import (
    PARSER_VERSION,
    classify_document_type,
    is_relevant_operating_title,
    parse_company_operating_text,
)
from infrastructure.system.clock import SystemClock

_ANNOUNCEMENTS_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
# Live-verified 2026-07-17 IRM interactive Q&A search (POST JSON).
_IRM_SEARCH_URL = "https://irm.cninfo.com.cn/newircs/index/search"
_JSON_CONTENT = ("application/json", "text/json", "text/plain")
_PDF_CONTENT = ("application/pdf", "application/octet-stream", "binary/octet-stream")
_PDF_MAGIC = b"%PDF"
_MIN_PDF_BYTES = 100
_MAX_PDF_BYTES = 20_000_000
_OFFICIAL_PDF_HOST = "static.cninfo.com.cn"
_OFFICIAL_PDF_PATH = re.compile(
    r"^/finalpage/\d{4}-\d{2}-\d{2}/\d+\.pdf$",
    re.IGNORECASE,
)
_MAX_OBSERVATIONS = 200
_OPERATING_SEARCH_TERMS = (
    "销售简报",
    "经营简报",
    "产销快报",
    "月度经营",
    "业绩预告",
    "季度报告",
    "半年度报告",
    "年度报告",
)
_SEARCH_HIGHLIGHT_TAG = re.compile(r"</?em>", re.IGNORECASE)

_COLUMN_BY_SUFFIX: Mapping[str, str] = {
    "SH": "sse",
    "SZ": "szse",
    "BJ": "bjse",
}

_SUPPORTED_CATEGORIES = frozenset(
    {
        DataCategory.ANNOUNCEMENTS,
        DataCategory.INTERACTIVE_QA,
        DataCategory.COMPANY_OPERATING_METRICS,
    }
)


class CninfoAShareAdapter:
    """CategoryProvider implementing announcements only (disclosure primary)."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        current_window_seconds: int = 300,
        org_id_map: Mapping[str, str] | None = None,
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
        self._current_window_seconds = require_nonnegative_exact_int(
            current_window_seconds, field="current_window_seconds"
        )
        # Injectable map only for tests; production loads the static inventory.
        if org_id_map is not None:
            self._org_id_map = dict(org_id_map)
        else:
            self._org_id_map = dict(load_cninfo_org_map())

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.CNINFO

    @property
    def provider_name(self) -> str:
        return VendorId.CNINFO.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in _SUPPORTED_CATEGORIES

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Cninfo A-share adapter is disabled",
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

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Cninfo rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Cninfo access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Cninfo HTTP failure",
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
        as_of: datetime,
        fetched_at: datetime,
        warnings: tuple[str, ...] = (),
        category: DataCategory = DataCategory.ANNOUNCEMENTS,
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=SourceRole.PRIMARY,
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

    def _resolve_org(self, code6: str, suffix: str) -> tuple[str, str]:
        org_id = require_org_id(self._org_id_map, code6, vendor=self.vendor_id.value)
        column = _COLUMN_BY_SUFFIX.get(suffix)
        if column is None:
            raise DataContractError(
                "unsupported exchange suffix for cninfo",
                details={"field": "symbol", "rule": "exchange_suffix"},
            )
        return org_id, column

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
        # Fail before network when mapping is missing.
        org_id, column = self._resolve_org(code6, suffix)
        # Strict POST form contract (application/x-www-form-urlencoded).
        form = {
            "stock": f"{code6},{org_id}",
            "tabName": "fulltext",
            "pageSize": str(limit),
            "pageNum": "1",
            "column": column,
            "category": "",
            "plate": "",
            "seDate": "",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        body = urlencode(form).encode("utf-8")
        response = await self._transport.send(
            HttpRequest(
                method="POST",
                url=_ANNOUNCEMENTS_URL,
                params={},
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "User-Agent": self._user_agent,
                    "Referer": "http://www.cninfo.com.cn/",
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="announcements")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "Cninfo response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "announcements",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        items, unknown_excluded = self._parse_announcements(
            payload, limit=limit, as_of=as_of, now=now
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        warnings: tuple[str, ...] = ()
        if unknown_excluded:
            warnings = ("PUBLICATION_TIME_UNKNOWN_EXCLUDED",)
        # Legitimate empty list is success.
        return ProviderSuccess(
            value=tuple(items),
            meta=self._meta(as_of=as_of, fetched_at=fetched_at, warnings=warnings),
        )

    def _parse_announcements(
        self,
        payload: object,
        *,
        limit: int,
        as_of: datetime,
        now: datetime,
    ) -> tuple[list[AnnouncementItem], bool]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Cninfo announcements payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        # Legitimate no-data: explicit empty announcements array.
        announcements = payload.get("announcements")
        if announcements is None:
            # Some responses wrap under data.
            data = payload.get("data")
            if isinstance(data, dict):
                announcements = data.get("announcements")
            elif isinstance(data, list):
                announcements = data
        if announcements is None and payload.get("totalAnnouncement") == 0:
            # Live CNINFO search uses null, not [], for a legitimate zero-hit page.
            announcements = []
        if announcements is None:
            raise DataContractError(
                "Cninfo announcements payload missing announcements field",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        if not isinstance(announcements, list):
            raise DataContractError(
                "Cninfo announcements must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "announcements",
                    "rule": "contract_drift",
                },
            )
        items: list[AnnouncementItem] = []
        unknown_excluded = False
        for idx, row in enumerate(announcements):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "Cninfo announcement row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key_raw = row.get("announcementId") or row.get("id") or row.get("adjunctUrl")
            title_raw = row.get("announcementTitle") or row.get("title")
            if not isinstance(key_raw, str) or not key_raw.strip():
                raise DataContractError(
                    "Cninfo announcement missing key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            if not isinstance(title_raw, str) or not title_raw.strip():
                raise DataContractError(
                    "Cninfo announcement missing title",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            pub_raw = row.get("announcementTime") or row.get("publishTime")
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            keep, excluded = publication_cutoff_keep(
                published_at,
                as_of=as_of,
                now=now,
                current_window_seconds=self._current_window_seconds,
            )
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            if published_at is None:
                # Current-query only path: still need a concrete datetime for domain.
                # Design: never relabel unknown as fetched_at — exclude rather than invent.
                unknown_excluded = True
                continue
            adjunct = row.get("adjunctUrl")
            if isinstance(adjunct, str) and adjunct.strip():
                if adjunct.startswith("http"):
                    source_url = sanitize_public_url(adjunct, field="source_url")
                else:
                    source_url = sanitize_public_url(
                        f"http://static.cninfo.com.cn/{adjunct.lstrip('/')}",
                        field="source_url",
                    )
            else:
                source_url = sanitize_public_url(
                    f"http://www.cninfo.com.cn/new/disclosure/detail?announceId={key_raw.strip()}",
                    field="source_url",
                )
            if source_url is None:
                raise DataContractError(
                    "Cninfo announcement source_url invalid",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "announcements",
                        "rule": "contract_drift",
                    },
                )
            pdf_url = None
            if isinstance(adjunct, str) and adjunct.lower().endswith(".pdf"):
                if adjunct.startswith("http"):
                    pdf_url = sanitize_public_url(adjunct, field="pdf_url")
                else:
                    pdf_url = sanitize_public_url(
                        f"http://static.cninfo.com.cn/{adjunct.lstrip('/')}",
                        field="pdf_url",
                    )
            category = row.get("announcementTypeName") or row.get("category")
            if category is not None and not isinstance(category, str):
                category = None
            items.append(
                AnnouncementItem(
                    announcement_key=key_raw.strip()[:200],
                    title=title_raw.strip()[:500],
                    published_at=published_at,
                    category=category.strip()[:100] if isinstance(category, str) else None,
                    source_url=source_url,
                    pdf_url=pdf_url,
                )
            )
        # Deterministic order: published_at desc, key asc.
        items.sort(key=lambda a: (-a.published_at.timestamp(), a.announcement_key))
        return items, unknown_excluded

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
        # Protocol presence only — corporate actions are Eastmoney-owned.
        raise DataContractError(
            "cninfo does not implement corporate actions",
            details={
                "vendor": self.vendor_id.value,
                "operation": "corporate_actions",
                "rule": "unsupported",
                "category": DataCategory.CORPORATE_ACTIONS.value,
            },
        )

    async def get_interactive_qa(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[InteractiveQAItem, ...]]:
        """IRM interactive Q&A for one instrument.

        Live-verified 2026-07-17: POST ``/newircs/index/search`` returns
        question (mainContent/pubDate) and optional answer
        (attachedContent/attachedPubDate). ``answered_at`` is required and must
        be ``<= as_of``; unknown ``asked_at`` is allowed.
        """
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        code6, _suffix = require_a_share_instrument(instrument)
        # searchkey is the live-observed filter field for stock code text.
        body_obj = {
            "pageNo": 1,
            "pageSize": limit,
            "searchkey": code6,
        }
        body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = await self._transport.send(
            HttpRequest(
                method="POST",
                url=_IRM_SEARCH_URL,
                params={},
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                    "Referer": "https://irm.cninfo.com.cn/",
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="interactive_qa")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "Cninfo IRM Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "interactive_qa",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        items = self._parse_interactive_qa(
            payload,
            code6=code6,
            limit=limit,
            as_of=as_of,
            now=now,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(items),
            meta=self._meta(
                as_of=as_of,
                fetched_at=fetched_at,
                category=DataCategory.INTERACTIVE_QA,
            ),
        )

    def _parse_interactive_qa(
        self,
        payload: object,
        *,
        code6: str,
        limit: int,
        as_of: datetime,
        now: datetime,
    ) -> list[InteractiveQAItem]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Cninfo IRM payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "interactive_qa",
                    "rule": "contract_drift",
                },
            )
        results = payload.get("results")
        if results is None:
            return []
        if not isinstance(results, list):
            raise DataContractError(
                "Cninfo IRM results failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "interactive_qa",
                    "rule": "contract_drift",
                },
            )
        items: list[InteractiveQAItem] = []
        seen_keys: set[str] = set()
        for idx, row in enumerate(results):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "Cninfo IRM row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "interactive_qa",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            stock_code = row.get("stockCode")
            if not isinstance(stock_code, str) or not stock_code.strip():
                # Skip non-instrument rows rather than invent identity.
                continue
            if stock_code.strip().zfill(6) != code6:
                continue
            # Answered Q&A only: attachedContent + attachedPubDate required.
            answer_raw = row.get("attachedContent")
            answered_raw = row.get("attachedPubDate")
            if not isinstance(answer_raw, str):
                continue
            if answered_raw is None:
                continue
            answered_at = self._irm_ms_to_datetime(
                answered_raw, field=f"results[{idx}].attachedPubDate"
            )
            if answered_at is None:
                continue
            if answered_at > as_of:
                continue
            # asked_at optional; unknown allowed.
            asked_at = self._irm_ms_to_datetime(row.get("pubDate"), field=f"results[{idx}].pubDate")
            question_raw = row.get("mainContent")
            if not isinstance(question_raw, str) or not question_raw.strip():
                raise DataContractError(
                    "Cninfo IRM row missing question",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "interactive_qa",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key_raw = row.get("esId") or row.get("indexId")
            if not isinstance(key_raw, str) or not key_raw.strip():
                raise DataContractError(
                    "Cninfo IRM row missing qa_key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "interactive_qa",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            qa_key = key_raw.strip()[:200]
            if qa_key in seen_keys:
                raise DataContractError(
                    "Cninfo IRM returned duplicate qa_key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "interactive_qa",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen_keys.add(qa_key)
            index_id = row.get("indexId")
            source_url = None
            if isinstance(index_id, str) and index_id.strip():
                source_url = sanitize_public_url(
                    f"https://irm.cninfo.com.cn/newircs/index?id={index_id.strip()}",
                    field="source_url",
                )
            items.append(
                InteractiveQAItem(
                    qa_key=qa_key,
                    question=question_raw.strip()[:8000],
                    asked_at=asked_at,
                    answer=answer_raw[:20000],
                    answered_at=answered_at,
                    source_url=source_url,
                )
            )
        # Deterministic: answered_at desc, qa_key asc.
        items.sort(key=lambda q: (-q.answered_at.timestamp(), q.qa_key))
        return items

    def _irm_ms_to_datetime(self, raw: object, *, field: str) -> datetime | None:
        if raw is None:
            return None
        if isinstance(raw, Decimal):
            if raw != raw.to_integral_value():
                raise DataContractError(
                    f"{field} must be integer milliseconds",
                    details={"field": field, "rule": "time_format"},
                )
            ms = int(raw)
        elif isinstance(raw, int) and not isinstance(raw, bool):
            ms = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            ms = int(raw.strip())
        else:
            raise DataContractError(
                f"{field} failed contract validation",
                details={"field": field, "rule": "contract_drift"},
            )
        if ms < 0:
            raise DataContractError(
                f"{field} must be nonnegative",
                details={"field": field, "rule": "nonnegative"},
            )
        # Live-observed epoch milliseconds (integer arithmetic only).
        seconds, millis = divmod(ms, 1000)
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds, milliseconds=millis)

    async def get_company_operating_metrics(
        self,
        instrument: Instrument,
        *,
        lookback_months: int,
        document_limit: int,
        metric_codes: tuple[str, ...],
        as_of: datetime,
    ) -> ProviderSuccess[CompanyOperatingMetricsSnapshot]:
        """Download and parse official disclosure PDFs into operating metrics.

        Lists publication-cutoff-safe announcements, filters relevant titles,
        downloads only official static.cninfo.com.cn finalpage PDFs, extracts
        text with pypdf, and returns a bounded snapshot. One bad document is a
        typed partial warning when another document yields data.
        """
        self._require_configured()
        if find_spec("pypdf") is None:
            raise ProviderNotConfigured(
                "CNINFO PDF extraction is unavailable; install trading-partner[company-pdf]"
            )
        self._require_as_of(as_of)
        if (
            not isinstance(lookback_months, int)
            or isinstance(lookback_months, bool)
            or lookback_months < 3
            or lookback_months > 120
        ):
            raise DataContractError(
                "lookback_months must be an int in 3..120",
                details={"field": "lookback_months", "rule": "range"},
            )
        if (
            not isinstance(document_limit, int)
            or isinstance(document_limit, bool)
            or document_limit < 1
            or document_limit > 30
        ):
            raise DataContractError(
                "document_limit must be an int in 1..30",
                details={"field": "document_limit", "rule": "range"},
            )
        if not isinstance(metric_codes, tuple) or any(
            not isinstance(code, str) for code in metric_codes
        ):
            raise DataContractError(
                "metric_codes must be a tuple of strings",
                details={"field": "metric_codes", "rule": "type"},
            )
        asset_type, market, _symbol = parse_instrument_id(instrument.instrument_id)
        if market is not Market.A_SHARE or asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "company operating metrics require equity A-share instrument",
                details={"field": "instrument_id", "rule": "equity_a_share"},
            )

        period_floor = self._lookback_floor(as_of=as_of, lookback_months=lookback_months)
        candidates, search_warnings = await self._search_operating_announcements(
            instrument,
            period_floor=period_floor,
            document_limit=document_limit,
            as_of=as_of,
        )

        documents: list[DocumentParseReceipt] = []
        observations: list[CompanyOperatingMetricObservation] = []
        partial = False
        had_sales_brief = False
        for announcement in candidates:
            receipt, metrics = await self._parse_operating_document(
                announcement,
                instrument_id=instrument.instrument_id,
            )
            documents.append(receipt)
            if receipt.status is CompanyDocumentParseStatus.PARSED:
                observations.extend(metrics)
                if receipt.document_type is CompanyDocumentType.MONTHLY_OPERATING_BRIEF:
                    had_sales_brief = True
            elif receipt.status is not CompanyDocumentParseStatus.NO_METRICS:
                partial = True

        if metric_codes:
            allowed = frozenset(metric_codes)
            filtered = [
                item
                for item in observations
                if item.period_end >= period_floor and item.metric_code in allowed
            ]
        else:
            filtered = [item for item in observations if item.period_end >= period_floor]

        # Deduplicate across documents: prefer newest published_at, then key.
        deduped = self._dedupe_observations(filtered)
        truncated = False
        if len(deduped) > _MAX_OBSERVATIONS:
            deduped = deduped[:_MAX_OBSERVATIONS]
            truncated = True
        ordered = tuple(
            sorted(
                deduped,
                key=lambda item: (
                    -item.period_end.toordinal(),
                    item.metric_code,
                    item.measurement_basis.value,
                ),
            )
        )
        present = {item.metric_code for item in ordered}
        missing = tuple(code for code in metric_codes if code not in present)
        if not ordered:
            raise NoMarketData(
                "no company operating metrics parsed from official disclosures",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "company_operating_metrics",
                    "documents_considered": len(candidates),
                },
            )

        warnings: list[str] = []
        warnings.extend(search_warnings)
        if partial:
            warnings.append("COMPANY_OPERATING_DOCUMENT_PARTIAL")
        if truncated:
            warnings.append("COMPANY_OPERATING_OBSERVATIONS_TRUNCATED")
        if had_sales_brief:
            warnings.append("COMPANY_OPERATING_UNAUDITED_SALES_BRIEF")
        # Preserve order while unique.
        unique_warnings = tuple(dict.fromkeys(warnings))

        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        snapshot = CompanyOperatingMetricsSnapshot(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            lookback_months=lookback_months,
            observations=ordered,
            documents=tuple(documents),
            missing_metric_codes=missing,
        )
        return ProviderSuccess(
            value=snapshot,
            meta=self._meta(
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=unique_warnings,
                category=DataCategory.COMPANY_OPERATING_METRICS,
            ),
        )

    async def _search_operating_announcements(
        self,
        instrument: Instrument,
        *,
        period_floor: date,
        document_limit: int,
        as_of: datetime,
    ) -> tuple[tuple[AnnouncementItem, ...], tuple[str, ...]]:
        """Search bounded title families instead of scanning the latest generic page."""
        code6, suffix = require_a_share_instrument(instrument)
        org_id, column = self._resolve_org(code6, suffix)
        now = self._require_as_of(as_of)
        by_key: dict[str, AnnouncementItem] = {}
        failures = 0
        unknown_excluded = False
        date_window = f"{period_floor.isoformat()}~{as_of.date().isoformat()}"
        for term in _OPERATING_SEARCH_TERMS:
            form = {
                "stock": f"{code6},{org_id}",
                "tabName": "fulltext",
                "pageSize": "30",
                "pageNum": "1",
                "column": column,
                "category": "",
                "plate": "",
                "seDate": date_window,
                "searchkey": term,
                "secid": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            try:
                response = await self._transport.send(
                    HttpRequest(
                        method="POST",
                        url=_ANNOUNCEMENTS_URL,
                        params={},
                        headers={
                            "Accept": "application/json,text/plain,*/*",
                            "Content-Type": ("application/x-www-form-urlencoded; charset=UTF-8"),
                            "User-Agent": self._user_agent,
                            "Referer": "http://www.cninfo.com.cn/",
                        },
                        body=urlencode(form).encode("utf-8"),
                        timeout_seconds=self._timeout_seconds,
                    )
                )
                self._raise_for_http_status(
                    response.status_code,
                    operation="company_operating_announcements",
                )
                if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
                    raise DataContractError(
                        "Cninfo operating-announcement Content-Type is not acceptable"
                    )
                payload = loads_json_decimal(response.body)
                items, excluded = self._parse_announcements(
                    payload,
                    limit=30,
                    as_of=as_of,
                    now=now,
                )
                unknown_excluded = unknown_excluded or excluded
            except (
                ProviderRateLimitError,
                ProviderUnavailableError,
                DataContractError,
            ):
                failures += 1
                continue
            for item in items:
                clean_title = _SEARCH_HIGHLIGHT_TAG.sub("", item.title)
                clean = replace(item, title=clean_title)
                if (
                    clean.published_at.date() >= period_floor
                    and clean.pdf_url is not None
                    and is_relevant_operating_title(clean.title)
                ):
                    by_key[clean.announcement_key] = clean

        if not by_key:
            if failures == len(_OPERATING_SEARCH_TERMS):
                raise ProviderUnavailableError(
                    "all Cninfo operating-announcement searches failed",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "company_operating_announcements",
                    },
                )
            return (), ()
        ordered = tuple(
            sorted(
                by_key.values(),
                key=lambda item: (item.published_at, item.announcement_key),
                reverse=True,
            )[:document_limit]
        )
        warnings: list[str] = []
        if failures:
            warnings.append("COMPANY_OPERATING_ANNOUNCEMENT_SEARCH_PARTIAL")
        if unknown_excluded:
            warnings.append("PUBLICATION_TIME_UNKNOWN_EXCLUDED")
        return ordered, tuple(warnings)

    async def _parse_operating_document(
        self,
        announcement: AnnouncementItem,
        *,
        instrument_id: str,
    ) -> tuple[DocumentParseReceipt, tuple[CompanyOperatingMetricObservation, ...]]:
        doc_type = classify_document_type(announcement.title)
        pdf_url = announcement.pdf_url

        def receipt(
            status: CompanyDocumentParseStatus,
            warning_code: str | None,
            *,
            page_count: int | None = None,
            extracted_metric_count: int = 0,
        ) -> DocumentParseReceipt:
            return DocumentParseReceipt(
                announcement_key=announcement.announcement_key,
                title=announcement.title,
                document_type=doc_type,
                published_at=announcement.published_at,
                source_url=announcement.source_url,
                pdf_url=pdf_url,
                parser_version=PARSER_VERSION,
                page_count=page_count,
                status=status,
                extracted_metric_count=extracted_metric_count,
                warning_code=warning_code,
            )

        if pdf_url is None or not self._is_official_finalpage_pdf(pdf_url):
            return (
                receipt(
                    CompanyDocumentParseStatus.UNSUPPORTED_URL,
                    "COMPANY_OPERATING_UNSUPPORTED_PDF_URL",
                ),
                (),
            )
        try:
            body, content_type = await self._download_pdf(pdf_url)
        except (
            ProviderRateLimitError,
            ProviderUnavailableError,
            DataContractError,
            ProviderNotConfigured,
        ):
            return (
                receipt(
                    CompanyDocumentParseStatus.DOWNLOAD_FAILED,
                    "COMPANY_OPERATING_PDF_DOWNLOAD_FAILED",
                ),
                (),
            )
        if not self._validate_pdf_body(body, content_type=content_type):
            return (
                receipt(
                    CompanyDocumentParseStatus.INVALID_PDF,
                    "COMPANY_OPERATING_INVALID_PDF",
                ),
                (),
            )
        try:
            text, page_count = self._extract_pdf_text(body)
        except Exception:
            return (
                receipt(
                    CompanyDocumentParseStatus.PARSE_FAILED,
                    "COMPANY_OPERATING_PDF_TEXT_FAILED",
                ),
                (),
            )
        try:
            metrics = parse_company_operating_text(
                text,
                instrument_id=instrument_id,
                title=announcement.title,
                published_at=announcement.published_at,
                source_url=announcement.source_url,
                pdf_url=pdf_url,
                announcement_key=announcement.announcement_key,
            )
        except DataContractError:
            return (
                receipt(
                    CompanyDocumentParseStatus.PARSE_FAILED,
                    "COMPANY_OPERATING_PARSE_FAILED",
                    page_count=page_count,
                ),
                (),
            )
        if not metrics:
            return (
                receipt(
                    CompanyDocumentParseStatus.NO_METRICS,
                    "COMPANY_OPERATING_NO_METRICS",
                    page_count=page_count,
                ),
                (),
            )
        return (
            receipt(
                CompanyDocumentParseStatus.PARSED,
                None,
                page_count=page_count,
                extracted_metric_count=len(metrics),
            ),
            metrics,
        )

    async def _download_pdf(self, pdf_url: str) -> tuple[bytes, str | None]:
        # Prefer https official static host; accept already-validated absolute URL.
        url = pdf_url
        parts = urlsplit(url)
        if parts.scheme == "http":
            url = f"https://{parts.netloc}{parts.path}"
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params={},
                headers={
                    "Accept": "application/pdf,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "http://www.cninfo.com.cn/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="company_operating_pdf")
        content_type = None
        if isinstance(response.headers, Mapping):
            raw_ct = response.headers.get("content-type") or response.headers.get("Content-Type")
            if isinstance(raw_ct, str):
                content_type = raw_ct
        return response.body, content_type

    @staticmethod
    def _is_official_finalpage_pdf(url: str) -> bool:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        host = (parts.hostname or "").lower()
        if host != _OFFICIAL_PDF_HOST:
            return False
        if parts.scheme not in {"http", "https"}:
            return False
        return _OFFICIAL_PDF_PATH.fullmatch(parts.path or "") is not None

    @staticmethod
    def _validate_pdf_body(body: bytes, *, content_type: str | None) -> bool:
        if not isinstance(body, (bytes, bytearray)):
            return False
        if len(body) < _MIN_PDF_BYTES or len(body) > _MAX_PDF_BYTES:
            return False
        if not bytes(body[:4]).startswith(_PDF_MAGIC):
            return False
        if content_type is not None and content_type.strip():
            lower = content_type.casefold()
            if not any(token in lower for token in _PDF_CONTENT) and any(
                token in lower for token in ("html", "json", "text/plain")
            ):
                return False
        return True

    @staticmethod
    def _extract_pdf_text(body: bytes) -> tuple[str, int]:
        # Import locally so unit tests that only exercise announcement listing
        # do not require pypdf at import time of unrelated paths.
        from pypdf import PdfReader  # noqa: PLC0415

        reader = PdfReader(io.BytesIO(body), strict=False)
        pages = list(reader.pages)
        chunks: list[str] = []
        for page in pages:
            extracted = page.extract_text() or ""
            if extracted:
                chunks.append(extracted)
        return "\n".join(chunks), len(pages)

    @staticmethod
    def _lookback_floor(*, as_of: datetime, lookback_months: int) -> date:
        # Inclusive month window ending at as_of's calendar month.
        year = as_of.year
        month = as_of.month - (lookback_months - 1)
        while month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)

    @staticmethod
    def _dedupe_observations(
        items: list[CompanyOperatingMetricObservation],
    ) -> list[CompanyOperatingMetricObservation]:
        best: dict[tuple[str, date, str], CompanyOperatingMetricObservation] = {}
        for item in items:
            key = (item.metric_code, item.period_end, item.measurement_basis.value)
            existing = best.get(key)
            if existing is None or item.published_at > existing.published_at:
                best[key] = item
        return list(best.values())
