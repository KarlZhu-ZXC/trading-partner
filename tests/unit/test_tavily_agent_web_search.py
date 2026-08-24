from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
)
from infrastructure.providers.llm.tavily_agent_web_search import (
    TavilyAgentWebSearchProvider,
)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 12, tzinfo=UTC)


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_tavily_returns_bounded_structured_sources_without_requesting_answer() -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "request_id": "req_tavily",
                "results": [
                    {
                        "title": " First result ",
                        "url": "https://example.com/one",
                        "content": " Current   public background. ",
                        "score": 0.9,
                    },
                    {
                        "title": "Second result",
                        "url": "https://example.com/two",
                        "content": "More background.",
                        "score": 0.8,
                    },
                ],
                "usage": {"credits": 1},
            },
        )

    client = _client(httpx.MockTransport(handle))
    provider = TavilyAgentWebSearchProvider(
        api_key="test-tavily-key",
        clock=Clock(),
        client=client,
    )

    result = await provider.search(" current   topic ", max_results=1)

    assert captured["authorization"] == "Bearer test-tavily-key"
    assert captured["payload"] == {
        "query": "current topic",
        "topic": "general",
        "search_depth": "basic",
        "max_results": 1,
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    assert result.result["provider"] == "tavily"  # type: ignore[index]
    assert result.result["source_urls"] == ["https://example.com/one"]  # type: ignore[index]
    assert "Current public background." in result.result["summary"]  # type: ignore[operator,index]
    assert result.receipt.request_id == "req_tavily"
    assert result.receipt.source_codes == ("tavily",)

    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (432, ProviderQuotaExceededError),
        (433, ProviderQuotaExceededError),
    ],
)
async def test_tavily_maps_safe_provider_errors(
    status: int, error_type: type[Exception]
) -> None:
    client = _client(
        httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": "hidden"}))
    )
    provider = TavilyAgentWebSearchProvider(
        api_key="test-tavily-key",
        clock=Clock(),
        client=client,
    )

    with pytest.raises(error_type):
        await provider.search("topic")

    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_rejects_unbounded_query_before_network() -> None:
    called = False

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    client = _client(httpx.MockTransport(handle))
    provider = TavilyAgentWebSearchProvider(
        api_key="test-tavily-key",
        clock=Clock(),
        client=client,
    )

    with pytest.raises(DataContractError):
        await provider.search("x" * 501)

    assert called is False
    await client.aclose()
