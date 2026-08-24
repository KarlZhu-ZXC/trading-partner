from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from infrastructure.config.settings import AppSettings
from infrastructure.providers import local_embedding


def test_enabled_local_embedding_failure_falls_back_to_lexical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_model_id: str) -> object:
        raise RuntimeError("model path must-not-persist")

    monkeypatch.setattr(
        local_embedding,
        "FastEmbedLocalEmbeddingProvider",
        fail,
    )
    settings = cast(
        AppSettings,
        SimpleNamespace(
            research_semantic_search_enabled=True,
            research_semantic_embedding_model="local-test-model",
        ),
    )

    assert local_embedding.build_local_embedding_provider(settings) is None
