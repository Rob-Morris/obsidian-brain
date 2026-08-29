"""Mechanical adapter projection for canonical application command contracts."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
from pathlib import Path
import types
from collections.abc import Mapping as AbcMapping
from typing import ClassVar, Literal, Mapping, Union, get_args, get_origin, get_type_hints

from .requests import CommandRequest
from .results import CommandError, CommandResult, Error, Ok, Partial
from .types import validate_command_id


@dataclass(frozen=True, slots=True)
class ProjectionIdentity:
    """One canonical command identifier projected without aliases."""

    command_id: str
    noun: str
    verb: str
    mcp_tool: str
    cli_argv: tuple[str, str]
    script_argv: tuple[str, str]
    module_path: str

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.cli_argv != (self.noun, self.verb):
            raise ValueError("CLI projection must preserve canonical noun and verb")
        if self.script_argv != self.cli_argv:
            raise ValueError("script and CLI noun/verb projections must agree")


def project_identity(command_id: str) -> ProjectionIdentity:
    """Project ``noun.verb`` mechanically onto every dynamic adapter name."""

    validate_command_id(command_id)
    noun, verb = command_id.split(".", 1)
    projected_noun = noun.replace("-", "_")
    projected_verb = verb.replace("-", "_")
    return ProjectionIdentity(
        command_id=command_id,
        noun=noun,
        verb=verb,
        mcp_tool=command_id,
        cli_argv=(noun, verb),
        script_argv=(noun, verb),
        module_path=f"_application/{projected_noun}/{projected_verb}.py",
    )


def command_id_from_argv(
    noun: str,
    verb: str,
    command_ids: tuple[str, ...],
) -> str:
    """Resolve only the canonical CLI/script spelling owned by a catalogue."""

    command_id = f"{noun}.{verb}"
    validate_command_id(command_id)
    projected = project_identity(command_id)
    if (noun, verb) != projected.cli_argv:
        raise ValueError("command arguments are not in canonical noun/verb form")
    if command_id not in command_ids:
        raise ValueError("command arguments are not owned by this catalogue")
    return command_id


def command_id_from_mcp_tool(tool_name: str, command_ids: tuple[str, ...]) -> str:
    """Resolve an exact canonical MCP name owned by the selected catalogue."""

    validate_command_id(tool_name)
    if tool_name not in command_ids:
        raise ValueError("MCP tool name is not owned by this catalogue")
    return tool_name


def request_schema(request_type: type[CommandRequest]) -> dict[str, object]:
    """Return a strict transport schema derived from one sealed request type.

    Request classes may define ``FIELD_DESCRIPTIONS`` to replace the bounded
    mechanical fallback. The request type remains the field/default authority.
    """

    if not is_dataclass(request_type):
        raise TypeError("application requests must be dataclasses")
    command_id = request_type.COMMAND_ID
    validate_command_id(command_id)
    descriptions = getattr(request_type, "FIELD_DESCRIPTIONS", {})
    if not isinstance(descriptions, Mapping):
        raise TypeError("request FIELD_DESCRIPTIONS must be a mapping")
    hints = get_type_hints(request_type)
    properties: dict[str, object] = {}
    required = []
    for field in fields(request_type):
        if not field.init:
            continue
        annotation = hints.get(field.name, field.type)
        schema = _type_schema(annotation, command_id=command_id, trail=(request_type,))
        description = descriptions.get(field.name)
        if description is None:
            description = _fallback_description(field.name, command_id)
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{command_id} field description must be non-empty: {field.name}")
        schema["description"] = description
        if field.default is not MISSING:
            schema["default"] = _wire_value(field.default)
        properties[field.name] = schema
        if field.default is MISSING and field.default_factory is MISSING:
            required.append(field.name)
    result: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        result["required"] = required
    return result


def result_payload_schema(request_type: type[CommandRequest]) -> dict[str, object]:
    """Project the typed successful payload declared by one request owner."""

    command_id = request_type.COMMAND_ID
    validate_command_id(command_id)
    return _type_schema(
        request_type.RESULT_TYPE,
        command_id=command_id,
        trail=(request_type,),
    )


def minimal_request_payload(request_type: type[CommandRequest]) -> dict[str, object]:
    """Build the smallest schema-valid transport example for discovery."""

    declared = getattr(request_type, "MINIMAL_EXAMPLE", None)
    if declared is not None:
        if not isinstance(declared, Mapping):
            raise TypeError("request MINIMAL_EXAMPLE must be a mapping")
        return _wire_value(declared)
    schema = request_schema(request_type)
    required = schema.get("required", ())
    properties = schema["properties"]
    return {
        name: _example_value(properties[name], field_name=name)
        for name in required
    }


def canonical_result_envelope(result: CommandResult) -> dict[str, object]:
    """Serialise one structural application result for every public adapter."""

    if not isinstance(result, (Ok, Partial, Error)):
        raise TypeError("canonical result projection requires a command result")
    envelope: dict[str, object] = {
        "schema": result.schema,
        "command": result.command_id,
        "command_version": result.command_version,
        "status": result.status,
        "warnings": _wire_value(result.warnings),
    }
    if isinstance(result, Ok):
        envelope["result"] = _wire_value(result.result)
        envelope["committed_effects"] = _wire_value(result.committed_effects)
        return envelope
    if isinstance(result, Partial):
        envelope["result"] = {
            "committed_effects": _wire_value(result.committed_effects),
        }
        envelope["error"] = _error_value(result.error, effects="known")
        return envelope
    envelope["result"] = None
    envelope["error"] = _error_value(
        result.error,
        effects=result.effects,
        retryable=result.retryable,
        outcome_reference=result.outcome_reference,
    )
    return envelope


def canonical_result_json(result: CommandResult) -> str:
    """Return deterministic compact JSON suitable for stdout or structured content."""

    return json.dumps(
        canonical_result_envelope(result),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _error_value(
    error: CommandError,
    *,
    effects: str,
    retryable: bool = False,
    outcome_reference=None,
) -> dict[str, object]:
    value = {
        "code": error.code.value,
        "message": error.message,
        "details": _wire_value(error.details),
        "next_action": _wire_value(error.next_action),
        "effects": effects,
        "retryable": retryable,
    }
    if outcome_reference is not None:
        value["outcome_reference"] = _wire_value(outcome_reference)
    return value


def canonical_wire_value(value):
    """Convert typed request/result values to canonical transport primitives."""

    if isinstance(value, Enum):
        return canonical_wire_value(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("canonical datetimes must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: canonical_wire_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical mappings require string keys")
        return {key: canonical_wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_wire_value(item) for item in value]
    raise TypeError(f"value is not canonically serialisable: {type(value).__name__}")


_wire_value = canonical_wire_value


def _type_schema(annotation, *, command_id: str, trail: tuple[type, ...]) -> dict[str, object]:
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is ClassVar:
        raise TypeError("ClassVar is not a semantic request field")
    if origin is Literal:
        values = [_wire_value(value) for value in arguments]
        kinds = {type(value) for value in values}
        schema: dict[str, object] = {"enum": values}
        if len(kinds) == 1:
            schema["type"] = _primitive_name(next(iter(kinds)))
        return schema
    if origin in {Union, types.UnionType}:
        branches = [
            _type_schema(item, command_id=command_id, trail=trail)
            for item in arguments
        ]
        compact = _compact_distinct_type_union(branches)
        return compact if compact is not None else {"anyOf": branches}
    if origin in {tuple, list}:
        if origin is tuple and len(arguments) == 2 and arguments[1] is Ellipsis:
            item_type = arguments[0]
        elif origin is list and len(arguments) == 1:
            item_type = arguments[0]
        else:
            return {
                "type": "array",
                "prefixItems": [
                    _type_schema(item, command_id=command_id, trail=trail)
                    for item in arguments
                ],
                "minItems": len(arguments),
                "maxItems": len(arguments),
            }
        return {
            "type": "array",
            "items": _type_schema(item_type, command_id=command_id, trail=trail),
        }
    if origin in {dict, Mapping, AbcMapping}:
        key_type, value_type = arguments or (str, object)
        if key_type is not str:
            raise TypeError(f"{command_id} transport mappings require string keys")
        return {
            "type": "object",
            "additionalProperties": _type_schema(
                value_type,
                command_id=command_id,
                trail=trail,
            ),
        }
    if annotation is type(None):
        return {"type": "null"}
    if annotation in {str, int, float, bool}:
        return {"type": _primitive_name(annotation)}
    if annotation is Path:
        return {"type": "string", "format": "path"}
    if annotation in {date, datetime}:
        return {
            "type": "string",
            "format": "date-time" if annotation is datetime else "date",
        }
    if annotation is object:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        values = [_wire_value(item) for item in annotation]
        return {
            "type": _primitive_name(type(values[0])) if values else "string",
            "enum": values,
        }
    if isinstance(annotation, type) and is_dataclass(annotation):
        if annotation in trail:
            raise TypeError(f"recursive request type is not transport-projectable: {annotation.__name__}")
        hints = get_type_hints(annotation)
        descriptions = getattr(annotation, "FIELD_DESCRIPTIONS", {})
        if not isinstance(descriptions, Mapping):
            raise TypeError(f"{annotation.__name__} FIELD_DESCRIPTIONS must be a mapping")
        properties = {}
        required = []
        for field in fields(annotation):
            field_annotation = hints.get(field.name, field.type)
            if not field.init and get_origin(field_annotation) is not Literal:
                continue
            field_schema = _type_schema(
                field_annotation,
                command_id=command_id,
                trail=(*trail, annotation),
            )
            field_schema["description"] = descriptions.get(
                field.name,
                _fallback_description(field.name, command_id),
            )
            properties[field.name] = field_schema
            if (
                not field.init
                or (field.default is MISSING and field.default_factory is MISSING)
            ):
                required.append(field.name)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema
    raise TypeError(f"{command_id} has an unsupported request annotation: {annotation!r}")


def _compact_distinct_type_union(
    branches: list[dict[str, object]],
) -> dict[str, object] | None:
    """Use an equivalent compact schema when union branches have distinct types."""

    types = [branch.get("type") for branch in branches]
    if not types or any(not isinstance(item, str) for item in types):
        return None
    if len(types) != len(set(types)):
        return None
    result: dict[str, object] = {"type": types}
    nullable = "null" in types
    for branch in branches:
        for key, value in branch.items():
            if key == "type":
                continue
            if key in result:
                return None
            if key == "enum" and nullable:
                value = [*value, None]
            result[key] = value
    return result


def _primitive_name(value_type: type) -> str:
    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }[value_type]


def _fallback_description(field_name: str, command_id: str) -> str:
    del command_id
    return field_name.replace("_", " ").capitalize()


def _example_value(schema: Mapping[str, object], *, field_name: str):
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    branches = schema.get("anyOf") or schema.get("oneOf")
    if branches:
        non_null = [branch for branch in branches if branch.get("type") != "null"]
        return _example_value(non_null[0], field_name=field_name)
    value_type = schema.get("type")
    if isinstance(value_type, list):
        selected = next((item for item in value_type if item != "null"), "null")
        return _example_value(
            {**schema, "type": selected},
            field_name=field_name,
        )
    if value_type == "object":
        properties = schema.get("properties", {})
        return {
            name: _example_value(properties[name], field_name=name)
            for name in schema.get("required", ())
        }
    if value_type == "array":
        return []
    if value_type == "boolean":
        return False
    if value_type == "integer":
        return 1
    if value_type == "number":
        return 1.0
    if value_type == "string":
        if field_name in {"content_base64", "content"}:
            return "ZXhhbXBsZQ==" if field_name == "content_base64" else "Example content."
        if "hash" in field_name or "sha256" in field_name:
            return "sha256:" + "0" * 64
        if field_name in {"path", "source", "target"} or field_name.endswith("_path"):
            return "Example.md"
        if field_name in {"type", "type_key", "target_type"}:
            return "living/wiki"
        if field_name == "title":
            return "Example"
        return "example"
    if value_type == "null":
        return None
    raise TypeError(f"cannot derive a minimal example for {field_name}")
