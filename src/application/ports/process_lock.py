"""Cross-process advisory lock boundary for local operational writes."""

from __future__ import annotations

from typing import Protocol


class ProcessLock(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...
