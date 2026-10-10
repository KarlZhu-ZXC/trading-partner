"""Durable read/write boundary for the latest Review Queue scan receipt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class ReviewScanReceiptStore(Protocol):
    """Persist safe scan metadata without storing any source business body."""

    def save(self, receipt: Mapping[str, object]) -> str: ...

    def latest(self) -> dict[str, object] | None: ...
