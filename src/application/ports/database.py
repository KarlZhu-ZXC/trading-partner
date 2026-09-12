"""Database health port (repositories arrive in later phases)."""

from __future__ import annotations

from typing import Final, Protocol

# The application and its Alembic migration graph are released together. This
# is a compatibility marker for read-only runtime preflight and diagnostics;
# initialization commands remain the only code allowed to upgrade a database.
CURRENT_MIGRATION_HEAD: Final[str] = "0072_external_note_review_drafts"


class Database(Protocol):
    def check_connection(self) -> None:
        """Raise PersistenceError or MigrationError when the database is unhealthy."""
        ...

    def migration_heads(self) -> tuple[str, ...]:
        """Return the persisted Alembic heads without mutating the database."""
        ...
