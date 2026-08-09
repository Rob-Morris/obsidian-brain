"""Mechanical adapter projection for canonical application command contracts."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
import types
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
        mcp_tool=f"brain_{projected_noun}_{projected_verb}",
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
    """Resolve an MCP name only when exactly one catalogue command owns it."""

    if not isinstance(tool_name, str) or not tool_name.startswith("brain_"):
        raise ValueError("MCP command tools must use the brain_<noun>_<verb> grammar")
    candidates = [
        command_id
        for command_id in command_ids
        if project_identity(command_id).mcp_tool == tool_name
    ]
    if len(candidates) != 1:
        raise ValueError("MCP tool name is ambiguous without an owning catalogue")
    return candidates[0]


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


def _wire_value(value):
    if isinstance(value, Enum):
        return _wire_value(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _wire_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical mappings require string keys")
        return {key: _wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_value(item) for item in value]
    raise TypeError(f"value is not canonically serialisable: {type(value).__name__}")


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
        return {
            "anyOf": [
                _type_schema(item, command_id=command_id, trail=trail)
                for item in arguments
            ]
        }
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
    if origin in {dict, Mapping}:
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
            if not field.init:
                continue
            field_schema = _type_schema(
                hints.get(field.name, field.type),
                command_id=command_id,
                trail=(*trail, annotation),
            )
            field_schema["description"] = descriptions.get(
                field.name,
                _fallback_description(field.name, command_id),
            )
            properties[field.name] = field_schema
            if field.default is MISSING and field.default_factory is MISSING:
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


def _primitive_name(value_type: type) -> str:
    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }[value_type]


def _fallback_description(field_name: str, command_id: str) -> str:
    return f"{field_name.replace('_', ' ').capitalize()} for {command_id}."
