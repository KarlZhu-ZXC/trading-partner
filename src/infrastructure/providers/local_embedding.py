"""Lazy FastEmbed adapter; always local and disabled unless explicitly configured."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from application.ports.local_embedding_provider import LocalEmbeddingProvider
from domain.common.errors import ConfigurationError, DataContractError
from infrastructure.config.settings import AppSettings

_LOGGER = logging.getLogger(__name__)


class FastEmbedLocalEmbeddingProvider(LocalEmbeddingProvider):
    def __init__(self, model_id: str) -> None:
        try:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]
        except ImportError as error:
            raise ConfigurationError(
                "Local semantic search requires the semantic-search optional dependency"
            ) from error
        self._model_id = model_id
        self._model: Any = TextEmbedding(model_name=model_id)

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts or any(not isinstance(item, str) or not item.strip() for item in texts):
            raise DataContractError("Embedding texts must be bounded nonblank strings")
        vectors: list[tuple[float, ...]] = []
        for raw in self._model.embed(list(texts)):
            vector = tuple(float(item) for item in raw)
            if not vector or any(not math.isfinite(item) for item in vector):
                raise DataContractError("Local embedding returned an invalid vector")
            vectors.append(vector)
        if len(vectors) != len(texts) or len({len(item) for item in vectors}) != 1:
            raise DataContractError("Local embedding result shape is invalid")
        return tuple(vectors)


def build_local_embedding_provider(
    settings: AppSettings,
) -> LocalEmbeddingProvider | None:
    if not settings.research_semantic_search_enabled:
        return None
    try:
        return FastEmbedLocalEmbeddingProvider(settings.research_semantic_embedding_model)
    except Exception as error:  # noqa: BLE001 - lexical search remains available
        _LOGGER.warning(
            "Local semantic search disabled: error_type=%s",
            type(error).__name__,
        )
        return None


__all__ = ["FastEmbedLocalEmbeddingProvider", "build_local_embedding_provider"]
