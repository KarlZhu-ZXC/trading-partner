"""Read Moomoo private Notes from the signed-in desktop Chromium cache."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from application.ports.clock import Clock
from application.ports.external_note_provider import (
    ExternalNoteScanResult,
    ObservationSourceCapability,
)
from domain.common.errors import ProviderNotConfigured
from domain.external_note.attribution import attributed_blocks, prefer_proven_complete_text
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteSourceSnapshot

_NOTE_LIST_MARKER = b"/community/v2/api/note/list"
_EDITOR_MARKER = b"window.__INITIAL_STATE__ = "
_MAX_CACHE_FILE_BYTES = 5 * 1024 * 1024
_MAX_COOKIE_BYTES = 32 * 1024
_NOTE_LIST_URL = "https://www.moomoo.com/community/v2/api/note/list"
_NOTE_EDITOR_URL = "https://www.moomoo.com/hans/community/sns-editor"
_REMOTE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
)
_SECURE_RANDOM = secrets.SystemRandom()


@dataclass(frozen=True, slots=True)
class _CachedNote:
    external_id: str
    title: str
    summary: str
    full_body: str | None
    source_timestamp: datetime | None
    related_stock_ids: tuple[str, ...]
    visibility: str
    mtime_ns: int


class _CookieUnavailable(Exception):
    """Internal secret-safe marker; its detail must never leave this module."""


class OwnerOnlyMoomooNoteCredentialStore:
    source_code = "MOOMOO_NOTE"

    def __init__(self, path: Path) -> None:
        self._path = path

    def configured(self) -> bool:
        return owner_only_cookie_file_configured(self._path)

    def set_secret(self, value: str) -> None:
        write_owner_only_cookie_file(self._path, value)


class MoomooNotesRemoteClient:
    """Read authenticated internal note pages without desktop automation."""

    def __init__(
        self,
        *,
        cookie_file: Path,
        delay_min_seconds: float,
        delay_max_seconds: float,
        timeout_seconds: float,
        max_stock_ids: int = 50,
        max_notes: int = 30,
        proxy_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = _SECURE_RANDOM.uniform,
    ) -> None:
        if delay_min_seconds < 0 or delay_max_seconds < delay_min_seconds:
            raise ValueError("invalid Moomoo note request delay window")
        if timeout_seconds <= 0:
            raise ValueError("Moomoo note request timeout must be positive")
        self._cookie_file = cookie_file
        self._delay_min_seconds = delay_min_seconds
        self._delay_max_seconds = delay_max_seconds
        self._timeout_seconds = timeout_seconds
        self._max_stock_ids = max(1, min(max_stock_ids, 100))
        self._max_notes = max(1, min(max_notes, 100))
        self._proxy_url = proxy_url
        self._transport = transport
        self._sleeper = sleeper
        self._random_uniform = random_uniform

    def fetch(
        self,
        *,
        listed: dict[str, _CachedNote],
        full: dict[str, _CachedNote],
        candidate_stock_ids: set[str],
    ) -> tuple[dict[str, _CachedNote], dict[str, _CachedNote], tuple[str, ...]]:
        try:
            cookie = read_owner_only_cookie_file(self._cookie_file)
        except _CookieUnavailable:
            return listed, full, ("MOOMOO_NOTES_REMOTE_COOKIE_UNAVAILABLE",)
        remote_listed = dict(listed)
        remote_full = dict(full)
        warnings: list[str] = []
        client_kwargs: dict[str, Any] = {
            "headers": {
                "Cookie": cookie,
                "Referer": "https://www.moomoo.com/hans/community/note/list",
                "User-Agent": _REMOTE_USER_AGENT,
            },
            "timeout": self._timeout_seconds,
            "follow_redirects": False,
            "trust_env": False,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        elif self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        for logger_name in ("httpx", "httpcore"):
            logging.getLogger(logger_name).disabled = True
        try:
            with httpx.Client(**client_kwargs) as client:
                stock_ids_to_refresh = (
                    ()
                    if remote_listed
                    else tuple(sorted(candidate_stock_ids)[: self._max_stock_ids])
                )
                for stock_id in stock_ids_to_refresh:
                    response = self._request(
                        client,
                        _NOTE_LIST_URL,
                        params={
                            "stockId": stock_id,
                            "loadListType": "2",
                            "num": "10",
                            "moreMark": "",
                            "sequence": "",
                            "lastReqFirstFeedId": "0",
                            "_": str(int(time.time() * 1000)),
                            "clientjs_reqid": str(uuid.uuid4()),
                        },
                    )
                    state = _response_state(response)
                    if state == "AUTH":
                        return listed, full, ("MOOMOO_NOTES_REMOTE_AUTH_REQUIRED",)
                    if state == "RATE_LIMIT":
                        warnings.append("MOOMOO_NOTES_REMOTE_RATE_LIMITED")
                        break
                    if state != "OK":
                        warnings.append("MOOMOO_NOTES_REMOTE_LIST_UNAVAILABLE")
                        continue
                    parsed = _parse_note_list_payload(response.content, time.time_ns())
                    if parsed is None:
                        warnings.append("MOOMOO_NOTES_REMOTE_LIST_INVALID")
                        continue
                    for note in parsed:
                        _prefer(remote_listed, note)

                detail_candidates = sorted(
                    remote_listed.values(),
                    key=lambda item: item.source_timestamp
                    or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )[: self._max_notes]
                detail_failures = 0
                for summary_note in detail_candidates:
                    if not summary_note.related_stock_ids:
                        detail_failures += 1
                        continue
                    response = self._request(
                        client,
                        _NOTE_EDITOR_URL,
                        params={
                            "security_id": summary_note.related_stock_ids[0],
                            "feed_type": "9",
                            "feed_id": summary_note.external_id,
                            "client_hour_clock": "24",
                            "clientlang": "0",
                            "clienttype": "121",
                            "is_visitor": "0",
                            "skintype": "1",
                        },
                    )
                    state = _response_state(response)
                    if state == "AUTH":
                        return listed, full, ("MOOMOO_NOTES_REMOTE_AUTH_REQUIRED",)
                    if state == "RATE_LIMIT":
                        warnings.append("MOOMOO_NOTES_REMOTE_RATE_LIMITED")
                        break
                    if state != "OK":
                        detail_failures += 1
                        continue
                    matches = tuple(
                        item
                        for item in _parse_editor_state(response.content, time.time_ns())
                        if item.external_id == summary_note.external_id
                    )
                    if not matches:
                        detail_failures += 1
                        continue
                    resolved = matches[-1]
                    _prefer(
                        remote_full,
                        _CachedNote(
                            external_id=resolved.external_id,
                            title=resolved.title,
                            summary=resolved.summary,
                            full_body=resolved.full_body,
                            source_timestamp=(
                                summary_note.source_timestamp or resolved.source_timestamp
                            ),
                            related_stock_ids=(
                                resolved.related_stock_ids or summary_note.related_stock_ids
                            ),
                            visibility=summary_note.visibility,
                            mtime_ns=resolved.mtime_ns,
                        ),
                    )
                if detail_failures:
                    warnings.append("MOOMOO_NOTES_REMOTE_DETAIL_PARTIAL")
        except (httpx.HTTPError, OSError):
            warnings.append("MOOMOO_NOTES_REMOTE_UNAVAILABLE")
        return remote_listed, remote_full, tuple(dict.fromkeys(warnings))

    def _request(
        self,
        client: httpx.Client,
        url: str,
        *,
        params: dict[str, str],
    ) -> httpx.Response:
        delay = self._random_uniform(self._delay_min_seconds, self._delay_max_seconds)
        self._sleeper(max(self._delay_min_seconds, min(delay, self._delay_max_seconds)))
        accept = (
            "text/html,application/xhtml+xml"
            if url == _NOTE_EDITOR_URL
            else "application/json, text/plain, */*"
        )
        return client.get(url, params=params, headers={"Accept": accept})


def read_owner_only_cookie_file(path: Path) -> str:
    """Read one raw Cookie header while rejecting unsafe paths and header injection."""

    try:
        if path.is_symlink():
            raise _CookieUnavailable
        stat = path.stat()
        if not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise _CookieUnavailable
        if stat.st_size <= 0 or stat.st_size > _MAX_COOKIE_BYTES:
            raise _CookieUnavailable
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise _CookieUnavailable from None
    try:
        return _normalize_cookie(value)
    except ValueError:
        raise _CookieUnavailable from None


def owner_only_cookie_file_configured(path: Path) -> bool:
    try:
        read_owner_only_cookie_file(path)
    except _CookieUnavailable:
        return False
    return True


def write_owner_only_cookie_file(path: Path, value: str) -> None:
    """Atomically persist a raw Cookie header with mode 0600."""

    normalized = _normalize_cookie(value)
    encoded = normalized.encode("utf-8")
    if len(encoded) > _MAX_COOKIE_BYTES:
        raise ValueError("Moomoo note Cookie is too large")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw_temp_path = tempfile.mkstemp(prefix=".moomoo-note-cookie-", dir=path.parent)
    temp_path = Path(raw_temp_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        path.chmod(0o600)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _normalize_cookie(value: str) -> str:
    normalized = value.strip()
    if normalized.lower().startswith("cookie:"):
        normalized = normalized.partition(":")[2].strip()
    if (
        not normalized
        or "=" not in normalized
        or "\r" in normalized
        or "\n" in normalized
        or len(normalized.encode("utf-8")) > _MAX_COOKIE_BYTES
    ):
        raise ValueError("Moomoo note Cookie must be one bounded header line")
    return normalized


def _response_state(response: httpx.Response) -> str:
    if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
        return "AUTH"
    if response.status_code == 429:
        return "RATE_LIMIT"
    if response.status_code != 200 or len(response.content) > _MAX_CACHE_FILE_BYTES:
        return "ERROR"
    return "OK"


class MoomooNotesCacheProvider:
    source = "MOOMOO_NOTE"
    capability = ObservationSourceCapability(
        source_code=source,
        display_name="Moomoo Private Notes",
        supports_full_text=True,
        supports_incremental_sync=True,
        requires_interactive_session=True,
        content_modes=(
            "AUTHENTICATED_EDITOR_HTML",
            "EDITOR_FULL_TEXT",
            "LIST_SUMMARY",
        ),
    )

    def __init__(
        self,
        *,
        cache_data_dir: Path,
        stock_database_path: Path,
        clock: Clock,
        remote_client: MoomooNotesRemoteClient | None = None,
    ) -> None:
        self._cache_data_dir = cache_data_dir
        self._stock_database_path = stock_database_path
        self._clock = clock
        self._remote_client = remote_client

    @classmethod
    def default(
        cls,
        clock: Clock,
        *,
        remote_client: MoomooNotesRemoteClient | None = None,
    ) -> MoomooNotesCacheProvider:
        support = (
            Path.home() / "Library/Containers/com.moomoo.mm-mac/Data/Library/Application Support"
        )
        return cls(
            cache_data_dir=support / "Common/cef_cache/Cache/Cache_Data",
            stock_database_path=support / "Common/stock_v18.db",
            clock=clock,
            remote_client=remote_client,
        )

    def scan(self) -> ExternalNoteScanResult:
        if not self._cache_data_dir.is_dir() or not self._stock_database_path.is_file():
            raise ProviderNotConfigured("Moomoo desktop Notes cache is unavailable")
        listed: dict[str, _CachedNote] = {}
        full: dict[str, _CachedNote] = {}
        candidate_stock_ids: set[str] = set()
        candidates: list[tuple[Path, int]] = []
        for entry in os.scandir(self._cache_data_dir):
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith("_0"):
                continue
            stat = entry.stat(follow_symlinks=False)
            if stat.st_size <= 0 or stat.st_size > _MAX_CACHE_FILE_BYTES:
                continue
            candidates.append((Path(entry.path), stat.st_mtime_ns))
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="moomoo-notes") as executor:
            cache_values = executor.map(_read_note_cache_file, candidates, chunksize=128)
            for raw, mtime_ns in cache_values:
                if raw is None:
                    continue
                if _NOTE_LIST_MARKER in raw:
                    candidate_stock_ids.update(_stock_ids_from_cache_key(raw))
                    for note in _parse_note_list(raw, mtime_ns):
                        _prefer(listed, note)
                if _EDITOR_MARKER in raw:
                    for note in _parse_editor_state(raw, mtime_ns):
                        _prefer(full, note)
        remote_warnings: tuple[str, ...] = ()
        if self._remote_client is not None:
            listed, full, remote_warnings = self._remote_client.fetch(
                listed=listed,
                full=full,
                candidate_stock_ids=candidate_stock_ids
                | {
                    stock_id
                    for value in (*listed.values(), *full.values())
                    for stock_id in value.related_stock_ids
                },
            )
        stock_metadata = _stock_metadata(
            self._stock_database_path,
            {
                stock_id
                for value in (*listed.values(), *full.values())
                for stock_id in value.related_stock_ids
            },
        )
        snapshots: list[ExternalNoteSourceSnapshot] = []
        for external_id in sorted(set(listed) | set(full)):
            summary_note = listed.get(external_id)
            full_note = full.get(external_id)
            chosen = full_note or summary_note
            assert chosen is not None
            summary = (
                summary_note.summary
                if summary_note is not None and summary_note.summary
                else chosen.summary
            )
            body = (
                prefer_proven_complete_text(full_note.full_body or "", summary)
                if full_note is not None
                else None
            )
            stock_ids = tuple(
                dict.fromkeys(
                    item
                    for source in (full_note, summary_note)
                    if source is not None
                    for item in source.related_stock_ids
                )
            )
            codes = tuple(stock_metadata[item][0] for item in stock_ids if item in stock_metadata)
            instrument_ids = tuple(
                stock_metadata[item][1]
                for item in stock_ids
                if item in stock_metadata and stock_metadata[item][1] is not None
            )
            blocks = attributed_blocks(body or summary)
            snapshots.append(
                ExternalNoteSourceSnapshot(
                    source=self.source,
                    external_id=external_id,
                    title=chosen.title,
                    summary=summary,
                    full_body=body,
                    coverage=(NoteCoverage.FULL if body is not None else NoteCoverage.SUMMARY_ONLY),
                    source_timestamp=chosen.source_timestamp,
                    observed_at=self._clock.now(),
                    primary_instrument_id=instrument_ids[0] if instrument_ids else None,
                    related_provider_stock_ids=stock_ids,
                    related_provider_codes=codes,
                    visibility=chosen.visibility,
                    blocks=blocks,
                )
            )
        warnings = remote_warnings + (() if snapshots else ("MOOMOO_NOTES_CACHE_EMPTY",))
        return ExternalNoteScanResult(
            snapshots=tuple(snapshots),
            cache_files_scanned=len(candidates),
            warning_codes=warnings,
        )


def _parse_note_list(raw: bytes, mtime_ns: int) -> tuple[_CachedNote, ...]:
    text = raw.decode("utf-8", "ignore")
    start = text.find('{"code":')
    if start < 0:
        return ()
    return _parse_note_list_payload(text[start:].encode(), mtime_ns) or ()


def _parse_note_list_payload(raw: bytes, mtime_ns: int) -> tuple[_CachedNote, ...] | None:
    try:
        payload, _ = json.JSONDecoder().raw_decode(raw.decode("utf-8", "ignore"))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    feeds = data.get("feed") if isinstance(data, dict) else None
    if not isinstance(feeds, list):
        return None
    result: list[_CachedNote] = []
    for value in feeds:
        if not isinstance(value, dict):
            continue
        external_id = _string(value.get("feedId") or value.get("feed_id"))
        title = _string(value.get("feedTitle") or value.get("feed_title"))
        if external_id is None or title is None:
            continue
        summary = _string(value.get("summaryDesc") or value.get("summary_desc")) or ""
        related = value.get("allRelatedStockInfos") or value.get("all_related_stock_infos")
        stock_ids = _related_stock_ids(related)
        permission = value.get("viewPermission") or value.get("view_permission")
        permission_type = (
            permission.get("permissionType")
            if isinstance(permission, dict)
            else value.get("permissionType")
        )
        result.append(
            _CachedNote(
                external_id=external_id,
                title=title,
                summary=summary,
                full_body=None,
                source_timestamp=_timestamp(value.get("timestamp")),
                related_stock_ids=stock_ids,
                visibility="SELF" if permission_type == 2 else "UNKNOWN",
                mtime_ns=mtime_ns,
            )
        )
    return tuple(result)


def _stock_ids_from_cache_key(raw: bytes) -> tuple[str, ...]:
    prefix = raw[: min(len(raw), 4096)].decode("utf-8", "ignore")
    return tuple(dict.fromkeys(re.findall(r"[?&]stockId=([0-9]+)", prefix)))


def _read_note_cache_file(value: tuple[Path, int]) -> tuple[bytes | None, int]:
    path, mtime_ns = value
    try:
        raw = path.read_bytes()
    except OSError:
        return None, mtime_ns
    if _NOTE_LIST_MARKER not in raw and _EDITOR_MARKER not in raw:
        return None, mtime_ns
    return raw, mtime_ns


def _parse_editor_state(raw: bytes, mtime_ns: int) -> tuple[_CachedNote, ...]:
    text = raw.decode("utf-8", "ignore")
    prefix = "window.__INITIAL_STATE__ = "
    start = text.find(prefix)
    if start < 0:
        return ()
    try:
        state, _ = json.JSONDecoder().raw_decode(text[start + len(prefix) :])
    except (TypeError, ValueError):
        return ()
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            feed = value.get("feedData")
            if isinstance(feed, dict) and _string(value.get("fid")) is not None:
                found.append((value, feed))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(state)
    result: list[_CachedNote] = []
    for parent, feed in found:
        external_id = _string(parent.get("fid"))
        title = _string(feed.get("feed_title") or feed.get("title"))
        if external_id is None or title is None:
            continue
        body = _full_body(feed.get("module_items"))
        if not body:
            continue
        stocks = feed.get("all_related_stock_infos") or feed.get("allRelatedStockInfos")
        result.append(
            _CachedNote(
                external_id=external_id,
                title=title,
                summary=body,
                full_body=body,
                source_timestamp=(
                    _timestamp(feed.get("timestamp"))
                    or datetime.fromtimestamp(mtime_ns / 1_000_000_000, UTC)
                ),
                related_stock_ids=_related_stock_ids(stocks),
                visibility="SELF",
                mtime_ns=mtime_ns,
            )
        )
    return tuple(result)


def _full_body(raw: object) -> str:
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rich = item.get("rich_text")
        if not isinstance(rich, dict) or not isinstance(rich.get("content"), str):
            continue
        value = html.unescape(rich["content"])
        value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
        value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<[^>]+>", "", value)
        value = "\n".join(line.strip() for line in value.splitlines() if line.strip())
        if value:
            parts.append(value)
    return "\n\n".join(parts)


def _stock_metadata(path: Path, stock_ids: set[str]) -> dict[str, tuple[str, str | None]]:
    if not stock_ids:
        return {}
    result: dict[str, tuple[str, str | None]] = {}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        for stock_id in stock_ids:
            row = connection.execute(
                "SELECT code, exchange, instrument_type FROM t_stock WHERE stock_id=?",
                (stock_id,),
            ).fetchone()
            if row is None:
                continue
            code, exchange, instrument_type = str(row[0]), str(row[1]).upper(), int(row[2])
            prefix = {
                "US": "US",
                "SEHK": "HK",
                "SSE": "SH",
                "SZSE": "SZ",
            }.get(exchange)
            if prefix is not None:
                provider_code = f"{prefix}.{code}"
                asset = {3: "equity", 4: "etf", 6: "index", 10: "future"}.get(instrument_type)
                market = {"US": "US", "HK": "HK", "SH": "A_SHARE", "SZ": "A_SHARE"}.get(prefix)
                symbol = f"{code}.{prefix}" if prefix in {"SH", "SZ"} else code
                instrument_id = (
                    f"{asset}:{market}:{symbol}"
                    if asset is not None and market is not None
                    else None
                )
                result[stock_id] = (provider_code, instrument_id)
    return result


def _related_stock_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        dict.fromkeys(
            value
            for item in raw
            if isinstance(item, dict)
            if (value := _string(item.get("stockId") or item.get("stock_id"))) is not None
        )
    )


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _prefer(target: dict[str, _CachedNote], value: _CachedNote) -> None:
    current = target.get(value.external_id)
    if current is None or value.mtime_ns > current.mtime_ns:
        target[value.external_id] = value


__all__ = [
    "MoomooNotesCacheProvider",
    "MoomooNotesRemoteClient",
    "OwnerOnlyMoomooNoteCredentialStore",
    "owner_only_cookie_file_configured",
    "read_owner_only_cookie_file",
    "write_owner_only_cookie_file",
]
