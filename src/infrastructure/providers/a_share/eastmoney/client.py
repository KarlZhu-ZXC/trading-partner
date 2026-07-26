"""Shared Eastmoney HTTP/gate/status/content-type boundary."""

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
from infrastructure.providers.a_share.eastmoney_gate import EastmoneyRequestGate

_JSON_CONTENT = ("application/json", "text/json", "text/plain")


class EastmoneyHttpClient:
    """Own transport access and response-envelope validation for all endpoints."""

    def __init__(
        self,
        transport: HttpTransport,
        gate: EastmoneyRequestGate,
        *,
        user_agent: str,
    ) -> None:
        self._transport = transport
        self._gate = gate
        self._user_agent = user_agent

    async def send(self, request: HttpRequest) -> HttpResponse:
        async def operation() -> HttpResponse:
            return await self._transport.send(request)

        return await self._gate.run(operation)

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
            "Referer": "https://quote.eastmoney.com/",
        }

    @staticmethod
    def require_success(status_code: int, *, operation: str) -> None:
        details = {"vendor": VendorId.EASTMONEY.value, "operation": operation}
        if status_code == 429:
            raise ProviderRateLimitError(
                "Eastmoney rate limited",
                details={**details, "error_type": "rate_limit", "status_class": "4xx"},
            )
        if status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Eastmoney access blocked",
                details={**details, "error_type": "blocked", "status_class": "4xx"},
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Eastmoney HTTP failure",
                details={
                    **details,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    @staticmethod
    def require_json_content_type(
        headers: Mapping[str, str], *, operation: str
    ) -> None:
        if content_type_matches(headers, allowed_substrings=_JSON_CONTENT):
            return
        details = {
            "vendor": VendorId.EASTMONEY.value,
            "operation": operation,
            "rule": "content_type",
        }
        if not headers.get("content-type") and not headers.get("Content-Type"):
            raise DataContractError(
                "Eastmoney response missing Content-Type", details=details
            )
        raise DataContractError(
            "Eastmoney response Content-Type is not acceptable", details=details
        )
