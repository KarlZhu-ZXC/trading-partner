"""Entity ID contract tests."""

from __future__ import annotations

import re

import pytest

from domain.common.ids import EntityIdPrefix, format_entity_id
from infrastructure.system.id_generator import Uuid7IdGenerator


def test_entity_id_prefix_frozen_values() -> None:
    expected = {
        "req",
        "case",
        "thesis",
        "rev",
        "evidence",
        "report",
        "event",
        "decision",
        "journal",
        "watch_group",
        "watch_membership",
        "watch_mutation",
        "risk_policy",
        "monitor",
        "monitor_event",
        "monitor_run",
        "monitor_judgment",
        "monitor_notification",
        "monitor_resolution",
        "futures_product",
        "futures_product_version",
        "futures_contract_version",
        "trade_plan",
        "validation",
        "activity_coverage",
        "provider_route",
        "retro_plan",
        "retro",
        "retro_review",
        "retro_export",
        "scorecard",
        "agenda",
        "broker_order",
        "agent_conversation",
        "agent_binding",
        "agent_message",
        "agent_tool_receipt",
        "agent_pending_action",
        "agent_handoff",
        "agent_cursor",
        "agent_turn",
        "agent_preferences",
        "agent_preference_revision",
        "review_item",
        "snapshot",
        "run",
        "audit",
    }
    assert {p.value for p in EntityIdPrefix} == expected


def test_uuid7_id_format() -> None:
    gen = Uuid7IdGenerator()
    entity_id = gen.new(EntityIdPrefix.REQ)
    assert entity_id.startswith("req_")
    token = entity_id.removeprefix("req_")
    assert token == token.lower()
    # Standard UUID string form
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        token,
    )


def test_id_generator_rejects_arbitrary_string_prefix() -> None:
    gen = Uuid7IdGenerator()
    with pytest.raises(TypeError):
        gen.new("req")  # type: ignore[arg-type]


def test_format_entity_id() -> None:
    assert (
        format_entity_id(EntityIdPrefix.AUDIT, "01901945-7f5d-7cc3-98c4-dc0c0c07398f")
        == "audit_01901945-7f5d-7cc3-98c4-dc0c0c07398f"
    )


def test_snapshot_and_audit_prefixes() -> None:
    gen = Uuid7IdGenerator()
    assert gen.new(EntityIdPrefix.SNAPSHOT).startswith("snapshot_")
    assert gen.new(EntityIdPrefix.AUDIT).startswith("audit_")
