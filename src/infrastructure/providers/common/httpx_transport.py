"""Bounded allowlist HTTP transport (Phase 1E E2 / E4c query policy).

Implements ``HttpTransport`` with a frozen host/path allowlist (exact vs prefix),
http/https only, no credentials/fragments/raw query strings, private/loopback/
link-local IP literals denied, non-default ports denied, redirects disabled,
streaming response body bounds (Content-Length early reject + chunk max+1
cutoff), deterministic query encoding from structured params (comma preserved
in values only; keys fully encoded; no raw query overrides), and sanitized
typed errors that never echo URL/query/body/headers/cause chains.

The allowlist is fixed at module level (§20). Construction does **not** accept
arbitrary host overrides; tests must exercise official allowlisted hosts via
``httpx.MockTransport`` / custom ``httpx.AsyncClient`` transports.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final
from urllib.parse import quote_plus, unquote, urlsplit

import httpx

from application.ports.http_transport import HttpRequest, HttpResponse
from domain.common.errors import (
    ConfigurationError,
    DataContractError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)


class _MatchMode(Enum):
    EXACT = "exact"
    PREFIX = "prefix"


@dataclass(frozen=True, slots=True)
class _AllowEntry:
    host: str
    path: str
    mode: _MatchMode
    methods: frozenset[str] = frozenset({"GET", "POST"})


# Phase 1E §20 — exact endpoints vs explicit wildcard prefix families.
# Path matching uses the URL path only (query excluded). Hosts are lowercase.
_ALLOWLIST: Final[tuple[_AllowEntry, ...]] = (
    # Tencent
    _AllowEntry("qt.gtimg.cn", "/q", _MatchMode.EXACT),
    _AllowEntry("smartbox.gtimg.cn", "/s3/", _MatchMode.EXACT),
    _AllowEntry("web.ifzq.gtimg.cn", "/appstock/app/fqkline/get", _MatchMode.EXACT),
    # Eastmoney push2
    _AllowEntry("push2.eastmoney.com", "/api/qt/stock/get", _MatchMode.EXACT),
    _AllowEntry("push2.eastmoney.com", "/api/qt/stock/details/get", _MatchMode.EXACT),
    _AllowEntry("push2.eastmoney.com", "/api/qt/fflow/kline/get", _MatchMode.EXACT),
    # Live-verified stock fund-flow paths (design §20 host family; stock/ segment).
    _AllowEntry("push2.eastmoney.com", "/api/qt/stock/fflow/kline/get", _MatchMode.EXACT),
    _AllowEntry("push2.eastmoney.com", "/api/qt/clist/get", _MatchMode.EXACT),
    _AllowEntry("push2.eastmoney.com", "/api/qt/slist/get", _MatchMode.EXACT),
    # Eastmoney push2his
    _AllowEntry("push2his.eastmoney.com", "/api/qt/stock/kline/get", _MatchMode.EXACT),
    _AllowEntry("push2his.eastmoney.com", "/api/qt/fflow/daykline/get", _MatchMode.EXACT),
    _AllowEntry(
        "push2his.eastmoney.com",
        "/api/qt/stock/fflow/daykline/get",
        _MatchMode.EXACT,
    ),
    # Eastmoney datacenter / report / search / news
    _AllowEntry("datacenter-web.eastmoney.com", "/api/data/v1/get", _MatchMode.EXACT),
    _AllowEntry("reportapi.eastmoney.com", "/report/list", _MatchMode.EXACT),
    _AllowEntry("search-api-web.eastmoney.com", "/search/jsonp", _MatchMode.EXACT),
    _AllowEntry("np-weblist.eastmoney.com", "/getfastnewslist", _MatchMode.EXACT),
    # Eastmoney limit pools (public path casing; match is case-insensitive)
    _AllowEntry("push2ex.eastmoney.com", "/getTopicZTPool", _MatchMode.EXACT),
    _AllowEntry("push2ex.eastmoney.com", "/getTopicZBPool", _MatchMode.EXACT),
    _AllowEntry("push2ex.eastmoney.com", "/getTopicDTPool", _MatchMode.EXACT),
    # Live-verified 2026-07-17: previous-day limit-up pool is getYesterdayZTPool.
    # getLastZTPool returns live 404 and is not allowlisted.
    _AllowEntry("push2ex.eastmoney.com", "/getYesterdayZTPool", _MatchMode.EXACT),
    # Eastmoney stockrank exact POST-only endpoints.
    _AllowEntry(
        "emappdata.eastmoney.com",
        "/stockrank/getAllCurrentList",
        _MatchMode.EXACT,
        frozenset({"POST"}),
    ),
    _AllowEntry(
        "emappdata.eastmoney.com",
        "/stockrank/getHotStockRankList",
        _MatchMode.EXACT,
        frozenset({"POST"}),
    ),
    # Sina
    _AllowEntry(
        "quotes.sina.cn",
        "/cn/api/openapi.php/companyfinanceservice.getfinancereport2022",
        _MatchMode.EXACT,
    ),
    _AllowEntry("hq.sinajs.cn", "/list", _MatchMode.EXACT),
    # Live-verified 2026-07-17: capital daily-flow fallback (exact path only).
    _AllowEntry(
        "vip.stock.finance.sina.com.cn",
        "/quotes_service/api/json_v2.php/moneyflow.ssl_qsfx_zjlrqs",
        _MatchMode.EXACT,
    ),
    # Live-verified 2026-07-17: ETF option chain metadata (exact paths only).
    _AllowEntry(
        "stock.finance.sina.com.cn",
        "/futures/api/openapi.php/stockoptionservice.getstockname",
        _MatchMode.EXACT,
    ),
    _AllowEntry(
        "stock.finance.sina.com.cn",
        "/futures/api/openapi.php/stockoptionservice.getremainderday",
        _MatchMode.EXACT,
    ),
    # Cninfo
    _AllowEntry("www.cninfo.com.cn", "/new/hisannouncement/query", _MatchMode.EXACT),
    _AllowEntry("irm.cninfo.com.cn", "/newircs/", _MatchMode.PREFIX),
    # Official CNINFO static finalpage PDF bodies (company operating metrics).
    _AllowEntry(
        "static.cninfo.com.cn",
        "/finalpage/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    # National Animal Husbandry Service official price/capacity publications.
    _AllowEntry("www.nahs.org.cn", "/jcyj/scxs/", _MatchMode.PREFIX, frozenset({"GET"})),
    _AllowEntry("www.nahs.org.cn", "/jcyj/jghq/", _MatchMode.PREFIX, frozenset({"GET"})),
    _AllowEntry("www.nahs.org.cn", "/jcyj/jcgz/", _MatchMode.PREFIX, frozenset({"GET"})),
    # THS
    _AllowEntry("basic.10jqka.com.cn", "/new/", _MatchMode.PREFIX),
    _AllowEntry(
        "data.10jqka.com.cn",
        "/dataapi/limit_up/limit_up_pool",
        _MatchMode.EXACT,
    ),
    _AllowEntry("dq.10jqka.com.cn", "/", _MatchMode.PREFIX),
    # CLS
    _AllowEntry("www.cls.cn", "/v1/roll/get_roll_list", _MatchMode.EXACT),
    # SSE official disclosure family
    _AllowEntry("query.sse.com.cn", "/infodisplay/", _MatchMode.PREFIX),
    # SZSE
    _AllowEntry("www.szse.cn", "/api/report/showreport/data", _MatchMode.EXACT),
    _AllowEntry("www.szse.cn", "/api/disc/announcement/annlist", _MatchMode.EXACT),
    _AllowEntry("disc.static.szse.cn", "/download", _MatchMode.EXACT),
    # HKEX northbound daily (path family under /chi/csm/dailystat/)
    _AllowEntry("www.hkex.com.hk", "/chi/csm/dailystat/", _MatchMode.PREFIX),
    # iwencai base
    _AllowEntry("openapi.iwencai.com", "/", _MatchMode.PREFIX),
    # Phase 1F US market providers (fixed official endpoint families).
    _AllowEntry(
        "query1.finance.yahoo.com",
        "/v8/finance/chart/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "query1.finance.yahoo.com",
        "/v1/finance/search",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "www.alphavantage.co",
        "/query",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "api.stlouisfed.org",
        "/fred/series",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "api.stocktwits.com",
        "/api/2/streams/symbol/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "ai-news-search.moomoo.com",
        "/stock_feed",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "www.reddit.com",
        "/r/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "api.apify.com",
        "/v2/acts/harshmaur~reddit-scraper/runs",
        _MatchMode.EXACT,
        frozenset({"POST"}),
    ),
    _AllowEntry(
        "api.apify.com",
        "/v2/actor-runs/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "api.apify.com",
        "/v2/datasets/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "gamma-api.polymarket.com",
        "/public-search",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "api.stlouisfed.org",
        "/fred/series/observations",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    # Phase 1G G2 SEC EDGAR (fixed official endpoint families; GET only).
    _AllowEntry(
        "www.sec.gov",
        "/files/company_tickers.json",
        _MatchMode.EXACT,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "data.sec.gov",
        "/submissions/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "data.sec.gov",
        "/api/xbrl/companyfacts/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
    _AllowEntry(
        "www.sec.gov",
        "/Archives/edgar/data/",
        _MatchMode.PREFIX,
        frozenset({"GET"}),
    ),
)

_LOCALHOST_NAMES: Final[frozenset[str]] = frozenset({"localhost", "localhost.localdomain"})

_PROHIBITED_REQUEST_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
    }
)

_ALLOWED_REQUEST_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "content-type",
        "user-agent",
        "referer",
    }
)

# Encoded path-traversal / separator probes in the raw path string.
_ENCODED_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"%2f|%5c|%00|\\", re.IGNORECASE)
_DOT_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(r"(^|/)\.\.?(/|$)", re.IGNORECASE)


def _status_class(status_code: int | None) -> str:
    if status_code is None or status_code < 100 or status_code > 599:
        return "none"
    return f"{status_code // 100}xx"


def _safe_transport_error(
    exc_type: type[TradingPartnerError],
    message: str,
    *,
    error_type: str,
    status_code: int | None = None,
    retryable: bool | None = None,
) -> TradingPartnerError:
    """Build a typed error with no URL/query/body/header/cause leakage."""
    details: dict[str, object] = {
        "error_type": error_type,
        "status_class": _status_class(status_code),
    }
    if status_code is not None and 100 <= status_code <= 599:
        details["status_code"] = status_code
    return exc_type(message, details=details, retryable=retryable)


def _is_private_or_local_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _default_port_for_scheme(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _entry_matches_path(entry: _AllowEntry, host: str, path: str) -> bool:
    if host != entry.host:
        return False
    path_lower = path.lower()
    allowed = entry.path.lower()
    if entry.mode is _MatchMode.EXACT:
        return path_lower == allowed
    if allowed == "/":
        return path_lower.startswith("/")
    if not allowed.endswith("/"):
        return path_lower == allowed
    return path_lower.startswith(allowed) or path_lower == allowed.rstrip("/")


def _path_is_allowlisted(host: str, path: str) -> bool:
    return any(_entry_matches_path(entry, host, path) for entry in _ALLOWLIST)


def _method_is_allowlisted(host: str, path: str, method: str) -> bool:
    for entry in _ALLOWLIST:
        if _entry_matches_path(entry, host, path):
            return method in entry.methods
    return False


def _encode_query_params(params: Mapping[str, str]) -> str:
    """Deterministic production query encoding for ``HttpRequest.params``.

    Policy (fixed; not caller-configurable):

    * Encode from the structured ``Mapping[str, str]`` only — never accept a
      raw query string override from adapters.
    * Keys: percent-encode reserved/unsafe characters (including ``,``).
    * Values: percent-encode reserved/unsafe characters **except** ``,`` so
      list-style batch values (e.g. Sina ``hq.sinajs.cn/list``) reach the
      wire unescaped. Space uses ``+`` (``application/x-www-form-urlencoded``).
    * Pair order follows the mapping's iteration order (stable for dicts).
    * Empty mapping yields an empty string (no ``?``).
    """
    if not params:
        return ""
    parts: list[str] = []
    for key, value in params.items():
        # Keys must never treat comma as safe — only values may preserve it.
        enc_key = quote_plus(key, safe="")
        enc_value = quote_plus(value, safe=",")
        parts.append(f"{enc_key}={enc_value}")
    return "&".join(parts)


class HttpxTransport:
    """``HttpTransport`` backed by ``httpx.AsyncClient`` with hard URL policy.

    ``allowed_endpoints`` is **not** a public override — the allowlist is the
    frozen module constant matching design §20.
    """

    def __init__(
        self,
        *,
        max_response_bytes: int,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
        proxy_url: str | None = None,
    ) -> None:
        if not isinstance(max_response_bytes, int) or isinstance(max_response_bytes, bool):
            raise ConfigurationError(
                "max_response_bytes must be a positive int",
                details={"field": "max_response_bytes", "rule": "type"},
            )
        if max_response_bytes < 1:
            raise ConfigurationError(
                "max_response_bytes must be a positive int",
                details={"field": "max_response_bytes", "rule": "positive"},
            )
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ConfigurationError(
                "timeout_seconds must be a positive number",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        # httpx's INFO message renders the complete URL after query encoding.
        # Provider credentials (FRED/Alpha Vantage) live in structured query
        # params, so suppress these dependency loggers at the transport boundary,
        # including embedded/in-process MCP use that bypasses the stdio entrypoint.
        for logger_name in ("httpx", "httpcore"):
            logging.getLogger(logger_name).disabled = True
        self._max_response_bytes = max_response_bytes
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout_seconds,
            trust_env=False,
            proxy=proxy_url,
        )

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    @property
    def allowlist(self) -> tuple[tuple[str, str, str], ...]:
        """Frozen allowlist as (host, path, mode) tuples (read-only view)."""
        return tuple((e.host, e.path, e.mode.value) for e in _ALLOWLIST)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, request: HttpRequest) -> HttpResponse:
        if not isinstance(request, HttpRequest):
            raise DataContractError(
                "request must be HttpRequest",
                details={"field": "request", "rule": "type"},
            )
        self._validate_request_shape(request)
        url = request.url.strip()
        host, path = self._validate_url(url)
        if not _path_is_allowlisted(host, path):
            raise DataContractError(
                "url host/path is not in the transport allowlist",
                details={"field": "url", "rule": "allowlist"},
            )
        if not _method_is_allowlisted(host, path, request.method):
            raise DataContractError(
                "HTTP method is not allowed for this endpoint",
                details={"field": "method", "rule": "method_allowlist"},
            )
        # Build the final URL ourselves so comma-in-value survives (httpx's
        # params= dict path percent-encodes commas, which breaks Sina list
        # batching). Never pass a raw adapter query string or httpx params=.
        query = _encode_query_params(request.params)
        request_url = f"{url}?{query}" if query else url
        headers = self._sanitize_headers(request.headers, host=host, path=path)

        try:
            # Stream so we can enforce max_response_bytes without buffering
            # the entire body first via response.content.
            async with self._client.stream(
                method=request.method,
                url=request_url,
                params=None,
                headers=headers,
                content=request.body,
                timeout=request.timeout_seconds,
                follow_redirects=False,
            ) as response:
                status = int(response.status_code)
                if status in {301, 302, 303, 307, 308}:
                    raise _safe_transport_error(
                        ProviderUnavailableError,
                        "HTTP redirects are not allowed",
                        error_type="redirect_blocked",
                        status_code=status,
                    )
                if status == 429:
                    raise _safe_transport_error(
                        ProviderRateLimitError,
                        "HTTP rate limited",
                        error_type="rate_limit",
                        status_code=status,
                    )
                if status in {401, 403}:
                    raise _safe_transport_error(
                        ProviderUnavailableError,
                        "HTTP access blocked",
                        error_type="blocked",
                        status_code=status,
                    )

                # Early reject on declared Content-Length when over limit.
                cl_header = response.headers.get("content-length")
                if cl_header is not None:
                    try:
                        declared = int(cl_header.strip())
                    except ValueError:
                        declared = -1
                    if declared > self._max_response_bytes:
                        raise _safe_transport_error(
                            ProviderUnavailableError,
                            "HTTP response body exceeds configured maximum",
                            error_type="body_too_large",
                            status_code=status,
                        )

                body = await self._read_body_bounded(response, status_code=status)

                safe_headers: dict[str, str] = {}
                for key, value in response.headers.multi_items():
                    lower = key.lower()
                    if lower in _PROHIBITED_REQUEST_HEADERS or lower == "set-cookie":
                        continue
                    if not isinstance(value, str):
                        continue
                    safe_headers[key] = value

                return HttpResponse(status_code=status, headers=safe_headers, body=body)
        except TradingPartnerError:
            raise
        except httpx.TimeoutException:
            raise _safe_transport_error(
                ProviderTimeoutError,
                "HTTP request timed out",
                error_type="timeout",
            ) from None
        except httpx.TooManyRedirects:
            raise _safe_transport_error(
                ProviderUnavailableError,
                "HTTP redirects are not allowed",
                error_type="redirect_blocked",
            ) from None
        except httpx.HTTPError:
            raise _safe_transport_error(
                ProviderUnavailableError,
                "HTTP transport failure",
                error_type="transport_failure",
            ) from None
        except OSError:
            raise _safe_transport_error(
                ProviderUnavailableError,
                "HTTP transport failure",
                error_type="transport_failure",
            ) from None

    async def _read_body_bounded(self, response: httpx.Response, *, status_code: int) -> bytes:
        """Stream response body with chunk-by-chunk max+1 cutoff."""
        max_bytes = self._max_response_bytes
        chunks: list[bytes] = []
        total = 0
        # Read one byte past the limit so oversize is detected without needing
        # Content-Length; never retain more than max_bytes of usable payload.
        limit_plus_one = max_bytes + 1
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            remaining = limit_plus_one - total
            if remaining <= 0:
                raise _safe_transport_error(
                    ProviderUnavailableError,
                    "HTTP response body exceeds configured maximum",
                    error_type="body_too_large",
                    status_code=status_code,
                )
            if len(chunk) > remaining:
                # Take only enough to prove oversize, then reject.
                chunks.append(chunk[:remaining])
                total += remaining
                raise _safe_transport_error(
                    ProviderUnavailableError,
                    "HTTP response body exceeds configured maximum",
                    error_type="body_too_large",
                    status_code=status_code,
                )
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise _safe_transport_error(
                    ProviderUnavailableError,
                    "HTTP response body exceeds configured maximum",
                    error_type="body_too_large",
                    status_code=status_code,
                )
        return b"".join(chunks)

    def _validate_request_shape(self, request: HttpRequest) -> None:
        if request.method not in {"GET", "POST"}:
            raise DataContractError(
                "HTTP method must be GET or POST",
                details={"field": "method", "rule": "method"},
            )
        if not isinstance(request.url, str) or not request.url.strip():
            raise DataContractError(
                "url must be a non-blank string",
                details={"field": "url", "rule": "non_blank"},
            )
        if not isinstance(request.params, Mapping):
            raise DataContractError(
                "params must be a mapping of str to str",
                details={"field": "params", "rule": "type"},
            )
        for key, value in request.params.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise DataContractError(
                    "params keys and values must be strings",
                    details={"field": "params", "rule": "str_str"},
                )
        if not isinstance(request.headers, Mapping):
            raise DataContractError(
                "headers must be a mapping of str to str",
                details={"field": "headers", "rule": "type"},
            )
        if request.body is not None and not isinstance(request.body, (bytes, bytearray)):
            raise DataContractError(
                "body must be bytes or None",
                details={"field": "body", "rule": "type"},
            )
        if (
            not isinstance(request.timeout_seconds, (int, float))
            or isinstance(request.timeout_seconds, bool)
            or request.timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be a positive number",
                details={"field": "timeout_seconds", "rule": "positive"},
            )

    def _validate_url(self, url: str) -> tuple[str, str]:
        # Reject encoded separators and backslashes before parse ambiguity.
        if _ENCODED_SEPARATOR_RE.search(url.split("?", 1)[0]):
            raise DataContractError(
                "url path contains encoded separators or backslashes",
                details={"field": "url", "rule": "path_encoding"},
            )
        try:
            parts = urlsplit(url)
        except ValueError:
            raise DataContractError(
                "url is not a valid absolute URL",
                details={"field": "url", "rule": "parse"},
            ) from None

        scheme = (parts.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise DataContractError(
                "url scheme must be http or https",
                details={"field": "url", "rule": "scheme"},
            )
        if parts.username is not None or parts.password is not None:
            raise DataContractError(
                "url must not include credentials",
                details={"field": "url", "rule": "no_credentials"},
            )
        if parts.fragment:
            raise DataContractError(
                "url must not include a fragment",
                details={"field": "url", "rule": "no_fragment"},
            )
        # Query must come only from structured HttpRequest.params via our
        # encoder — reject any raw query embedded in the URL string.
        if "?" in url.split("#", 1)[0]:
            raise DataContractError(
                "url must not include a raw query string",
                details={"field": "url", "rule": "no_raw_query"},
            )
        host = (parts.hostname or "").lower()
        if not host:
            raise DataContractError(
                "url must include a host",
                details={"field": "url", "rule": "host_required"},
            )
        if host in _LOCALHOST_NAMES or host.endswith(".localhost"):
            raise DataContractError(
                "localhost hosts are not allowed",
                details={"field": "url", "rule": "no_localhost"},
            )
        if _is_private_or_local_ip(host):
            raise DataContractError(
                "private, loopback, or link-local IP literals are not allowed",
                details={"field": "url", "rule": "no_private_ip"},
            )
        # Non-default ports rejected (including explicit :443 on https).
        port = parts.port
        if port is not None and port != _default_port_for_scheme(scheme):
            raise DataContractError(
                "url must use the default port for its scheme",
                details={"field": "url", "rule": "default_port"},
            )

        path = parts.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        # Reject dot segments and decoded traversal after unquote of path only.
        if _DOT_SEGMENT_RE.search(path):
            raise DataContractError(
                "url path must not contain dot segments",
                details={"field": "url", "rule": "no_dot_segments"},
            )
        try:
            decoded = unquote(path)
        except Exception:
            raise DataContractError(
                "url path could not be decoded",
                details={"field": "url", "rule": "path_decode"},
            ) from None
        if _DOT_SEGMENT_RE.search(decoded) or "\\" in decoded:
            raise DataContractError(
                "url path must not contain traversal or backslash sequences",
                details={"field": "url", "rule": "no_traversal"},
            )
        # Query is intentionally ignored for allowlist matching (path only).
        return host, path

    def _sanitize_headers(
        self,
        headers: Mapping[str, str],
        *,
        host: str,
        path: str,
    ) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise DataContractError(
                    "headers keys and values must be strings",
                    details={"field": "headers", "rule": "str_str"},
                )
            lower = key.lower().strip()
            apify_auth_path = (
                path == "/v2/acts/harshmaur~reddit-scraper/runs"
                or path.startswith("/v2/actor-runs/")
                or path.startswith("/v2/datasets/")
            )
            if lower == "authorization" and host == "api.apify.com" and apify_auth_path:
                scheme, separator, credential = value.strip().partition(" ")
                if (
                    not separator
                    or scheme.casefold() != "bearer"
                    or not credential
                    or any(character.isspace() for character in credential)
                ):
                    raise DataContractError(
                        "Apify authorization must be a Bearer credential",
                        details={"field": "headers", "rule": "apify_bearer"},
                    )
                cleaned[key] = value
                continue
            if lower in _PROHIBITED_REQUEST_HEADERS:
                raise DataContractError(
                    "request header is not permitted",
                    details={"field": "headers", "rule": "prohibited_header"},
                )
            if lower not in _ALLOWED_REQUEST_HEADERS:
                raise DataContractError(
                    "request header is not on the allowlist",
                    details={"field": "headers", "rule": "header_allowlist"},
                )
            cleaned[key] = value
        return cleaned
