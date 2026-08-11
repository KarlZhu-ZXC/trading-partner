"""Shared Agent interface boundary (transport-neutral, read-only Agent-A)."""

from application.ports.agent_tool_gateway import (
    AgentToolDescriptor,
    AgentToolReceipt,
    AgentToolResult,
)
from interfaces.agent.action_gateway import (
    AgentActionGateway,
    CompactAgentActionOperationGateway,
)
from interfaces.agent.capability_gateway import (
    AgentCapabilityAccessDeniedError,
    AgentCapabilityDescriptor,
    AgentCapabilityGateway,
    AgentGateway,
    CapabilityDescriptor,
    CapabilityGateway,
    build_agent_capability_gateway,
    compact_result,
    compact_tool_result,
    create_agent_capability_gateway,
)

__all__ = [
    "AgentCapabilityAccessDeniedError",
    "AgentActionGateway",
    "CompactAgentActionOperationGateway",
    "AgentCapabilityDescriptor",
    "AgentCapabilityGateway",
    "AgentGateway",
    "CapabilityGateway",
    "CapabilityDescriptor",
    "AgentToolDescriptor",
    "AgentToolReceipt",
    "AgentToolResult",
    "build_agent_capability_gateway",
    "compact_result",
    "compact_tool_result",
    "create_agent_capability_gateway",
]
