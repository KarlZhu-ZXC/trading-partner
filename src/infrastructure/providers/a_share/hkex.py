"""HKEX A-share northbound daily adapter (Phase 1E E4a).

Primary owner of market-scope northbound daily statistics via the frozen
§20 path family:
``www.hkex.com.hk/chi/csm/DailyStat/data_tab_daily_{YYYYMMDD}c.js``

Parses the official ``tabData = [...]`` assignment (UTF-8 BOM allowed).
Post-change Northbound style=1 tables expose aggregate fields only
(Total Turnover, Total Trade Count, DQB, ETF Turnover) — not buy/sell/net.
Does not claim other capital methods.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.models import NorthboundFlowPoint
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
from domain.market.session import infer_session_basic
from infrastructure.providers.a_share._parsing import (
    SHANGHAI,
    content_type_matches,
    loads_json_decimal,
    parse_shanghai_date,
)
from infrastructure.providers.common.adapter_guards import require_as_of
from infrastructure.system.clock import SystemClock

_HKEX_DAILY_URL_TMPL = (
    "https://www.hkex.com.hk/chi/csm/DailyStat/data_tab_daily_{yyyymmdd}c.js"
)
_JS_CONTENT = (
    "application/javascript",
    "text/javascript",
    "application/json",
    "text/plain",
    "text/js",
)
_HTML_CONTENT = ("text/html", "application/xhtml")

_TABDATA_ASSIGN_RE = re.compile(
    r"^\s*tabData\s*=\s*",
    re.IGNORECASE | re.DOTALL,
)

_NORTHBOUND_MARKET_TO_CHANNEL: dict[str, str] = {
    "SSE Northbound": "sh",
    "SZSE Northbound": "sz",
}

_DISCLOSURE_NOTE = (
    "HKEX CSM DailyStat Northbound style=1 post-change disclosure is aggregate-only "
    "(Total Turnover, Total Trade Count, DQB, ETF Turnover); buy/sell/net are not disclosed."
)

_STYLE1_SCHEMA = ("Total Turnover", "Total Trade Count", "DQB", "ETF Turnover")


class HkexNorthboundAdapter:
    """CategoryProvider implementing AShareNorthboundProvider only."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
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

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.HKEX

    @property
    def provider_name(self) -> str:
        return VendorId.HKEX.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category is DataCategory.CAPITAL

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "HKEX northbound adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        return require_as_of(as_of=as_of, clock_now=self._clock.now())

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "HKEX rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                    "status_class": "4xx",
                },
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "HKEX access blocked",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "blocked",
                    "status_class": "4xx",
                },
            )
        if status_code == 404:
            # No daily file for that date — legitimate empty for that day.
            return
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "HKEX HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    def _meta(
        self, *, as_of: datetime, fetched_at: datetime, warnings: tuple[str, ...] = ()
    ) -> ProviderResultMeta:
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=DataCategory.CAPITAL,
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

    async def get_northbound(
        self, *, start: date | None, end: date | None, as_of: datetime
    ) -> ProviderSuccess[tuple[NorthboundFlowPoint, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        as_of_day = as_of.astimezone(SHANGHAI).date()
        end_day = end if end is not None else as_of_day
        if end_day > as_of_day:
            end_day = as_of_day
        start_day = start if start is not None else end_day - timedelta(days=7)
        if start_day > end_day:
            return ProviderSuccess(
                value=(),
                meta=self._meta(as_of=as_of, fetched_at=self._clock.now()),
            )

        points: list[NorthboundFlowPoint] = []
        day = start_day
        while day <= end_day:
            day_points = await self._fetch_day(day)
            points.extend(day_points)
            day = day + timedelta(days=1)

        points.sort(key=lambda p: (p.trade_date, p.channel))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        warnings: tuple[str, ...] = ()
        if points:
            warnings = ("NORTHBOUND_DISCLOSURE_INCOMPLETE",)
        return ProviderSuccess(
            value=tuple(points),
            meta=self._meta(as_of=as_of, fetched_at=fetched_at, warnings=warnings),
        )

    async def _fetch_day(self, day: date) -> list[NorthboundFlowPoint]:
        ymd = day.strftime("%Y%m%d")
        url = _HKEX_DAILY_URL_TMPL.format(yyyymmdd=ymd)
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params={},
                headers={
                    "Accept": "application/javascript,text/javascript,text/plain,*/*",
                    "User-Agent": self._user_agent,
                    "Referer": "https://www.hkex.com.hk/",
                },
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="northbound")
        if response.status_code == 404:
            return []
        # HTML is no-data / fallback path — never parse.
        if content_type_matches(response.headers, allowed_substrings=_HTML_CONTENT):
            return []
        if not content_type_matches(response.headers, allowed_substrings=_JS_CONTENT):
            body_preview = response.body.lstrip()[:64]
            # BOM + tabData may arrive with missing content-type.
            if not (
                body_preview.startswith(b"\xef\xbb\xbf")
                or body_preview.startswith(b"var")
                or body_preview.lower().startswith(b"tabdata")
                or b"tabData" in body_preview
            ):
                # Non-JS body (e.g. HTML error page without content-type) → empty.
                lower = body_preview.lstrip().lower()
                if lower.startswith(b"<!doctype") or lower.startswith(b"<html"):
                    return []
                raise DataContractError(
                    "HKEX response Content-Type is not acceptable",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "content_type",
                    },
                )
        try:
            text = response.body.decode("utf-8-sig")
        except UnicodeDecodeError:
            return []
        return self._parse_daily_js(text, trade_date=day)

    def _parse_daily_js(
        self, text: str, *, trade_date: date
    ) -> list[NorthboundFlowPoint]:
        """Strict parse of official ``tabData = [...]`` assignment.

        Only market names ending with ``Northbound`` that map to ``sh``/``sz``
        are emitted. Buy/sell/net are always None under post-change style=1
        aggregate-only disclosure. Fabricated totals are never emitted.
        Structural drift fails closed. HTML/empty is no-data.
        """
        stripped = text.lstrip("\ufeff").strip()
        if not stripped:
            return []
        lower = stripped[:64].lower()
        if lower.startswith("<!doctype") or lower.startswith("<html") or lower.startswith(
            "<"
        ):
            return []

        match = _TABDATA_ASSIGN_RE.match(stripped)
        if match is None:
            raise DataContractError(
                "HKEX daily stat payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                },
            )
        json_text = stripped[match.end() :].strip()
        if json_text.endswith(";"):
            json_text = json_text[:-1].rstrip()
        try:
            payload = loads_json_decimal(json_text.encode("utf-8"))
        except DataContractError:
            raise DataContractError(
                "HKEX daily stat tabData JSON failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                },
            ) from None

        if not isinstance(payload, list):
            raise DataContractError(
                "HKEX tabData must be a JSON array",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                },
            )

        points: list[NorthboundFlowPoint] = []
        seen_channels: set[str] = set()
        for idx, entry in enumerate(payload):
            if not isinstance(entry, dict):
                raise DataContractError(
                    "HKEX tabData entry failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            market = entry.get("market")
            if not isinstance(market, str) or not market.strip():
                raise DataContractError(
                    "HKEX tabData market must be a non-empty string",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            market_name = market.strip()
            # Southbound and any non-Northbound markets are ignored.
            if not market_name.endswith("Northbound"):
                continue

            channel = _NORTHBOUND_MARKET_TO_CHANNEL.get(market_name)
            if channel is None:
                raise DataContractError(
                    "HKEX Northbound market name is not allowlisted",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )

            date_raw = entry.get("date")
            if not isinstance(date_raw, str) or not date_raw.strip():
                raise DataContractError(
                    "HKEX tabData date missing",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            entry_day = parse_shanghai_date(date_raw)
            if entry_day != trade_date:
                raise DataContractError(
                    "HKEX tabData date must match requested trade date",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )

            self._require_northbound_style1(entry, index=idx)

            if channel in seen_channels:
                raise DataContractError(
                    "HKEX Northbound channel must be unique per day",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            seen_channels.add(channel)
            points.append(
                NorthboundFlowPoint(
                    trade_date=trade_date,
                    channel=channel,
                    net_buy_cny=None,
                    buy_cny=None,
                    sell_cny=None,
                    disclosure_note=_DISCLOSURE_NOTE,
                    source_vendor=VendorId.HKEX,
                    reliability=ReliabilityLevel.HIGH,
                    is_authoritative=True,
                )
            )

        points.sort(key=lambda p: (p.trade_date, p.channel))
        return points

    def _require_northbound_style1(self, entry: dict[str, Any], *, index: int) -> None:
        """Fail closed unless style=1 aggregate schema is present (not buy/sell)."""
        content = entry.get("content")
        if not isinstance(content, list) or not content:
            raise DataContractError(
                "HKEX Northbound content must be a non-empty list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        style1: dict[str, Any] | None = None
        for block in content:
            if not isinstance(block, dict):
                raise DataContractError(
                    "HKEX Northbound content block failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                        "index": index,
                    },
                )
            style = block.get("style")
            # JSON ints stay int under loads_json_decimal; Decimal only for floats.
            if style == 1 or style == "1":
                style1 = block
                break
        if style1 is None:
            raise DataContractError(
                "HKEX Northbound style=1 aggregate table missing",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        table = style1.get("table")
        if not isinstance(table, dict):
            raise DataContractError(
                "HKEX Northbound style=1 table missing",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        schema = table.get("schema")
        if not isinstance(schema, list) or not schema:
            raise DataContractError(
                "HKEX Northbound style=1 schema missing",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        header = schema[0]
        if not isinstance(header, list):
            raise DataContractError(
                "HKEX Northbound style=1 schema header invalid",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        headers = tuple(str(h).strip() for h in header)
        if headers != _STYLE1_SCHEMA:
            raise DataContractError(
                "HKEX Northbound style=1 schema drifted from aggregate-only contract",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        # Guard: buy/sell must not appear in the aggregate schema (would invite misparse).
        lowered = {h.lower() for h in headers}
        if any(token in lowered for token in ("buy turnover", "sell turnover", "net")):
            raise DataContractError(
                "HKEX Northbound style=1 must not expose buy/sell/net columns",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "northbound",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
