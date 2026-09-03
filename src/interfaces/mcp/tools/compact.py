"""MCP vNext Shadow surface over explicit capability adapters."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache, reduce, wraps
from operator import or_
from types import SimpleNamespace
from typing import Annotated, Any, Literal, Protocol, cast, get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool as FastMCPTool
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from bootstrap import ApplicationContainer
from interfaces.mcp.tool_inventory import MCP_VNEXT_TOOL_NAMES
from interfaces.mcp.tools.a_share import build_a_share_adapters
from interfaces.mcp.tools.challenge import build_challenge_adapters
from interfaces.mcp.tools.execution import build_execution_adapters
from interfaces.mcp.tools.instrument import build_instrument_adapters
from interfaces.mcp.tools.market_technical import build_market_technical_adapters
from interfaces.mcp.tools.monitoring import build_monitoring_adapters
from interfaces.mcp.tools.portfolio import build_portfolio_adapters
from interfaces.mcp.tools.research import build_research_adapters
from interfaces.mcp.tools.research_memory import build_research_memory_adapters
from interfaces.mcp.tools.risk import build_risk_adapters
from interfaces.mcp.tools.system import build_system_adapters
from interfaces.mcp.tools.us_context import build_us_context_adapters
from interfaces.mcp.tools.us_research import build_us_research_adapters
from interfaces.mcp.tools.view_review import build_view_review_adapters
from interfaces.mcp.tools.watchlist import build_watchlist_adapters
from interfaces.mcp.tools.workflows import build_workflow_adapters
from interfaces.mcp.validation import tool_input_invalid_envelope
from interfaces.shared.result_compaction import compact_mcp_result


class CapabilityEffect(StrEnum):
    READ_DURABLE = "READ_DURABLE"
    READ_PROVIDER = "READ_PROVIDER"
    CACHE_DISCOVERY = "CACHE_DISCOVERY"
    APPEND = "APPEND"
    APPEND_OPEN_WORLD = "APPEND_OPEN_WORLD"
    MANAGE = "MANAGE"
    MANAGE_OPEN_WORLD = "MANAGE_OPEN_WORLD"
    SYNC = "SYNC"
    EVALUATE = "EVALUATE"
    LOCAL_ARTIFACT = "LOCAL_ARTIFACT"


class ConfirmationPolicy(StrEnum):
    NONE = "NONE"
    MATCH_CAPABILITY_NAME = "MATCH_CAPABILITY_NAME"


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    effect: CapabilityEffect
    confirmation: ConfirmationPolicy
    annotations: ToolAnnotations

    @property
    def confirmation_required(self) -> bool:
        return self.confirmation is not ConfirmationPolicy.NONE


def _policy(
    effect: CapabilityEffect,
    *,
    confirmation: ConfirmationPolicy,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> CapabilityPolicy:
    return CapabilityPolicy(
        effect=effect,
        confirmation=confirmation,
        annotations=ToolAnnotations(
            readOnlyHint=read_only,
            destructiveHint=destructive,
            idempotentHint=idempotent,
            openWorldHint=open_world,
        ),
    )


READ_DURABLE = _policy(
    CapabilityEffect.READ_DURABLE,
    confirmation=ConfirmationPolicy.NONE,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
READ_PROVIDER = _policy(
    CapabilityEffect.READ_PROVIDER,
    confirmation=ConfirmationPolicy.NONE,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=True,
)
CACHE_DISCOVERY = _policy(
    CapabilityEffect.CACHE_DISCOVERY,
    confirmation=ConfirmationPolicy.NONE,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
MANAGE = _policy(
    CapabilityEffect.MANAGE,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=False,
)
MANAGE_OPEN_WORLD = _policy(
    CapabilityEffect.MANAGE_OPEN_WORLD,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=True,
)
APPEND = _policy(
    CapabilityEffect.APPEND,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
APPEND_OPEN_WORLD = _policy(
    CapabilityEffect.APPEND_OPEN_WORLD,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
SYNC = _policy(
    CapabilityEffect.SYNC,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=False,
    open_world=True,
)
EVALUATE = _policy(
    CapabilityEffect.EVALUATE,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
LOCAL_ARTIFACT = _policy(
    CapabilityEffect.LOCAL_ARTIFACT,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)


class CapabilityNotFoundError(LookupError):
    pass


class CapabilityConfirmationRequiredError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    tool: FastMCPTool
    policy: CapabilityPolicy


@dataclass(frozen=True, slots=True)
class CompactOperationDescriptor:
    """Transport-neutral metadata for one exact compact operation.

    ``tools/list`` deliberately exposes a flattened schema for a few large
    grouped capabilities.  Agent callers need the closed operation variant,
    however, so the registry keeps that exact schema separately from the
    public MCP representation.  ``operation`` is ``None`` for a direct
    capability (for example ``system_health``); callers may use the capability
    name as a convenient direct-operation alias.
    """

    capability: str
    operation: str | None
    description: str
    schema: dict[str, Any]
    policy: CapabilityPolicy
    direct: bool = False

    @property
    def name(self) -> str:
        """Compatibility alias used by transport-neutral clients."""

        return self.capability

    @property
    def capability_name(self) -> str:
        return self.capability

    @property
    def input_schema(self) -> dict[str, Any]:
        return deepcopy(self.schema)

    @property
    def request_schema(self) -> dict[str, Any]:
        return self.input_schema

    @property
    def exact_schema(self) -> dict[str, Any]:
        return self.input_schema

    @property
    def arguments_schema(self) -> dict[str, Any]:
        """Schema for ``tp_read.arguments`` (operation discriminator removed)."""

        schema = self.input_schema
        properties = schema.get("properties")
        if isinstance(properties, dict) and "operation" in properties:
            properties.pop("operation")
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [item for item in required if item != "operation"]
            if not schema["required"]:
                schema.pop("required", None)
        return schema

    @property
    def effect(self) -> CapabilityEffect:
        return self.policy.effect

    @property
    def confirmation_required(self) -> bool:
        return self.policy.confirmation_required

    @property
    def auto_allowed(self) -> bool:
        """Whether Agent-A may execute this read without a pending action."""

        return _agent_operation_allowed(self)

    def as_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-ready descriptor projection."""

        return {
            "capability": self.capability,
            "operation": self.operation,
            "description": self.description,
            "schema": deepcopy(self.schema),
            "exact_schema": deepcopy(self.schema),
            "arguments_schema": self.arguments_schema,
            "effect": self.policy.effect.value,
            "confirmation_required": self.confirmation_required,
            "auto_allowed": self.auto_allowed,
            "direct": self.direct,
        }


@dataclass(frozen=True, slots=True)
class _RegisteredOperation:
    descriptor: CompactOperationDescriptor
    invoke: Callable[[dict[str, Any]], Awaitable[Any]]
    validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None


class CapabilityRegistrar(Protocol):
    def add_capability(
        self,
        fn: Any,
        *,
        name: str | None,
        description: str | None,
        policy: CapabilityPolicy,
        register_direct: bool = True,
    ) -> None: ...

    def register_operation(
        self,
        descriptor: CompactOperationDescriptor,
        invoke: Callable[[dict[str, Any]], Awaitable[Any]],
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None: ...


def _bound_arguments(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    return {key: value for key, value in bound.arguments.items() if key != "self"}


def _mcp_result_wrapper(tool: FastMCPTool) -> Any:
    fn = tool.fn

    @wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        resolved = await result if inspect.isawaitable(result) else result
        return compact_mcp_result(
            resolved,
            capability=tool.name,
            arguments=_bound_arguments(fn, args, kwargs),
        )

    return wrapped


class CompactCapabilityRegistry:
    """One validated compact capability graph shared by MCP and HTTP adapters."""

    def __init__(self) -> None:
        self._capabilities: dict[str, RegisteredCapability] = {}
        self._operations: dict[tuple[str, str | None], _RegisteredOperation] = {}

    def add_capability(
        self,
        fn: Any,
        *,
        name: str | None = None,
        description: str | None = None,
        policy: CapabilityPolicy,
        register_direct: bool = True,
    ) -> None:
        tool = FastMCPTool.from_function(
            fn,
            name=name,
            description=description,
            annotations=policy.annotations,
        )
        if tool.name in self._capabilities:
            raise RuntimeError(f"compact capability already exists: {tool.name}")
        self._capabilities[tool.name] = RegisteredCapability(tool=tool, policy=policy)
        if register_direct:
            self._register_operation(
                CompactOperationDescriptor(
                    capability=tool.name,
                    operation=None,
                    description=tool.description or description or "",
                    schema=_compact_exact_schema(tool.parameters),
                    policy=policy,
                    direct=True,
                ),
                self._direct_invoker(tool),
                self._direct_validator(tool),
            )

    @staticmethod
    def _direct_invoker(tool: FastMCPTool) -> Callable[[dict[str, Any]], Awaitable[Any]]:
        async def invoke(arguments: dict[str, Any]) -> Any:
            # FastMCP's Tool.run performs the same Pydantic argument validation
            # used by the MCP and Console adapters.  Keeping this path intact
            # prevents the Agent transport from growing a second DTO contract.
            return await tool.run(arguments)

        return invoke

    @staticmethod
    def _direct_validator(tool: FastMCPTool) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def validate(arguments: dict[str, Any]) -> dict[str, Any]:
            value = tool.fn_metadata.arg_model.model_validate(dict(arguments))
            return value.model_dump(mode="python")

        return validate

    def _register_operation(
        self,
        descriptor: CompactOperationDescriptor,
        invoke: Callable[[dict[str, Any]], Awaitable[Any]],
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        key = (descriptor.capability, descriptor.operation)
        if key in self._operations:
            raise RuntimeError(f"compact operation already exists: {key[0]}:{key[1]}")
        self._operations[key] = _RegisteredOperation(
            descriptor=descriptor,
            invoke=invoke,
            validate=validate,
        )

    def register_operation(
        self,
        descriptor: CompactOperationDescriptor,
        invoke: Callable[[dict[str, Any]], Awaitable[Any]],
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        """Register one exact operation for transport-neutral callers.

        This method is intentionally separate from :meth:`list_tools`; these
        descriptors never become MCP tools and therefore cannot change the
        public MCP inventory.
        """

        self._register_operation(descriptor, invoke, validate)

    def validate_operation(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate one exact operation without invoking its side effects."""

        normalized_operation = operation
        if normalized_operation in {"", capability}:
            normalized_operation = None
        registered = self._operations.get((capability, normalized_operation))
        if registered is None:
            raise CapabilityNotFoundError(
                capability if normalized_operation is None else f"{capability}:{operation}"
            )
        if registered.validate is None:
            # This fallback is intentionally strict: an operation without a
            # registered validator cannot become a pending write by accident.
            raise ToolError(f"No validation contract for {capability}:{operation or ''}")
        return registered.validate(dict(arguments))

    def operation_descriptors(self) -> tuple[CompactOperationDescriptor, ...]:
        return tuple(item.descriptor for item in self._operations.values())

    @property
    def descriptors(self) -> tuple[CompactOperationDescriptor, ...]:
        return self.operation_descriptors()

    def find_operation(
        self,
        capability: str,
        operation: str | None = None,
    ) -> CompactOperationDescriptor:
        """Find an exact operation without invoking a handler."""

        normalized_operation = operation
        if normalized_operation in {"", capability}:
            normalized_operation = None
        registered = self._operations.get((capability, normalized_operation))
        if registered is None:
            raise CapabilityNotFoundError(
                capability if normalized_operation is None else f"{capability}:{operation}"
            )
        return registered.descriptor

    async def invoke_validated(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        enforce_confirmation: bool = True,
    ) -> Any:
        """Invoke a registered direct/closed operation after exact validation.

        ``invoke`` remains the public MCP/Console-compatible entry point and
        retains its confirmation requirement.  The Agent gateway calls this
        method with ``enforce_confirmation=False`` only after independently
        applying the Agent-A read policy.  No synthetic confirmation is ever
        passed to a handler.
        """

        normalized_operation = operation
        if normalized_operation in {"", capability}:
            normalized_operation = None
        registered = self._operations.get((capability, normalized_operation))
        if registered is None:
            raise CapabilityNotFoundError(
                capability if normalized_operation is None else f"{capability}:{operation}"
            )
        if (
            enforce_confirmation
            and registered.descriptor.policy.confirmation
            is ConfirmationPolicy.MATCH_CAPABILITY_NAME
            and confirmation != capability
        ):
            raise CapabilityConfirmationRequiredError(capability)
        try:
            return await registered.invoke(dict(arguments))
        except (CapabilityNotFoundError, CapabilityConfirmationRequiredError, ToolError):
            raise
        except Exception as error:
            # FastMCP's public ``Tool.run`` uses the same typed boundary. Keep
            # internal Agent invocation behavior aligned without changing the
            # existing MCP/Console ``invoke`` path.
            raise ToolError(f"Error executing tool {capability}: {error}") from error

    async def invoke_operation(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        enforce_confirmation: bool = True,
    ) -> Any:
        """Alias for callers that name the internal path explicitly."""

        return await self.invoke_validated(
            capability,
            operation,
            arguments,
            confirmation=confirmation,
            enforce_confirmation=enforce_confirmation,
        )

    # Internal naming aliases used by non-MCP transports.
    validated_invoke = invoke_validated

    @property
    def policies(self) -> dict[str, CapabilityPolicy]:
        return {name: item.policy for name, item in self._capabilities.items()}

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
                annotations=item.policy.annotations,
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
        capability = self._capabilities.get(name)
        if capability is None:
            raise CapabilityNotFoundError(name)
        if (
            capability.policy.confirmation is ConfirmationPolicy.MATCH_CAPABILITY_NAME
            and confirmation != name
        ):
            raise CapabilityConfirmationRequiredError(name)
        # HTTP callers need the validated handler result before MCP content-block
        # conversion. FastMCP performs that transport conversion only when serving
        # an MCP request.
        result = await capability.tool.run(arguments)
        return compact_mcp_result(result, capability=name, arguments=arguments)

    async def invoke_uncompacted(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> Any:
        """Invoke the same validated capability without MCP transport compaction.

        This path is reserved for trusted local application surfaces such as the
        loopback Console BFF. It preserves the exact FastMCP/Pydantic validation,
        confirmation policy, and handler used by :meth:`invoke`; only the final
        15 KiB MCP transport projection is skipped.
        """

        capability = self._capabilities.get(name)
        if capability is None:
            raise CapabilityNotFoundError(name)
        if (
            capability.policy.confirmation is ConfirmationPolicy.MATCH_CAPABILITY_NAME
            and confirmation != name
        ):
            raise CapabilityConfirmationRequiredError(name)
        return await capability.tool.run(arguments)

    def bind_mcp(self, server: FastMCP) -> None:
        """Render the same registry as FastMCP transport tools."""
        for capability in self._capabilities.values():
            tool = capability.tool
            server.add_tool(
                _mcp_result_wrapper(tool),
                name=tool.name,
                title=tool.title,
                description=tool.description,
                annotations=capability.policy.annotations,
                icons=tool.icons,
                meta=tool.meta,
            )


@dataclass(frozen=True, slots=True)
class VariantSpec:
    operation: str
    adapter: Any
    fields: tuple[str, ...] = ()
    adapter_operation: str | None = None
    overrides: dict[str, object] = field(default_factory=dict)
    extra_fields: dict[str, tuple[object, object]] = field(default_factory=dict)
    adapter_operation_field: str | None = None


def _spec(
    operation: str,
    adapter: Any,
    fields: tuple[str, ...] = (),
    *,
    adapter_operation: str | None = None,
    overrides: dict[str, object] | None = None,
    extra_fields: dict[str, tuple[object, object]] | None = None,
    adapter_operation_field: str | None = None,
) -> VariantSpec:
    return VariantSpec(
        operation=operation,
        adapter=adapter,
        fields=fields,
        adapter_operation=adapter_operation,
        overrides=overrides or {},
        extra_fields=extra_fields or {},
        adapter_operation_field=adapter_operation_field,
    )


@cache
def _adapter_fields(adapter: Any) -> dict[str, Any]:
    hints = get_type_hints(adapter, include_extras=True)
    definitions: dict[str, Any] = {}
    for parameter in inspect.signature(adapter).parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            raise TypeError("variadic compact adapters are unsupported")
        annotation = hints.get(parameter.name, Any)
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        definitions[parameter.name] = (annotation, default)
    model = create_model(
        f"{adapter.__name__}_arguments",
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )
    return cast(dict[str, Any], model.model_fields)


def _variant_model(*, compact_tool_name: str, spec: VariantSpec) -> type[BaseModel]:
    source_fields = _adapter_fields(spec.adapter)
    definitions: dict[str, Any] = {
        "operation": (Literal[spec.operation], ...),
    }
    for name in spec.fields:
        source_field = source_fields.get(name)
        if source_field is None:
            raise RuntimeError(f"compact adapter field is missing: {spec.adapter.__name__}.{name}")
        copied = deepcopy(source_field)
        # The compact tool description and closed operation literals carry routing
        # guidance. Repeating legacy prose in every union variant inflates tools/list
        # without adding validation value; constraints and defaults stay intact.
        copied.title = None
        copied.description = None
        copied.examples = None
        definitions[name] = (copied.annotation, copied)
    definitions.update(spec.extra_fields)
    model_name = "".join(
        part.capitalize() for part in f"{compact_tool_name}_{spec.operation}_request".split("_")
    )
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


async def _invoke_variant(
    spec: VariantSpec,
    exact_model: type[BaseModel],
    arguments: dict[str, Any],
    *,
    capability: str,
) -> Any:
    """Validate and dispatch one closed operation variant.

    Grouped MCP tools validate the flattened request before reaching their
    dispatch closure.  Agent callers invoke this helper directly so they get
    the very same closed DTO and adapter translation without constructing an
    MCP request or inventing a confirmation token.
    """

    payload = dict(arguments)
    if "operation" not in payload:
        payload["operation"] = spec.operation
    try:
        exact_request = exact_model.model_validate(payload)
    except ValidationError as error:
        return tool_input_invalid_envelope(
            tool=capability,
            operation=spec.operation,
            error=error,
        )
    translated = exact_request.model_dump(mode="python")
    translated.pop("operation", None)
    if spec.adapter_operation_field is not None:
        translated["operation"] = translated.pop(spec.adapter_operation_field)
    elif spec.adapter_operation is not None:
        translated["operation"] = spec.adapter_operation
    translated.update(spec.overrides)
    result = spec.adapter(**translated)
    resolved = await result if inspect.isawaitable(result) else result
    return _legacy_subject_transport(resolved)


def _register_dispatch_tool(
    registry: CapabilityRegistrar,
    *,
    name: str,
    description: str,
    variants: tuple[VariantSpec, ...],
    policy: CapabilityPolicy,
) -> None:
    models = tuple(_variant_model(compact_tool_name=name, spec=spec) for spec in variants)
    request_union = reduce(or_, models)
    request_type = Annotated[request_union, Field(discriminator="operation")]  # type: ignore[valid-type]
    by_operation = {spec.operation: spec for spec in variants}
    models_by_operation = dict(zip((spec.operation for spec in variants), models, strict=True))

    async def dispatch(request: Any) -> Any:
        operation = request.operation
        spec = by_operation[operation]
        model = models_by_operation[operation]
        return await _invoke_variant(
            spec, model, request.model_dump(mode="python"), capability=name
        )

    dispatch.__name__ = name
    dispatch.__doc__ = description
    dispatch.__annotations__ = {"request": request_type, "return": Any}
    registry.add_capability(
        dispatch,
        name=name,
        description=description,
        policy=policy,
        register_direct=False,
    )
    for spec, model in zip(variants, models, strict=True):

        async def invoke_variant(
            arguments: dict[str, Any],
            *,
            _spec: VariantSpec = spec,
            _model: type[BaseModel] = model,
        ) -> Any:
            return await _invoke_variant(
                _spec, _model, arguments, capability=name
            )

        def validate_variant(
            arguments: dict[str, Any],
            *,
            _spec: VariantSpec = spec,
            _model: type[BaseModel] = model,
        ) -> dict[str, Any]:
            payload = dict(arguments)
            payload.setdefault("operation", _spec.operation)
            exact_request = _model.model_validate(payload)
            translated = exact_request.model_dump(mode="python")
            translated.pop("operation", None)
            if _spec.adapter_operation_field is not None:
                translated["operation"] = translated.pop(_spec.adapter_operation_field)
            elif _spec.adapter_operation is not None:
                translated["operation"] = _spec.adapter_operation
            translated.update(_spec.overrides)
            return translated

        registry.register_operation(
            CompactOperationDescriptor(
                capability=name,
                operation=spec.operation,
                description=f"{description} Operation: {spec.operation}.",
                schema=_compact_exact_schema(model.model_json_schema()),
                policy=policy,
            ),
            invoke_variant,
            validate_variant,
        )


def _register_flat_dispatch_tool(
    registry: CapabilityRegistrar,
    *,
    name: str,
    description: str,
    variants: tuple[VariantSpec, ...],
    policy: CapabilityPolicy,
) -> None:
    """Publish one small operation schema, then enforce exact variants at runtime.

    This is reserved for large grouped tools whose repeated discriminated branches
    dominate tools/list. Required and operation-owned fields are still validated
    against the same closed variant models before an adapter is invoked.
    """

    operation_type = Literal.__getitem__(tuple(spec.operation for spec in variants))
    definitions: dict[str, Any] = {"operation": (operation_type, ...)}
    candidates: dict[str, list[Any]] = {}
    for spec in variants:
        source_fields = _adapter_fields(spec.adapter)
        for field_name in spec.fields:
            source_field = source_fields.get(field_name)
            if source_field is None:
                raise RuntimeError(
                    f"compact adapter field is missing: {spec.adapter.__name__}.{field_name}"
                )
            candidates.setdefault(field_name, []).append(source_field)
        for field_name, definition in spec.extra_fields.items():
            candidates.setdefault(field_name, []).append(definition)

    for field_name, fields in sorted(candidates.items()):
        if all(hasattr(item, "annotation") for item in fields):
            schemas = {
                json.dumps(
                    cast(Any, item).asdict(),
                    default=str,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for item in fields
            }
            if len(schemas) == 1:
                copied = deepcopy(fields[0])
                copied.default = None
                copied.title = None
                copied.description = None
                copied.examples = None
                definitions[field_name] = (copied.annotation, copied)
                continue
        definitions[field_name] = (Any, None)

    request_model = create_model(
        "".join(part.capitalize() for part in f"{name}_request".split("_")),
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )
    by_operation = {spec.operation: spec for spec in variants}

    async def dispatch(request: Any) -> Any:
        spec = by_operation[request.operation]
        exact_model = _variant_model(compact_tool_name=name, spec=spec)
        return await _invoke_variant(
            spec,
            exact_model,
            request.model_dump(mode="python", exclude_unset=True),
            capability=name,
        )

    dispatch.__name__ = name
    dispatch.__doc__ = description
    dispatch.__annotations__ = {"request": request_model, "return": Any}
    registry.add_capability(
        dispatch,
        name=name,
        description=description,
        policy=policy,
        register_direct=False,
    )
    for spec in variants:
        model = _variant_model(compact_tool_name=name, spec=spec)

        async def invoke_variant(
            arguments: dict[str, Any],
            *,
            _spec: VariantSpec = spec,
            _model: type[BaseModel] = model,
        ) -> Any:
            return await _invoke_variant(
                _spec, _model, arguments, capability=name
            )

        def validate_variant(
            arguments: dict[str, Any],
            *,
            _spec: VariantSpec = spec,
        ) -> dict[str, Any]:
            _model = _variant_model(compact_tool_name=name, spec=_spec)
            payload = dict(arguments)
            payload.setdefault("operation", _spec.operation)
            exact_request = _model.model_validate(payload)
            translated = exact_request.model_dump(mode="python")
            translated.pop("operation", None)
            if _spec.adapter_operation_field is not None:
                translated["operation"] = translated.pop(_spec.adapter_operation_field)
            elif _spec.adapter_operation is not None:
                translated["operation"] = _spec.adapter_operation
            translated.update(_spec.overrides)
            return translated

        registry.register_operation(
            CompactOperationDescriptor(
                capability=name,
                operation=spec.operation,
                description=f"{description} Operation: {spec.operation}.",
                schema=_compact_exact_schema(model.model_json_schema()),
                policy=policy,
            ),
            invoke_variant,
            validate_variant,
        )


def _copy_handler(
    registry: CapabilityRegistrar,
    *,
    adapter: Any,
    target_name: str | None = None,
    policy: CapabilityPolicy,
) -> None:
    @wraps(adapter)
    async def legacy_transport_adapter(*args: Any, **kwargs: Any) -> Any:
        result = adapter(*args, **kwargs)
        resolved = await result if inspect.isawaitable(result) else result
        return _legacy_subject_transport(resolved)

    registry.add_capability(
        legacy_transport_adapter,
        name=target_name or adapter.__name__,
        description=inspect.getdoc(adapter) or "",
        policy=policy,
    )


def _legacy_subject_transport(value: Any) -> Any:
    """Keep legacy ``case_*`` vocabulary at the MCP compatibility boundary.

    Research Subject is the canonical product/domain term. Existing MCP clients
    nevertheless discover and send ``case_id``, ``case_type``, and
    ``linked_case_ids``. Translate only those frozen transport keys; all other
    Research Subject terminology remains canonical.
    """

    if isinstance(value, dict):
        aliases = {
            "subject_id": "case_id",
            "subject_type": "case_type",
            "linked_subject_ids": "linked_case_ids",
        }
        return {
            aliases.get(key, key): _legacy_subject_transport(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_legacy_subject_transport(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_legacy_subject_transport(item) for item in value)
    return value


def _all_fields(adapter: Any, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    return tuple(name for name in _adapter_fields(adapter) if name not in exclude)


def _compact_exact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return one closed operation schema without exposing a public tool union."""

    # The operation descriptor is intentionally transport-neutral.  It still
    # uses the same schema minimizer as ``tools/list`` so callers cannot infer a
    # second, more permissive DTO from generated Pydantic metadata.
    return _minimize_public_schema(deepcopy(schema))


def _agent_operation_allowed(descriptor: CompactOperationDescriptor) -> bool:
    """Agent-A's default operation-level read policy.

    ``technical_render_chart`` is a local artifact operation whose public MCP
    policy remains confirmation-gated for compatibility.  Agent-A may invoke
    it through the explicit internal dispatch because it has no execution
    effect; this exception is deliberately capability-name scoped.
    """

    if descriptor.capability == "technical_render_chart":
        return descriptor.operation is None
    return descriptor.policy.effect in {
        CapabilityEffect.READ_DURABLE,
        CapabilityEffect.READ_PROVIDER,
        CapabilityEffect.CACHE_DISCOVERY,
    }


def _minimize_public_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Render a validation-equivalent, compact JSON Schema 2020-12 contract."""

    def clean(value: Any, *, property_map: bool = False) -> Any:
        if isinstance(value, dict):
            # ``oneOf`` plus each variant's required literal operation already
            # carries the dispatch contract. Pydantic's discriminator ``mapping``
            # repeats those refs and is optional JSON Schema metadata.
            cleaned = {}
            for key, item in value.items():
                # Schema annotations are expendable, but a user field can itself
                # legitimately be named ``title``, ``mapping``, or ``default``.
                if not property_map and key in {"title", "mapping", "default"}:
                    continue
                cleaned[key] = clean(item, property_map=key == "properties")
            # ``const`` and ``enum`` already constrain both value and JSON type.
            # Pydantic emits a redundant string type beside them.
            if cleaned.get("type") == "string" and ("const" in cleaned or "enum" in cleaned):
                cleaned.pop("type")
            # JSON Schema permits nullable scalar types as a type array. Preserve
            # every non-null constraint (format, pattern, bounds) while avoiding
            # Pydantic's longer two-branch ``anyOf`` spelling.
            any_of = cleaned.get("anyOf")
            if isinstance(any_of, list) and len(any_of) == 2 and set(cleaned) == {"anyOf"}:
                null_branch = {"type": "null"}
                non_null = [item for item in any_of if item != null_branch]
                if (
                    len(non_null) == 1
                    and isinstance(non_null[0], dict)
                    and isinstance(non_null[0].get("type"), str)
                ):
                    cleaned = dict(non_null[0])
                    cleaned["type"] = [cleaned["type"], "null"]
                elif (
                    len(non_null) == 1
                    and isinstance(non_null[0], dict)
                    and set(non_null[0]) == {"enum"}
                    and isinstance(non_null[0]["enum"], list)
                ):
                    # Pydantic renders Optional[Enum] as two branches. A single
                    # enum containing JSON null is validation-equivalent and
                    # materially smaller across grouped operation schemas.
                    cleaned = {"enum": [*non_null[0]["enum"], None]}
            return cleaned
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    minimized = cast(dict[str, Any], clean(schema))
    _close_discriminated_request_union(minimized)
    _hoist_common_request_properties(minimized)
    definitions = minimized.get("$defs", {})
    if not definitions:
        return minimized
    aliases = {name: _schema_alias(index) for index, name in enumerate(definitions)}
    minimized["$defs"] = {aliases[name]: value for name, value in definitions.items()}

    def rewrite_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and item.startswith("#/$defs/"):
                    definition = item.rsplit("/", 1)[-1]
                    if definition in aliases:
                        value[key] = f"#/$defs/{aliases[definition]}"
                else:
                    rewrite_refs(item)
        elif isinstance(value, list):
            for item in value:
                rewrite_refs(item)

    rewrite_refs(minimized)
    _share_repeated_property_schemas(minimized)
    _inline_request_variants(minimized)
    minimized = _inline_profitable_definitions(minimized)
    return minimized


def _schema_alias(index: int) -> str:
    """Return the shortest stable base-36 definition name for one schema."""
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if index == 0:
        return alphabet[0]
    parts: list[str] = []
    while index:
        index, remainder = divmod(index, len(alphabet))
        parts.append(alphabet[remainder])
    return "".join(reversed(parts))


def _schema_wire_size(schema: dict[str, Any]) -> int:
    return len(json.dumps(schema, separators=(",", ":")))


def _compact_nullable_unions(value: Any) -> Any:
    """Re-run nullable compaction after a referenced definition is inlined."""

    if isinstance(value, list):
        return [_compact_nullable_unions(item) for item in value]
    if not isinstance(value, dict):
        return value
    compacted = {key: _compact_nullable_unions(item) for key, item in value.items()}
    any_of = compacted.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2 or set(compacted) != {"anyOf"}:
        return compacted
    non_null = [item for item in any_of if item != {"type": "null"}]
    if len(non_null) != 1 or not isinstance(non_null[0], dict):
        return compacted
    branch = non_null[0]
    if isinstance(branch.get("type"), str):
        return {**branch, "type": [branch["type"], "null"]}
    if set(branch) == {"enum"} and isinstance(branch["enum"], list):
        return {"enum": [*branch["enum"], None]}
    return compacted


def _count_exact_refs(value: Any, reference: str) -> int:
    if isinstance(value, list):
        return sum(_count_exact_refs(item, reference) for item in value)
    if not isinstance(value, dict):
        return 0
    if value == {"$ref": reference}:
        return 1
    return sum(_count_exact_refs(item, reference) for item in value.values())


def _replace_exact_refs(value: Any, reference: str, replacement: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_replace_exact_refs(item, reference, replacement) for item in value]
    if not isinstance(value, dict):
        return value
    if value == {"$ref": reference}:
        return deepcopy(replacement)
    return {
        key: _replace_exact_refs(item, reference, replacement)
        for key, item in value.items()
    }


def _inline_profitable_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local definitions only when the complete schema becomes smaller."""

    optimized = cast(dict[str, Any], _compact_nullable_unions(schema))
    while isinstance(optimized.get("$defs"), dict):
        current_size = _schema_wire_size(optimized)
        best: tuple[int, dict[str, Any]] | None = None
        definitions = cast(dict[str, Any], optimized["$defs"])
        for name, definition in definitions.items():
            if not isinstance(definition, dict):
                continue
            reference = f"#/$defs/{name}"
            if _count_exact_refs(definition, reference):
                continue
            if not _count_exact_refs(optimized, reference):
                continue
            candidate = deepcopy(optimized)
            candidate_definitions = cast(dict[str, Any], candidate["$defs"])
            replacement = cast(dict[str, Any], candidate_definitions.pop(name))
            candidate = cast(
                dict[str, Any],
                _replace_exact_refs(candidate, reference, replacement),
            )
            if not candidate_definitions:
                candidate.pop("$defs", None)
            candidate = cast(dict[str, Any], _compact_nullable_unions(candidate))
            saving = current_size - _schema_wire_size(candidate)
            if saving > 0 and (best is None or saving > best[0]):
                best = (saving, candidate)
        if best is None:
            break
        optimized = best[1]
    return optimized


def _close_discriminated_request_union(schema: dict[str, Any]) -> None:
    """Close grouped operation variants once at their request-union boundary."""
    definitions = schema.get("$defs")
    request = schema.get("properties", {}).get("request")
    if not isinstance(definitions, dict) or not isinstance(request, dict):
        return
    variants = request.get("oneOf")
    if not isinstance(variants, list) or len(variants) < 2:
        return
    variant_names: list[str] = []
    for variant in variants:
        if not isinstance(variant, dict) or not isinstance(variant.get("$ref"), str):
            return
        variant_names.append(variant["$ref"].rsplit("/", 1)[-1])
    # The literal operation in each branch is sufficient dispatch metadata; the
    # generated discriminator annotation repeats it without affecting validation.
    request.pop("discriminator", None)
    request["type"] = "object"
    request["required"] = ["operation"]
    # JSON Schema 2020-12 applies this after the successful ``oneOf`` branch.
    # A field owned only by another operation is therefore unevaluated and rejected.
    request["unevaluatedProperties"] = False
    for name in variant_names:
        definition = definitions.get(name)
        if isinstance(definition, dict):
            definition.pop("additionalProperties", None)
            definition.pop("type", None)
            required = [
                field_name
                for field_name in definition.get("required", [])
                if field_name != "operation"
            ]
            if required:
                definition["required"] = required
            else:
                definition.pop("required", None)


def _hoist_common_request_properties(schema: dict[str, Any]) -> None:
    """Publish identical fields shared by every operation only once."""

    definitions = schema.get("$defs")
    request = schema.get("properties", {}).get("request")
    if not isinstance(definitions, dict) or not isinstance(request, dict):
        return
    variants = request.get("oneOf")
    if not isinstance(variants, list) or len(variants) < 2:
        return
    variant_definitions: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict) or not isinstance(variant.get("$ref"), str):
            return
        definition = definitions.get(variant["$ref"].rsplit("/", 1)[-1])
        if not isinstance(definition, dict) or not isinstance(definition.get("properties"), dict):
            return
        variant_definitions.append(definition)

    common_names = set(cast(dict[str, Any], variant_definitions[0]["properties"]))
    for definition in variant_definitions[1:]:
        common_names.intersection_update(cast(dict[str, Any], definition["properties"]))
    common_names.discard("operation")

    shared: dict[str, Any] = {}
    for field_name in sorted(common_names):
        field_schemas = [
            cast(dict[str, Any], definition["properties"])[field_name]
            for definition in variant_definitions
        ]
        encoded = {
            json.dumps(item, sort_keys=True, separators=(",", ":")) for item in field_schemas
        }
        if len(encoded) == 1:
            shared[field_name] = field_schemas[0]
    if not shared:
        return

    request["properties"] = shared
    request["type"] = "object"
    required_by_all = set(variant_definitions[0].get("required", []))
    for definition in variant_definitions[1:]:
        required_by_all.intersection_update(definition.get("required", []))
    shared_required = [name for name in shared if name in required_by_all]
    if shared_required:
        request["required"].extend(shared_required)

    for definition in variant_definitions:
        properties = cast(dict[str, Any], definition["properties"])
        for field_name in shared:
            properties.pop(field_name)
        required = [
            field_name
            for field_name in definition.get("required", [])
            if field_name not in shared_required
        ]
        if required:
            definition["required"] = required
        else:
            definition.pop("required", None)


def _inline_request_variants(schema: dict[str, Any]) -> None:
    """Inline one-use operation branches and discard their reference wrappers."""

    definitions = schema.get("$defs")
    request = schema.get("properties", {}).get("request")
    if not isinstance(definitions, dict) or not isinstance(request, dict):
        return
    variants = request.get("oneOf")
    if not isinstance(variants, list):
        return
    inlined: list[dict[str, Any]] = []
    names: list[str] = []
    for variant in variants:
        if not isinstance(variant, dict) or not isinstance(variant.get("$ref"), str):
            return
        name = variant["$ref"].rsplit("/", 1)[-1]
        definition = definitions.get(name)
        if not isinstance(definition, dict):
            return
        names.append(name)
        inlined.append(definition)
    request["oneOf"] = inlined
    for name in names:
        definitions.pop(name, None)
    if not definitions:
        schema.pop("$defs", None)


def _share_repeated_property_schemas(schema: dict[str, Any]) -> None:
    """Hoist repeated variant property schemas when doing so reduces wire bytes."""
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    occurrences: dict[str, list[tuple[dict[str, Any], str]]] = {}
    values: dict[str, dict[str, Any]] = {}
    for definition in tuple(definitions.values()):
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        for field_name, field_schema in properties.items():
            if field_name == "operation" or not isinstance(field_schema, dict):
                continue
            canonical = json.dumps(field_schema, sort_keys=True, separators=(",", ":"))
            occurrences.setdefault(canonical, []).append((properties, field_name))
            values[canonical] = field_schema

    shared_index = len(definitions)
    for canonical in sorted(occurrences):
        locations = occurrences[canonical]
        if len(locations) < 2:
            continue
        shared_name = _schema_alias(shared_index)
        while shared_name in definitions:
            shared_index += 1
            shared_name = _schema_alias(shared_index)
        reference = {"$ref": f"#/$defs/{shared_name}"}
        reference_size = len(json.dumps(reference, separators=(",", ":")))
        definition_cost = len(shared_name) + len(canonical) + 6
        if len(locations) * len(canonical) <= definition_cost + len(locations) * reference_size:
            continue
        definitions[shared_name] = values[canonical]
        for properties, field_name in locations:
            properties[field_name] = reference.copy()
        shared_index += 1


class CompactFastMCP(FastMCP):
    """FastMCP surface that minimizes schemas only at the public protocol boundary."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema = _minimize_public_schema(tool.inputSchema)
            if isinstance(tool.outputSchema, dict):
                tool.outputSchema = _minimize_public_schema(tool.outputSchema)
        return tools


def create_compact_capability_registry(
    container: ApplicationContainer,
    *,
    chart_persister: Any,
) -> CompactCapabilityRegistry:
    """Build the sole MCP vNext handler/schema/policy registry."""
    from interfaces.mcp.tools.compact_registration_a_share import _register_a_share
    from interfaces.mcp.tools.compact_registration_external_sync import _register_external_sync
    from interfaces.mcp.tools.compact_registration_market_us import _register_market_and_us
    from interfaces.mcp.tools.compact_registration_portfolio_challenge import (
        _register_portfolio_challenge_workflows,
    )
    from interfaces.mcp.tools.compact_registration_watchlist_risk_monitoring import (
        _register_watchlist_risk_monitoring,
    )

    adapters = SimpleNamespace(
        system=build_system_adapters(
            container,
            surface_profile="mcp_vnext_shadow",
            public_tool_count=len(MCP_VNEXT_TOOL_NAMES),
            surface_schema_version="mcp-vnext-shadow-v3",
        ),
        instrument=build_instrument_adapters(container),
        research=build_research_adapters(container),
        research_memory=build_research_memory_adapters(container),
        a_share=build_a_share_adapters(container),
        market=build_market_technical_adapters(container, chart_persister),
        us_research=build_us_research_adapters(container),
        us_context=build_us_context_adapters(container),
        portfolio=build_portfolio_adapters(container),
        challenge=build_challenge_adapters(container),
        execution=build_execution_adapters(container),
        workflows=build_workflow_adapters(container),
        watchlist=build_watchlist_adapters(container),
        risk=build_risk_adapters(container),
        monitoring=build_monitoring_adapters(container),
        view_review=build_view_review_adapters(container),
    )
    registry = CompactCapabilityRegistry()

    _copy_handler(
        registry,
        adapter=adapters.system.system_health,
        policy=READ_DURABLE,
    )
    _copy_handler(
        registry,
        adapter=adapters.instrument.instrument_resolve,
        policy=CACHE_DISCOVERY,
    )
    _copy_handler(registry, adapter=adapters.view_review.view_inbox, policy=READ_DURABLE)
    _copy_handler(
        registry,
        adapter=adapters.view_review.view_review_get,
        policy=READ_DURABLE,
    )
    _copy_handler(
        registry,
        adapter=adapters.view_review.current_view_get,
        policy=READ_DURABLE,
    )

    _register_dispatch_tool(
        registry,
        name="investment_case_read",
        description=(
            "Read durable Research Subjects (标的), build one bounded current research "
            "context, or read the cross-domain durable-only decision inbox. Legacy "
            "transport keeps investment_case_read plus case_id/case_type. "
            "operation=attention is read-only and never reconciles ReviewItems."
        ),
        variants=(
            _spec(
                "query",
                adapters.research.investment_case_query,
                _all_fields(adapters.research.investment_case_query),
            ),
            _spec(
                "context",
                adapters.research.research_context_build,
                _all_fields(adapters.research.research_context_build),
            ),
            _spec(
                "attention",
                adapters.research.attention_read,
                _all_fields(adapters.research.attention_read),
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_flat_dispatch_tool(
        registry,
        name="investment_case_manage",
        description=(
            "Create, update, or archive a durable Research Subject (标的) with confirmation "
            "and idempotency. Legacy transport keeps investment_case_manage plus case_id, "
            "case_type, and linked_case_ids. "
            "Title identifies the research object/question; summary defines research scope. "
            "Action levels, sizing, and entry/exit belong to the Thesis or Trade Plan."
        ),
        variants=(
            _spec(
                "create",
                adapters.research.investment_case_create,
                _all_fields(adapters.research.investment_case_create),
            ),
            _spec(
                "update",
                adapters.research.investment_case_update,
                _all_fields(adapters.research.investment_case_update),
            ),
            _spec(
                "archive",
                adapters.research.investment_case_archive,
                _all_fields(adapters.research.investment_case_archive),
            ),
        ),
        policy=MANAGE,
    )
    _register_dispatch_tool(
        registry,
        name="research_judgment_get",
        description=(
            "Read current research state, one Thesis history, or immutable deterministic "
            "Judgment Scorecard history."
        ),
        variants=(
            _spec(
                "state",
                adapters.research.research_state_get,
                _all_fields(adapters.research.research_state_get),
            ),
            _spec(
                "thesis_history",
                adapters.research.thesis_history_get,
                _all_fields(adapters.research.thesis_history_get),
            ),
            _spec(
                "scorecard_history",
                adapters.research.judgment_scorecard_history,
                _all_fields(adapters.research.judgment_scorecard_history),
            ),
            _spec(
                "challenge_review",
                adapters.challenge.challenge_review_get,
                _all_fields(adapters.challenge.challenge_review_get),
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_flat_dispatch_tool(
        registry,
        name="research_judgment_propose",
        description=(
            "Propose Research state, an Instrument attachment, Trade Plan, or Thesis "
            "changes; never confirm them. A confirmed watchlist_item create attaches "
            "the Instrument directly; do not require Shortlist or Select afterward."
        ),
        variants=(
            _spec(
                "research_state",
                adapters.research.research_state_update,
                _all_fields(adapters.research.research_state_update),
            ),
            _spec(
                "thesis_revision",
                adapters.research.thesis_revision_propose,
                _all_fields(adapters.research.thesis_revision_propose),
            ),
            _spec(
                "challenge_review",
                adapters.challenge.challenge_review_start,
                _all_fields(adapters.challenge.challenge_review_start),
            ),
        ),
        policy=APPEND,
    )
    _register_dispatch_tool(
        registry,
        name="research_judgment_confirm",
        description=(
            "Apply an explicit candidate decision or resolve a non-executing Challenge Review."
        ),
        variants=(
            _spec(
                "candidate",
                adapters.research.thesis_revision_confirm,
                _all_fields(adapters.research.thesis_revision_confirm),
            ),
            _spec(
                "challenge_review",
                adapters.challenge.challenge_review_resolve,
                _all_fields(adapters.challenge.challenge_review_resolve),
            ),
        ),
        policy=APPEND,
    )

    _register_flat_dispatch_tool(
        registry,
        name="research_memory_get",
        description=(
            "Search durable research memory, read one report, restore a Research Subject "
            "(标的) timeline, or read its user-confirmed Catalyst Agenda."
        ),
        variants=(
            _spec(
                "search",
                adapters.research_memory.research_search,
                _all_fields(adapters.research_memory.research_search),
            ),
            _spec(
                "report",
                adapters.research_memory.research_report_get,
                _all_fields(adapters.research_memory.research_report_get),
            ),
            _spec(
                "timeline",
                adapters.research_memory.research_timeline_get,
                _all_fields(adapters.research_memory.research_timeline_get),
            ),
            _spec(
                "agenda",
                adapters.research_memory.catalyst_agenda_get,
                _all_fields(adapters.research_memory.catalyst_agenda_get),
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_flat_dispatch_tool(
        registry,
        name="research_memory_append",
        description=(
            "Append a confirmed Journal, Decision intent, Broker-activity annotation, "
            "or Catalyst Agenda version; never create an order."
        ),
        variants=(
            _spec(
                "journal",
                adapters.research_memory.journal_append,
                _all_fields(adapters.research_memory.journal_append),
            ),
            _spec(
                "decision",
                adapters.research_memory.decision_record_append,
                _all_fields(adapters.research_memory.decision_record_append),
            ),
            _spec(
                "agenda_item",
                adapters.research_memory.catalyst_agenda_manage,
                _all_fields(adapters.research_memory.catalyst_agenda_manage),
            ),
            _spec(
                "activity_annotation",
                adapters.research_memory.activity_annotation_append,
                _all_fields(adapters.research_memory.activity_annotation_append),
            ),
            _spec(
                "trade_cycle_override",
                adapters.research_memory.trade_cycle_override_append,
                _all_fields(adapters.research_memory.trade_cycle_override_append),
            ),
            _spec(
                "behavior_review",
                adapters.research_memory.behavior_review_run,
                _all_fields(adapters.research_memory.behavior_review_run),
            ),
        ),
        policy=APPEND,
    )

    _register_a_share(registry, adapters.a_share)
    _register_market_and_us(
        registry,
        adapters.market,
        adapters.us_research,
        adapters.us_context,
    )

    _register_dispatch_tool(
        registry,
        name="account_get",
        description="Read durable positions or transactions without contacting a broker.",
        variants=(
            _spec(
                "positions",
                adapters.portfolio.account_get,
                ("snapshot_id",),
                adapter_operation="positions",
            ),
            _spec(
                "transactions",
                adapters.portfolio.account_list_transactions,
                _all_fields(adapters.portfolio.account_list_transactions),
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_external_sync(registry, adapters.portfolio, adapters.watchlist)
    _register_dispatch_tool(
        registry,
        name="broker_order_manage",
        description=(
            "Preview an SGOV cash sweep or manage one exact Schwab US equity/ETF order. "
            "Live submit/cancel requires a short-lived durable preview and explicit user "
            "authorization; uncertain writes are never retried automatically."
        ),
        variants=(
            _spec(
                "cash_sweep_preview",
                adapters.execution.cash_sweep_preview,
                _all_fields(adapters.execution.cash_sweep_preview),
            ),
            _spec(
                "preview",
                adapters.execution.order_preview,
                _all_fields(adapters.execution.order_preview),
            ),
            _spec(
                "submit",
                adapters.execution.order_submit,
                _all_fields(adapters.execution.order_submit),
            ),
            _spec(
                "status",
                adapters.execution.order_status,
                _all_fields(adapters.execution.order_status),
            ),
            _spec(
                "cancel",
                adapters.execution.order_cancel,
                _all_fields(adapters.execution.order_cancel),
            ),
        ),
        policy=MANAGE_OPEN_WORLD,
    )
    _register_portfolio_challenge_workflows(
        registry,
        adapters.portfolio,
        adapters.workflows,
    )
    _register_watchlist_risk_monitoring(
        registry,
        adapters.watchlist,
        adapters.risk,
        adapters.monitoring,
    )
    if set(registry.policies) != set(MCP_VNEXT_TOOL_NAMES):
        raise RuntimeError("MCP capability registry does not match vNext inventory")
    return registry


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
    server = CompactFastMCP(container.settings.mcp_server_name)
    registry.bind_mcp(server)
    return server
