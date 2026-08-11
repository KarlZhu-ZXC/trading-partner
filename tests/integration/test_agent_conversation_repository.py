"""Focused persistence tests for Agent-A conversation primitives."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from domain.agent import (
    AgentChannel,
    AgentChannelBinding,
    AgentConversation,
    AgentMessage,
    AgentMessageRole,
    AgentPendingAction,
    AgentPendingActionStatus,
    arguments_digest,
)
from domain.common.errors import PersistenceError
from infrastructure.persistence.agent_conversation_repository import (
    SqlAlchemyAgentConversationRepository,
)
from infrastructure.persistence.database import create_engine_from_url


def _conversation() -> tuple[AgentConversation, datetime]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AgentConversation("agent_conversation_test", "user", "Test", now, now), now


def test_append_sequence_external_dedupe_and_summary_cas(orm_sqlite_url: str) -> None:
    repo = SqlAlchemyAgentConversationRepository(create_engine_from_url(orm_sqlite_url))
    conversation, now = _conversation()
    repo.create_conversation(conversation)

    first = repo.append_message(
        AgentMessage(
            "agent_message_one",
            conversation.conversation_id,
            AgentMessageRole.USER,
            "hello",
            now,
            channel=AgentChannel.CONSOLE,
            external_message_ref="console:1",
        )
    )
    replay = repo.append_message(
        AgentMessage(
            "agent_message_replay",
            conversation.conversation_id,
            AgentMessageRole.USER,
            "hello",
            now,
            channel=AgentChannel.CONSOLE,
            external_message_ref="console:1",
        )
    )
    assert first.sequence == replay.sequence == 1
    assert repo.get_message_by_external_ref(AgentChannel.CONSOLE, "console:1") == first
    assert repo.get_message_by_external_ref(AgentChannel.TELEGRAM, "console:1") is None
    second = repo.append_message(
        AgentMessage(
            "agent_message_two",
            conversation.conversation_id,
            AgentMessageRole.ASSISTANT,
            "world",
            now,
        )
    )
    assert second.sequence == 2
    updated = repo.update_summary(conversation.conversation_id, "hello", 1)
    assert updated.summary_through_sequence == 1
    with pytest.raises(PersistenceError):
        repo.update_summary(conversation.conversation_id, "stale", 2, 0)


def test_binding_cursor_and_pending_action_exact_cas(orm_sqlite_url: str) -> None:
    repo = SqlAlchemyAgentConversationRepository(create_engine_from_url(orm_sqlite_url))
    conversation, now = _conversation()
    repo.create_conversation(conversation)
    binding = AgentChannelBinding(
        "agent_binding_test",
        conversation.conversation_id,
        AgentChannel.TELEGRAM,
        "chat:1",
        now,
        now,
    )
    assert repo.bind_channel(binding).is_active
    assert repo.get_binding(AgentChannel.TELEGRAM, "chat:1") is not None
    assert repo.deactivate_channel(AgentChannel.TELEGRAM, "chat:1") is not None
    second_conversation = AgentConversation(
        "agent_conversation_second",
        "user",
        "Second",
        now,
        now,
    )
    repo.create_conversation(second_conversation)
    rebound = repo.bind_channel(
        AgentChannelBinding(
            "agent_binding_second",
            second_conversation.conversation_id,
            AgentChannel.TELEGRAM,
            "chat:1",
            now,
            now,
        )
    )
    assert rebound.conversation_id == second_conversation.conversation_id
    repo.advance_cursor(AgentChannel.TELEGRAM, "poller", 10)
    assert repo.advance_cursor(AgentChannel.TELEGRAM, "poller", 11, 10).version == 2
    with pytest.raises(PersistenceError):
        repo.advance_cursor(AgentChannel.TELEGRAM, "poller", 12, 10)

    args = {"operation": "create", "title": "Bounded"}
    action = AgentPendingAction(
        "agent_pending_action_test",
        conversation.conversation_id,
        AgentChannel.CONSOLE,
        "user",
        args,
        arguments_digest(args),
        "Confirm this exact action",
        now + timedelta(minutes=5),
        now,
        now,
    )
    assert repo.create_pending_action(action).status is AgentPendingActionStatus.PROPOSED
    presented = repo.transition_exact(
        action.action_id,
        AgentPendingActionStatus.PRESENTED,
        arguments_sha256=action.arguments_sha256,
        channel=action.channel,
        principal=action.principal,
        expected_version=1,
        now=now,
    )
    assert presented.version == 2
    with pytest.raises(PersistenceError):
        repo.transition_exact(
            action.action_id,
            AgentPendingActionStatus.CONFIRMED,
            arguments_sha256=action.arguments_sha256,
            channel=action.channel,
            principal=action.principal,
            expected_version=1,
            now=now,
        )


def test_message_sequence_is_atomic_and_external_refs_are_channel_scoped(
    orm_sqlite_url: str,
) -> None:
    engine = create_engine_from_url(orm_sqlite_url)
    repo = SqlAlchemyAgentConversationRepository(engine)
    conversation, now = _conversation()
    repo.create_conversation(conversation)

    def append(index: int) -> int:
        stored = repo.append_message(
            AgentMessage(
                f"agent_message_{index}",
                conversation.conversation_id,
                AgentMessageRole.USER,
                f"message {index}",
                now,
                channel=AgentChannel.CONSOLE,
                external_message_ref=f"{index}",
            )
        )
        return stored.sequence

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = tuple(pool.map(append, range(12)))

    assert sorted(sequences) == list(range(1, 13))
    telegram = repo.append_message(
        AgentMessage(
            "agent_message_telegram_reused_ref",
            conversation.conversation_id,
            AgentMessageRole.USER,
            "Telegram message with the same external id",
            now,
            channel=AgentChannel.TELEGRAM,
            external_message_ref="0",
        )
    )
    assert telegram.sequence == 13
