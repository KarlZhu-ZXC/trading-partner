"""Transport-neutral capability metadata, validation, and invocation.

This module owns the one canonical function-signature validation path shared by
local and remote interface adapters.  It deliberately has no dependency on any
transport SDK.  Adapters are responsible for rendering tool metadata, result
compaction, and protocol-specific error types.
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Protocol, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, WithJsonSchema, create_model

from application.ports.capability_usage import CapabilityUsageStore

from .policy import CapabilityEffect, CapabilityPolicy, ConfirmationPolicy
from .schemas import exact_schema
from .usage import CapabilityUsageMeter


class CapabilityNotFoundError(LookupError):
    """Raised when a capability or exact operation is not registered."""


class CapabilityConfirmationRequiredError(PermissionError):
    """Raised when a confirmation-gated capability lacks exact authorization."""


class CapabilityInvocationError(RuntimeError):
    """Closed invocation error independent of the underlying transport."""

    def __init__(self, capability: str, cause: BaseException) -> None:
        self.capability = capability
        self.cause = cause
        super().__init__(f"Error executing tool {capability}: {cause}")


class _FunctionArguments(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def model_dump_one_level(self) -> dict[str, Any]:
        """Return validated arguments using declared aliases at the call boundary."""

        values: dict[str, Any] = {}
        for field_name, field_info in type(self).model_fields.items():
            output_name = field_info.alias or field_name
            values[output_name] = getattr(self, field_name)
        return values


@dataclass(frozen=True, slots=True)
class FunctionMetadata:
    """Pydantic argument model and callable metadata for one registered function."""

    arg_model: type[_FunctionArguments]
    is_async: bool
    output_schema: dict[str, Any] | None

    def pre_parse_json(self, data: dict[str, Any]) -> dict[str, Any]:
        """Match the existing bounded JSON-in-string compatibility behavior."""

        parsed = data.copy()
        key_to_field: dict[str, Any] = {}
        for name, field_info in self.arg_model.model_fields.items():
            key_to_field[name] = field_info
            if field_info.alias:
                key_to_field[field_info.alias] = field_info
        for key, value in data.items():
            candidate_field = key_to_field.get(key)
            if (
                candidate_field is None
                or not isinstance(value, str)
                or candidate_field.annotation is str
            ):
                continue
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, str | int | float):
                continue
            parsed[key] = decoded
        return parsed

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        model = self.arg_model.model_validate(self.pre_parse_json(dict(arguments)))
        return model.model_dump_one_level()

    async def call(self, fn: Callable[..., Any], arguments: dict[str, Any]) -> Any:
        values = self.validate(arguments)
        if self.is_async:
            return await fn(**values)
        return fn(**values)


def _is_async_callable(value: Any) -> bool:
    while isinstance(value, functools.partial):
        value = value.func
    return inspect.iscoroutinefunction(value) or (
        callable(value) and inspect.iscoroutinefunction(value.__call__)
    )


def _function_metadata(fn: Callable[..., Any]) -> FunctionMetadata:
    try:
        signature = inspect.signature(fn, eval_str=True)
    except (TypeError, ValueError, NameError) as error:
        raise TypeError(f"Unable to evaluate type annotations for {fn!r}") from error

    definitions: dict[str, Any] = {}
    for parameter in signature.parameters.values():
        if parameter.name.startswith("_"):
            raise TypeError(f"Parameter {parameter.name} of {fn.__name__} cannot start with '_'")
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            raise TypeError("Variadic capability arguments are unsupported")
        parameter_annotation: Any = parameter.annotation
        metadata: list[Any] = []
        if parameter_annotation is inspect.Parameter.empty:
            parameter_annotation = Any
            metadata.append(WithJsonSchema({"title": parameter.name, "type": "string"}))
        field_name = parameter.name
        field_kwargs: dict[str, Any] = {}
        if hasattr(BaseModel, field_name) and callable(getattr(BaseModel, field_name)):
            field_kwargs["alias"] = field_name
            field_name = f"field_{field_name}"
        annotated = Annotated[(parameter_annotation, *metadata, Field(**field_kwargs))]
        default = parameter.default if parameter.default is not inspect.Parameter.empty else ...
        definitions[field_name] = (annotated, default)

    model: type[_FunctionArguments] = create_model(
        f"{fn.__name__}Arguments",
        __base__=_FunctionArguments,
        **definitions,
    )

    output_schema = _output_schema(fn.__name__, signature.return_annotation)
    return FunctionMetadata(
        arg_model=model,
        is_async=_is_async_callable(fn),
        output_schema=output_schema,
    )


def _output_schema(function_name: str, annotation: Any) -> dict[str, Any] | None:
    """Build the same structured-output shape used by the original adapter.

    Direct string-keyed mappings can be returned as objects.  Other generic
    return annotations are wrapped in a ``result`` field so structured output
    remains an object at the protocol boundary.
    """

    if annotation is inspect.Parameter.empty:
        return None
    try:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is dict and len(args) == 2 and args[0] is str:
            schema = TypeAdapter(annotation).json_schema()
            if isinstance(schema, dict):
                schema = dict(schema)
                schema["title"] = f"{function_name}DictOutput"
                return schema
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation.model_json_schema()
        wrapped = create_model(
            f"{function_name}Output",
            result=(annotation, ...),
        )
        return wrapped.model_json_schema()
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """Transport-neutral description and callable for one public capability."""

    fn: Callable[..., Any]
    name: str
    title: str | None
    description: str
    parameters: dict[str, Any]
    fn_metadata: FunctionMetadata
    icons: list[Any] | None = None
    meta: dict[str, Any] | None = None

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> RegisteredTool:
        tool_name = name or getattr(fn, "__name__", "")
        if not tool_name or tool_name == "<lambda>":
            raise ValueError("A named callable is required for a capability")
        metadata = _function_metadata(fn)
        return cls(
            fn=fn,
            name=tool_name,
            title=title,
            description=description or fn.__doc__ or "",
            parameters=metadata.arg_model.model_json_schema(by_alias=True),
            fn_metadata=metadata,
            icons=icons,
            meta=meta,
        )

    @property
    def output_schema(self) -> dict[str, Any] | None:
        return self.fn_metadata.output_schema

    async def run(self, arguments: dict[str, Any]) -> Any:
        try:
            return await self.fn_metadata.call(self.fn, arguments)
        except CapabilityInvocationError:
            raise
        except Exception as error:
            raise CapabilityInvocationError(self.name, error) from error


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    tool: RegisteredTool
    policy: CapabilityPolicy


@dataclass(frozen=True, slots=True)
class CompactOperationDescriptor:
    """Exact schema and policy metadata for one direct or grouped operation."""

    capability: str
    operation: str | None
    description: str
    schema: dict[str, Any]
    policy: CapabilityPolicy
    direct: bool = False

    @property
    def name(self) -> str:
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
        schema = self.input_schema
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("operation", None)
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
        if self.capability == "technical_render_chart":
            return self.operation is None
        return self.policy.effect in {
            CapabilityEffect.READ_DURABLE,
            CapabilityEffect.READ_PROVIDER,
            CapabilityEffect.CACHE_DISCOVERY,
        }

    def as_dict(self) -> dict[str, Any]:
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


class CompactCapabilityRegistry:
    """One canonical capability graph independent of a transport protocol."""

    def __init__(
        self,
        usage_store: CapabilityUsageStore | None = None,
        *,
        source: str = "INTERNAL",
    ) -> None:
        self._usage = CapabilityUsageMeter(usage_store, source=source)
        self._capabilities: dict[str, RegisteredCapability] = {}
        self._operations: dict[tuple[str, str | None], _RegisteredOperation] = {}

    @property
    def usage_recording_failures(self) -> int:
        return self._usage.recording_failures

    async def observe_invocation(
        self,
        capability: str,
        arguments: dict[str, Any],
        invoke: Callable[[], Awaitable[Any]],
        *,
        operation: str | None = None,
    ) -> Any:
        """Meter canonical identities only; unknown user strings never reach storage."""
        known_name = capability if capability in self._capabilities else "__unknown__"
        selected: object = operation
        if selected is None:
            request = arguments.get("request")
            if isinstance(request, str) and len(request) <= 1_000_000:
                try:
                    request = json.loads(request)
                except (ValueError, TypeError):
                    request = None
            if isinstance(request, Mapping):
                selected = request.get("operation")
            elif isinstance(request, BaseModel):
                selected = getattr(request, "operation", None)
        normalized = (
            self._operation_key(capability, selected)[1] if isinstance(selected, str) else None
        )
        if isinstance(selected, str) and (capability, normalized) in self._operations:
            known_operation = normalized or "__direct__"
        elif selected is None and (capability, None) in self._operations:
            known_operation = "__direct__"
        else:
            known_operation = "__invalid__"
        return await self._usage.call(known_name, known_operation, invoke)

    def _schema_for_tool(self, tool: RegisteredTool) -> dict[str, Any]:
        return exact_schema(tool.parameters)

    def add_capability(
        self,
        fn: Any,
        *,
        name: str | None = None,
        description: str | None = None,
        policy: CapabilityPolicy,
        register_direct: bool = True,
    ) -> None:
        tool = RegisteredTool.from_function(fn, name=name, description=description)
        if tool.name in self._capabilities:
            raise RuntimeError(f"compact capability already exists: {tool.name}")
        self._capabilities[tool.name] = RegisteredCapability(tool=tool, policy=policy)
        if register_direct:
            self._register_operation(
                CompactOperationDescriptor(
                    capability=tool.name,
                    operation=None,
                    description=tool.description or description or "",
                    schema=self._schema_for_tool(tool),
                    policy=policy,
                    direct=True,
                ),
                self._direct_invoker(tool),
                self._direct_validator(tool),
            )

    @staticmethod
    def _direct_invoker(tool: RegisteredTool) -> Callable[[dict[str, Any]], Awaitable[Any]]:
        async def invoke(arguments: dict[str, Any]) -> Any:
            return await tool.run(arguments)

        return invoke

    @staticmethod
    def _direct_validator(tool: RegisteredTool) -> Callable[[dict[str, Any]], dict[str, Any]]:
        return tool.fn_metadata.validate

    def _register_operation(
        self,
        descriptor: CompactOperationDescriptor,
        invoke: Callable[[dict[str, Any]], Awaitable[Any]],
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        key = (descriptor.capability, descriptor.operation)
        if key in self._operations:
            raise RuntimeError(f"compact operation already exists: {key[0]}:{key[1]}")
        self._operations[key] = _RegisteredOperation(descriptor, invoke, validate)

    def register_operation(
        self,
        descriptor: CompactOperationDescriptor,
        invoke: Callable[[dict[str, Any]], Awaitable[Any]],
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._register_operation(descriptor, invoke, validate)

    @staticmethod
    def _operation_key(capability: str, operation: str | None) -> tuple[str, str | None]:
        normalized = None if operation in {"", capability} else operation
        return capability, normalized

    def _registered_operation(
        self,
        capability: str,
        operation: str | None,
    ) -> _RegisteredOperation:
        key = self._operation_key(capability, operation)
        registered = self._operations.get(key)
        if registered is None:
            suffix = key[0] if key[1] is None else f"{key[0]}:{operation}"
            raise CapabilityNotFoundError(suffix)
        return registered

    def validate_operation(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        registered = self._registered_operation(capability, operation)
        if registered.validate is None:
            raise TypeError(f"No validation contract for {capability}:{operation or ''}")
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
        return self._registered_operation(capability, operation).descriptor

    async def invoke_validated(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        enforce_confirmation: bool = True,
    ) -> Any:
        return await self.observe_invocation(
            capability,
            arguments,
            lambda: self._invoke_validated(
                capability,
                operation,
                arguments,
                confirmation=confirmation,
                enforce_confirmation=enforce_confirmation,
            ),
            operation=operation,
        )

    async def _invoke_validated(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        enforce_confirmation: bool = True,
    ) -> Any:
        registered = self._registered_operation(capability, operation)
        if (
            enforce_confirmation
            and registered.descriptor.policy.confirmation
            is ConfirmationPolicy.MATCH_CAPABILITY_NAME
            and confirmation != capability
        ):
            raise CapabilityConfirmationRequiredError(capability)
        try:
            return await registered.invoke(dict(arguments))
        except (
            CapabilityNotFoundError,
            CapabilityConfirmationRequiredError,
            CapabilityInvocationError,
        ):
            raise
        except Exception as error:
            raise CapabilityInvocationError(capability, error) from error

    async def invoke_operation(
        self,
        capability: str,
        operation: str | None,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
        enforce_confirmation: bool = True,
    ) -> Any:
        return await self.invoke_validated(
            capability,
            operation,
            arguments,
            confirmation=confirmation,
            enforce_confirmation=enforce_confirmation,
        )

    validated_invoke = invoke_validated

    @property
    def policies(self) -> dict[str, CapabilityPolicy]:
        return {name: item.policy for name, item in self._capabilities.items()}

    def capabilities(self) -> tuple[RegisteredCapability, ...]:
        """Return transport-neutral registrations in deterministic order."""

        return tuple(self._capabilities.values())

    @property
    def registered_capabilities(self) -> tuple[RegisteredCapability, ...]:
        """Compatibility/readability alias for interface adapters."""

        return self.capabilities()

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> Any:
        return await self.invoke_uncompacted(name, arguments, confirmation=confirmation)

    async def invoke_uncompacted(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> Any:
        return await self.observe_invocation(
            name,
            arguments,
            lambda: self._invoke_uncompacted(name, arguments, confirmation=confirmation),
        )

    async def _invoke_uncompacted(
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
        return await capability.tool.run(dict(arguments))
