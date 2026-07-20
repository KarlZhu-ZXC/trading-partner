"""Moomoo Quote Context watchlist adapter."""

from __future__ import annotations

import asyncio
import ipaddress
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from application.dto.watchlist_source import (
    WatchlistSourceGroup,
    WatchlistSourceGroupType,
    WatchlistSourceMembership,
)
from application.ports.clock import Clock
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError, ProviderNotConfigured, ProviderUnavailableError
from domain.common.values import build_instrument_id
from domain.watchlist.enums import WatchlistSource
from infrastructure.providers.moomoo_rate_limiter import (
    MoomooOpenDOperation,
    OpenDRequestLimiter,
)
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)
from infrastructure.system.clock import SystemClock


class _WatchlistContext(Protocol):
    def get_user_security_group(self) -> tuple[object, object]:
        ...

    def get_user_security(self, group_name: str) -> tuple[object, object]:
        ...

    def modify_user_security(
        self, group_name: str, op: str, code_list: Sequence[str]
    ) -> tuple[object, object]:
        ...

    def close(self) -> object:
        ...


ContextFactory = Callable[[str, int], _WatchlistContext]

_WRITE_OPERATIONS = {"ADD", "DEL"}


class _SdkWatchlistContext:
    """Translate the stable adapter operation strings to Moomoo SDK enums."""

    def __init__(self, context: Any, operation_type: object) -> None:
        self._context = context
        self._operation_type = operation_type

    def get_user_security_group(self) -> tuple[object, object]:
        return cast(tuple[object, object], self._context.get_user_security_group())

    def get_user_security(self, group_name: str) -> tuple[object, object]:
        return cast(tuple[object, object], self._context.get_user_security(group_name))

    def modify_user_security(
        self, group_name: str, op: str, code_list: Sequence[str]
    ) -> tuple[object, object]:
        try:
            sdk_op = getattr(self._operation_type, op)
        except AttributeError as exc:
            raise DataContractError(
                "watchlist operation unsupported", details={"op": op}
            ) from exc
        return cast(
            tuple[object, object],
            self._context.modify_user_security(group_name, sdk_op, code_list),
        )

    def close(self) -> object:
        return self._context.close()


def _default_context_factory(host: str, port: int) -> _WatchlistContext:
    try:
        import moomoo
    except ImportError as exc:
        raise ProviderNotConfigured("Moomoo SDK is unavailable") from exc
    moomoo.SysConfig.enable_console_log(False)
    context = moomoo.OpenQuoteContext(host=host, port=port)
    return _SdkWatchlistContext(context, moomoo.ModifyUserSecurityOp)


def _records(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return list(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        rows = to_dict(orient="records")
        if isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows):
            return list(rows)
    raise DataContractError(
        "Moomoo watchlist payload is invalid",
        details={"vendor": WatchlistSource.MOOMOO.value},
    )


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be text",
            details={"field": field, "type": type(value).__name__},
        )
    if value == "":
        raise DataContractError(f"{field} must not be blank", details={"field": field})
    if value.startswith(("=", "+", "-", "@")):
        raise DataContractError(
            f"{field} contains forbidden formula prefix",
            details={"field": field},
        )
    return value


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be text or null",
            details={"field": field, "type": type(value).__name__},
        )
    return value if value else None


def _require_positive_int(value: int, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DataContractError(
            f"{field} must be a positive integer",
            details={"field": field, "value": value},
        )
    return value


def _parse_code_instrument_id(
    code: str,
    provider_asset_type: str | None,
    *,
    corrected_asset_type: AssetType | None = None,
) -> tuple[str | None, str | None]:
    asset_type_by_provider = {
        "STOCK": AssetType.EQUITY,
        "ETF": AssetType.ETF,
        "IDX": AssetType.INDEX,
        "INDEX": AssetType.INDEX,
        "OPTION": AssetType.OPTION,
    }
    asset_type = corrected_asset_type or asset_type_by_provider.get(
        (provider_asset_type or "").upper()
    )
    if asset_type is None:
        return None, None
    if "." not in code:
        return None, None
    prefix, symbol = code.split(".", 1)
    prefix = prefix.upper()
    symbol = symbol.strip()
    if not symbol:
        return None, None
    if prefix == "US":
        return build_instrument_id(asset_type, Market.US, symbol.upper()), "US"
    if prefix in {"SH", "SZ"}:
        return (
            build_instrument_id(
                asset_type,
                Market.A_SHARE,
                f"{symbol}.{prefix}",
            ),
            "A_SHARE",
        )
    return None, None


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _InProcessRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = _require_positive_int(max_requests, field="max_requests")
        self._window_seconds = window_seconds
        if not isinstance(window_seconds, int | float) or window_seconds <= 0:
            raise DataContractError(
                "window_seconds must be a positive float",
                details={"field": "window_seconds"},
            )
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            now = time.monotonic()
            with self._lock:
                while self._events and now - self._events[0] >= self._window_seconds:
                    self._events.popleft()
                if len(self._events) < self._max_requests:
                    self._events.append(now)
                    return
                wait_for = self._window_seconds - (now - self._events[0])
            time.sleep(max(wait_for, 0.0) + 0.0001)


class MoomooWatchlistAdapter(WatchlistSourceProvider):
    """Read/write watchlist adapter over Moomoo Quote Context."""

    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        port: int,
        clock: Clock | None = None,
        context_factory: ContextFactory | None = None,
        opend_rate_limiter: OpenDRequestLimiter | None = None,
        security_corrections: MoomooSecurityCorrections | None = None,
        max_groups_per_refresh: int = 120,
        rate_limit_requests: int = 10,
        rate_limit_window_seconds: float = 30.0,
    ) -> None:
        if not _is_loopback_host(host):
            raise DataContractError(
                "watchlist host must be loopback",
                details={"field": "host", "value": host},
            )
        self._enabled = enabled
        self._host = host
        self._port = _require_positive_int(port, field="port")
        self._clock = clock or SystemClock()
        self._context_factory = context_factory or _default_context_factory
        self._opend_rate_limiter = opend_rate_limiter
        self._security_corrections = security_corrections or MoomooSecurityCorrections.empty()
        self._in_process_rate_limiter = (
            _InProcessRateLimiter(
                rate_limit_requests,
                rate_limit_window_seconds,
            )
            if opend_rate_limiter is None
            else None
        )
        self._max_groups_per_refresh = _require_positive_int(
            max_groups_per_refresh,
            field="max_groups_per_refresh",
        )

    @property
    def source(self) -> WatchlistSource:
        return WatchlistSource.MOOMOO

    def is_configured(self) -> bool:
        return self._enabled

    async def list_groups(self) -> tuple[WatchlistSourceGroup, ...]:
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo watchlist adapter is disabled")
        return await asyncio.to_thread(self._list_groups)

    async def list_memberships(
        self,
        group_name: str | None = None,
    ) -> tuple[WatchlistSourceMembership, ...]:
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo watchlist adapter is disabled")
        return await asyncio.to_thread(self._list_memberships, group_name=group_name)

    async def add_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
        display_name: str,
    ) -> WatchlistSourceMembership:
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo watchlist adapter is disabled")
        _ = _require_text(display_name, field="display_name")
        return await asyncio.to_thread(
            self._add_remove_membership,
            group_name=group_name,
            provider_code=provider_code,
            op="ADD",
        )

    async def remove_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
    ) -> WatchlistSourceMembership:
        if not self._enabled:
            raise ProviderNotConfigured("Moomoo watchlist adapter is disabled")
        return await asyncio.to_thread(
            self._add_remove_membership,
            group_name=group_name,
            provider_code=provider_code,
            op="DEL",
        )

    @staticmethod
    def _query_rows(result: tuple[object, object]) -> tuple[Mapping[str, object], ...]:
        code, value = result
        if code != 0:
            raise ProviderUnavailableError("Moomoo watchlist request failed")
        return tuple(_records(value))

    @staticmethod
    def _query_mutation(result: tuple[object, object], *, op: str) -> None:
        code, value = result
        if code != 0:
            raise ProviderUnavailableError(f"Moomoo watchlist modify failed: {op}")
        if value in {None, "success", "Success", "ok", "OK"}:
            return
        raise DataContractError(
            "Moomoo watchlist mutation response is unexpected",
            details={"op": op, "value": value},
        )

    def _query_context(
        self,
        operation: MoomooOpenDOperation,
        fn: Callable[..., tuple[object, object]],
        *args: object,
    ) -> tuple[object, object]:
        if self._opend_rate_limiter is not None:
            self._opend_rate_limiter.wait(operation)
        elif self._in_process_rate_limiter is not None:
            self._in_process_rate_limiter.wait()
        return fn(*args)

    def _list_groups(self) -> tuple[WatchlistSourceGroup, ...]:
        try:
            context = self._context_factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError("Moomoo context creation failed") from None
        try:
            rows = self._query_rows(
                self._query_context(
                    MoomooOpenDOperation.WATCHLIST_GROUPS,
                    context.get_user_security_group,
                )
            )
            return tuple(self._group(group) for group in rows)
        finally:
            context.close()

    def _list_memberships(self, group_name: str | None) -> tuple[WatchlistSourceMembership, ...]:
        try:
            context = self._context_factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError("Moomoo context creation failed") from None
        try:
            groups = self._list_groups_within_context(context)
            if group_name is None:
                if len(groups) > self._max_groups_per_refresh:
                    raise DataContractError(
                        "watchlist group fanout exceeds policy limit",
                        details={"count": len(groups), "max": self._max_groups_per_refresh},
                    )
                selected = groups
            else:
                selected = tuple(group for group in groups if group.name == group_name)
                if not selected:
                    raise DataContractError(
                        "watchlist group not found",
                        details={"group_name": group_name},
                    )
            memberships: list[WatchlistSourceMembership] = []
            for group in selected:
                rows = self._query_rows(
                    self._query_context(
                        MoomooOpenDOperation.WATCHLIST_MEMBERS,
                        context.get_user_security,
                        group.name,
                    ),
                )
                memberships.extend(self._membership(group, row) for row in rows)
            return tuple(memberships)
        finally:
            context.close()

    def _add_remove_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
        op: str,
    ) -> WatchlistSourceMembership:
        provider_code = _require_text(provider_code, field="provider_code")
        try:
            context = self._context_factory(self._host, self._port)
        except ProviderNotConfigured:
            raise
        except Exception:
            raise ProviderUnavailableError("Moomoo context creation failed") from None
        try:
            if op not in _WRITE_OPERATIONS:
                raise DataContractError("watchlist operation unsupported", details={"op": op})
            groups = self._list_groups_within_context(context)
            target = None
            for group in groups:
                if group.name == group_name:
                    target = group
                    break
            if target is None:
                raise DataContractError(
                    "watchlist group not found",
                    details={"group_name": group_name},
                )
            if not target.writable:
                raise DataContractError(
                    "watchlist group is read-only",
                    details={"group_name": group_name},
                )

            before = tuple(
                item
                for item in self._memberships_within_context(context, target)
                if item.provider_code == provider_code
            )
            if op == "ADD":
                self._query_mutation(
                    self._query_context(
                        MoomooOpenDOperation.WATCHLIST_MODIFY,
                        context.modify_user_security,
                        target.name,
                        op,
                        [provider_code],
                    ),
                    op=op,
                )
            else:
                if not before:
                    raise DataContractError(
                        "watchlist member does not exist",
                        details={"group_name": group_name, "provider_code": provider_code},
                    )
                self._query_mutation(
                    self._query_context(
                        MoomooOpenDOperation.WATCHLIST_MODIFY,
                        context.modify_user_security,
                        target.name,
                        op,
                        [provider_code],
                    ),
                    op=op,
                )

            after = self._memberships_within_context(context, target)
            if op == "ADD":
                for item in after:
                    if item.provider_code == provider_code:
                        return item
                raise DataContractError(
                    "watchlist add verification failed",
                    details={"group_name": group_name, "provider_code": provider_code},
                )
            for item in after:
                if item.provider_code == provider_code:
                    raise DataContractError(
                        "watchlist remove verification failed",
                        details={"group_name": group_name, "provider_code": provider_code},
                    )
            return before[0]
        finally:
            context.close()

    def _list_groups_within_context(
        self,
        context: _WatchlistContext,
    ) -> tuple[WatchlistSourceGroup, ...]:
        rows = self._query_rows(
            self._query_context(
                MoomooOpenDOperation.WATCHLIST_GROUPS,
                context.get_user_security_group,
            )
        )
        return tuple(self._group(row) for row in rows)

    def _memberships_within_context(
        self,
        context: _WatchlistContext,
        group: WatchlistSourceGroup,
    ) -> tuple[WatchlistSourceMembership, ...]:
        rows = self._query_rows(
            self._query_context(
                MoomooOpenDOperation.WATCHLIST_MEMBERS,
                context.get_user_security,
                group.name,
            ),
        )
        return tuple(self._membership(group, row) for row in rows)

    @staticmethod
    def _group(row: Mapping[str, object]) -> WatchlistSourceGroup:
        name = _require_text(row.get("group_name"), field="group_name")
        group_type_raw = _require_text(row.get("group_type"), field="group_type")
        try:
            group_type = WatchlistSourceGroupType(group_type_raw.upper())
        except ValueError as exc:
            raise DataContractError(
                "Moomoo group type is unknown",
                details={"group_type": group_type_raw},
            ) from exc
        writable = group_type is WatchlistSourceGroupType.CUSTOM or (
            group_type is WatchlistSourceGroupType.SYSTEM and name == "Favorites"
        )
        return WatchlistSourceGroup(
            source=WatchlistSource.MOOMOO,
            name=name,
            group_type=group_type,
            writable=writable,
        )

    def _membership(
        self,
        group: WatchlistSourceGroup,
        row: Mapping[str, object],
    ) -> WatchlistSourceMembership:
        provider_code = _require_text(row.get("code"), field="code")
        correction = self._security_corrections.for_code(provider_code.upper())
        display_name = (
            correction.display_name
            if correction is not None
            else _require_text(row.get("name"), field="name")
        )
        provider_asset_type = _optional_text(row.get("stock_type"), field="stock_type")
        instrument_id, _ = _parse_code_instrument_id(
            provider_code,
            provider_asset_type,
            corrected_asset_type=(
                correction.asset_type if correction is not None else None
            ),
        )
        return WatchlistSourceMembership(
            source=WatchlistSource.MOOMOO,
            group_name=group.name,
            provider_code=provider_code,
            display_name=display_name,
            instrument_id=instrument_id,
            provider_asset_type=provider_asset_type,
            research_supported=instrument_id is not None,
            group_writable=group.writable,
        )
