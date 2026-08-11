"""Artifact-export port for a durable Trade Retro run."""

from pathlib import Path
from typing import Protocol

from domain.retro.models import TradeRetroReviewRevision, TradeRetroRun


class TradeRetroExporter(Protocol):
    def export(
        self,
        run: TradeRetroRun,
        review: TradeRetroReviewRevision | None = None,
    ) -> tuple[Path, str]: ...
