"""Shared canonical payload hashing for caller-supplied idempotency keys."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
