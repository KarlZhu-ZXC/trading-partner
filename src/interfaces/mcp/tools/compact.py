"""MCP vNext Shadow surface over explicit capability adapters."""
from __future__ import annotations

import inspect
from copy import deepcopy
from functools import wraps
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations

from bootstrap import ApplicationContainer
from interfaces.capabilities.factory import (
    create_capability_registry as _create_neutral_capability_registry,
)
from interfaces.capabilities.policy import (
    APPEND,
    APPEND_OPEN_WORLD,
    CACHE_DISCOVERY,
    EVALUATE,
    LOCAL_ARTIFACT,
    MANAGE,
    MANAGE_OPEN_WORLD,
    READ_DURABLE,
    READ_PROVIDER,
    SYNC,
    CapabilityEffect,
    CapabilityPolicy,
    ConfirmationPolicy,
)
from interfaces.capabilities.registration import (
    _all_fields,
    _copy_handler,
    _register_dispatch_tool,
    _register_flat_dispatch_tool,
    _spec,
)
from interfaces.capabilities.registry import (
    CapabilityConfirmationRequiredError,
    CapabilityInvocationError,
    CapabilityNotFoundError,
    CapabilityRegistrar,
    CompactOperationDescriptor,
    RegisteredCapability,
)
from interfaces.capabilities.registry import (
    CompactCapabilityRegistry as _TransportNeutralCapabilityRegistry,
)
from interfaces.capabilities.schemas import exact_schema as _compact_exact_schema
from interfaces.capabilities.schemas import minimize_schema as _minimize_public_schema
from interfaces.mcp.tool_inventory import DIRECT_PUBLIC_TOOL_NAMES
from interfaces.shared.result_compaction import compact_mcp_result

__all__ = [
    "APPEND",
    "APPEND_OPEN_WORLD",
    "CACHE_DISCOVERY",
    "CapabilityConfirmationRequiredError",
    "CapabilityEffect",
    "CapabilityNotFoundError",
    "CapabilityPolicy",
    "CapabilityRegistrar",
    "CompactCapabilityRegistry",
    "CompactOperationDescriptor",
    "ConfirmationPolicy",
    "EVALUATE",
    "LOCAL_ARTIFACT",
    "MANAGE",
    "MANAGE_OPEN_WORLD",
    "READ_DURABLE",
    "READ_PROVIDER",
    "RegisteredCapability",
    "SYNC",
    "_all_fields",
    "_copy_handler",
    "_compact_exact_schema",
    "_minimize_public_schema",
    "_register_dispatch_tool",
    "_register_flat_dispatch_tool",
    "_spec",
    "create_compact_capability_registry",
    "create_compact_mcp_server",
]


def _bound_arguments(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    return {key: value for key, value in bound.arguments.items() if key != "self"}


class CompactCapabilityRegistry(_TransportNeutralCapabilityRegistry):
    """MCP adapter over the transport-neutral capability registry."""

    def __init__(self, registry: _TransportNeutralCapabilityRegistry | None = None) -> None:
        super().__init__()
        if registry is not None:
            self._capabilities = registry._capabilities
            self._operations = registry._operations
            self._usage = registry._usage

    @classmethod
    def from_registry(
        cls,
        registry: _TransportNeutralCapabilityRegistry,
    ) -> CompactCapabilityRegistry:
        """Adopt one neutral graph without rebuilding validators or handlers."""

        return cls(registry)

    @staticmethod
    def _mcp_annotations(policy: CapabilityPolicy) -> ToolAnnotations:
        return ToolAnnotations(
            readOnlyHint=policy.read_only,
            destructiveHint=policy.destructive,
            idempotentHint=policy.idempotent,
            openWorldHint=policy.open_world,
        )

    def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name=item.tool.name,
                title=item.tool.title,
                description=item.tool.description,
                inputSchema=_minimize_public_schema(deepcopy(item.tool.parameters)),
                outputSchema=(
                    _minimize_public_schema(deepcopy(item.tool.output_schema))
                    if isinstance(item.tool.output_schema, dict)
                    else item.tool.output_schema
                ),
                annotations=self._mcp_annotations(item.policy),
                icons=item.tool.icons,
                _meta=item.tool.meta,
            )
            for item in self._capabilities.values()
        ]

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> Any:
        try:
            result = await super().invoke_uncompacted(
                name,
                arguments,
                confirmation=confirmation,
            )
        except CapabilityInvocationError as error:
            raise ToolError(str(error)) from error.cause
        return compact_mcp_result(result, capability=name, arguments=arguments)

    async def invoke_uncompacted(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> Any:
        try:
            return await super().invoke_uncompacted(
                name,
                arguments,
                confirmation=confirmation,
            )
        except CapabilityInvocationError as error:
            raise ToolError(str(error)) from error.cause

    async def invoke_validated(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        enforce_confirmation: bool = True,
    ) -> Any:
        try:
            return await super().invoke_validated(
                capability,
                operation,
                arguments,
                confirmation=confirmation,
                enforce_confirmation=enforce_confirmation,
            )
        except CapabilityInvocationError as error:
            raise ToolError(str(error)) from error.cause

    validated_invoke = invoke_validated

    def bind_mcp(self, server: FastMCP, *, names: frozenset[str] | None = None) -> None:
        """Render the same neutral registry through FastMCP's tool boundary."""
        for capability in self._capabilities.values():
            tool = capability.tool
            if names is not None and tool.name not in names:
                continue
            server.add_tool(
                self._mcp_result_wrapper(tool),
                name=tool.name,
                title=tool.title,
                description=tool.description,
                annotations=self._mcp_annotations(capability.policy),
                icons=tool.icons,
                meta=tool.meta,
            )

    def _mcp_result_wrapper(self, tool: Any) -> Any:
        fn = tool.fn

        @wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            async def invoke() -> Any:
                result = fn(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result

            resolved = await self.observe_invocation(
                tool.name, _bound_arguments(fn, args, kwargs), invoke,
            )
            return compact_mcp_result(
                resolved,
                capability=tool.name,
                arguments=_bound_arguments(fn, args, kwargs),
            )

        return wrapped


class CompactFastMCP(FastMCP):
    """FastMCP surface that minimizes schemas only at the public protocol boundary."""

    async def list_tools(self) -> list[MCPTool]:
        from interfaces.mcp.progressive import SHORT_DESCRIPTIONS, public_input_schema

        tools = await super().list_tools()
        for tool in tools:
            if tool.name in SHORT_DESCRIPTIONS:
                tool.description = SHORT_DESCRIPTIONS[tool.name]
                tool.inputSchema = (
                    _minimize_public_schema(tool.inputSchema)
                    if tool.name == "instrument_resolve"
                    else public_input_schema(tool)
                )
            else:
                tool.inputSchema = _minimize_public_schema(tool.inputSchema)
            # Exact output schemas remain owned by the registry. Publishing them
            # eagerly here would undo progressive input-schema discovery.
            tool.outputSchema = None
        return tools


def create_compact_capability_registry(
    container: ApplicationContainer,
    *,
    chart_persister: Any,
) -> CompactCapabilityRegistry:
    """Build and adapt the canonical neutral graph for legacy MCP callers."""

    registry = _create_neutral_capability_registry(
        container,
        chart_persister=chart_persister,
        source="MCP",
    )
    return CompactCapabilityRegistry.from_registry(registry)


def create_compact_mcp_server(
    container: ApplicationContainer,
    *,
    chart_persister: Any,
) -> FastMCP:
    """Render the compact capability registry through the FastMCP transport."""
    registry = create_compact_capability_registry(
        container,
        chart_persister=chart_persister,
    )
    from interfaces.mcp.progressive import register_discovery, register_gateways

    server = CompactFastMCP(
        container.settings.mcp_server_name,
        instructions=(
            "Investment views, portfolio, market facts, risk and monitoring. "
            "Discover available capabilities with capability_discover(); pass tool and "
            "operation for exact inputs. Reuse known schemas. Discovery returns call_tool "
            "and the original inputSchema. For capability_read/write pass the original "
            "tool name and full inputs as arguments; other call_tools take inputs directly. "
            "Writes require explicit user authorization and confirmation=original tool name. "
            "Discovery never authorizes actions. Preserve sources, time and missing data."
        ),
    )
    registry.bind_mcp(server, names=DIRECT_PUBLIC_TOOL_NAMES)
    register_discovery(server, registry)
    register_gateways(server, registry)
    return server
