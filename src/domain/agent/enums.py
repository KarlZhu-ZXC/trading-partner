"""Closed vocabularies used by the shared Agent Runtime persistence layer."""

from enum import StrEnum


class AgentConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AgentChannel(StrEnum):
    CONSOLE = "CONSOLE"
    TELEGRAM = "TELEGRAM"


class AgentMessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class AgentPendingActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PRESENTED = "PRESENTED"
    CONFIRMED = "CONFIRMED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class AgentTurnStatus(StrEnum):
    """Durable lifecycle for one model turn.

    ``WAITING_TOOL`` means the model has requested one or more bounded tools
    and the runtime is still processing that tool round.  ``CANCELLED`` is
    modelled for forward compatibility; the current runtime does not cancel
    an upstream request when a transport disconnects.
    """

    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Short aliases are useful at the interface boundary while the longer names
# make the ownership of these vocabularies explicit in type annotations.
ConversationStatus = AgentConversationStatus
Channel = AgentChannel
MessageRole = AgentMessageRole
PendingActionStatus = AgentPendingActionStatus
TurnStatus = AgentTurnStatus
