"""Typed owner-controlled runtime path layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    data: Path
    secrets: Path
    observations: Path
    agent_attachments: Path
    historical_validation: Path
    reconciliation: Path
    backups: Path

    @classmethod
    def from_root(cls, root: Path) -> RuntimePaths:
        resolved = root.expanduser().resolve()
        data = resolved / "data"
        artifacts = data / "artifacts"
        return cls(
            root=resolved,
            data=data,
            secrets=data / "secrets",
            observations=data / "observations",
            agent_attachments=data / "agent" / "attachments",
            historical_validation=artifacts / "historical_validation",
            reconciliation=artifacts / "reconciliation",
            backups=data / "backups",
        )


__all__ = ["RuntimePaths"]
