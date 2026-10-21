"""Expand request-local references for independent schema contract assertions."""

from copy import deepcopy


def expand_local_schema(schema):
    definitions = schema.get('$defs', {})
    maps = {'properties', '$defs', 'definitions', 'patternProperties', 'dependentSchemas'}
    arrays = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    values = {'items', 'additionalProperties', 'contains', 'propertyNames', 'not', 'if', 'then', 'else',
              'unevaluatedProperties', 'unevaluatedItems', 'contentSchema'}
    def expand(node, pending=()):
        if not isinstance(node, dict):
            return deepcopy(node)
        if '$ref' in node:
            reference = node['$ref']
            assert reference.startswith('#/$defs/') and reference not in pending
            base = expand(definitions[reference.removeprefix('#/$defs/')], (*pending, reference))
            node = {**base, **{key: value for key, value in node.items() if key != '$ref'}}
        result = {}
        for key, value in node.items():
            if key == '$defs':
                continue
            if key in maps and isinstance(value, dict):
                result[key] = {name: expand(child, pending) for name, child in value.items()}
            elif key in arrays and isinstance(value, list):
                result[key] = [expand(child, pending) for child in value]
            elif key in values and isinstance(value, dict):
                result[key] = expand(value, pending)
            else:
                result[key] = deepcopy(value)
        return result
    return expand(schema)
