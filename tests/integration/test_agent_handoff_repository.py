from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from infrastructure.persistence.database import create_engine_from_url


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
