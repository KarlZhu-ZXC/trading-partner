"""Moomoo OpenD US community-attention hot-list adapter."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    ProviderNotConfigured,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.us_market.models import USCommunityHeatItem, USCommunityHeatSnapshot
from infrastructure.providers.moomoo_rate_limiter import (
    MoomooOpenDOperation,
    OpenDRequestLimiter,
)
from infrastructure.system.clock import SystemClock

_BASIS = "moomoo_opend_hot_list_composite_heat"
_WARNINGS = (
    "MOOMOO_COMMUNITY_HEAT_IS_ATTENTION_NOT_DIRECTION",
    "MOOMOO_COMMUNITY_HEAT_OBSERVED_AT_FETCH_TIME",
)


class _CommunityContext(Protocol):
    def get_hot_list(self, *, count: int) -> tuple[bool, object]: ...

    def close(self) -> object: ...


ContextFactory = Callable[[str, int], _CommunityContext]


class _SdkCommunityContext:
    def __init__(self, context: Any, market_us: object, ret_ok: object) -> None:
        self._context = context
        self._market_us = market_us
        self._ret_ok = ret_ok

    def get_hot_list(self, *, count: int) -> tuple[bool, object]:
        ret, value = self._context.get_hot_list(market=self._market_us, count=count)
        return ret == self._ret_ok, value

    def close(self) -> object:
        return self._context.close()


def _default_context_factory(host: str, port: int) -> _CommunityContext:
    try:
        import moomoo
    except ImportError as exc:
        raise ProviderNotConfigured("Moomoo SDK is unavailable") from exc
    moomoo.SysConfig.enable_console_log(False)
    return _SdkCommunityContext(
        moomoo.OpenQuoteContext(host=host, port=port),
        moomoo.Market.US,
        moomoo.RET_OK,
    )


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _records(value: object) -> list[Mapping[str, object]]:
    payload = value
    if isinstance(value, tuple) and len(value) == 2:
        payload = value[1]
    if isinstance(payload, list) and all(isinstance(row, Mapping) for row in payload):
        return list(payload)
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        rows = to_dict(orient="records")
        if isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows):
            return list(rows)
    raise DataContractError(
        "Moomoo community hot-list payload is invalid",
        details={"vendor": VendorId.MOOMOO.value, "operation": "community_hot_list"},
    )


def _text(value: object, *, max_len: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise DataContractError("Moomoo community text field is required")
        return None
    if not isinstance(value, str):
        raise DataContractError("Moomoo community text field has invalid type")
    normalized = " ".join(value.split())
    if not normalized:
        if required:
            raise DataContractError("Moomoo community text field is blank")
        return None
    return normalized[:max_len]


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


class MoomooCommunityHeatAdapter:
    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        port: int,
        clock: Clock | None = None,
        context_factory: ContextFactory | None = None,
        opend_rate_limiter: OpenDRequestLimiter | None = None,
    ) -> None:
        if not _is_loopback(host):
            raise DataContractError(
                "Moomoo community host must be loopback",
                details={"field": "host", "rule": "loopback"},
            )
        if type(port) is not int or not 1 <= port <= 65535:
            raise DataContractError(
                "Moomoo community port is invalid",
                details={"field": "port", "rule": "range"},
            )
        self._enabled = bool(enabled)
        self._host = host
        self._port = port
        self._clock = clock or SystemClock()
        self._context_factory = context_factory or _default_context_factory
        self._opend_rate_limiter = opend_rate_limiter

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.MOOMOO

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.COMMUNITY_HEAT

    def is_configured(self) -> bool:
        return self._enabled

    async def get_community_heat(
        self, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[USCommunityHeatSnapshot]:
        require_aware_datetime(as_of, field_name="as_of")
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo community heat provider is disabled")
        if as_of > self._clock.now():
            raise DataContractError("as_of must not be in the future")
        if not 1 <= limit <= 200:
            raise DataContractError("community heat limit must be in [1,200]")
        return await asyncio.to_thread(self._read, limit, as_of)

    def _read(self, limit: int, as_of: datetime) -> ProviderSuccess[USCommunityHeatSnapshot]:
        if self._opend_rate_limiter is not None:
            self._opend_rate_limiter.wait(MoomooOpenDOperation.COMMUNITY_HOT_LIST)
        context = self._context_factory(self._host, self._port)
        try:
            ok, payload = context.get_hot_list(count=limit)
        finally:
            context.close()
        if not ok:
            message = str(payload)
            if "unknown protocol" in message.casefold():
                raise ProviderUnavailableError(
                    "Moomoo OpenD does not support the community hot-list protocol",
                    code="MOOMOO_OPEND_VERSION_UNSUPPORTED",
                    retryable=False,
                    details={
                        "vendor": VendorId.MOOMOO.value,
                        "operation": "community_hot_list",
                        "minimum_opend_version": "10.9",
                    },
                )
            raise ProviderUnavailableError(
                "Moomoo community hot-list request failed",
                details={"vendor": VendorId.MOOMOO.value, "operation": "community_hot_list"},
            )

        fetched_at = self._clock.now()
        items = tuple(
            self._item(row, rank=index)
            for index, row in enumerate(_records(payload)[:limit], start=1)
        )
        return ProviderSuccess(
            USCommunityHeatSnapshot(observed_at=fetched_at, basis=_BASIS, items=items),
            ProviderResultMeta(
                vendor=self.vendor_id,
                category=DataCategory.COMMUNITY_HEAT,
                role=SourceRole.SUPPLEMENTAL,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.FRESH,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=None,
                data_delay_seconds=None,
                warnings=_WARNINGS,
            ),
        )

    @staticmethod
    def _item(row: Mapping[str, object], *, rank: int) -> USCommunityHeatItem:
        provider_code = _text(row.get("security"), max_len=64, required=True)
        name = _text(row.get("name"), max_len=256, required=True)
        return USCommunityHeatItem(
            provider_code=cast(str, provider_code),
            name=cast(str, name),
            rank=rank,
            trade_heat=_decimal(row.get("trade_heat")),
            trade_heat_change=_decimal(row.get("trade_heat_change")),
            search_heat=_decimal(row.get("search_heat")),
            search_heat_change=_decimal(row.get("search_heat_change")),
            news_heat=_decimal(row.get("news_heat")),
            news_heat_change=_decimal(row.get("news_heat_change")),
            average_heat=_decimal(row.get("average_heat")),
            average_heat_change=_decimal(row.get("average_heat_change")),
            related_content_type=_text(row.get("news_type"), max_len=32),
            related_title=_text(row.get("news_title"), max_len=500),
            related_url=_text(row.get("news_url"), max_len=2_000),
        )
