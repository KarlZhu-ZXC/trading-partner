"""Shared Sina HTTP/status/header boundary."""

from __future__ import annotations

from collections.abc import Mapping

from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from domain.common.enums import VendorId
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from infrastructure.providers.a_share._parsing import content_type_matches

_JSON_CONTENT = (
    "application/json",
    "text/json",
    "text/plain",
    "text/javascript",
)


class SinaHttpClient:
    def __init__(self, transport: HttpTransport, *, user_agent: str) -> None:
        self._transport = transport
        self._user_agent = user_agent

    async def send(self, request: HttpRequest) -> HttpResponse:
        return await self._transport.send(request)

    def json_headers(self, *, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
            "Referer": referer,
        }

    def script_headers(self, *, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/javascript,text/plain,*/*",
            "User-Agent": self._user_agent,
            "Referer": referer,
        }

    @staticmethod
    def require_success(status_code: int, *, operation: str) -> None:
        details = {"vendor": VendorId.SINA.value, "operation": operation}
        if status_code == 429:
            raise ProviderRateLimitError(
                "Sina rate limited",
                details={**details, "error_type": "rate_limit", "status_class": "4xx"},
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Sina access blocked",
                details={**details, "error_type": "blocked", "status_class": "4xx"},
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Sina HTTP failure",
                details={
                    **details,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    @staticmethod
    def require_json_content(headers: Mapping[str, str], *, operation: str) -> None:
        if content_type_matches(headers, allowed_substrings=_JSON_CONTENT):
            return
        raise DataContractError(
            "Sina response Content-Type is not acceptable",
            details={
                "vendor": VendorId.SINA.value,
                "operation": operation,
                "rule": "content_type",
            },
        )
