"""Instrument Unit of Work — single Session for instrument master + aliases."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from application.ports.instrument_repository import InstrumentRepository


class InstrumentUnitOfWork(Protocol):
    def __enter__(self) -> InstrumentUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @property
    def instruments(self) -> InstrumentRepository: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
