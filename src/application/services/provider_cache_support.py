"""Application-facing provider cache key API (Phase 1D D5a).

Pure key grammar and primitive field-coherence live in
``domain.providers.cache_key``. This module re-exports that API and adds a
thin ``CacheEntry`` wrapper for application callers.
"""

from __future__ import annotations

from application.dto.provider_state import CacheEntry
from domain.common.errors import DataContractError
from domain.providers.cache_key import (
    ParsedCacheKey,
    build_cache_key,
    parse_cache_key,
    require_cache_key_matches_fields,
    require_valid_fingerprint,
    validate_cache_instrument_id,
)

__all__ = [
    "ParsedCacheKey",
    "build_cache_key",
    "parse_cache_key",
    "require_cache_key_matches_entry",
    "require_cache_key_matches_fields",
    "require_valid_fingerprint",
    "validate_cache_instrument_id",
]


def require_cache_key_matches_entry(key: str, entry: CacheEntry) -> ParsedCacheKey:
    """Require ``key`` is a valid v1 key equal to ``entry.key`` with matching fields.

    Thin wrapper over domain primitive-field coherence. Never echoes the
    rejected key.
    """
    if not isinstance(entry, CacheEntry):
        raise DataContractError(
            "entry must be a CacheEntry",
            details={"field": "entry", "type": type(entry).__name__},
        )
    return require_cache_key_matches_fields(
        key,
        entry_key=entry.key,
        market=entry.market,
        category=entry.category,
        instrument_id=entry.instrument_id,
        as_of=entry.as_of,
    )
