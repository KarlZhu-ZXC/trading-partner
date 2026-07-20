"""Infrastructure-only SEC ticker → CIK identity resolution (Phase 1G G2).

Fetches the official company_tickers.json allowlisted endpoint and caches a
stable ticker→CIK10 map. Shared by filings and companyfacts adapters.
"""

from __future__ import annotations

import asyncio
from typing import Final

from application.ports.http_transport import HttpRequest, HttpTransport
from domain.common.enums import VendorId
from domain.common.errors import DataContractError, NoMarketData
from infrastructure.providers.us.sec_common import (
    JSON_CONTENT_TYPES,
    TICKERS_URL,
    content_type_ok,
    loads_sec_json_strict,
    pad_cik,
    raise_for_sec_http_status,
    sec_contract,
)

_OPERATION: Final[str] = "company_tickers"


class SECIdentityResolver:
    """Resolve US equity tickers to zero-padded 10-digit CIKs."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        user_agent: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        if transport is None:
            raise DataContractError(
                "transport is required",
                details={"field": "transport", "rule": "required"},
            )
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise DataContractError(
                "user_agent is required",
                details={"field": "user_agent", "rule": "required"},
            )
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
        self._user_agent = user_agent.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._ticker_map: dict[str, str] | None = None
        self._lock = asyncio.Lock()

    async def resolve_cik(self, symbol: str) -> str:
        mapping = await self._load_ticker_map()
        cik = mapping.get(symbol.strip().upper())
        if cik is None:
            raise NoMarketData(
                "SEC EDGAR has no CIK for symbol",
                details={
                    "vendor": VendorId.SEC_EDGAR.value,
                    "operation": "resolve_cik",
                    "rule": "unknown_symbol",
                },
            )
        return cik

    async def _load_ticker_map(self) -> dict[str, str]:
        if self._ticker_map is not None:
            return self._ticker_map
        async with self._lock:
            if self._ticker_map is not None:
                return self._ticker_map
            response = await self._transport.send(
                HttpRequest(
                    method="GET",
                    url=TICKERS_URL,
                    params={},
                    headers={
                        "Accept": "application/json,text/plain,*/*",
                        "User-Agent": self._user_agent,
                    },
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            raise_for_sec_http_status(response.status_code, operation=_OPERATION)
            if not content_type_ok(response.headers, JSON_CONTENT_TYPES):
                raise sec_contract(
                    "SEC EDGAR response Content-Type is not acceptable",
                    operation=_OPERATION,
                    rule="content_type",
                )
            payload = loads_sec_json_strict(response.body)
            if not isinstance(payload, dict):
                raise sec_contract(
                    "company_tickers payload must be an object",
                    operation=_OPERATION,
                    rule="contract_drift",
                )
            items: list[tuple[int, object]] = []
            for key, value in payload.items():
                if isinstance(key, str) and key.isdigit():
                    items.append((int(key), value))
                else:
                    items.append((10**12, value))
            items.sort(key=lambda pair: pair[0])
            mapping: dict[str, str] = {}
            for _idx, value in items:
                if not isinstance(value, dict):
                    continue
                ticker = value.get("ticker")
                cik_raw = value.get("cik_str")
                if not isinstance(ticker, str) or not ticker.strip():
                    continue
                cik = pad_cik(cik_raw)
                if cik is None:
                    continue
                key = ticker.strip().upper()
                if key not in mapping:
                    mapping[key] = cik
            if not mapping:
                raise sec_contract(
                    "company_tickers mapping is empty",
                    operation=_OPERATION,
                    rule="contract_drift",
                )
            self._ticker_map = mapping
            return mapping
