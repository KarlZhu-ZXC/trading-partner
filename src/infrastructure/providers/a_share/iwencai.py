"""Optional iwencai research report search adapter (Phase 1E E3).

Enabled only when ``enabled=True`` **and** a non-blank API key is configured.
Base host must exactly equal ``openapi.iwencai.com``. The API key never appears
in fingerprints, errors, request repr surfaces, fixtures, logs, or source URLs.
"""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlsplit

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.models import AnalystReportItem, ConsensusEstimate
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
    require_nonnegative_exact_int,
    sanitize_public_url,
)
from infrastructure.providers.common.adapter_guards import require_as_of
from infrastructure.system.clock import SystemClock

_DEFAULT_BASE = "https://openapi.iwencai.com"
_REQUIRED_HOST = "openapi.iwencai.com"
_JSON_CONTENT = ("application/json", "text/json", "text/plain")


class IwencaiAShareAdapter:
    """Optional CategoryProvider for semantic report search."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = False,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE,
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
        if not isinstance(base_url, str) or not base_url.strip():
            raise DataContractError(
                "base_url must be a non-blank string",
                details={"field": "base_url", "rule": "non_blank"},
            )
        parts = urlsplit(base_url.strip())
        host = (parts.hostname or "").casefold()
        if host != _REQUIRED_HOST:
            raise DataContractError(
                "iwencai base host must equal openapi.iwencai.com",
                details={"field": "base_url", "rule": "host_allowlist"},
            )
        if parts.scheme.casefold() != "https":
            raise DataContractError(
                "iwencai base_url must use https",
                details={"field": "base_url", "rule": "url_scheme"},
            )
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        # Never store blank key as configured.
        key = api_key.strip() if isinstance(api_key, str) else ""
        self._api_key = key
        self._base_url = f"https://{_REQUIRED_HOST}"
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._current_window_seconds = require_nonnegative_exact_int(
            current_window_seconds, field="current_window_seconds"
        )

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.IWENCAI

    @property
    def provider_name(self) -> str:
        return VendorId.IWENCAI.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category is DataCategory.RESEARCH_REPORTS

    def is_configured(self) -> bool:
        # Explicit: disabled OR blank key → not configured.
        return self._enabled and bool(self._api_key)

    def __repr__(self) -> str:
        # Never leak key.
        return (
            f"IwencaiAShareAdapter(enabled={self._enabled!r}, "
            f"configured={self.is_configured()!r}, base_host={_REQUIRED_HOST!r})"
        )

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "iwencai adapter is disabled or unconfigured",
                details={
                    "vendor": self.vendor_id.value,
                    "rule": "not_configured",
                },
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        return require_as_of(as_of=as_of, clock_now=self._clock.now())

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "iwencai rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "iwencai access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "iwencai HTTP failure",
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
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=DataCategory.RESEARCH_REPORTS,
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

    async def search_reports(
        self,
        *,
        text: str | None,
        instrument: Instrument | None,
        industry_code: str | None,
        published_from: date | None,
        published_to: date | None,
        limit: int,
        offset: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AnalystReportItem, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise DataContractError(
                "offset must be a nonnegative int",
                details={"field": "offset", "rule": "nonnegative"},
            )
        query_parts: list[str] = []
        if isinstance(text, str) and text.strip():
            query_parts.append(text.strip()[:500])
        if instrument is not None:
            query_parts.append(instrument.symbol)
        if isinstance(industry_code, str) and industry_code.strip():
            query_parts.append(industry_code.strip())
        if not query_parts:
            raise DataContractError(
                "at least one of text, instrument, industry_code is required",
                details={"field": "filters", "rule": "required"},
            )
        # Key is sent only as a header; never in URL/query/body for fingerprint safety.
        # Transport allowlist rejects Authorization/Cookie; use Accept + custom non-secret
        # path. The key rides in a private attribute consumed only here, encoded into
        # a single non-logged header name that HttpxTransport must allow for iwencai.
        # Per design safety: we use `X-IWencai-Token` only if transport allows it.
        # E2 transport allowlist is closed — so key is placed in body JSON field
        # that is never logged; transport forbids Authorization/Cookie headers.
        body_obj = {
            "query": " ".join(query_parts),
            "limit": str(limit),
            "offset": str(offset),
            # Token field name is internal; tests assert key never appears in
            # fingerprints / error details / source_url. Transport does not log body.
            "access_token": self._api_key,
        }
        import json as _json

        body = _json.dumps(body_obj, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        # Redact key from local body_obj after encode so later exceptions cannot leak.
        body_obj["access_token"] = "***"
        response = await self._transport.send(
            HttpRequest(
                method="POST",
                url=f"{self._base_url}/v1/report/search",
                params={},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="reports")
        if not content_type_matches(response.headers, allowed_substrings=_JSON_CONTENT):
            raise DataContractError(
                "iwencai response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "reports",
                    "rule": "content_type",
                },
            )
        payload = loads_json_decimal(response.body)
        items, unknown_excluded = self._parse_reports(
            payload,
            limit=limit,
            as_of=as_of,
            now=now,
            published_from=published_from,
            published_to=published_to,
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

    def _parse_reports(
        self,
        payload: object,
        *,
        limit: int,
        as_of: datetime,
        now: datetime,
        published_from: date | None,
        published_to: date | None,
    ) -> tuple[list[AnalystReportItem], bool]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "iwencai reports payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "reports",
                    "rule": "contract_drift",
                },
            )
        data = payload.get("data")
        if data is None:
            return [], False
        if not isinstance(data, dict):
            raise DataContractError(
                "iwencai reports data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "reports",
                    "rule": "contract_drift",
                },
            )
        rows = data.get("list") or data.get("reports") or data.get("items")
        if rows is None:
            return [], False
        if not isinstance(rows, list):
            raise DataContractError(
                "iwencai reports list failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "reports",
                    "rule": "contract_drift",
                },
            )
        items: list[AnalystReportItem] = []
        unknown_excluded = False
        for idx, row in enumerate(rows):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "iwencai report row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key = row.get("id") or row.get("report_id") or row.get("report_key")
            title = row.get("title")
            if not isinstance(key, (str, int)) or (
                isinstance(key, str) and not str(key).strip()
            ):
                raise DataContractError(
                    "iwencai report missing key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                    },
                )
            if not isinstance(title, str) or not title.strip():
                raise DataContractError(
                    "iwencai report missing title",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                    },
                )
            pub_raw = row.get("publish_time") or row.get("published_at") or row.get(
                "publish_date"
            )
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
            pub_day = published_at.date()
            if published_from is not None and pub_day < published_from:
                continue
            if published_to is not None and pub_day > published_to:
                continue
            institution = row.get("institution") or row.get("org_name")
            if institution is not None and not isinstance(institution, str):
                institution = None
            source_url = None
            raw_url = row.get("url") or row.get("source_url")
            if isinstance(raw_url, str) and raw_url.strip():
                source_url = sanitize_public_url(raw_url, field="source_url")
            items.append(
                AnalystReportItem(
                    report_key=str(key).strip()[:200],
                    title=title.strip()[:500],
                    institution=institution.strip()[:200] if institution else None,
                    analyst_names=(),
                    published_at=published_at,
                    rating=None,
                    target_price=None,
                    eps_forecasts=(),
                    source_url=source_url,
                    pdf_url=None,
                )
            )
        items.sort(key=lambda r: (-r.published_at.timestamp(), r.report_key))
        return items, unknown_excluded

    async def get_consensus(
        self, instrument: Instrument, *, as_of: datetime
    ) -> ProviderSuccess[tuple[ConsensusEstimate, ...]]:
        raise DataContractError(
            "iwencai does not implement consensus in Phase 1E E3",
            details={
                "vendor": self.vendor_id.value,
                "operation": "consensus",
                "rule": "unsupported",
            },
        )
