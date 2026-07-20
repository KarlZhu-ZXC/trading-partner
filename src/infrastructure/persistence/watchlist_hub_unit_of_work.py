"""SQLAlchemy Watchlist-hub Unit of Work — one Session for hub repositories."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.ports.audit_log_writer import AuditLogWriter
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.errors import PersistenceError
from infrastructure.persistence.audit_log_writer import SqlAlchemySessionAuditLogWriter
from infrastructure.persistence.repositories.watchlist_group import (
    SqlAlchemyWatchlistGroupRepository,
)
from infrastructure.persistence.repositories.watchlist_membership import (
    SqlAlchemyWatchlistMembershipRepository,
)
from infrastructure.persistence.repositories.watchlist_mutation import (
    SqlAlchemyWatchlistMutationRepository,
)


class SqlAlchemyWatchlistHubUnitOfWork:
    """Context-managed UoW binding watchlist-hub repositories to one Session."""

    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._session: Session | None = None
        self._groups: SqlAlchemyWatchlistGroupRepository | None = None
        self._memberships: SqlAlchemyWatchlistMembershipRepository | None = None
        self._mutations: SqlAlchemyWatchlistMutationRepository | None = None
        self._audit: AuditLogWriter | None = None

    def __enter__(self) -> SqlAlchemyWatchlistHubUnitOfWork:
        if self._session is not None:
            raise PersistenceError("WatchlistHubUnitOfWork is already entered")
        session = Session(self._engine)
        if self._engine.dialect.name == "sqlite":
            from sqlalchemy import text

            session.execute(text("PRAGMA foreign_keys=ON"))
        self._session = session
        self._groups = SqlAlchemyWatchlistGroupRepository(session)
        self._memberships = SqlAlchemyWatchlistMembershipRepository(session)
        self._mutations = SqlAlchemyWatchlistMutationRepository(session)
        self._audit = SqlAlchemySessionAuditLogWriter(
            session,
            self._clock,
            self._id_generator,
            self._secret_redactor,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._session is not None:
                self._session.rollback()
                self._session.close()
        finally:
            self._session = None
            self._groups = None
            self._memberships = None
            self._mutations = None
            self._audit = None

    def _require_session(self) -> Session:
        if self._session is None:
            raise PersistenceError(
                "WatchlistHubUnitOfWork is not active; use as context manager"
            )
        return self._session

    @property
    def groups(self) -> SqlAlchemyWatchlistGroupRepository:
        self._require_session()
        assert self._groups is not None
        return self._groups

    @property
    def memberships(self) -> SqlAlchemyWatchlistMembershipRepository:
        self._require_session()
        assert self._memberships is not None
        return self._memberships

    @property
    def mutations(self) -> SqlAlchemyWatchlistMutationRepository:
        self._require_session()
        assert self._mutations is not None
        return self._mutations

    @property
    def audit(self) -> AuditLogWriter:
        self._require_session()
        assert self._audit is not None
        return self._audit

    def commit(self) -> None:
        session = self._require_session()
        try:
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise PersistenceError(
                f"WatchlistHubUnitOfWork commit failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__},
            ) from exc

    def rollback(self) -> None:
        session = self._require_session()
        session.rollback()
