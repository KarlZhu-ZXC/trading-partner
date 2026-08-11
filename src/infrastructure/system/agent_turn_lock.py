"""Cross-process advisory locks for one Agent conversation turn."""

from __future__ import annotations

import hashlib
from pathlib import Path

from infrastructure.system.process_file_lock import ProcessFileLock


class AgentTurnLockFactory:
    """Map opaque conversation IDs to local lock files without exposing IDs."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def __call__(self, conversation_id: str) -> ProcessFileLock:
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        return ProcessFileLock(self._root / f"{digest}.lock")


__all__ = ["AgentTurnLockFactory"]
