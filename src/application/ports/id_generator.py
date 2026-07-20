"""ID generator port — only EntityIdPrefix values accepted."""

from __future__ import annotations

from typing import Protocol

from domain.common.ids import EntityIdPrefix


class IdGenerator(Protocol):
    def new(self, prefix: EntityIdPrefix) -> str:
        """Create a new ``<prefix>_<uuid7>`` identifier."""
        ...
