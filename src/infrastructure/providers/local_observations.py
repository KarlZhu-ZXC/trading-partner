"""Owner-controlled file bridge for full-text external observations."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from application.ports.clock import Clock
from application.ports.external_note_provider import (
    ExternalNoteScanResult,
    ObservationSourceCapability,
)
from domain.external_note.attribution import attributed_blocks
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteSourceSnapshot

_SOURCE_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_MAX_FILE_BYTES = 256 * 1024


class LocalObservationInboxProvider:
    capability = ObservationSourceCapability(
        source_code="LOCAL_OBSERVATION_BRIDGE",
        display_name="Local Observation Bridge",
        supports_full_text=True,
        supports_incremental_sync=True,
        requires_interactive_session=False,
        content_modes=("CANONICAL_JSON_FULL_TEXT",),
    )

    def __init__(self, inbox_dir: Path, clock: Clock) -> None:
        self._inbox_dir = inbox_dir
        self._clock = clock

    def scan(self) -> ExternalNoteScanResult:
        if not self._inbox_dir.is_dir():
            return ExternalNoteScanResult(
                snapshots=(),
                cache_files_scanned=0,
            )
        snapshots: list[ExternalNoteSourceSnapshot] = []
        warning_codes: list[str] = []
        scanned = 0
        for path in sorted(self._inbox_dir.glob("*.json"))[:500]:
            scanned += 1
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    raise ValueError("observation file is too large")
                payload = json.loads(path.read_text(encoding="utf-8"))
                snapshots.append(self._snapshot(payload))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                warning_codes.append("LOCAL_OBSERVATION_FILE_INVALID")
        return ExternalNoteScanResult(
            snapshots=tuple(snapshots),
            cache_files_scanned=scanned,
            warning_codes=tuple(dict.fromkeys(warning_codes)),
        )

    def _snapshot(self, payload: object) -> ExternalNoteSourceSnapshot:
        if not isinstance(payload, dict):
            raise ValueError("observation payload must be an object")
        source = _required_text(payload, "source_code", 64)
        if _SOURCE_CODE.fullmatch(source) is None:
            raise ValueError("observation source code is invalid")
        body = _required_text(payload, "full_body", 100_000)
        observed_at = _aware_datetime(payload.get("observed_at")) or self._clock.now()
        source_timestamp = _aware_datetime(payload.get("source_timestamp"))
        return ExternalNoteSourceSnapshot(
            source=source,
            external_id=_required_text(payload, "external_id", 200),
            title=_required_text(payload, "title", 500),
            summary=_optional_text(payload.get("summary"), 50_000) or body,
            full_body=body,
            coverage=NoteCoverage.FULL,
            source_timestamp=source_timestamp,
            observed_at=observed_at,
            primary_instrument_id=_optional_text(payload.get("primary_instrument_id"), 200),
            related_provider_stock_ids=_strings(payload.get("related_provider_stock_ids")),
            related_provider_codes=_strings(payload.get("related_provider_codes")),
            visibility=_optional_text(payload.get("visibility"), 40) or "SELF",
            blocks=attributed_blocks(body),
        )


def _required_text(payload: dict[str, object], field: str, maximum: int) -> str:
    value = _optional_text(payload.get(field), maximum)
    if value is None:
        raise ValueError(f"{field} is required")
    return value


def _optional_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("observation text field is invalid")
    return value.strip()


def _aware_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("observation timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observation timestamp must be timezone-aware")
    return parsed


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("observation identifier list is invalid")
    result = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(result) != len(value):
        raise ValueError("observation identifier list is invalid")
    return result


__all__ = ["LocalObservationInboxProvider"]
