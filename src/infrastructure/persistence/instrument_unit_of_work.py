"""SQLAlchemy Instrument Unit of Work — one Session for instruments + aliases."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from domain.common.errors import PersistenceError
from infrastructure.persistence.instrument_repository import SqlAlchemyInstrumentRepository


class SqlAlchemyInstrumentUnitOfWork:
    """Context-managed UoW binding InstrumentRepository to one Session.

    Repositories may flush but never commit/rollback. Explicit ``commit()`` is
    required to persist. On context exit, any uncommitted work is rolled back
    and the session is closed.
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._session: Session | None = None
        self._instruments: SqlAlchemyInstrumentRepository | None = None

    def __enter__(self) -> SqlAlchemyInstrumentUnitOfWork:
        if self._session is not None:
            raise PersistenceError("InstrumentUnitOfWork is already entered")
        session = Session(self._engine)
        if self._engine.dialect.name == "sqlite":
            from sqlalchemy import text

            session.execute(text("PRAGMA foreign_keys=ON"))
        self._session = session
        self._instruments = SqlAlchemyInstrumentRepository(session, self._clock)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._session is not None:
                # Always discard uncommitted work; explicit commit is required.
                self._session.rollback()
                self._session.close()
        finally:
            self._session = None
            self._instruments = None

    def _require_session(self) -> Session:
        if self._session is None:
            raise PersistenceError(
                "InstrumentUnitOfWork is not active; use as context manager"
            )
        return self._session

    @property
    def instruments(self) -> SqlAlchemyInstrumentRepository:
        self._require_session()
        assert self._instruments is not None
        return self._instruments

    def commit(self) -> None:
        session = self._require_session()
        try:
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise PersistenceError(
                f"InstrumentUnitOfWork commit failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__},
            ) from exc

    def rollback(self) -> None:
        session = self._require_session()
        session.rollback()
