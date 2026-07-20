"""Cninfo A-share disclosure + interactive QA adapter (Phase 1E E3 + E4b).

Primary owner of announcements via
``www.cninfo.com.cn/new/hisAnnouncement/query``.

E4b interactive QA via live-verified 2026-07-17
``irm.cninfo.com.cn/newircs/index/search`` (POST JSON).

Corporate actions are **not** claimed (Eastmoney-owned). The protocol method
exists only so ``isinstance(..., AShareDisclosureProvider)`` can narrow; it
raises a typed unsupported contract error.

OrgId resolution uses the official static CNINFO inventory (vendored map).
Runtime never synthesizes orgId from code.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.models import (
    AnnouncementItem,
    DividendRecord,
    InteractiveQAItem,
    UnlockRecord,
)
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
    publication_cutoff_keep,
    require_a_share_instrument,
    require_nonnegative_exact_int,
    sanitize_public_url,
)
from infrastructure.providers.a_share.cninfo_org_map import (
    load_cninfo_org_map,
    require_org_id,
)
from infrastructure.system.clock import SystemClock

_ANNOUNCEMENTS_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
# Live-verified 2026-07-17 IRM interactive Q&A search (POST JSON).
_IRM_SEARCH_URL = "https://irm.cninfo.com.cn/newircs/index/search"
_JSON_CONTENT = ("application/json", "text/json", "text/plain")

_COLUMN_BY_SUFFIX: Mapping[str, str] = {
    "SH": "sse",
    "SZ": "szse",
    "BJ": "bjse",
}

_SUPPORTED_CATEGORIES = frozenset(
    {
        DataCategory.ANNOUNCEMENTS,
        DataCategory.INTERACTIVE_QA,
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
        org_id = require_org_id(
            self._org_id_map, code6, vendor=self.vendor_id.value
        )
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
        body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
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
            asked_at = self._irm_ms_to_datetime(
                row.get("pubDate"), field=f"results[{idx}].pubDate"
            )
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
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=seconds, milliseconds=millis
        )
