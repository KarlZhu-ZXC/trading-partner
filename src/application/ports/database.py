"""Database health port (repositories arrive in later phases)."""

from __future__ import annotations

from typing import Protocol


class Database(Protocol):
    def check_connection(self) -> None:
        """Raise PersistenceError or MigrationError when the database is unhealthy."""
        ...
