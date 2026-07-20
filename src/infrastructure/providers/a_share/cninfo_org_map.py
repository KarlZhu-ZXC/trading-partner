"""CNINFO official A-share orgId map loader (Phase 1E E3).

Loads the versioned static inventory vendored under ``config/cninfo_org_map.v1.json``
(and the wheel force-include copy). Runtime adapters never synthesize orgId from
code — official orgIds are not universally derivable.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from domain.common.errors import ConfigurationError, DataContractError
from infrastructure.config.settings import (
    PACKAGED_CNINFO_ORG_MAP_PATH,
    PROJECT_ROOT,
)

SCHEMA_VERSION = "cninfo_org_map.v1"
_CODE_RE = re.compile(r"^\d{6}$")
_ORG_RE = re.compile(r"^[A-Za-z0-9]+$")
_ALLOWED_CATEGORIES = frozenset({"A股"})
_REQUIRED_TOP = frozenset(
    {
        "schema_version",
        "version",
        "generated_at",
        "source_urls",
        "allowed_categories",
        "entry_count",
        "content_sha256",
        "entries",
    }
)
_REQUIRED_ENTRY = frozenset({"code", "org_id", "category", "source"})

# Project-root tracked source + packaged wheel copy (force-include).
DEFAULT_ORG_MAP_PATH = PROJECT_ROOT / "config" / "cninfo_org_map.v1.json"
PACKAGED_ORG_MAP_PATH = PACKAGED_CNINFO_ORG_MAP_PATH

# Completeness thresholds (union of SSE/SZSE/BSE A-shares after A股 filter).
MIN_ENTRY_COUNT = 6000


def _config_error(message: str, *, reason: str, **details: object) -> ConfigurationError:
    return ConfigurationError(message, details={"reason": reason, **details})


def resolve_default_org_map_path() -> Path:
    """Prefer project-root config; fall back to wheel-packaged copy."""
    if DEFAULT_ORG_MAP_PATH.is_file():
        return DEFAULT_ORG_MAP_PATH
    if PACKAGED_ORG_MAP_PATH.is_file():
        return PACKAGED_ORG_MAP_PATH
    raise _config_error(
        "cninfo org map not found",
        reason="org_map_missing",
        project_path=str(DEFAULT_ORG_MAP_PATH),
        packaged_path=str(PACKAGED_ORG_MAP_PATH),
    )


def _canonical_entries_bytes(entries: list[dict[str, str]]) -> bytes:
    return json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def validate_org_map_document(doc: object) -> dict[str, str]:
    """Validate schema and return ``code -> org_id`` mapping (sorted deterministically)."""
    if not isinstance(doc, dict):
        raise _config_error(
            "cninfo org map root must be an object",
            reason="invalid_schema",
        )
    keys = set(doc.keys())
    if keys != _REQUIRED_TOP:
        raise _config_error(
            "cninfo org map has unknown or missing schema keys",
            reason="unknown_schema",
            extra=sorted(keys - _REQUIRED_TOP),
            missing=sorted(_REQUIRED_TOP - keys),
        )
    if doc["schema_version"] != SCHEMA_VERSION:
        raise _config_error(
            "cninfo org map schema_version mismatch",
            reason="schema_version",
            expected=SCHEMA_VERSION,
        )
    if not isinstance(doc["version"], str) or not doc["version"].strip():
        raise _config_error(
            "cninfo org map version must be non-blank",
            reason="invalid_version",
        )
    if not isinstance(doc["generated_at"], str) or not doc["generated_at"].strip():
        raise _config_error(
            "cninfo org map generated_at must be non-blank",
            reason="invalid_generated_at",
        )
    urls = doc["source_urls"]
    if not isinstance(urls, list) or not urls or not all(
        isinstance(u, str) and u.startswith("https://") for u in urls
    ):
        raise _config_error(
            "cninfo org map source_urls must be non-empty https URLs",
            reason="invalid_source_urls",
        )
    cats = doc["allowed_categories"]
    if not isinstance(cats, list) or set(cats) != _ALLOWED_CATEGORIES:
        raise _config_error(
            "cninfo org map allowed_categories must be exactly A股",
            reason="invalid_categories",
        )
    entries = doc["entries"]
    if not isinstance(entries, list):
        raise _config_error(
            "cninfo org map entries must be a list",
            reason="invalid_entries",
        )
    if not isinstance(doc["entry_count"], int) or isinstance(doc["entry_count"], bool):
        raise _config_error(
            "cninfo org map entry_count must be int",
            reason="invalid_entry_count",
        )
    if doc["entry_count"] != len(entries):
        raise _config_error(
            "cninfo org map entry_count mismatch",
            reason="entry_count_mismatch",
            declared=doc["entry_count"],
            actual=len(entries),
        )
    if len(entries) < MIN_ENTRY_COUNT:
        raise _config_error(
            "cninfo org map below completeness threshold",
            reason="incomplete",
            entry_count=len(entries),
            min_entry_count=MIN_ENTRY_COUNT,
        )
    digest_declared = doc["content_sha256"]
    if not isinstance(digest_declared, str) or len(digest_declared) != 64:
        raise _config_error(
            "cninfo org map content_sha256 invalid",
            reason="invalid_digest",
        )

    mapping: dict[str, str] = {}
    canon: list[dict[str, str]] = []
    prev_code = ""
    for idx, row in enumerate(entries):
        if not isinstance(row, dict):
            raise _config_error(
                "cninfo org map entry must be object",
                reason="invalid_entry",
                index=idx,
            )
        if set(row.keys()) != _REQUIRED_ENTRY:
            raise _config_error(
                "cninfo org map entry has unknown or missing keys",
                reason="unknown_entry_schema",
                index=idx,
            )
        code = row["code"]
        org_id = row["org_id"]
        category = row["category"]
        source = row["source"]
        if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
            raise _config_error(
                "cninfo org map code must be 6 digits",
                reason="malformed_code",
                index=idx,
            )
        if not isinstance(org_id, str) or not _ORG_RE.fullmatch(org_id):
            raise _config_error(
                "cninfo org map org_id malformed",
                reason="malformed_org_id",
                index=idx,
            )
        if category not in _ALLOWED_CATEGORIES:
            raise _config_error(
                "cninfo org map category not allowed",
                reason="category_rejected",
                index=idx,
            )
        if not isinstance(source, str) or not source.strip():
            raise _config_error(
                "cninfo org map source must be non-blank",
                reason="invalid_source",
                index=idx,
            )
        if code in mapping:
            if mapping[code] != org_id:
                raise _config_error(
                    "cninfo org map duplicate code with conflicting org_id",
                    reason="duplicate_code_conflict",
                    code=code,
                )
            raise _config_error(
                "cninfo org map duplicate code",
                reason="duplicate_code",
                code=code,
            )
        if prev_code and code < prev_code:
            raise _config_error(
                "cninfo org map entries must be sorted by code ascending",
                reason="not_sorted",
                index=idx,
            )
        prev_code = code
        mapping[code] = org_id
        canon.append(
            {
                "code": code,
                "org_id": org_id,
                "category": category,
                "source": source,
            }
        )

    digest = hashlib.sha256(_canonical_entries_bytes(canon)).hexdigest()
    if digest != digest_declared:
        raise _config_error(
            "cninfo org map content_sha256 mismatch",
            reason="digest_mismatch",
        )
    return mapping


def load_cninfo_org_map(path: Path | None = None) -> Mapping[str, str]:
    """Load and validate the static map; returns a read-only mapping."""
    resolved = path if path is not None else resolve_default_org_map_path()
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise _config_error(
            "cninfo org map unreadable",
            reason="read_error",
            path=str(resolved),
        ) from exc
    try:
        doc: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _config_error(
            "cninfo org map is not valid JSON",
            reason="invalid_json",
            path=str(resolved),
        ) from exc
    mapping = validate_org_map_document(doc)
    return MappingProxyType(mapping)


def require_org_id(org_map: Mapping[str, str], code6: str, *, vendor: str) -> str:
    """Resolve orgId or fail closed with typed config/contract details (no network)."""
    if not isinstance(code6, str) or not _CODE_RE.fullmatch(code6):
        raise DataContractError(
            "instrument code invalid for cninfo org map",
            details={
                "vendor": vendor,
                "operation": "announcements",
                "rule": "malformed_code",
            },
        )
    org_id = org_map.get(code6)
    if org_id is None:
        raise DataContractError(
            "cninfo org mapping missing for instrument",
            details={
                "vendor": vendor,
                "operation": "announcements",
                "rule": "org_map_missing",
                "code": code6,
            },
        )
    return org_id
