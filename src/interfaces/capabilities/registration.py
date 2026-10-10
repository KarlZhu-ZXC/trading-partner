"""Transport-neutral grouped capability registration helpers."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cache, reduce, wraps
from operator import or_
from typing import Annotated, Any, Literal, cast, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from interfaces.capabilities.policy import CapabilityPolicy
from interfaces.capabilities.registry import (
    CapabilityRegistrar,
    CompactOperationDescriptor,
)
from interfaces.capabilities.schemas import exact_schema
from interfaces.mcp.validation import tool_input_invalid_envelope


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
    if "operation" in fields or "operation" in (extra_fields or {}):
        raise ValueError("operation is reserved for the compact operation discriminator")
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
            return await _invoke_variant(_spec, _model, arguments, capability=name)

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
                schema=exact_schema(model.model_json_schema()),
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
            return await _invoke_variant(_spec, _model, arguments, capability=name)

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
                schema=exact_schema(model.model_json_schema()),
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
