"""System audit log writers — sole runtime writers for system_audit_log.

Phase 1A: engine-bound ``SqlAlchemyAuditLogWriter`` (own short-lived Session + commit).
Phase 1B: session-bound ``SqlAlchemySessionAuditLogWriter`` (add/flush only; UoW commits).
Both implement the ``AuditLogWriter`` port (``append``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.errors import PersistenceError
from domain.common.ids import EntityIdPrefix
from infrastructure.persistence.orm import SystemAuditLogRow


def _build_audit_row(
    *,
    event_type: str,
    payload: Mapping[str, object],
    request_id: str | None,
    clock: Clock,
    id_generator: IdGenerator,
    secret_redactor: SecretRedactor,
) -> SystemAuditLogRow:
    if not event_type or not event_type.strip():
        raise PersistenceError("event_type must be non-empty")

    audit_id = id_generator.new(EntityIdPrefix.AUDIT)
    recorded_at = clock.now().isoformat()
    redacted = secret_redactor.redact_mapping(payload)
    payload_json = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
    return SystemAuditLogRow(
        audit_id=audit_id,
        event_type=event_type.strip(),
        request_id=request_id,
        recorded_at=recorded_at,
        payload_json=payload_json,
    )


class SqlAlchemyAuditLogWriter:
    """Engine-bound writer: opens its own Session and commits immediately."""

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

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object],
        request_id: str | None = None,
    ) -> str:
        row = _build_audit_row(
            event_type=event_type,
            payload=payload,
            request_id=request_id,
            clock=self._clock,
            id_generator=self._id_generator,
            secret_redactor=self._secret_redactor,
        )
        audit_id = row.audit_id
        try:
            with Session(self._engine) as session:
                session.add(row)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"Failed to append audit log: {type(exc).__name__}",
                details={"error_type": type(exc).__name__, "event_type": event_type},
            ) from exc
        return audit_id


class SqlAlchemySessionAuditLogWriter:
    """Session-bound writer for ResearchUnitOfWork.

    Only ``add`` / ``flush`` — never commits. Business rows and audit share the
    same UoW transaction.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._session = session
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object],
        request_id: str | None = None,
    ) -> str:
        row = _build_audit_row(
            event_type=event_type,
            payload=payload,
            request_id=request_id,
            clock=self._clock,
            id_generator=self._id_generator,
            secret_redactor=self._secret_redactor,
        )
        try:
            self._session.add(row)
            self._session.flush()
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"Failed to append audit log: {type(exc).__name__}",
                details={"error_type": type(exc).__name__, "event_type": event_type},
            ) from exc
        return row.audit_id
