"""Equivalent schemas preserve native request contracts and declaration annotations."""

from copy import deepcopy
from dataclasses import fields

import pytest
from jsonschema import Draft202012Validator

from _application._schema_compaction import compact_request_schema, factor_request_schema
from _application.projection import _request_schema, request_schema, minimal_request_payload
from _application.registry import current_application_catalogue, current_request_resolver
from schema_assertions import expand_local_schema


CATALOGUE = current_application_catalogue()


def schema_example(schema, *, all_fields=False):
    if 'enum' in schema:
        return deepcopy(schema['enum'][0])
    if 'const' in schema:
        return deepcopy(schema['const'])
    if 'anyOf' in schema:
        return schema_example(next(branch for branch in schema['anyOf'] if branch.get('type') != 'null'),
                              all_fields=all_fields)
    kind = schema.get('type')
    if isinstance(kind, list):
        kind = next((item for item in kind if item != 'null'), 'null')
    if kind == 'object':
        names = schema.get('properties', {}) if all_fields else schema.get('required', ())
        value = {name: schema_example(schema['properties'][name], all_fields=all_fields) for name in names}
        while len(value) < schema.get('minProperties', 0):
            extra = schema.get('additionalProperties', {})
            value['field' + str(len(value))] = schema_example(extra if isinstance(extra, dict) else {}, all_fields=all_fields)
        return value
    if kind == 'array':
        if 'prefixItems' in schema:
            return [schema_example(item, all_fields=all_fields) for item in schema['prefixItems']]
        return [schema_example(schema.get('items', {}), all_fields=all_fields)] * max(schema.get('minItems', 0), int(all_fields))
    if kind == 'string':
        return 'x' * max(1, schema.get('minLength', 0))
    if kind in ('number', 'integer'):
        return max(1, schema.get('minimum', 0))
    if kind == 'boolean':
        return True
    return None


@pytest.mark.parametrize('entry', CATALOGUE.entries, ids=lambda entry: entry.command_id)
def test_every_request_factors_exactly_and_preserves_acceptance(entry):
    before = _request_schema(entry.request_type)
    original = deepcopy(before)
    assert expand_local_schema(factor_request_schema(before)) == before
    after = request_schema(entry.request_type)
    Draft202012Validator.check_schema(after)
    assert before == original
    validators = [Draft202012Validator(schema) for schema in (before, after)]
    for all_fields in (False, True):
        value = schema_example(before, all_fields=all_fields)
        assert all(validator.is_valid(value) for validator in validators), entry.command_id
        assert not any(validator.is_valid({**value, '__unknown__': True}) for validator in validators)
    if before.get('required'):
        value = schema_example(before)
        del value[before['required'][0]]
        assert not any(validator.is_valid(value) for validator in validators)
    payload = minimal_request_payload(entry.request_type)
    assert all(validator.is_valid(payload) for validator in validators)
    assert type(current_request_resolver().resolve(entry.command_id, payload)) is entry.request_type


@pytest.mark.parametrize('value', [None, True, False, 0, 1, 1.0, 1.5, 'a', 'other', [], {}, {'x': 1}])
def test_numeric_and_enum_simplification_preserves_typed_negative_controls(value):
    for schema in ({'type': ['integer', 'number', 'null']}, {'type': 'string', 'enum': ['a', 'b']},
                   {'type': 'integer', 'enum': [1]}, {'type': 'boolean', 'enum': [True]}):
        compact = compact_request_schema(schema)
        assert Draft202012Validator(schema).is_valid(value) == Draft202012Validator(compact).is_valid(value)


def test_refs_preserve_site_annotations_and_literal_keyword_named_data():
    shape = {'type': 'object', 'additionalProperties': False, 'properties': {
        name: {'type': 'string', 'description': 'Literal named property ' + name}
        for name in ('title', 'description', 'default')}, 'required': ['title']}
    literal = {'type': ['number', 'integer'], 'properties': {'title': 'literal'}, '$ref': 'literal-data',
               'description': 'Preserve every literal byte-level value', 'default': {'enum': ['x']}}
    source = {'type': 'object', 'additionalProperties': False, 'properties': {
        'first': {**shape, 'description': 'First site', 'title': 'First title', 'default': literal},
        'second': {**shape, 'description': 'Second site', 'title': 'Second title', 'default': {'title': 'second'}},
    }, 'default': literal}
    compact = factor_request_schema(source)
    assert '$defs' in compact
    assert compact['properties']['first']['description'] == 'First site'
    assert compact['properties']['second']['default'] == {'title': 'second'}
    assert compact['default'] == literal
    assert expand_local_schema(compact) == source
    simplified = expand_local_schema(compact_request_schema(source))
    assert simplified == source


def test_grouping_discriminators_retains_template_frontmatter_refusal():
    entry = next(entry for entry in CATALOGUE.entries if entry.command_id == 'resource.create')
    before, after = (Draft202012Validator(schema) for schema in (_request_schema(entry.request_type), request_schema(entry.request_type)))
    for resource in ('memory', 'skill', 'style', 'template'):
        payload = {'target': {'resource': resource, 'name': 'example', 'frontmatter': {'tags': ['one']}},
                   'content': {'source': 'inline', 'content': 'Example'}}
        expected = resource != 'template'
        assert before.is_valid(payload) is expected
        assert after.is_valid(payload) is expected


def test_only_create_loses_unused_dispositions_while_sixteen_consumers_remain():
    expected = {'artefact.archive', 'artefact.convert', 'artefact.delete', 'artefact.rename', 'artefact.reparent',
        'artefact.reparent-children', 'artefact.set-key', 'artefact.set-naming-field', 'artefact.set-status',
        'artefact.set-workspace', 'artefact.unarchive', 'document.replace-text', 'document.structured-edit',
        'document.update-frontmatter', 'document.write-body', 'shaping.start'}
    assert {entry.command_id for entry in CATALOGUE.entries
            if 'relationship_dispositions' in {field.name for field in fields(entry.request_type)}} == expected
    entry = next(entry for entry in CATALOGUE.entries if entry.command_id == 'artefact.create')
    payload = minimal_request_payload(entry.request_type)
    assert 'relationship_dispositions' not in request_schema(entry.request_type)['properties']
    for value in ([], [{'identity': 'relationship:old', 'expected_revision': 'revision'}]):
        with pytest.raises(ValueError, match='unexpected|Unexpected'):
            current_request_resolver().resolve('artefact.create', {**payload, 'relationship_dispositions': value})


@pytest.mark.parametrize('command', ['relationship.query', 'history.query', 'relationship.definitions'])
def test_graph_cursor_contract_remains_structured(command):
    entry = next(entry for entry in CATALOGUE.entries if entry.command_id == command)
    cursor = expand_local_schema(request_schema(entry.request_type))['properties']['cursor']
    assert 'object' in cursor['type']
    assert set(cursor['properties']) == {'generation', 'offset', 'selectors', 'traversal'}
    assert cursor['required'] == ['generation', 'offset', 'selectors']
