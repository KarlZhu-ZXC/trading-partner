"""Telegram Bot API transport for the Shared Agent Runtime.

Polling policy lives in :mod:`interfaces.telegram.agent_poller`; this module
owns only the HTTP client and keeps the historical import path as a compatibility
facade for local callers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from domain.common.errors import DataContractError
from domain.notifications.rendering import TELEGRAM_MAX_TEXT_LENGTH
from interfaces.telegram.agent_poller import (
    _MAX_UPDATE_BATCH,
    TELEGRAM_AGENT_CHANNEL,
    TELEGRAM_AGENT_CURSOR_KEY,
    TELEGRAM_AGENT_OWNER_PRINCIPAL,
    TelegramActionGateway,
    TelegramAgentClient,
    TelegramAgentClientError,
    TelegramAgentPoller,
    TelegramPollReceipt,
    TelegramUpdate,
    _parse_update,
    split_telegram_text,
    validate_agent_chat_id,
    validate_agent_user_id,
)

_MAX_RESPONSE_BYTES = 64_000


class TelegramBotAgentClient:
    """Small allowlisted Bot API client used only by the Agent poller."""

    def __init__(
        self,
        *,
        bot_token: str,
        timeout_seconds: float = 40.0,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token.strip():
            raise DataContractError("Telegram Agent bot token is required")
        if timeout_seconds <= 0:
            raise DataContractError("Telegram Agent timeout must be positive")
        self._bot_token = bot_token.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout_seconds,
            trust_env=False,
            proxy=proxy_url,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_updates(
        self,
        *,
        offset: int,
        timeout_seconds: int,
    ) -> tuple[TelegramUpdate, ...]:
        if type(offset) is not int or offset < 0:
            raise DataContractError("Telegram Agent offset must be nonnegative")
        if type(timeout_seconds) is not int or not 0 <= timeout_seconds <= 50:
            raise DataContractError("Telegram Agent poll timeout is invalid")
        payload = {
            "offset": offset,
            "timeout": timeout_seconds,
            "limit": _MAX_UPDATE_BATCH,
            "allowed_updates": ["message", "callback_query"],
        }
        response = await self._post("getUpdates", payload)
        result = response.get("result")
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
            raise TelegramAgentClientError("Telegram update response is invalid", retryable=False)
        updates: list[TelegramUpdate] = []
        for item in result:
            parsed = _parse_update(item)
            if parsed is not None:
                updates.append(parsed)
        return tuple(updates)

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Mapping[str, object] | None = None,
    ) -> bool:
        if validate_agent_chat_id(chat_id) != chat_id:
            raise DataContractError("Telegram Agent chat id is invalid")
        if not text or len(text) > TELEGRAM_MAX_TEXT_LENGTH:
            raise DataContractError("Telegram Agent message is outside the size bound")
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)
        response = await self._post("sendMessage", payload)
        return response.get("ok") is True and isinstance(response.get("result"), Mapping)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        if not callback_query_id or len(callback_query_id) > 256:
            raise DataContractError("Telegram callback query id is invalid")
        payload: dict[str, object] = {
            "callback_query_id": callback_query_id,
            "show_alert": bool(show_alert),
        }
        if text:
            payload["text"] = text[:200]
        response = await self._post("answerCallbackQuery", payload)
        return response.get("ok") is True

    async def _post(self, method: str, payload: Mapping[str, object]) -> dict[str, Any]:
        endpoint = f"https://api.telegram.org/bot{self._bot_token}/{method}"
        try:
            response = await self._client.post(
                endpoint,
                json=dict(payload),
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise TelegramAgentClientError("Telegram Agent request timed out") from exc
        except httpx.HTTPError as exc:
            raise TelegramAgentClientError("Telegram Agent transport failed") from exc
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise TelegramAgentClientError(
                "Telegram Agent response exceeded the safety bound",
                retryable=False,
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise TelegramAgentClientError(
                "Telegram Agent response is not valid JSON",
                retryable=False,
            ) from exc
        if not isinstance(value, dict):
            raise TelegramAgentClientError(
                "Telegram Agent response is not an object",
                retryable=False,
            )
        if response.status_code == 429:
            raise TelegramAgentClientError("Telegram Agent rate limit reached")
        if response.status_code >= 500:
            raise TelegramAgentClientError("Telegram Agent provider unavailable")
        if response.status_code == 401:
            raise TelegramAgentClientError(
                "Telegram Agent authentication failed",
                retryable=False,
                code="TELEGRAM_AGENT_AUTH_FAILED",
            )
        if response.status_code >= 400 or value.get("ok") is not True:
            raise TelegramAgentClientError(
                "Telegram Agent request was rejected",
                retryable=False,
                code="TELEGRAM_AGENT_REQUEST_REJECTED",
            )
        return value


__all__ = [
    "TELEGRAM_AGENT_CHANNEL",
    "TELEGRAM_AGENT_CURSOR_KEY",
    "TELEGRAM_AGENT_OWNER_PRINCIPAL",
    "TelegramAgentClient",
    "TelegramAgentClientError",
    "TelegramAgentPoller",
    "TelegramActionGateway",
    "TelegramBotAgentClient",
    "TelegramPollReceipt",
    "TelegramUpdate",
    "split_telegram_text",
    "validate_agent_chat_id",
    "validate_agent_user_id",
]
