"""SQLAlchemy repository for exact, confirmation-gated Agent actions."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.agent.enums import AgentChannel, AgentPendingActionStatus
from domain.agent.models import (
    AgentPendingAction,
    canonical_json,
)
from domain.common.errors import DataContractError, IdempotencyConflict, PersistenceError
from domain.common.time import require_aware_datetime
from infrastructure.persistence.orm import AgentPendingActionRow

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _now(value: datetime | None) -> datetime:
    return value if value is not None else datetime.now(UTC)


def _pending(row: AgentPendingActionRow) -> AgentPendingAction:
    try:
        arguments = json.loads(row.normalized_arguments_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistenceError(
            "Stored Agent pending arguments are invalid", retryable=False
        ) from exc
    if not isinstance(arguments, dict):
        raise PersistenceError("Stored Agent pending arguments are not an object", retryable=False)
    return AgentPendingAction(
        action_id=row.action_id,
        conversation_id=row.conversation_id,
        channel=AgentChannel(row.channel),
        principal=row.principal,
        normalized_arguments=arguments,
        arguments_sha256=row.arguments_sha256,
        presented_summary=row.presented_summary,
        expires_at=datetime.fromisoformat(row.expires_at),
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
        status=AgentPendingActionStatus(row.status),
        version=row.version,
        capability=row.capability or "",
        operation=row.operation or "",
        token_sha256=row.token_sha256,
        result_receipt_json=row.result_receipt_json,
    )


def _same_action(left: AgentPendingAction, right: AgentPendingAction) -> bool:
    return (
        left.conversation_id == right.conversation_id
        and left.channel is right.channel
        and left.principal == right.principal
        and left.arguments_sha256 == right.arguments_sha256
        and canonical_json(left.normalized_arguments)
        == canonical_json(right.normalized_arguments)
        and left.presented_summary == right.presented_summary
        and left.expires_at == right.expires_at
        and left.capability == right.capability
        and left.operation == right.operation
        and left.token_sha256 == right.token_sha256
        and left.result_receipt_json == right.result_receipt_json
    )


_ALLOWED_TRANSITIONS: dict[AgentPendingActionStatus, frozenset[AgentPendingActionStatus]] = {
    AgentPendingActionStatus.PROPOSED: frozenset(
        {
            AgentPendingActionStatus.PRESENTED,
            AgentPendingActionStatus.REJECTED,
            AgentPendingActionStatus.EXPIRED,
        }
    ),
    AgentPendingActionStatus.PRESENTED: frozenset(
        {
            AgentPendingActionStatus.CONFIRMED,
            AgentPendingActionStatus.REJECTED,
            AgentPendingActionStatus.EXPIRED,
        }
    ),
    AgentPendingActionStatus.CONFIRMED: frozenset(
        {
            AgentPendingActionStatus.EXECUTING,
            AgentPendingActionStatus.REJECTED,
            AgentPendingActionStatus.EXPIRED,
        }
    ),
    AgentPendingActionStatus.EXECUTING: frozenset(
        {
            AgentPendingActionStatus.SUCCEEDED,
            AgentPendingActionStatus.FAILED,
            AgentPendingActionStatus.UNKNOWN,
        }
    ),
    AgentPendingActionStatus.SUCCEEDED: frozenset(),
    AgentPendingActionStatus.REJECTED: frozenset(),
    AgentPendingActionStatus.EXPIRED: frozenset(),
    AgentPendingActionStatus.FAILED: frozenset(),
    AgentPendingActionStatus.UNKNOWN: frozenset(),
}


class SqlAlchemyAgentPendingActionRepository:
    """Persist and CAS transition pending actions without executing them."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_pending_action(self, value: AgentPendingAction) -> AgentPendingAction:
        encoded = canonical_json(value.normalized_arguments)
        row = AgentPendingActionRow(
            action_id=value.action_id,
            conversation_id=value.conversation_id,
            channel=value.channel.value,
            principal=value.principal,
            normalized_arguments_json=encoded,
            arguments_sha256=value.arguments_sha256,
            presented_summary=value.presented_summary,
            status=value.status.value,
            expires_at=value.expires_at.isoformat(),
            version=value.version,
            created_at=value.created_at.isoformat(),
            updated_at=value.updated_at.isoformat(),
            capability=value.capability or None,
            operation=value.operation or None,
            token_sha256=value.token_sha256,
            result_receipt_json=value.result_receipt_json,
        )
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                session.add(row)
            return value
        except IntegrityError as exc:
            existing = self.get_pending_action(value.action_id)
            if existing is not None and _same_action(existing, value):
                return existing
            raise IdempotencyConflict("Agent pending action id was reused") from exc

    # ``create`` is intentionally a small compatibility alias for service code
    # that treats all Agent durable records as repository aggregates.
    create = create_pending_action

    def get_pending_action(self, action_id: str) -> AgentPendingAction | None:
        with Session(self._engine) as session:
            row = session.get(AgentPendingActionRow, action_id)
            return None if row is None else _pending(row)

    get = get_pending_action

    def get_pending_action_by_token_sha256(
        self,
        token_sha256: str,
    ) -> AgentPendingAction | None:
        if _SHA256.fullmatch(token_sha256) is None:
            raise DataContractError("token_sha256 must be a lowercase SHA-256 digest")
        with Session(self._engine) as session:
            row = session.scalar(
                select(AgentPendingActionRow).where(
                    AgentPendingActionRow.token_sha256 == token_sha256
                )
            )
            return None if row is None else _pending(row)

    get_by_token_sha256 = get_pending_action_by_token_sha256

    def transition_exact(
        self,
        action_id: str,
        status: AgentPendingActionStatus | None = None,
        *,
        target_status: AgentPendingActionStatus | None = None,
        arguments_sha256: str | None = None,
        arguments_hash: str | None = None,
        channel: AgentChannel | None = None,
        principal: str | None = None,
        expected_version: int = 1,
        token_sha256: str | None = None,
        token_hash: str | None = None,
        result_receipt_json: str | None = None,
        now: datetime | None = None,
    ) -> AgentPendingAction:
        target = target_status or status
        if target is None:
            raise DataContractError("pending action transition status is required")
        if arguments_sha256 is None:
            arguments_sha256 = arguments_hash
        if arguments_sha256 is None:
            raise DataContractError("pending action transition hash is required")
        if channel is None or principal is None:
            raise DataContractError("pending action transition identity is required")
        if token_sha256 is None:
            token_sha256 = token_hash
        if type(expected_version) is not int or expected_version < 1:
            raise DataContractError("expected_version must be a positive integer")
        if not isinstance(target, AgentPendingActionStatus):
            raise DataContractError("pending action transition status is invalid")
        if not isinstance(channel, AgentChannel):
            raise DataContractError("pending action transition channel is invalid")
        timestamp = _now(now)

        current = self.get_pending_action(action_id)
        if current is None:
            raise PersistenceError("Agent pending action was not found", retryable=False)
        if current.version != expected_version:
            raise PersistenceError(
                "Agent pending action version conflict",
                details={"action_id": action_id, "expected_version": expected_version},
                retryable=False,
            )
        if current.arguments_sha256 != arguments_sha256:
            raise PersistenceError(
                "Agent pending action arguments changed",
                details={"action_id": action_id},
                retryable=False,
            )
        if current.channel is not channel or current.principal != principal:
            raise PersistenceError(
                "Agent pending action confirmation identity mismatch",
                details={"action_id": action_id},
                retryable=False,
            )
        if current.token_sha256 is not None and current.token_sha256 != token_sha256:
            raise PersistenceError(
                "Agent pending action token mismatch",
                details={"action_id": action_id},
                retryable=False,
                code="AGENT_PENDING_ACTION_TOKEN_MISMATCH",
            )
        if target not in _ALLOWED_TRANSITIONS[current.status]:
            raise PersistenceError(
                "Agent pending action transition is not allowed",
                details={"from": current.status.value, "to": target.value},
                retryable=False,
            )
        if target is not AgentPendingActionStatus.EXPIRED and timestamp >= current.expires_at:
            # Mark expiry through the same version gate before reporting it.  A
            # caller cannot race an expired confirmation back into CONFIRMED.
            self._expire_if_current(current, timestamp)
            raise PersistenceError(
                "Agent pending action has expired",
                details={"action_id": action_id},
                retryable=False,
                code="AGENT_PENDING_ACTION_EXPIRED",
            )

        with Session(self._engine) as session:
            result = session.execute(
                update(AgentPendingActionRow)
                .where(
                    AgentPendingActionRow.action_id == action_id,
                    AgentPendingActionRow.version == expected_version,
                    AgentPendingActionRow.arguments_sha256 == arguments_sha256,
                    AgentPendingActionRow.channel == channel.value,
                    AgentPendingActionRow.principal == principal,
                    AgentPendingActionRow.status == current.status.value,
                )
                .values(
                    status=target.value,
                    version=expected_version + 1,
                    updated_at=timestamp.isoformat(),
                    result_receipt_json=(
                        result_receipt_json
                        if result_receipt_json is not None
                        else current.result_receipt_json
                    ),
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                raise PersistenceError(
                    "Agent pending action version conflict",
                    details={"action_id": action_id},
                    retryable=False,
                )
            session.commit()
        updated = self.get_pending_action(action_id)
        assert updated is not None
        return updated

    def reissue_confirmation_token(
        self,
        action_id: str,
        *,
        conversation_id: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int,
        token_sha256: str,
        now: datetime,
    ) -> AgentPendingAction:
        """Atomically replace the one-time confirmation digest.

        Reissuing a confirmation challenge is deliberately narrower than a
        normal state transition.  It is only valid for the exact live
        ``PRESENTED`` action owned by the supplied conversation/channel/
        principal.  The CAS update touches only the digest, version, and
        timestamp; arguments, routing, and expiry remain immutable.
        """

        if _SHA256.fullmatch(token_sha256) is None:
            raise DataContractError("token_sha256 must be a lowercase SHA-256 digest")
        if type(expected_version) is not int or expected_version < 1:
            raise DataContractError("expected_version must be a positive integer")
        if not isinstance(channel, AgentChannel):
            raise DataContractError("pending action reissue channel is invalid")
        timestamp = require_aware_datetime(now, field_name="now")

        current = self.get_pending_action(action_id)
        if current is None:
            raise PersistenceError("Agent pending action was not found", retryable=False)
        if current.conversation_id != conversation_id:
            raise PersistenceError(
                "Agent pending action conversation mismatch",
                details={"action_id": action_id},
                retryable=False,
                code="AGENT_PENDING_ACTION_IDENTITY_MISMATCH",
            )
        if current.channel is not channel or current.principal != principal:
            raise PersistenceError(
                "Agent pending action confirmation identity mismatch",
                details={"action_id": action_id},
                retryable=False,
                code="AGENT_PENDING_ACTION_IDENTITY_MISMATCH",
            )
        if current.version != expected_version:
            raise PersistenceError(
                "Agent pending action version conflict",
                details={"action_id": action_id, "expected_version": expected_version},
                retryable=False,
                code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
            )
        if current.status is not AgentPendingActionStatus.PRESENTED:
            raise PersistenceError(
                "Agent pending action cannot reissue confirmation",
                details={"action_id": action_id, "status": current.status.value},
                retryable=False,
                code="AGENT_PENDING_ACTION_STATE_CONFLICT",
            )
        if timestamp >= current.expires_at:
            self._expire_if_current(current, timestamp)
            raise PersistenceError(
                "Agent pending action has expired",
                details={"action_id": action_id},
                retryable=False,
                code="AGENT_PENDING_ACTION_EXPIRED",
            )

        with Session(self._engine) as session:
            result = session.execute(
                update(AgentPendingActionRow)
                .where(
                    AgentPendingActionRow.action_id == action_id,
                    AgentPendingActionRow.conversation_id == conversation_id,
                    AgentPendingActionRow.channel == channel.value,
                    AgentPendingActionRow.principal == principal,
                    AgentPendingActionRow.version == expected_version,
                    AgentPendingActionRow.status == AgentPendingActionStatus.PRESENTED.value,
                )
                .values(
                    token_sha256=token_sha256,
                    version=expected_version + 1,
                    updated_at=timestamp.isoformat(),
                )
            )
            cas_succeeded = result.rowcount == 1  # type: ignore[attr-defined]
            if cas_succeeded:
                session.commit()
            else:
                session.rollback()
        if not cas_succeeded:
            latest = self.get_pending_action(action_id)
            if latest is None:
                raise PersistenceError("Agent pending action was not found", retryable=False)
            if latest.conversation_id != conversation_id or (
                latest.channel is not channel or latest.principal != principal
            ):
                raise PersistenceError(
                    "Agent pending action confirmation identity mismatch",
                    details={"action_id": action_id},
                    retryable=False,
                    code="AGENT_PENDING_ACTION_IDENTITY_MISMATCH",
                )
            if latest.version != expected_version:
                raise PersistenceError(
                    "Agent pending action version conflict",
                    details={"action_id": action_id, "expected_version": expected_version},
                    retryable=False,
                    code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
                )
            if latest.status is not AgentPendingActionStatus.PRESENTED:
                raise PersistenceError(
                    "Agent pending action cannot reissue confirmation",
                    details={"action_id": action_id, "status": latest.status.value},
                    retryable=False,
                    code="AGENT_PENDING_ACTION_STATE_CONFLICT",
                )
            if latest.expires_at <= timestamp:
                self._expire_if_current(latest, timestamp)
                raise PersistenceError(
                    "Agent pending action has expired",
                    details={"action_id": action_id},
                    retryable=False,
                    code="AGENT_PENDING_ACTION_EXPIRED",
                )
            raise PersistenceError(
                "Agent pending action version conflict",
                details={"action_id": action_id},
                retryable=False,
                code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
            )
        updated = self.get_pending_action(action_id)
        assert updated is not None
        return updated

    def list_pending_actions(
        self,
        conversation_id: str,
        *,
        channel: AgentChannel | None = None,
        principal: str | None = None,
        include_terminal: bool = False,
        limit: int = 100,
    ) -> tuple[AgentPendingAction, ...]:
        bounded_limit = max(1, min(limit, 500))
        with Session(self._engine) as session:
            query = select(AgentPendingActionRow).where(
                AgentPendingActionRow.conversation_id == conversation_id
            )
            if channel is not None:
                query = query.where(AgentPendingActionRow.channel == channel.value)
            if principal is not None:
                query = query.where(AgentPendingActionRow.principal == principal)
            if not include_terminal:
                query = query.where(
                    AgentPendingActionRow.status.in_(
                        [
                            AgentPendingActionStatus.PROPOSED.value,
                            AgentPendingActionStatus.PRESENTED.value,
                            AgentPendingActionStatus.CONFIRMED.value,
                            AgentPendingActionStatus.EXECUTING.value,
                        ]
                    )
                )
            rows = session.scalars(
                query.order_by(
                    AgentPendingActionRow.created_at.desc(),
                    AgentPendingActionRow.action_id,
                ).limit(bounded_limit)
            )
            return tuple(_pending(row) for row in rows)

    def list_unresolved(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> tuple[AgentPendingAction, ...]:
        """Return pending actions requiring operator attention without mutation.

        An ``UNKNOWN`` action always remains unresolved.  An ``EXECUTING``
        action is unresolved only after its durable expiry timestamp has been
        reached; this read must not call ``expire_due`` or otherwise update the
        row.  Timestamp filtering and ordering happen in Python so persisted
        ISO timestamps with different, but valid, offsets retain chronological
        semantics.
        """
        timestamp = require_aware_datetime(now, field_name="now")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DataContractError("limit must be an integer in [1,500]")
        with Session(self._engine) as session:
            rows = tuple(
                session.scalars(
                    select(AgentPendingActionRow).where(
                        AgentPendingActionRow.status.in_(
                            (
                                AgentPendingActionStatus.UNKNOWN.value,
                                AgentPendingActionStatus.EXECUTING.value,
                            )
                        )
                    )
                )
            )

        unresolved = [
            action
            for action in (_pending(row) for row in rows)
            if action.status is AgentPendingActionStatus.UNKNOWN
            or (
                action.status is AgentPendingActionStatus.EXECUTING
                and action.expires_at <= timestamp
            )
        ]
        unresolved.sort(
            key=lambda action: (action.updated_at, action.created_at, action.action_id),
            reverse=True,
        )
        return tuple(unresolved[:limit])

    def _expire_if_current(self, current: AgentPendingAction, timestamp: datetime) -> None:
        with Session(self._engine) as session:
            result = session.execute(
                update(AgentPendingActionRow)
                .where(
                    AgentPendingActionRow.action_id == current.action_id,
                    AgentPendingActionRow.version == current.version,
                    AgentPendingActionRow.status == current.status.value,
                )
                .values(
                    status=AgentPendingActionStatus.EXPIRED.value,
                    version=current.version + 1,
                    updated_at=timestamp.isoformat(),
                )
            )
            if result.rowcount == 1:  # type: ignore[attr-defined]
                session.commit()
            else:
                session.rollback()

    def expire_due(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Expire due records without exposing any execution capability."""

        timestamp = _now(now)
        bounded_limit = max(1, min(limit, 500))
        with Session(self._engine) as session:
            rows = tuple(
                session.scalars(
                    select(AgentPendingActionRow)
                    .where(
                        AgentPendingActionRow.expires_at <= timestamp.isoformat(),
                        AgentPendingActionRow.status.in_(
                            [
                                AgentPendingActionStatus.PROPOSED.value,
                                AgentPendingActionStatus.PRESENTED.value,
                                AgentPendingActionStatus.CONFIRMED.value,
                                AgentPendingActionStatus.EXECUTING.value,
                            ]
                        ),
                    )
                    .order_by(AgentPendingActionRow.expires_at)
                    .limit(bounded_limit)
                )
            )
            if not rows:
                return 0
            for row in rows:
                row.status = (
                    AgentPendingActionStatus.UNKNOWN.value
                    if row.status == AgentPendingActionStatus.EXECUTING.value
                    else AgentPendingActionStatus.EXPIRED.value
                )
                row.version += 1
                row.updated_at = timestamp.isoformat()
            session.commit()
            return len(rows)


# Shorter import name used by some composition code.
AgentPendingActionRepositorySqlAlchemy = SqlAlchemyAgentPendingActionRepository
