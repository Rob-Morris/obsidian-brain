"""Equivalent JSON Schema compaction for canonical request declarations."""

from collections import Counter
import hashlib
import json


_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"})
_SCHEMA_ARRAYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUES = frozenset({"items", "additionalProperties", "contains", "propertyNames", "not",
                            "if", "then", "else", "unevaluatedProperties", "unevaluatedItems", "contentSchema"})
_ANNOTATIONS = frozenset({"description", "default", "title"})


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _children(node, visit):
    """Visit schema positions, preserving property names and literal data."""
    result = {}
    for key, value in node.items():
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: visit(schema) for name, schema in value.items()}
        elif key in _SCHEMA_ARRAYS and isinstance(value, list):
            result[key] = [visit(schema) for schema in value]
        elif key in _SCHEMA_VALUES and isinstance(value, dict):
            result[key] = visit(value)
        else:
            result[key] = value
    return result


def _walk(schema, transform):
    if not isinstance(schema, dict):
        return schema
    return transform(_children(schema, lambda child: _walk(child, transform)))


def _group_discriminators(node):
    branches = node.get("anyOf")
    if not isinstance(branches, list) or not all(isinstance(branch, dict) for branch in branches):
        return node
    fields = sorted({name for branch in branches for name, prop in branch.get("properties", {}).items()
                     if isinstance(prop, dict) and prop.get("enum")
                     and all(isinstance(value, str) for value in prop["enum"])})
    for field in fields:
        groups = {}
        for index, branch in enumerate(branches):
            prop = branch.get("properties", {}).get(field, {})
            if not prop.get("enum") or not all(isinstance(value, str) for value in prop["enum"]):
                continue
            signature = {**branch, "properties": {**branch["properties"], field: {**prop, "enum": []}}}
            groups.setdefault(_canonical(signature), []).append(index)
        replacements, dropped = {}, set()
        for indices in groups.values():
            if len(indices) < 2:
                continue
            first = branches[indices[0]]
            values = list(dict.fromkeys(value for index in indices
                                       for value in branches[index]["properties"][field]["enum"]))
            replacements[indices[0]] = {**first, "properties": {**first["properties"],
                field: {**first["properties"][field], "enum": values}}}
            dropped.update(indices[1:])
        if dropped:
            return {**node, "anyOf": [replacements.get(index, branch) for index, branch in enumerate(branches)
                                      if index not in dropped]}
    return node


def _simplify(node):
    kinds = node.get("type")
    if isinstance(kinds, list) and "number" in kinds and "integer" in kinds:
        node = {**node, "type": [kind for kind in kinds if kind != "integer"]}
    if node.get("type") == "string" and node.get("enum") and all(isinstance(value, str) for value in node["enum"]):
        node = {key: value for key, value in node.items() if key != "type"}
    return _group_discriminators(node)


def factor_request_schema(schema):
    """Share exact object shapes locally, keeping annotations at each use."""
    counts, shapes = Counter(), {}
    def shape(node):
        kinds = node.get("type")
        if "properties" in node and (kinds == "object" or isinstance(kinds, list) and "object" in kinds):
            return {key: value for key, value in node.items() if key not in _ANNOTATIONS}
        return None
    def count(node):
        candidate = shape(node)
        if candidate is not None:
            key = _canonical(candidate)
            if len(key) > 100:
                counts[key] += 1
                shapes[key] = candidate
        return node
    _walk(schema, count)
    repeated = {key: "shape_" + hashlib.sha256(key.encode()).hexdigest()[:10]
                for key, count in counts.items() if count > 1}
    if not repeated:
        return schema
    if len(set(repeated.values())) != len(repeated):
        raise ValueError("local schema definition name collision")
    def visit(node, skip=None):
        if not isinstance(node, dict):
            return node
        candidate = shape(node)
        key = _canonical(candidate) if candidate is not None else None
        if key in repeated and key != skip:
            return {"$ref": "#/$defs/" + repeated[key],
                    **{name: value for name, value in node.items() if name in _ANNOTATIONS}}
        return _children(node, visit)
    result = visit(schema)
    definitions = {name: visit(shapes[key], skip=key) for key, name in repeated.items()}
    used = set()
    def references(node):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            used.add(reference.removeprefix("#/$defs/"))
        return node
    _walk(result, references)
    previous = None
    while previous != len(used):
        previous = len(used)
        for name in tuple(used):
            if name in definitions:
                _walk(definitions[name], references)
    existing = result.get("$defs", {})
    if set(existing) & set(definitions):
        raise ValueError("generated schema definition collides with existing local definition")
    result["$defs"] = {**existing, **{name: definitions[name] for name in sorted(used) if name in definitions}}
    return result


def compact_request_schema(schema):
    """Preserve acceptance and annotations while removing repeated declaration cost."""
    return factor_request_schema(_walk(schema, _simplify))
