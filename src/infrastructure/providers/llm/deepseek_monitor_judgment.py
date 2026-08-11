"""DeepSeek V4 Flash structured adapter retained as a selectable fallback."""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from application.ports.monitor_judgment_provider import (
    MonitorJudgmentRequest,
    MonitorJudgmentResponse,
)
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class _StructuredResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgency: str
    phase: str = Field(max_length=100)
    market_state: str = Field(max_length=500)
    divergence: str
    conclusion: str
    quantity_min: int = Field(ge=0)
    quantity_max: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=1000)
    evidence_feature_ids: tuple[str, ...] = Field(max_length=3)
    next_trigger: str = Field(min_length=1, max_length=500)
    invalidation: str = Field(min_length=1, max_length=500)

    @field_validator("market_state", "summary", "next_trigger", "invalidation")
    @classmethod
    def _require_chinese_explanation(cls, value: str) -> str:
        if re.search(r"[\u3400-\u9fff]", value) is None:
            raise ValueError("Monitor explanation fields must contain Chinese")
        normalized = re.sub(r"\s+", "", value).lower()
        if any(
            ambiguous in normalized
            for ambiguous in (
                "昨收",
                "昨日收盘",
                "昨天收盘",
                "上一收盘",
                "上次收盘",
                "上一根k线",
                "前一根k线",
                "上根k线",
                "yesterdayclose",
                "yesterday'sclose",
                "previousclose",
                "priorclose",
                "previouscandleclose",
            )
        ):
            raise ValueError("Monitor explanation used an ambiguous previous-close term")
        return value


_SYSTEM_PROMPT = """You are the bounded judgment layer of a read-only investment monitor.
Use only the supplied deterministic feature snapshot and confirmed state. Never claim a fill,
change confirmed state, invent a price, or recommend a quantity beyond supplied limits.
Judge cross-asset alignment per fact window: quote_sessions_aligned covers fresh time-aligned
quotes, while hourly_returns_aligned and daily_returns_aligned cover their respective comparable
return windows. Do not assert strict divergence from a window that is not aligned. Treat
latest_price/price_time/price_session as quote facts. previous_regular_session_close means only
the most recent completed regular trading-session close before the quote's session; it is neither
the literal prior calendar day's close nor the close of an arbitrary previous candle. In Chinese,
name it only "前收（前一已完成常规交易时段收盘）" and never "昨收", "昨日收盘", or "昨天收盘".
A return_from_previous_regular_session_close_pct is a price-only change from that baseline and may
support preliminary pre-market, post-market, or overnight
follow-through when fresh quotes are aligned. Never require the regular US open solely because a
quote is from an extended session. Hourly and daily return fields have separate as-of timestamps
and must not be described as the latest quote. A point quote is not a complete candle, volume, or
regular-session confirmation. Explanatory fields must use Simplified Chinese.
Do not call every instrument stale when any supplied quote is newer. Enum values and feature IDs
retain their specified form. Return one JSON
object with exactly: urgency (WATCH|ACTION|URGENT), phase, market_state, divergence
(BULLISH|BEARISH|NONE), conclusion
(WATCH|HOLD|REDUCE|WAIT|PREPARE_TO_BUY|BUY_SMALL|BUY|BUY_AGGRESSIVELY|PAUSE_BUYING|INVALIDATE),
quantity_min, quantity_max, summary, evidence_feature_ids (max 3 IDs from the snapshot),
next_trigger, invalidation. No markdown and no additional keys."""


class DeepSeekMonitorJudgmentProvider:
    provider_name = "deepseek"

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
        self.reasoning_effort = reasoning_effort
        self._api_key = api_key
        self._max_output_tokens = max_output_tokens
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            proxy=proxy_url,
            follow_redirects=False,
        )

    async def judge(self, request: MonitorJudgmentRequest) -> MonitorJudgmentResponse:
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "playbook": request.playbook,
                            "confirmed_state": json.loads(request.confirmed_state_json),
                            "features": json.loads(request.feature_snapshot_json),
                            "allowed_feature_ids": request.allowed_feature_ids,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "thinking": {"type": "enabled"},
            "reasoning_effort": self.reasoning_effort,
            "response_format": {"type": "json_object"},
            "max_tokens": self._max_output_tokens,
        }
        payload = await self._post(request_payload, retry=False)
        effective_effort = self.reasoning_effort
        content = _content(payload)
        if not content and self.reasoning_effort == "max":
            effective_effort = "high"
            payload = await self._post(
                {**request_payload, "reasoning_effort": effective_effort}, retry=True
            )
            content = _content(payload)
        try:
            result = _StructuredResponse.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise DataContractError("DeepSeek returned an invalid Monitor judgment") from exc
        if result.quantity_max < result.quantity_min:
            raise DataContractError("DeepSeek returned inverted quantity bounds")
        return MonitorJudgmentResponse(
            **result.model_dump(), reasoning_effort_used=effective_effort
        )

    async def _post(self, payload: dict[str, object], *, retry: bool) -> dict[str, object]:
        label = "retry" if retry else "request"
        try:
            response = await self._client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"DeepSeek Monitor judgment {label} timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"DeepSeek Monitor judgment {label} transport failed"
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("DeepSeek Monitor judgment authentication failed")
        if response.status_code == 429:
            raise ProviderRateLimitError("DeepSeek Monitor judgment was rate limited")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"DeepSeek Monitor judgment {label} failed",
                details={"status_code": response.status_code},
            )
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError("response must be an object")
            return value
        except (TypeError, ValueError) as exc:
            raise DataContractError("DeepSeek returned a non-JSON response") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _content(payload: dict[str, object]) -> str:
    try:
        choices = payload["choices"]
        assert isinstance(choices, list)
        message = choices[0]["message"]
        assert isinstance(message, dict)
        content = message["content"]
        return content if isinstance(content, str) else ""
    except (AssertionError, KeyError, IndexError, TypeError):
        return ""
