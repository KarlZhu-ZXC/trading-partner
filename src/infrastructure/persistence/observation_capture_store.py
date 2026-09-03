"""Owner-only canonical JSON spool for browser and desktop capture adapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC
from pathlib import Path

from domain.external_note.models import ExternalNoteSourceSnapshot

_SAFE_SOURCE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


class OwnerOnlyObservationCaptureStore:
    def __init__(self, inbox_dir: Path) -> None:
        self._inbox_dir = inbox_dir

    def append(self, snapshot: ExternalNoteSourceSnapshot) -> bool:
        if _SAFE_SOURCE.fullmatch(snapshot.source) is None:
            raise ValueError("observation source code is invalid")
        payload = {
            "source_code": snapshot.source,
            "external_id": snapshot.external_id,
            "title": snapshot.title,
            "summary": snapshot.summary,
            "full_body": snapshot.full_body,
            "observed_at": snapshot.observed_at.isoformat(),
            "source_timestamp": (
                snapshot.source_timestamp.isoformat()
                if snapshot.source_timestamp is not None
                else None
            ),
            "primary_instrument_id": snapshot.primary_instrument_id,
            "related_provider_stock_ids": list(snapshot.related_provider_stock_ids),
            "related_provider_codes": list(snapshot.related_provider_codes),
            "visibility": snapshot.visibility,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        identity_hash = hashlib.sha256(
            f"{snapshot.source}\0{snapshot.external_id}".encode()
        ).hexdigest()[:16]
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        observed = snapshot.observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{snapshot.source.lower()}-{identity_hash}-{observed}-{content_hash}.json"
        self._inbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._inbox_dir / filename
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return True


__all__ = ["OwnerOnlyObservationCaptureStore"]
