"""Schema checks for the Shared Agent Runtime migration chain through 0046."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_agent_runtime_migration_creates_agent_tables(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent.db'}"
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")

    engine = create_engine(database_url, future=True)
    tables = set(inspect(engine).get_table_names())
    assert {
        "agent_conversations",
        "agent_channel_bindings",
        "agent_messages",
        "agent_tool_receipts",
        "agent_pending_actions",
        "agent_channel_cursors",
        "agent_channel_handoffs",
        "agent_turns",
    }.issubset(tables)
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_conversations "
                "(conversation_id, owner_principal, title, status, rolling_summary, "
                "summary_through_sequence, next_message_sequence, version, created_at, updated_at) "
                "VALUES ('c', 'u', 't', 'ACTIVE', '', 0, 1, 1, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
        )
        connection.commit()
    engine.dispose()
