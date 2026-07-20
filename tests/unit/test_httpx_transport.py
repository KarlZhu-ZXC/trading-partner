"""Phase 1E E2: HttpxTransport allowlist / redirect / body / error secrecy."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from application.ports.http_transport import HttpRequest
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from infrastructure.providers.common.httpx_transport import HttpxTransport


def _req(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    body: bytes | None = None,
) -> HttpRequest:
    return HttpRequest(
        method=method,  # type: ignore[arg-type]
        url=url,
        params=params or {},
        headers=headers or {"Accept": "application/json"},
        body=body,
        timeout_seconds=5.0,
    )


def _transport(
    handler: httpx.AsyncBaseTransport,
    *,
    max_bytes: int = 10_000,
) -> HttpxTransport:
    client = httpx.AsyncClient(transport=handler, follow_redirects=False, trust_env=False)
    return HttpxTransport(max_response_bytes=max_bytes, client=client)


class _FixedHandler(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"{}",
        headers: dict[str, str] | None = None,
        raise_exc: Exception | None = None,
        stream_chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.raise_exc = raise_exc
        self.stream_chunks = stream_chunks
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.stream_chunks is not None:

            async def _aiter():
                for chunk in self.stream_chunks:
                    yield chunk

            return httpx.Response(
                self.status,
                headers=self.headers,
                stream=httpx.AsyncByteStream(),  # placeholder replaced below
                request=request,
            )
        return httpx.Response(
            self.status,
            content=self.body,
            headers=self.headers,
            request=request,
        )


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


class _StreamingHandler(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        chunks: list[bytes],
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.chunks = chunks
        self.status = status
        self.headers = headers or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self.status,
            headers=self.headers,
            stream=_ChunkStream(self.chunks),
            request=request,
        )


@pytest.mark.asyncio
async def test_allowlist_accepts_known_exact_endpoint() -> None:
    handler = _FixedHandler(body=b'{"ok":true}')
    transport = _transport(handler)
    resp = await transport.send(_req("https://qt.gtimg.cn/q", params={"q": "sh600519"}))
    assert resp.status_code == 200
    assert resp.body == b'{"ok":true}'
    await transport.aclose()


@pytest.mark.asyncio
async def test_allowlist_rejects_unknown_host() -> None:
    transport = _transport(_FixedHandler())
    with pytest.raises(DataContractError) as exc:
        await transport.send(_req("https://evil.example/api"))
    assert "allowlist" in str(exc.value.details.get("rule", ""))
    blob = json.dumps(exc.value.details) + exc.value.message
    assert "evil.example" not in blob
    await transport.aclose()


@pytest.mark.asyncio
async def test_exact_path_rejects_suffix_and_prefix_extension() -> None:
    transport = _transport(_FixedHandler())
    # /qevil and /q/evil must not match exact /q
    for url in (
        "https://qt.gtimg.cn/qevil",
        "https://qt.gtimg.cn/q/evil",
        "https://qt.gtimg.cn/qextra",
        "https://push2.eastmoney.com/api/qt/stock/getevil",
        "https://push2.eastmoney.com/api/qt/stock/get/extra",
    ):
        with pytest.raises(DataContractError) as exc:
            await transport.send(_req(url))
        assert exc.value.details.get("rule") == "allowlist"
    await transport.aclose()


@pytest.mark.asyncio
async def test_stockrank_requires_exact_post_only_endpoint() -> None:
    transport = _transport(_FixedHandler(body=b"ok"))
    # Only the two frozen stockrank endpoints are available, POST only.
    for endpoint in ("getAllCurrentList", "getHotStockRankList"):
        url = f"https://emappdata.eastmoney.com/stockrank/{endpoint}"
        ok = await transport.send(_req(url, method="POST"))
        assert ok.status_code == 200
        with pytest.raises(DataContractError) as exc:
            await transport.send(_req(url, method="GET"))
        assert exc.value.details.get("rule") == "method_allowlist"
    for path in ("list", "getHotStockRankList/extra", "getHotStockRankListEvil"):
        with pytest.raises(DataContractError) as exc:
            await transport.send(_req(f"https://emappdata.eastmoney.com/stockrank/{path}"))
        assert exc.value.details.get("rule") == "allowlist"
    # newircs/*
    ok2 = await transport.send(_req("https://irm.cninfo.com.cn/newircs/index"))
    assert ok2.status_code == 200
    with pytest.raises(DataContractError):
        await transport.send(_req("https://irm.cninfo.com.cn/newircsevil"))
    await transport.aclose()


@pytest.mark.asyncio
async def test_prefix_directory_stem_uses_same_match_for_method_allowlist() -> None:
    transport = _transport(_FixedHandler(body=b"ok"))
    response = await transport.send(_req("https://irm.cninfo.com.cn/newircs"))
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    (
        "https://query1.finance.yahoo.com/v8/finance/chart/NVDA",
        "https://www.alphavantage.co/query",
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0001045810.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json",
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000029/doc.htm",
    ),
)
async def test_us_market_provider_endpoints_are_allowlisted(url: str) -> None:
    transport = _transport(_FixedHandler())
    response = await transport.send(_req(url))
    assert response.status_code == 200
    await transport.aclose()
    await transport.aclose()


@pytest.mark.asyncio
async def test_query_not_part_of_path_matching() -> None:
    transport = _transport(_FixedHandler(body=b"ok"))
    # Structured params must not affect allowlist; path remains exact /q
    resp = await transport.send(
        _req(
            "https://qt.gtimg.cn/q",
            params={"q": "sh600519", "evil": "/admin"},
        )
    )
    assert resp.status_code == 200
    await transport.aclose()


@pytest.mark.asyncio
async def test_rejects_raw_query_string_in_url() -> None:
    """Query must come only from structured params — no raw URL query."""
    transport = _transport(_FixedHandler(body=b"ok"))
    with pytest.raises(DataContractError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q?q=sh600519"))
    assert exc.value.details.get("rule") == "no_raw_query"
    blob = json.dumps(exc.value.details) + exc.value.message
    assert "sh600519" not in blob
    assert "q=" not in blob
    await transport.aclose()


@pytest.mark.asyncio
async def test_sina_daily_flow_exact_host_path_allowlist() -> None:
    """Capital daily-flow is exact vip host/path only — no broad stock.finance prefix."""
    handler = _FixedHandler(body=b"[]")
    transport = _transport(handler)
    ok = await transport.send(
        _req(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
            "json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs",
            params={"page": "1", "num": "2", "daima": "sh600519"},
        )
    )
    assert ok.status_code == 200
    # Case-insensitive path match still exact (no sibling method / prefix).
    for url in (
        "https://stock.finance.sina.com.cn/stock/api/json_v2.php/MoneyFlow.ssl_qsfx_lscjfb",
        "https://stock.finance.sina.com.cn/",
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
        "json_v2.php/MoneyFlow.ssl_qsfx_lscjfb",
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
        "json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs/extra",
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/",
    ):
        with pytest.raises(DataContractError) as exc:
            await transport.send(_req(url))
        assert exc.value.details.get("rule") == "allowlist"
        blob = json.dumps(exc.value.details) + exc.value.message
        assert "stock.finance.sina" not in blob
        assert "MoneyFlow" not in blob
    await transport.aclose()


@pytest.mark.asyncio
async def test_sina_option_metadata_exact_host_path_allowlist() -> None:
    """ETF option metadata is exact stock.finance paths only (E4c)."""
    handler = _FixedHandler(body=b"{}")
    transport = _transport(handler)
    for path in (
        "/futures/api/openapi.php/StockOptionService.getStockName",
        "/futures/api/openapi.php/StockOptionService.getRemainderDay",
    ):
        ok = await transport.send(_req(f"https://stock.finance.sina.com.cn{path}"))
        assert ok.status_code == 200
    # hq list remains allowlisted for OP_*/CON_* series.
    hq = await transport.send(
        _req("https://hq.sinajs.cn/list", params={"list": "OP_UP_5100502607"})
    )
    assert hq.status_code == 200
    for url in (
        "https://stock.finance.sina.com.cn/futures/api/openapi.php/",
        "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
        "StockOptionService.getStockName/extra",
        "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.otherMethod",
        "https://vip.stock.finance.sina.com.cn/futures/api/openapi.php/"
        "StockOptionService.getStockName",
    ):
        with pytest.raises(DataContractError) as exc:
            await transport.send(_req(url))
        assert exc.value.details.get("rule") == "allowlist"
    await transport.aclose()


@pytest.mark.asyncio
async def test_rejects_dot_segments_and_encoded_slash() -> None:
    transport = _transport(_FixedHandler())
    for url in (
        "https://qt.gtimg.cn/./q",
        "https://qt.gtimg.cn/foo/../q",
        "https://qt.gtimg.cn/%2e%2e/q",
        "https://qt.gtimg.cn/q%2fevil",
        "https://qt.gtimg.cn/q%5cevil",
    ):
        with pytest.raises(DataContractError):
            await transport.send(_req(url))
    await transport.aclose()


@pytest.mark.asyncio
async def test_rejects_non_default_ports() -> None:
    transport = _transport(_FixedHandler())
    with pytest.raises(DataContractError) as exc:
        await transport.send(_req("https://qt.gtimg.cn:8443/q"))
    assert exc.value.details.get("rule") == "default_port"
    await transport.aclose()


@pytest.mark.asyncio
async def test_rejects_localhost_and_private_ip() -> None:
    transport = _transport(_FixedHandler())
    with pytest.raises(DataContractError):
        await transport.send(_req("http://127.0.0.1/secret"))
    with pytest.raises(DataContractError):
        await transport.send(_req("http://localhost/secret"))
    with pytest.raises(DataContractError):
        await transport.send(_req("http://10.0.0.1/secret"))
    await transport.aclose()


@pytest.mark.asyncio
async def test_rejects_credentials_and_fragment() -> None:
    transport = _transport(_FixedHandler())
    with pytest.raises(DataContractError) as e1:
        await transport.send(_req("https://user:pass@qt.gtimg.cn/q"))
    assert e1.value.details.get("rule") == "no_credentials"
    with pytest.raises(DataContractError) as e2:
        await transport.send(_req("https://qt.gtimg.cn/q#frag"))
    assert e2.value.details.get("rule") == "no_fragment"
    await transport.aclose()


@pytest.mark.asyncio
async def test_rejects_file_and_non_http_scheme() -> None:
    transport = _transport(_FixedHandler())
    with pytest.raises(DataContractError):
        await transport.send(_req("file:///etc/passwd"))
    await transport.aclose()


@pytest.mark.asyncio
async def test_redirect_status_blocked() -> None:
    transport = _transport(
        _FixedHandler(status=302, headers={"Location": "https://evil.example/x"}),
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q"))
    assert exc.value.details.get("error_type") == "redirect_blocked"
    assert "evil.example" not in str(exc.value.details)
    assert "Location" not in str(exc.value.details)
    await transport.aclose()


@pytest.mark.asyncio
async def test_body_size_limit_content_length_early() -> None:
    transport = _transport(
        _FixedHandler(body=b"x" * 10, headers={"content-length": "100"}),
        max_bytes=50,
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q"))
    assert exc.value.details.get("error_type") == "body_too_large"
    await transport.aclose()


@pytest.mark.asyncio
async def test_body_size_limit_streaming_chunks() -> None:
    transport = _transport(
        _StreamingHandler(chunks=[b"a" * 30, b"b" * 30]),
        max_bytes=50,
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q"))
    assert exc.value.details.get("error_type") == "body_too_large"
    await transport.aclose()


@pytest.mark.asyncio
async def test_fake_content_length_under_then_stream_oversize() -> None:
    """Declared Content-Length under limit but stream exceeds → reject by chunks."""
    transport = _transport(
        _StreamingHandler(
            chunks=[b"x" * 40, b"y" * 40],
            headers={"content-length": "10"},
        ),
        max_bytes=50,
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q"))
    assert exc.value.details.get("error_type") == "body_too_large"
    await transport.aclose()


@pytest.mark.asyncio
async def test_rate_limit_and_blocked_status() -> None:
    t1 = _transport(_FixedHandler(status=429))
    with pytest.raises(ProviderRateLimitError):
        await t1.send(_req("https://qt.gtimg.cn/q"))
    await t1.aclose()
    t2 = _transport(_FixedHandler(status=403))
    with pytest.raises(ProviderUnavailableError) as exc:
        await t2.send(_req("https://qt.gtimg.cn/q"))
    assert exc.value.details.get("error_type") == "blocked"
    await t2.aclose()


@pytest.mark.asyncio
async def test_timeout_sanitized() -> None:
    transport = _transport(
        _FixedHandler(raise_exc=httpx.ReadTimeout("slow")),
    )
    with pytest.raises(ProviderTimeoutError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q"))
    blob = json.dumps(exc.value.details) + exc.value.message
    assert "slow" not in blob
    assert "qt.gtimg.cn" not in blob
    await transport.aclose()


@pytest.mark.asyncio
async def test_prohibits_cookie_and_authorization_headers() -> None:
    transport = _transport(_FixedHandler())
    with pytest.raises(DataContractError):
        await transport.send(
            _req("https://qt.gtimg.cn/q", headers={"Authorization": "Bearer secret"})
        )
    with pytest.raises(DataContractError):
        await transport.send(_req("https://qt.gtimg.cn/q", headers={"Cookie": "sid=abc"}))
    await transport.aclose()


@pytest.mark.asyncio
async def test_allows_strict_bearer_only_for_apify_paths() -> None:
    transport = _transport(_FixedHandler(status=201))
    response = await transport.send(
        _req(
            "https://api.apify.com/v2/acts/harshmaur~reddit-scraper/runs",
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            body=b"{}",
        )
    )
    assert response.status_code == 201
    with pytest.raises(DataContractError):
        await transport.send(
            _req(
                "https://api.apify.com/v2/acts/harshmaur~reddit-scraper/runs",
                method="POST",
                headers={"Authorization": "Basic not-allowed"},
                body=b"{}",
            )
        )
    await transport.aclose()


@pytest.mark.asyncio
async def test_error_details_have_no_url_query_body() -> None:
    transport = _transport(
        _FixedHandler(status=500, body=b"secret-body-token"),
    )
    resp = await transport.send(
        _req(
            "https://qt.gtimg.cn/q",
            params={"token": "supersecret"},
        )
    )
    assert resp.status_code == 500
    transport2 = _transport(
        _FixedHandler(raise_exc=httpx.ConnectError("connect to qt.gtimg.cn?token=supersecret")),
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await transport2.send(
            _req(
                "https://qt.gtimg.cn/q",
                params={"token": "supersecret"},
            )
        )
    dumped: dict[str, Any] = dict(exc.value.details)
    text = json.dumps(dumped) + exc.value.message
    assert "supersecret" not in text
    assert "secret-body-token" not in text
    assert "qt.gtimg.cn" not in text
    assert "token" not in text
    await transport.aclose()
    await transport2.aclose()


def test_no_public_allowed_endpoints_override() -> None:
    import inspect

    sig = inspect.signature(HttpxTransport.__init__)
    assert "allowed_endpoints" not in sig.parameters


def test_transport_boundary_suppresses_query_bearing_dependency_logs(caplog) -> None:
    secret = "fake-fred-secret-for-log-test"
    loggers = tuple(logging.getLogger(name) for name in ("httpx", "httpcore"))
    previous = tuple(logger.disabled for logger in loggers)
    try:
        for logger in loggers:
            logger.disabled = False
        _transport(_CaptureHandler())
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://example.test/path?api_key=%s", secret
        )
        assert secret not in caplog.text
        assert "api_key=" not in caplog.text
    finally:
        for logger, disabled in zip(loggers, previous, strict=True):
            logger.disabled = disabled


class _CaptureHandler(httpx.AsyncBaseTransport):
    """Capture the exact request URL httpx receives (post-encoding)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, content=b"ok", request=request)


@pytest.mark.asyncio
async def test_query_encoding_preserves_comma_in_param_values() -> None:
    """Comma in values must reach the wire unescaped (Sina list batching)."""
    handler = _CaptureHandler()
    transport = _transport(handler)
    await transport.send(
        _req(
            "https://hq.sinajs.cn/list",
            params={"list": "OP_UP_5100502607,OP_DOWN_5100502607"},
        )
    )
    assert len(handler.requests) == 1
    wire_url = str(handler.requests[0].url)
    # Exact live-bug shape: httpx dict-params would send %2C and Sina would
    # echo var hq_str_OP_UP_5100502607%2COP_DOWN_5100502607="".
    assert "%2C" not in wire_url
    assert "%2c" not in wire_url
    assert "list=OP_UP_5100502607,OP_DOWN_5100502607" in wire_url
    await transport.aclose()


@pytest.mark.asyncio
async def test_query_encoding_encodes_reserved_chars_except_comma_in_values() -> None:
    handler = _CaptureHandler()
    transport = _transport(handler)
    await transport.send(
        _req(
            "https://qt.gtimg.cn/q",
            params={"q": "a b&c=d/e?f#g[h],keep"},
        )
    )
    wire_url = str(handler.requests[0].url)
    # Space → +; other reserved encoded; comma preserved.
    assert "q=a+b%26c%3Dd%2Fe%3Ff%23g%5Bh%5D,keep" in wire_url
    assert ",keep" in wire_url
    assert "%2Ckeep" not in wire_url and "%2ckeep" not in wire_url
    await transport.aclose()


@pytest.mark.asyncio
async def test_query_encoding_encodes_keys_including_comma() -> None:
    handler = _CaptureHandler()
    transport = _transport(handler)
    await transport.send(
        _req(
            "https://qt.gtimg.cn/q",
            params={"list,key": "v", "a b": "1"},
        )
    )
    wire_url = str(handler.requests[0].url)
    assert "list%2Ckey=v" in wire_url or "list%2ckey=v" in wire_url
    assert "a+b=1" in wire_url
    # Key commas must not appear raw.
    assert "list,key=" not in wire_url
    await transport.aclose()


@pytest.mark.asyncio
async def test_query_encoding_deterministic_pair_order() -> None:
    handler = _CaptureHandler()
    transport = _transport(handler)
    # Insertion order is preserved (deterministic for the same Mapping).
    await transport.send(
        _req(
            "https://qt.gtimg.cn/q",
            params={"z": "1", "a": "2", "m": "3"},
        )
    )
    wire_url = str(handler.requests[0].url)
    assert wire_url.endswith("?z=1&a=2&m=3") or "?z=1&a=2&m=3" in wire_url
    await transport.aclose()


@pytest.mark.asyncio
async def test_query_secrets_absent_from_repr_and_errors() -> None:
    secret = "supersecret-token-value"
    # HttpRequest.repr must never show param values / query.
    req = _req("https://qt.gtimg.cn/q", params={"token": secret, "list": "a,b"})
    rep = repr(req)
    assert secret not in rep
    assert "a,b" not in rep
    assert "token=" not in rep
    assert "param_keys=" in rep

    transport = _transport(
        _FixedHandler(raise_exc=httpx.ConnectError(f"connect fail token={secret}")),
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        await transport.send(_req("https://qt.gtimg.cn/q", params={"token": secret}))
    text = json.dumps(dict(exc.value.details)) + exc.value.message + repr(exc.value)
    assert secret not in text
    assert "token" not in text
    assert "qt.gtimg.cn" not in text
    await transport.aclose()
