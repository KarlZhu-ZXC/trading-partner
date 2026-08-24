"""Tavily Search sidecar shared by every Agent model route."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from application.ports.agent_tool_gateway import AgentToolReceipt, AgentToolResult
from application.ports.clock import Clock
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class TavilyAgentWebSearchProvider:
    """Perform one bounded Tavily Search without an additional LLM call."""

    provider_name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        clock: Clock,
        base_url: str = "https://api.tavily.com",
        search_depth: str = "basic",
        timeout_seconds: float = 30.0,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Tavily API key must be nonblank")
        if search_depth not in {"basic", "advanced"}:
            raise ValueError("Tavily search depth must be basic or advanced")
        if base_url.rstrip("/") != "https://api.tavily.com":
            raise ValueError("Tavily base URL must be the official API endpoint")
        self._api_key = api_key
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._search_depth = search_depth
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            proxy=proxy_url,
            follow_redirects=False,
            trust_env=False,
        )

    async def search(self, query: str, *, max_results: int = 5) -> AgentToolResult:
        normalized = " ".join(query.split())
        if not normalized or len(normalized) > 500:
            raise DataContractError("Agent Web Search query must be bounded nonblank text")
        if type(max_results) is not int or not 1 <= max_results <= 10:
            raise DataContractError("Agent Web Search max_results must be in [1,10]")

        try:
            response = await self._client.post(
                f"{self._base_url}/search",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": normalized,
                    "topic": "general",
                    "search_depth": self._search_depth,
                    "max_results": max_results,
                    "include_answer": False,
                    "include_images": False,
                    "include_raw_content": False,
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Tavily search timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Tavily search transport failed") from exc

        self._raise_for_status(response.status_code)
        if len(response.content) > 1_000_000:
            raise DataContractError("Tavily search response exceeded the size limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DataContractError("Tavily search returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise DataContractError("Tavily search response must be an object")

        entries = self._entries(payload.get("results"), max_results=max_results)
        source_urls = [item["url"] for item in entries]
        summary = "\n\n".join(
            f"[{index}] {item['title']}\n{item['content']}\nSource: {item['url']}"
            for index, item in enumerate(entries, start=1)
        )
        if not summary:
            summary = "Tavily returned no relevant public web results."
        warnings = () if source_urls else ("AGENT_WEB_SEARCH_NO_SOURCE_URLS",)
        request_id = self._bounded_text(payload.get("request_id"), 200)
        result: dict[str, Any] = {
            "ok": True,
            "summary": summary[:12_000],
            "source_urls": source_urls,
            "searched_at": self._clock.now().isoformat(),
            "web_search_used": True,
            "provider": self.provider_name,
        }
        return AgentToolResult(
            result=result,
            receipt=AgentToolReceipt(
                capability="agent_web_search",
                operation="search",
                request_id=request_id,
                effect="READ_PROVIDER",
                degraded=not bool(source_urls),
                source_codes=(self.provider_name,),
                warning_codes=warnings,
                result_size_bytes=len(str(result).encode("utf-8")),
            ),
        )

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        details = {"status_code": status_code}
        if status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Tavily rejected the configured credential", details=details
            )
        if status_code == 429:
            raise ProviderRateLimitError("Tavily rate limit was reached", details=details)
        if status_code in {432, 433}:
            raise ProviderQuotaExceededError(
                "Tavily account usage allowance was exhausted", details=details
            )
        if status_code in {400, 404, 405, 409, 422}:
            raise ProviderRequestRejectedError(
                "Tavily rejected the search request", details=details
            )
        if status_code >= 400:
            raise ProviderUnavailableError(
                "Tavily returned an HTTP error", details=details
            )

    @classmethod
    def _entries(cls, raw: object, *, max_results: int) -> list[dict[str, str]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise DataContractError("Tavily results must be a list")
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            url = cls._safe_url(item.get("url"))
            if url is None or url in seen:
                continue
            title = cls._bounded_text(item.get("title"), 300) or "Untitled result"
            content = cls._bounded_text(item.get("content"), 2_000) or "No excerpt."
            entries.append({"title": title, "content": content, "url": url})
            seen.add(url)
            if len(entries) >= max_results:
                break
        return entries

    @staticmethod
    def _safe_url(value: object) -> str | None:
        if not isinstance(value, str) or len(value) > 2_048:
            return None
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        return value

    @staticmethod
    def _bounded_text(value: object, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized[:limit] or None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["TavilyAgentWebSearchProvider"]
