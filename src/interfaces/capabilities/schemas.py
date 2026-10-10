"""Canonical exact JSON Schema normalization for capability contracts.

The normalizer keeps local references resolvable and preserves validation while
removing only redundant schema annotations and wire-size overhead.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast


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
    return {key: _replace_exact_refs(item, reference, replacement) for key, item in value.items()}


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



def exact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return one defensive copy of the canonical exact operation schema."""

    return _minimize_public_schema(deepcopy(schema))


def minimize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Public neutral alias for the canonical schema normalizer."""

    return _minimize_public_schema(schema)


__all__ = ["exact_schema", "minimize_schema"]
