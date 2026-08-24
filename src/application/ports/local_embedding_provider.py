"""Local-only embedding boundary for rebuildable Research Search projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class LocalEmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


__all__ = ["LocalEmbeddingProvider"]
