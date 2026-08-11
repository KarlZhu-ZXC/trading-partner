"""Alibaba Cloud Model Studio narrative adapter for deterministic Trade Retro facts."""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from application.ports.trade_retro_narrative_provider import (
    TradeRetroNarrativeRequest,
    TradeRetroNarrativeResponse,
)
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

_SYSTEM_PROMPT = """你是只读交易纪律复盘系统的解释层。
只使用输入中的已持久化成交、事前计划快照、Decision Record 和确定性 finding。
不得推断未记录成交，不得把事后计划冒充事前计划，不得修改标的、Thesis、Trade Plan、持仓或订单。
输出简体中文 Markdown，重点说明：本周做对了什么、纪律偏差、数据盲区、下周最多三条可执行流程改进。
不得给出新的买卖点位或仓位指令。只返回 JSON：{"summary_markdown":"..."}。"""


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_markdown: str = Field(min_length=1, max_length=50_000)

    @field_validator("summary_markdown")
    @classmethod
    def chinese(cls, value: str) -> str:
        if re.search(r"[\u3400-\u9fff]", value) is None:
            raise ValueError("Trade Retro narrative must contain Chinese")
        return value


class BailianTradeRetroNarrativeProvider:
    provider_name = "bailian"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float,
        max_output_tokens: int,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            proxy=proxy_url,
            follow_redirects=False,
        )

    async def narrate(
        self, request: TradeRetroNarrativeRequest
    ) -> TradeRetroNarrativeResponse:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": request.deterministic_facts_json},
            ],
            "reasoning": {"effort": self._reasoning_effort},
            "max_output_tokens": self._max_output_tokens,
        }
        raw = await self._post(payload)
        content = _response_text(raw)
        try:
            result = _Response.model_validate_json(_strip_json_fence(content))
        except (TypeError, ValueError, ValidationError) as exc:
            raise DataContractError(
                "Model Studio returned an invalid Trade Retro narrative"
            ) from exc
        return TradeRetroNarrativeResponse(
            summary_markdown=result.summary_markdown,
            provider_name=self.provider_name,
            model=self.model,
        )

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                "/responses",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Model Studio Trade Retro narrative timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Model Studio Trade Retro narrative transport failed"
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Model Studio Trade Retro narrative authentication failed"
            )
        if response.status_code == 429:
            raise ProviderRateLimitError("Model Studio Trade Retro narrative was rate limited")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "Model Studio Trade Retro narrative failed",
                details={"status_code": response.status_code},
            )
        try:
            return dict(response.json())
        except (TypeError, ValueError) as exc:
            raise DataContractError("Model Studio returned a non-JSON response") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for item in payload.get("output", ()):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", ()):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return stripped[7:-3].strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped[3:-3].strip()
    return stripped
