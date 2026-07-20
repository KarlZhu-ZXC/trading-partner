"""Provider cache codec port (Phase 1D D6b1).

Explicit typed codecs serialize domain/DTO-safe values to canonical JSON for
``ProviderCacheStore``. Generic cache must never use pickle, marshal,
import/type-name reflection, or ``json.dumps(..., default=str)``.
"""

from __future__ import annotations

from typing import Protocol

from application.dto.provider_routing import ProviderSuccess
from application.dto.provider_state import CacheEntry


class ProviderCacheCodec[T](Protocol):
    """Typed encode/decode boundary for a single cached value type ``T``."""

    @property
    def codec_id(self) -> str:
        """Stable codec identifier written into the payload envelope."""
        ...

    def encode(self, success: ProviderSuccess[T]) -> str:
        """Serialize a successful provider result to canonical JSON text."""
        ...

    def decode(self, entry: CacheEntry) -> ProviderSuccess[T]:
        """Deserialize a cache entry payload; returned meta uses HIT disposition."""
        ...
