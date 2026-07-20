"""Small non-blocking advisory file lock for scheduler process coordination."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO


class ProcessFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> ProcessFileLock:
        if not self.acquire():
            raise BlockingIOError("post-market synchronization is already running")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()
