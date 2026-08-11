"""Cross-process Agent conversation turn serialization."""

from __future__ import annotations

from infrastructure.system.agent_turn_lock import AgentTurnLockFactory


def test_same_conversation_is_locked_across_factory_instances(tmp_path) -> None:
    first = AgentTurnLockFactory(tmp_path)("agent_conversation_1")
    second = AgentTurnLockFactory(tmp_path)("agent_conversation_1")
    other = AgentTurnLockFactory(tmp_path)("agent_conversation_2")

    assert first.acquire() is True
    assert second.acquire() is False
    assert other.acquire() is True
    other.release()
    first.release()

    assert second.acquire() is True
    second.release()
