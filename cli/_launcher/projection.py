"""Stdlib-only request and result projection for launcher discovery/adapters."""

from __future__ import annotations

from collections.abc import Mapping as AbcMapping
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
from pathlib import Path
import types
from typing import ClassVar, Literal, Mapping, Union, get_args, get_origin, get_type_hints


def request_schema(request_type: type) -> dict[str, object]:
    """Project one typed launcher request as a strict JSON object schema."""

    if not is_dataclass(request_type):
        raise TypeError("launcher requests must be dataclasses")
    properties: dict[str, object] = {}
    required = []
    hints = get_type_hints(request_type)
    for field in fields(request_type):
        if not field.init:
            continue
        schema = _type_schema(hints.get(field.name, field.type), trail=(request_type,))
        schema["description"] = _description(field.name)
        if "example" in field.metadata:
            schema["examples"] = [_wire_value(field.metadata["example"])]
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


def result_schema(result_type: type) -> dict[str, object]:
    """Project the successful payload type owned by one launcher request."""

    return _type_schema(result_type, trail=())


def minimal_request_payload(request_type: type) -> dict[str, object]:
    """Build and validate the smallest launcher discovery example."""

    schema = request_schema(request_type)
    payload = {
        name: _example_value(schema["properties"][name], field_name=name)
        for name in schema.get("required", ())
    }
    payload.update({name: value["examples"][0] for name, value in schema["properties"].items()
                    if "examples" in value})
    resolve_request(request_type, payload)
    return payload


def resolve_request(request_type: type, payload: Mapping[str, object]):
    """Resolve a strict dynamic launcher payload into its sealed request type."""

    if not isinstance(payload, Mapping):
        raise ValueError("launcher request payload must be an object")
    declared_fields = {field.name: field for field in fields(request_type) if field.init}
    unknown = sorted(set(payload) - set(declared_fields))
    if unknown:
        raise ValueError(f"launcher request contains unknown fields: {', '.join(unknown)}")
    hints = get_type_hints(request_type)
    values = {
        name: _decode_value(value, hints.get(name, declared_fields[name].type))
        for name, value in payload.items()
    }
    try:
        return request_type(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"launcher request is invalid: {exc}") from exc


def canonical_wire_value(value):
    """Convert launcher request/result metadata into JSON-compatible values."""

    return _wire_value(value)


def _type_schema(annotation, *, trail: tuple[type, ...]) -> dict[str, object]:
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is ClassVar:
        raise TypeError("ClassVar is not a launcher transport field")
    if origin is Literal:
        values = [_wire_value(value) for value in arguments]
        schema: dict[str, object] = {"enum": values}
        kinds = {type(value) for value in values}
        if len(kinds) == 1:
            schema["type"] = _primitive_name(next(iter(kinds)))
        return schema
    if origin in {Union, types.UnionType}:
        branches = [_type_schema(item, trail=trail) for item in arguments]
        compact = _compact_distinct_type_union(branches)
        return compact if compact is not None else {"anyOf": branches}
    if origin in {tuple, list}:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            item_type = arguments[0]
            return {"type": "array", "items": _type_schema(item_type, trail=trail)}
        if origin is list and len(arguments) == 1:
            return {"type": "array", "items": _type_schema(arguments[0], trail=trail)}
        return {
            "type": "array",
            "prefixItems": [_type_schema(item, trail=trail) for item in arguments],
            "minItems": len(arguments),
            "maxItems": len(arguments),
        }
    if origin in {dict, Mapping, AbcMapping}:
        key_type, value_type = arguments or (str, object)
        if key_type is not str:
            raise TypeError("launcher transport mappings require string keys")
        return {
            "type": "object",
            "additionalProperties": _type_schema(value_type, trail=trail),
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
            raise TypeError(f"recursive launcher type is not projectable: {annotation.__name__}")
        hints = get_type_hints(annotation)
        properties = {}
        required = []
        for field in fields(annotation):
            if not field.init:
                continue
            schema = _type_schema(
                hints.get(field.name, field.type),
                trail=(*trail, annotation),
            )
            schema["description"] = _description(field.name)
            if field.default is not MISSING:
                schema["default"] = _wire_value(field.default)
            properties[field.name] = schema
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
    raise TypeError(f"unsupported launcher transport annotation: {annotation!r}")


def _decode_value(value, annotation):
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Literal:
        if value not in arguments:
            raise ValueError(f"expected one of {arguments!r}")
        return value
    if origin in {Union, types.UnionType}:
        if value is None and type(None) in arguments:
            return None
        matches = []
        for branch in arguments:
            if branch is type(None):
                continue
            try:
                matches.append(_decode_value(value, branch))
            except ValueError:
                continue
        if len(matches) != 1:
            raise ValueError("value does not match exactly one launcher request variant")
        return matches[0]
    if origin in {tuple, list}:
        if not isinstance(value, list):
            raise ValueError("expected an array")
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            decoded = [_decode_value(item, arguments[0]) for item in value]
        elif origin is list and len(arguments) == 1:
            decoded = [_decode_value(item, arguments[0]) for item in value]
        else:
            if len(value) != len(arguments):
                raise ValueError("fixed launcher array has the wrong length")
            decoded = [
                _decode_value(item, item_type)
                for item, item_type in zip(value, arguments, strict=True)
            ]
        return tuple(decoded) if origin is tuple else decoded
    if annotation is Path:
        if not isinstance(value, str):
            raise ValueError("expected a path string")
        return Path(value)
    if annotation is str:
        if not isinstance(value, str):
            raise ValueError("expected a string")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if annotation is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("expected an integer")
        return value
    if annotation is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("expected a number")
        return float(value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {annotation.__name__}") from exc
    if isinstance(annotation, type) and is_dataclass(annotation):
        return resolve_request(annotation, value)
    if annotation is object:
        return value
    raise ValueError(f"unsupported launcher request annotation: {annotation!r}")


def _compact_distinct_type_union(
    branches: list[dict[str, object]],
) -> dict[str, object] | None:
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


def _example_value(schema: Mapping[str, object], *, field_name: str):
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    branches = schema.get("anyOf") or schema.get("oneOf")
    if branches:
        return _example_value(branches[0], field_name=field_name)
    value_type = schema.get("type")
    if isinstance(value_type, list):
        selected = next(item for item in value_type if item != "null")
        return _example_value({**schema, "type": selected}, field_name=field_name)
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
    if value_type == "null":
        return None
    if schema.get("format") == "path" or field_name.endswith(("path", "root")):
        return "/tmp/brain"
    if field_name.endswith("brain_id") or field_name == "brain_id":
        return "example-brain"
    return "example"


def _wire_value(value):
    if isinstance(value, Enum):
        return _wire_value(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _wire_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"launcher value is not serialisable: {type(value).__name__}")


def _description(field_name: str) -> str:
    return field_name.replace("_", " ").capitalize()


def _primitive_name(value_type: type) -> str:
    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }[value_type]


def canonical_json(value) -> str:
    """Encode launcher metadata deterministically for CLI projection."""

    return json.dumps(_wire_value(value), ensure_ascii=False, separators=(",", ":"))
