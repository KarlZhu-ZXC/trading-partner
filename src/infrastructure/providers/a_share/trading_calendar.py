"""JSON-backed A-share trading calendar (Phase 1E E1/E2).

Loads ``config/a_share_trading_calendar.v1.json`` (official SSE schedule for
2024–2026). Does not invent open days outside explicit coverage; out-of-range
dates raise ``CalendarOutOfRange``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo

from domain.a_share.models import TradingSessionWindow
from domain.common.enums import TradingSession
from domain.common.errors import CalendarOutOfRange, ConfigurationError, DataContractError
from infrastructure.config.settings import (
    PACKAGED_A_SHARE_TRADING_CALENDAR_PATH,
    PROJECT_ROOT,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "version",
        "timezone",
        "coverage_from",
        "coverage_to",
        "open_days",
        "regular_sessions",
        "source",
        "generated_at",
        "content_sha256",
    }
)
_EXPECTED_SCHEMA = "a_share_trading_calendar.v1"
_TIME_FMT = "%H:%M"

# The repository copy is the single tracked source. Hatch force-includes that
# file beside ``infrastructure.config`` for installed wheels.
DEFAULT_A_SHARE_TRADING_CALENDAR_PATH = (
    PROJECT_ROOT / "config" / "a_share_trading_calendar.v1.json"
)


def _config_error(message: str, *, reason: str, **details: object) -> ConfigurationError:
    return ConfigurationError(message, details={"reason": reason, **details})


def _parse_date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise _config_error(
            f"{field} must be an ISO date string",
            reason="invalid_date_type",
            field=field,
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _config_error(
            f"{field} must be a valid ISO date",
            reason="invalid_date",
            field=field,
        ) from exc


def _parse_hhmm(value: object, *, field: str) -> time:
    if not isinstance(value, str):
        raise _config_error(
            f"{field} must be HH:MM string",
            reason="invalid_time_type",
            field=field,
        )
    try:
        return datetime.strptime(value, _TIME_FMT).time()
    except ValueError as exc:
        raise _config_error(
            f"{field} must be HH:MM",
            reason="invalid_time",
            field=field,
        ) from exc


class JsonAShareTradingCalendar:
    """Immutable trading calendar loaded from the versioned JSON fixture."""

    __slots__ = (
        "_version",
        "_timezone_name",
        "_coverage_from",
        "_coverage_to",
        "_open_days",
        "_session_specs",
        "_source",
        "_generated_at",
        "_content_sha256",
    )

    def __init__(
        self,
        *,
        version: str,
        timezone_name: str,
        coverage_from: date,
        coverage_to: date,
        open_days: frozenset[date],
        session_specs: tuple[tuple[time, time], ...],
        source: dict[str, object],
        generated_at: str,
        content_sha256: str,
    ) -> None:
        if coverage_to < coverage_from:
            raise DataContractError(
                "coverage_to must be >= coverage_from",
                details={"field": "coverage_to", "rule": "range_order"},
            )
        for day in open_days:
            if day < coverage_from or day > coverage_to:
                raise DataContractError(
                    "open_days must lie within coverage",
                    details={"field": "open_days", "rule": "within_coverage"},
                )
        self._version = version
        self._timezone_name = timezone_name
        self._coverage_from = coverage_from
        self._coverage_to = coverage_to
        self._open_days = open_days
        self._session_specs = session_specs
        self._source = dict(source)
        self._generated_at = generated_at
        self._content_sha256 = content_sha256

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load and validate calendar fixture from ``path``."""
        file_path = Path(path)
        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise _config_error(
                "A-share trading calendar file not found",
                reason="file_not_found",
                path=str(file_path),
            ) from exc
        except OSError as exc:
            raise _config_error(
                "A-share trading calendar file is not readable",
                reason="file_unreadable",
                path=str(file_path),
                error_type=type(exc).__name__,
            ) from exc

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise _config_error(
                "A-share trading calendar is malformed JSON",
                reason="malformed_json",
                error_type=type(exc).__name__,
            ) from None

        if not isinstance(payload, dict):
            raise _config_error(
                "A-share trading calendar root must be an object",
                reason="root_not_object",
            )

        missing = _REQUIRED_KEYS - set(payload.keys())
        if missing:
            raise _config_error(
                "A-share trading calendar missing required keys",
                reason="missing_keys",
                missing_keys=sorted(missing),
            )

        schema_version = payload["schema_version"]
        if schema_version != _EXPECTED_SCHEMA:
            raise _config_error(
                "unsupported A-share trading calendar schema_version",
                reason="unsupported_schema",
            )

        version = payload["version"]
        if not isinstance(version, str) or not version.strip():
            raise _config_error(
                "version must be a non-blank string",
                reason="invalid_version",
            )

        timezone_name = payload["timezone"]
        if timezone_name != "Asia/Shanghai":
            raise _config_error(
                "timezone must be Asia/Shanghai",
                reason="invalid_timezone",
            )

        coverage_from = _parse_date(payload["coverage_from"], field="coverage_from")
        coverage_to = _parse_date(payload["coverage_to"], field="coverage_to")
        if coverage_to < coverage_from:
            raise _config_error(
                "coverage_to must be >= coverage_from",
                reason="coverage_order",
            )

        open_raw = payload["open_days"]
        if not isinstance(open_raw, list) or not open_raw:
            raise _config_error(
                "open_days must be a non-empty list",
                reason="invalid_open_days",
            )
        open_days: set[date] = set()
        for item in open_raw:
            day = _parse_date(item, field="open_days")
            if day in open_days:
                raise _config_error(
                    "open_days must be unique",
                    reason="duplicate_open_day",
                )
            if day < coverage_from or day > coverage_to:
                raise _config_error(
                    "open_days must lie within coverage",
                    reason="open_day_out_of_coverage",
                )
            open_days.add(day)

        sessions_raw = payload["regular_sessions"]
        if not isinstance(sessions_raw, list) or not sessions_raw:
            raise _config_error(
                "regular_sessions must be a non-empty list",
                reason="invalid_sessions",
            )
        session_specs: list[tuple[time, time]] = []
        for idx, row in enumerate(sessions_raw):
            if not isinstance(row, dict):
                raise _config_error(
                    "regular_sessions entries must be objects",
                    reason="invalid_session_entry",
                    index=idx,
                )
            start = _parse_hhmm(row.get("start"), field=f"regular_sessions[{idx}].start")
            end = _parse_hhmm(row.get("end"), field=f"regular_sessions[{idx}].end")
            if end <= start:
                raise _config_error(
                    "session end must be after start",
                    reason="session_order",
                    index=idx,
                )
            session_specs.append((start, end))

        source = payload["source"]
        if not isinstance(source, dict):
            raise _config_error(
                "source must be an object",
                reason="invalid_source",
            )
        kind = source.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise _config_error(
                "source.kind must be a non-blank string",
                reason="invalid_source_kind",
            )

        generated_at = payload["generated_at"]
        if not isinstance(generated_at, str) or not generated_at.strip():
            raise _config_error(
                "generated_at must be a non-blank string",
                reason="invalid_generated_at",
            )

        content_sha256 = payload["content_sha256"]
        if not isinstance(content_sha256, str) or len(content_sha256) != 64:
            raise _config_error(
                "content_sha256 must be a 64-char hex digest",
                reason="invalid_sha256",
            )

        # Verify integrity over payload without content_sha256.
        check_payload = {k: v for k, v in payload.items() if k != "content_sha256"}
        canonical = json.dumps(
            check_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest != content_sha256:
            raise _config_error(
                "content_sha256 does not match calendar payload",
                reason="sha256_mismatch",
            )

        return cls(
            version=version,
            timezone_name=timezone_name,
            coverage_from=coverage_from,
            coverage_to=coverage_to,
            open_days=frozenset(open_days),
            session_specs=tuple(session_specs),
            source={k: source[k] for k in source},
            generated_at=generated_at,
            content_sha256=content_sha256,
        )

    @property
    def version(self) -> str:
        return self._version

    @property
    def coverage_from(self) -> date:
        return self._coverage_from

    @property
    def coverage_to(self) -> date:
        return self._coverage_to

    @property
    def content_sha256(self) -> str:
        return self._content_sha256

    @property
    def source(self) -> dict[str, Any]:
        return dict(self._source)

    def _ensure_in_coverage(self, day: date) -> None:
        if not isinstance(day, date) or type(day) is not date:
            raise DataContractError(
                "day must be a date",
                details={"field": "day", "rule": "date_type"},
            )
        if day < self._coverage_from or day > self._coverage_to:
            raise CalendarOutOfRange(
                "date is outside A-share trading calendar coverage",
                details={
                    "field": "day",
                    "coverage_from": self._coverage_from.isoformat(),
                    "coverage_to": self._coverage_to.isoformat(),
                    "calendar_version": self._version,
                },
            )

    def is_trading_day(self, day: date) -> bool:
        self._ensure_in_coverage(day)
        return day in self._open_days

    def previous_trading_day(self, day: date) -> date:
        # Frozen coverage rule: any input outside coverage_from/to raises
        # (including far after coverage_to). Do not walk from outside coverage.
        self._ensure_in_coverage(day)
        cursor = day - timedelta(days=1)
        while cursor >= self._coverage_from:
            if cursor in self._open_days:
                return cursor
            cursor -= timedelta(days=1)
        raise CalendarOutOfRange(
            "no previous trading day within calendar coverage",
            details={
                "field": "day",
                "coverage_from": self._coverage_from.isoformat(),
                "coverage_to": self._coverage_to.isoformat(),
                "calendar_version": self._version,
            },
        )

    def sessions_for(self, day: date) -> tuple[TradingSessionWindow, ...]:
        self._ensure_in_coverage(day)
        if day not in self._open_days:
            return ()
        windows: list[TradingSessionWindow] = []
        for start_t, end_t in self._session_specs:
            start_at = datetime.combine(day, start_t, tzinfo=_SHANGHAI)
            end_at = datetime.combine(day, end_t, tzinfo=_SHANGHAI)
            windows.append(
                TradingSessionWindow(
                    session=TradingSession.REGULAR,
                    start_at=start_at,
                    end_at=end_at,
                )
            )
        return tuple(windows)


def load_default_a_share_trading_calendar() -> JsonAShareTradingCalendar:
    """Load the tracked calendar, falling back to the installed-wheel copy.

    Selection is based only on file presence. A present but malformed preferred
    source is rejected by :meth:`JsonAShareTradingCalendar.load`; it must never
    be hidden by silently switching sources or inventing weekday-only sessions.
    """
    if DEFAULT_A_SHARE_TRADING_CALENDAR_PATH.is_file():
        selected = DEFAULT_A_SHARE_TRADING_CALENDAR_PATH
    elif PACKAGED_A_SHARE_TRADING_CALENDAR_PATH.is_file():
        selected = PACKAGED_A_SHARE_TRADING_CALENDAR_PATH
    else:
        raise _config_error(
            "A-share trading calendar is not installed",
            reason="calendar_missing",
            attempted_locations=["project_config", "packaged_resource"],
        )
    return JsonAShareTradingCalendar.load(selected)
