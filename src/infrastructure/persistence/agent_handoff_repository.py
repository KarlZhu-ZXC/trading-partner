"""SQLAlchemy persistence for single-use Agent channel handoffs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.agent_handoff_repository import AgentHandoffRepository
from domain.agent.enums import AgentChannel
from domain.agent.models import AgentChannelHandoff
from domain.common.errors import DataContractError, IdempotencyConflict, PersistenceError
from infrastructure.persistence.orm import AgentChannelHandoffRow


def _now(value: datetime | None) -> datetime:
    return value if value is not None else datetime.now(UTC)


def _value(row: AgentChannelHandoffRow) -> AgentChannelHandoff:
    return AgentChannelHandoff(
        handoff_id=row.handoff_id,
        conversation_id=row.conversation_id,
        owner_principal=row.owner_principal,
        target_channel=AgentChannel(row.target_channel),
        token_sha256=row.token_sha256,
        expires_at=datetime.fromisoformat(row.expires_at),
        created_at=datetime.fromisoformat(row.created_at),
        consumed_at=(
            None if row.consumed_at is None else datetime.fromisoformat(row.consumed_at)
        ),
        version=row.version,
    )


class SqlAlchemyAgentHandoffRepository(AgentHandoffRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_handoff(self, value: AgentChannelHandoff) -> AgentChannelHandoff:
        row = AgentChannelHandoffRow(
            handoff_id=value.handoff_id,
            conversation_id=value.conversation_id,
            owner_principal=value.owner_principal,
            target_channel=value.target_channel.value,
            token_sha256=value.token_sha256,
            expires_at=value.expires_at.isoformat(),
            created_at=value.created_at.isoformat(),
            consumed_at=(value.consumed_at.isoformat() if value.consumed_at else None),
            version=value.version,
        )
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                session.add(row)
            return value
        except IntegrityError as exc:
            existing = self._get_by_token(value.token_sha256)
            if existing is not None and existing == value:
                return existing
            raise IdempotencyConflict("Agent handoff token digest was reused") from exc

    def get_by_token_sha256(self, token_sha256: str) -> AgentChannelHandoff | None:
        _validate_digest(token_sha256)
        return self._get_by_token(token_sha256)

    def _get_by_token(self, token_sha256: str) -> AgentChannelHandoff | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(AgentChannelHandoffRow).where(
                    AgentChannelHandoffRow.token_sha256 == token_sha256
                )
            )
            return None if row is None else _value(row)

    def consume_exact(
        self,
        token_sha256: str,
        *,
        target_channel: AgentChannel,
        owner_principal: str,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> AgentChannelHandoff:
        _validate_digest(token_sha256)
        if not isinstance(target_channel, AgentChannel):
            raise DataContractError("handoff target_channel is invalid")
        if not isinstance(owner_principal, str) or not owner_principal.strip():
            raise DataContractError("handoff owner_principal must not be blank")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise DataContractError("handoff expected_version must be positive")
        timestamp = _now(now)
        current = self._get_by_token(token_sha256)
        if current is None:
            raise PersistenceError(
                "Agent handoff was not found",
                retryable=False,
                code="AGENT_HANDOFF_NOT_FOUND",
            )
        if (
            current.target_channel is not target_channel
            or current.owner_principal != owner_principal
        ):
            raise PersistenceError(
                "Agent handoff identity mismatch",
                retryable=False,
                code="AGENT_HANDOFF_IDENTITY_MISMATCH",
            )
        if current.consumed_at is not None:
            raise PersistenceError(
                "Agent handoff was already consumed",
                retryable=False,
                code="AGENT_HANDOFF_ALREADY_CONSUMED",
            )
        if timestamp >= current.expires_at:
            raise PersistenceError(
                "Agent handoff has expired",
                retryable=False,
                code="AGENT_HANDOFF_EXPIRED",
            )
        expected = current.version if expected_version is None else expected_version
        with Session(self._engine) as session:
            result = session.execute(
                update(AgentChannelHandoffRow)
                .where(
                    AgentChannelHandoffRow.token_sha256 == token_sha256,
                    AgentChannelHandoffRow.target_channel == target_channel.value,
                    AgentChannelHandoffRow.owner_principal == owner_principal,
                    AgentChannelHandoffRow.consumed_at.is_(None),
                    AgentChannelHandoffRow.version == expected,
                )
                .values(consumed_at=timestamp.isoformat(), version=expected + 1)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.rollback()
                raise PersistenceError(
                    "Agent handoff version conflict",
                    retryable=False,
                    code="AGENT_HANDOFF_VERSION_CONFLICT",
                )
            session.commit()
        updated = self._get_by_token(token_sha256)
        if updated is None:
            raise PersistenceError("Agent handoff disappeared", retryable=False)
        return updated


def _validate_digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise DataContractError("token_sha256 must be a lowercase SHA-256 digest")


__all__ = ["SqlAlchemyAgentHandoffRepository"]
