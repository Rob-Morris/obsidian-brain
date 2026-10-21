"""Logical relationship identities are independent of portable physical locators."""

import json
import re

import pytest

from test_relationship_commands import graph

from _relationships.checks import inspect
from _relationships.definitions import BUILTINS
from _relationships.model import validate_definition
from _relationships.storage import KINDS, MAX_RECORD_BYTES, Store
from _relationships.upgrade import catalogue_conflicts


PORTABLE_IDS = ('CON', 'NUL', 'aux.contract', 'edge.', 'edge', 'Edge')


def predicate(identity):
    value = {'kind': 'predicate', 'id': identity, 'name': identity,
             'meaning': 'Portable identity', 'cardinality': 'many'}
    return {**value, 'contract_revision': validate_definition(value)}


def test_portable_identities_survive_source_and_index_lifecycle(tmp_path):
    store = Store(tmp_path)
    records = [predicate(identity) for identity in PORTABLE_IDS]
    store.write(records)
    paths = [store.path('predicate', identity) for identity in PORTABLE_IDS]
    assert len({str(path).casefold() for path in paths}) == len(records)
    assert all(re.fullmatch(r'[0-9a-f]{64}\.json', path.name) for path in paths)
    for identity, path in zip(PORTABLE_IDS, paths):
        assert json.loads(path.read_bytes())['id'] == identity
        assert store.read('predicate', identity)['id'] == identity
        assert store.read_path(path)['id'] == identity
    cursor, visited = None, 0
    while True:
        page = store.refresh(budget=2, cursor=cursor)
        assert not page['findings'] and page['visited'] <= 2
        visited += page['visited']
        cursor = page['cursor']
        if cursor is None:
            assert page['complete']
            break
    assert visited == len(records)
    rows = store.rows(kind='predicate')
    assert {store.checked(row)['id'] for row in rows} == set(PORTABLE_IDS)
    assert store.source_freshness(kind='predicate')[0]
    checked = inspect(tmp_path, {}, identities=tuple('predicate:' + identity for identity in PORTABLE_IDS))
    assert checked['complete'] and not checked['findings']
    assert catalogue_conflicts(tmp_path) == []
    for identity, path in zip(PORTABLE_IDS, paths):
        store.write([], [('predicate', identity)])
        store.reconcile([], [('predicate', identity)])
        assert not path.exists() and store.read('predicate', identity) is None
    assert not store.rows(kind='predicate')
    assert store.refresh()['complete']


@pytest.mark.parametrize('kind', KINDS)
def test_every_record_family_uses_portable_case_sensitive_locators(tmp_path, kind):
    store = Store(tmp_path)
    paths = [store.path(kind, identity) for identity in PORTABLE_IDS]
    assert len({str(path).casefold() for path in paths}) == len(PORTABLE_IDS)
    assert all(re.fullmatch(r'[0-9a-f]{64}\.json', path.name) for path in paths)


def test_scans_reject_mislocated_envelopes_without_inventing_identity(tmp_path):
    store = Store(tmp_path)
    store.write([predicate('CON'), predicate('NUL')])
    path = store.path('predicate', 'CON')
    path.write_bytes(store.path('predicate', 'NUL').read_bytes())
    with pytest.raises(ValueError, match='storage locator'):
        store.read_path(path)
    assert not store.refresh()['complete']
    findings = inspect(tmp_path, {})['findings']
    assert any(item.get('identity') == 'NUL' and 'locator' in item.get('reason', '') for item in findings)
    path.write_text('{broken')
    findings = inspect(tmp_path, {})['findings']
    assert any(item.get('path') == str(path.relative_to(tmp_path)) and item['identity'] is None for item in findings)


def test_integrity_diagnostics_reject_oversized_nested_source_before_parsing(tmp_path):
    store = Store(tmp_path)
    path = store.path('assertion', 'oversized')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'[' * MAX_RECORD_BYTES + b'0' + b']' * MAX_RECORD_BYTES)

    findings = inspect(tmp_path, {})['findings']

    assert any(item.get('path') == str(path.relative_to(tmp_path))
               and item.get('identity') is None and item['state'] == 'malformed'
               and '256 KiB' in item['reason'] for item in findings)


def test_catalogue_conflicts_use_envelope_identity_and_preserve_bytes(tmp_path):
    store = Store(tmp_path)
    local = {**BUILTINS['native-transition'], 'meaning': 'Conflicting local meaning'}
    local['contract_revision'] = validate_definition(local)
    case_distinct = {**BUILTINS['native-transition'], 'id': 'Native-transition'}
    case_distinct['contract_revision'] = validate_definition(case_distinct)
    store.write([local, case_distinct, *[predicate(identity) for identity in PORTABLE_IDS]])
    before = {path: path.read_bytes() for path in store.source_page(budget=100)}
    warnings = catalogue_conflicts(tmp_path)
    assert len(warnings) == 1
    assert warnings[0]['target'] == str(store.path('event-type', 'native-transition').relative_to(tmp_path))
    assert 'local/core event contract conflict: native-transition' in warnings[0]['reason']
    assert {path: path.read_bytes() for path in before} == before


def test_portable_assertion_commands_and_scoped_adjacency(graph, monkeypatch):
    from pathlib import Path
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship.check import RelationshipCheckRequest
    from _application.relationship.define import RelationshipDefineRequest
    from _application.relationship.read import RelationshipReadRequest, RecordKind
    from _application.relationship.refresh import RelationshipRefreshRequest
    from _application.relationship._types import Action, BatchItem, DefinitionKind
    from test_relationship_commands import create, ok, SUBJECT, VALUE

    root, app = graph
    store = Store(root)
    for identity in PORTABLE_IDS:
        ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, identity,
            identity, 'Portable native definition')))
        assert ok(app.invoke(RelationshipReadRequest(identity, RecordKind.PREDICATE))).records[0]['id'] == identity
        ok(app.invoke(create(identity)))
        assert ok(app.invoke(RelationshipReadRequest(identity))).records[0]['id'] == identity
    ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, 'definition-use', SUBJECT, 'NUL', VALUE),))))
    assert ok(app.invoke(RelationshipRefreshRequest())).complete
    assert not ok(app.invoke(RelationshipCheckRequest())).findings
    removed = ok(app.invoke(RelationshipReadRequest('CON'))).records[0]
    ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.REMOVE, 'CON',
        expected_revision=removed['revision']),))))
    assert store.read('assertion', 'CON')['retired']
    assert not store.read('assertion', 'NUL').get('retired')

    # An external constraint change makes all remaining occupants conflict.
    definition = store.read('predicate', 'confidence')
    definition['cardinality'] = 'one'
    definition['contract_revision'] = validate_definition(definition)
    store.write([definition])
    selected_path = store.path('assertion', 'edge')
    reads = []
    original = Path.read_bytes

    def read_bytes(path):
        if path == selected_path:
            reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    checked = ok(app.invoke(RelationshipCheckRequest(identities=('assertion:edge',), work_budget=30)))
    assert checked.complete and checked.visited <= 30
    assert {item['identity'] for item in checked.findings if item['state'] == 'conflicting'} == set(PORTABLE_IDS) - {'CON'}
    # One opening read and one integrity recheck; adjacency reuses the selected source.
    assert len(reads) == 2


def test_source_reader_checks_containment_before_reading(tmp_path, monkeypatch):
    from pathlib import Path

    store = Store(tmp_path / 'brain')

    def unexpected_read(path):
        raise AssertionError('outside source must not be read')

    monkeypatch.setattr(Path, 'read_bytes', unexpected_read)
    with pytest.raises(ValueError, match='escapes the selected Brain'):
        store.read_path(tmp_path / 'outside.json')
