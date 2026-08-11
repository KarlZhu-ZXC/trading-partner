from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from application.services.agent_handoff_service import AgentHandoffService
from domain.agent.enums import AgentChannel
from domain.agent.models import AgentConversation
from domain.common.errors import PersistenceError
from infrastructure.persistence.agent_conversation_repository import (
    SqlAlchemyAgentConversationRepository,
)
from infrastructure.persistence.agent_handoff_repository import SqlAlchemyAgentHandoffRepository
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.system.id_generator import Uuid7IdGenerator


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


def test_handoff_owner_target_expiry_and_single_use(orm_sqlite_url: str) -> None:
    engine = create_engine_from_url(orm_sqlite_url)
    conversations = SqlAlchemyAgentConversationRepository(engine)
    handoffs = SqlAlchemyAgentHandoffRepository(engine)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    conversation = AgentConversation(
        "agent_conversation_handoff_test",
        "local-console",
        "Console",
        now,
        now,
    )
    conversations.create_conversation(conversation)
    clock = FixedClock(now)
    service = AgentHandoffService(handoffs, clock, Uuid7IdGenerator(), default_ttl_seconds=60)
    value, raw_token = service.create(
        conversation_id=conversation.conversation_id,
        owner_principal="local-console",
        target_channel=AgentChannel.TELEGRAM,
    )
    assert value.token_sha256 not in raw_token
    consumed = service.consume(
        raw_token,
        target_channel=AgentChannel.TELEGRAM,
        owner_principal="local-console",
    )
    assert consumed.consumed_at == now
    with pytest.raises(PersistenceError, match="already consumed"):
        service.consume(
            raw_token,
            target_channel=AgentChannel.TELEGRAM,
            owner_principal="local-console",
        )


def test_handoff_rejects_owner_or_target_mismatch_and_expiry(orm_sqlite_url: str) -> None:
    engine = create_engine_from_url(orm_sqlite_url)
    conversations = SqlAlchemyAgentConversationRepository(engine)
    handoffs = SqlAlchemyAgentHandoffRepository(engine)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    conversation = AgentConversation(
        "agent_conversation_handoff_expiry",
        "local-console",
        "Console",
        now,
        now,
    )
    conversations.create_conversation(conversation)
    clock = FixedClock(now)
    service = AgentHandoffService(handoffs, clock, Uuid7IdGenerator(), default_ttl_seconds=30)
    _, raw_token = service.create(
        conversation_id=conversation.conversation_id,
        owner_principal="local-console",
        target_channel=AgentChannel.TELEGRAM,
    )
    with pytest.raises(PersistenceError, match="identity mismatch"):
        service.consume(
            raw_token,
            target_channel=AgentChannel.CONSOLE,
            owner_principal="local-console",
        )
    clock.value = now + timedelta(seconds=31)
    with pytest.raises(PersistenceError, match="expired"):
        service.consume(
            raw_token,
            target_channel=AgentChannel.TELEGRAM,
            owner_principal="local-console",
        )


def test_handoff_token_digest_has_database_hex64_check(orm_sqlite_url: str) -> None:
    engine = create_engine_from_url(orm_sqlite_url)
    with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_conversations ("
                    "conversation_id, owner_principal, title, status, rolling_summary, "
                    "summary_through_sequence, next_message_sequence, version, "
                    "created_at, updated_at) "
                    "VALUES ('agent_conversation_digest_check', 'local-console', 'Check', "
                    "'ACTIVE', "
                    "'', 0, 1, 1, '2026-08-10T12:00:00+00:00', '2026-08-10T12:00:00+00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO agent_channel_handoffs ("
                    "handoff_id, conversation_id, owner_principal, target_channel, "
                    "token_sha256, expires_at, created_at, consumed_at, version) "
                    "VALUES (:handoff_id, :conversation_id, :owner_principal, :target_channel, "
                    ":token_sha256, :expires_at, :created_at, NULL, 1)"
                ),
                {
                    "handoff_id": "agent_handoff_invalid_digest",
                    "conversation_id": "agent_conversation_digest_check",
                    "owner_principal": "local-console",
                    "target_channel": "TELEGRAM",
                    "token_sha256": "NOT-A-DIGEST",
                    "expires_at": "2026-08-10T12:01:00+00:00",
                    "created_at": "2026-08-10T12:00:00+00:00",
                },
            )
