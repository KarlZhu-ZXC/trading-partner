"""Manual CSV watchlist adapter (Phase 2 v1 schema)."""

from __future__ import annotations

import asyncio
import csv
import os
import tempfile
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from application.dto.watchlist_source import (
    WatchlistSourceGroup,
    WatchlistSourceGroupType,
    WatchlistSourceMembership,
)
from application.ports.clock import Clock
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from domain.common.enums import Market
from domain.common.errors import DataContractError, ProviderNotConfigured
from domain.common.values import parse_instrument_id
from domain.watchlist.enums import WatchlistSource
from infrastructure.system.clock import SystemClock

fcntl: types.ModuleType | None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    fcntl = None
else:
    fcntl = _fcntl


_HEADERS = ("schema_version", "group_name", "instrument_id", "display_name")


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be text",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.strip():
        raise DataContractError(f"{field} must not be blank", details={"field": field})
    if value.startswith(("=", "+", "-", "@")):
        raise DataContractError(
            f"{field} contains forbidden formula prefix",
            details={"field": field},
        )
    return value


class ManualCsvWatchlistAdapter(WatchlistSourceProvider):
    """Manual CSV watchlist source (read/write)."""

    def __init__(
        self,
        path: Path | None,
        *,
        default_group: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._path = path
        self._default_group = (
            None
            if default_group is None
            else _require_text(default_group, field="default_group")
        )
        self._clock = clock or SystemClock()

    @property
    def source(self) -> WatchlistSource:
        return WatchlistSource.MANUAL_CSV

    async def list_groups(self) -> tuple[WatchlistSourceGroup, ...]:
        return await asyncio.to_thread(self._list_groups)

    async def list_memberships(
        self,
        group_name: str | None = None,
    ) -> tuple[WatchlistSourceMembership, ...]:
        return await asyncio.to_thread(
            self._list_memberships,
            group_name=group_name,
        )

    async def add_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
        display_name: str,
    ) -> WatchlistSourceMembership:
        _ = _require_text(group_name, field="group_name")
        _ = _require_text(display_name, field="display_name")
        _ = self._validate_instrument_id(provider_code)
        return await asyncio.to_thread(
            self._add_membership,
            group_name=group_name,
            provider_code=provider_code,
            display_name=display_name,
        )

    async def remove_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
    ) -> WatchlistSourceMembership:
        _ = _require_text(group_name, field="group_name")
        _ = self._validate_instrument_id(provider_code)
        return await asyncio.to_thread(
            self._remove_membership,
            group_name=group_name,
            provider_code=provider_code,
        )

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, str]]:
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != _HEADERS:
                    raise DataContractError(
                        "watchlist CSV header must be "
                        "schema_version,group_name,instrument_id,display_name",
                        details={"field": "header"},
                    )
                rows = [dict(row) for row in reader]
        except OSError as exc:
            raise ProviderNotConfigured("Manual watchlist CSV cannot be read") from exc

        seen = set[tuple[str, str]]()
        for row in rows:
            _require_text(row["schema_version"], field="schema_version")
            if row["schema_version"] != "1":
                raise DataContractError(
                    "schema_version must be 1",
                    details={"field": "schema_version"},
                )
            group_name = _require_text(row["group_name"], field="group_name")
            instrument_id = _require_text(row["instrument_id"], field="instrument_id")
            display_name = _require_text(row["display_name"], field="display_name")
            _, market, _ = parse_instrument_id(instrument_id)
            if market not in {Market.A_SHARE, Market.US, Market.KR}:
                raise DataContractError(
                    "manual watchlist supports A_SHARE, US, and KR instruments only",
                    details={"instrument_id": instrument_id},
                )
            key = (group_name, instrument_id)
            if key in seen:
                raise DataContractError(
                    "watchlist CSV duplicate membership",
                    details={"group_name": group_name, "instrument_id": instrument_id},
                )
            seen.add(key)
            row["group_name"] = group_name
            row["instrument_id"] = instrument_id
            row["display_name"] = display_name
        return rows

    @staticmethod
    def _validate_instrument_id(value: str) -> str:
        value = _require_text(value, field="instrument_id")
        _, market, _ = parse_instrument_id(value)
        if market not in {Market.A_SHARE, Market.US, Market.KR}:
            raise DataContractError(
                "manual watchlist supports A_SHARE, US, and KR instruments only",
                details={"instrument_id": value},
            )
        return value

    @staticmethod
    def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd: int | None = None
        tmp_path: Path | None = None
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".watchlist_", suffix=".csv")
            tmp_path = Path(tmp)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                fd = None
                writer = csv.DictWriter(handle, fieldnames=_HEADERS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            "schema_version": "1",
                            "group_name": row["group_name"],
                            "instrument_id": row["instrument_id"],
                            "display_name": row["display_name"],
                        },
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    @contextmanager
    def _exclusive_lock(self, path: Path) -> Iterator[None]:
        lock_path = path.with_suffix(".watchlist.lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _list_groups(self) -> tuple[WatchlistSourceGroup, ...]:
        if self._path is None:
            raise ProviderNotConfigured("Manual watchlist CSV path is not configured")
        rows = self._read_rows(self._path)
        groups = []
        seen = set[str]()
        for row in rows:
            group_name = row["group_name"]
            if group_name not in seen:
                seen.add(group_name)
                groups.append(
                    WatchlistSourceGroup(
                        source=WatchlistSource.MANUAL_CSV,
                        name=group_name,
                        group_type=WatchlistSourceGroupType.MANUAL,
                        writable=True,
                    ),
                )
        if not groups and self._default_group is not None:
            groups.append(
                WatchlistSourceGroup(
                    source=WatchlistSource.MANUAL_CSV,
                    name=self._default_group,
                    group_type=WatchlistSourceGroupType.MANUAL,
                    writable=True,
                )
            )
        return tuple(groups)

    def _list_memberships(
        self,
        group_name: str | None = None,
    ) -> tuple[WatchlistSourceMembership, ...]:
        if self._path is None:
            raise ProviderNotConfigured("Manual watchlist CSV path is not configured")
        rows = self._read_rows(self._path)
        memberships: list[WatchlistSourceMembership] = []
        for row in rows:
            if group_name is not None and row["group_name"] != group_name:
                continue
            membership = WatchlistSourceMembership(
                source=WatchlistSource.MANUAL_CSV,
                group_name=row["group_name"],
                provider_code=row["instrument_id"],
                display_name=row["display_name"],
                instrument_id=row["instrument_id"],
                provider_asset_type=None,
                research_supported=True,
                group_writable=True,
            )
            memberships.append(membership)
        return tuple(memberships)

    def _add_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
        display_name: str,
    ) -> WatchlistSourceMembership:
        if self._path is None:
            raise ProviderNotConfigured("Manual watchlist CSV path is not configured")
        with self._exclusive_lock(self._path):
            rows = self._read_rows(self._path)
            group_name = _require_text(group_name, field="group_name")
            provider_code = self._validate_instrument_id(provider_code)
            display_name = _require_text(display_name, field="display_name")
            new_key = (group_name, provider_code)
            for row in rows:
                if (row["group_name"], row["instrument_id"]) == new_key:
                    raise DataContractError(
                        "watchlist membership already exists",
                        details={"group_name": group_name, "instrument_id": provider_code},
                    )
            rows.append(
                {
                    "schema_version": "1",
                    "group_name": group_name,
                    "instrument_id": provider_code,
                    "display_name": display_name,
                },
            )
            self._write_rows(self._path, rows)
            verified = self._read_rows(self._path)
            for row in verified:
                if (row["group_name"], row["instrument_id"]) == new_key:
                    return WatchlistSourceMembership(
                        source=WatchlistSource.MANUAL_CSV,
                        group_name=row["group_name"],
                        provider_code=row["instrument_id"],
                        display_name=row["display_name"],
                        instrument_id=row["instrument_id"],
                        provider_asset_type=None,
                        research_supported=True,
                        group_writable=True,
                    )
            raise DataContractError(
                "watchlist add verification failed",
                details={"group_name": group_name, "instrument_id": provider_code},
            )

    def _remove_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
    ) -> WatchlistSourceMembership:
        if self._path is None:
            raise ProviderNotConfigured("Manual watchlist CSV path is not configured")
        target = None
        with self._exclusive_lock(self._path):
            rows = self._read_rows(self._path)
            group_name = _require_text(group_name, field="group_name")
            provider_code = self._validate_instrument_id(provider_code)
            remaining = []
            for row in rows:
                if row["group_name"] == group_name and row["instrument_id"] == provider_code:
                    if target is None:
                        target = row
                else:
                    remaining.append(row)
            if target is None:
                raise DataContractError(
                    "watchlist membership not found",
                    details={"group_name": group_name, "instrument_id": provider_code},
                )
            self._write_rows(self._path, remaining)
            verified = self._read_rows(self._path)
            for row in verified:
                if row["group_name"] == group_name and row["instrument_id"] == provider_code:
                    raise DataContractError(
                        "watchlist remove verification failed",
                        details={"group_name": group_name, "instrument_id": provider_code},
                    )
            return WatchlistSourceMembership(
                source=WatchlistSource.MANUAL_CSV,
                group_name=target["group_name"],
                provider_code=target["instrument_id"],
                display_name=target["display_name"],
                instrument_id=target["instrument_id"],
                provider_asset_type=None,
                research_supported=True,
                group_writable=True,
            )
