"""Unified entity ID prefixes and format helpers.

Format: ``<prefix>_<uuid7-compatible-lowercase-token>``

Example: ``req_01901945-7f5d-7cc3-98c4-dc0c0c07398f``

Instrument public identity uses a separate business key scheme
(``asset_type:market:symbol``), not UUID prefixes.
"""

from enum import StrEnum


class EntityIdPrefix(StrEnum):
    """Frozen ID prefixes. Phase 1A uses req, snapshot, and audit only."""

    REQ = "req"
    SUBJECT = "case"
    THESIS = "thesis"
    REV = "rev"
    EVIDENCE = "evidence"
    REPORT = "report"
    EVENT = "event"
    DECISION = "decision"
    JOURNAL = "journal"
    WATCH_GROUP = "watch_group"
    WATCH_MEMBERSHIP = "watch_membership"
    WATCH_MUTATION = "watch_mutation"
    RISK_POLICY = "risk_policy"
    MONITOR = "monitor"
    MONITOR_EVENT = "monitor_event"
    MONITOR_RUN = "monitor_run"
    MONITOR_JUDGMENT = "monitor_judgment"
    MONITOR_NOTIFICATION = "monitor_notification"
    MONITOR_RESOLUTION = "monitor_resolution"
    TRADE_PLAN = "trade_plan"
    VALIDATION = "validation"
    ACTIVITY_COVERAGE = "activity_coverage"
    PROVIDER_ROUTE = "provider_route"
    RETRO_PLAN = "retro_plan"
    RETRO = "retro"
    RETRO_REVIEW = "retro_review"
    RETRO_EXPORT = "retro_export"
    SCORECARD = "scorecard"
    AGENDA = "agenda"
    BROKER_ORDER = "broker_order"
    # Shared Agent Runtime durable entities.
    AGENT_CONVERSATION = "agent_conversation"
    AGENT_BINDING = "agent_binding"
    AGENT_MESSAGE = "agent_message"
    AGENT_ATTACHMENT = "agent_attachment"
    AGENT_TURN = "agent_turn"
    AGENT_TOOL_RECEIPT = "agent_tool_receipt"
    AGENT_PENDING_ACTION = "agent_pending_action"
    AGENT_CURSOR = "agent_cursor"
    AGENT_HANDOFF = "agent_handoff"
    AGENT_PREFERENCES = "agent_preferences"
    AGENT_PREFERENCE_REVISION = "agent_preference_revision"
    REVIEW_ITEM = "review_item"
    ACTIVITY_ANNOTATION = "activity_annotation"
    TRADE_CYCLE_OVERRIDE = "trade_cycle_override"
    EXTERNAL_NOTE = "external_note"
    EXTERNAL_NOTE_REVISION = "external_note_revision"
    EXTERNAL_NOTE_INTERPRETATION = "external_note_interpretation"
    EXTERNAL_NOTE_SYNC = "external_note_sync"
    OPERATIONAL_JOB_RUN = "operational_job_run"
    # Phase 3A append-only futures definition identity prefixes.
    FUTURES_PRODUCT = "futures_product"
    FUTURES_PRODUCT_VERSION = "futures_product_version"
    FUTURES_CONTRACT_VERSION = "futures_contract_version"
    SNAPSHOT = "snapshot"
    RUN = "run"
    AUDIT = "audit"


def format_entity_id(prefix: EntityIdPrefix, token: str) -> str:
    """Assemble a canonical entity id from a frozen prefix and token."""
    if not isinstance(prefix, EntityIdPrefix):
        raise TypeError("prefix must be EntityIdPrefix")
    normalized = token.strip().lower()
    if not normalized:
        raise ValueError("entity id token must be non-empty")
    return f"{prefix.value}_{normalized}"
