"""Observed publication failures, head races and retained correction failures."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from command_application import application_for
from _application.history.correct import HistoryCorrectRequest
from _application.history.read import HistoryReadRequest
from _application.history.repair import HistoryRepairRequest
from _application.history.write import HistoryWriteRequest
from _application.relationship._types import DefinitionKind
from _application.relationship.define import RelationshipDefineRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _relationships.storage import Store
from _relationships.model import event_revision
from test_relationship_commands import graph, ok, SUBJECT, create


@pytest.fixture
def event(graph):
    root, app = graph
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, 'milestone', 'Milestone', 'Caller milestone')))
    event = ok(app.invoke(HistoryWriteRequest('milestone', SUBJECT))).records[0]['event']
    return root, app, event


def test_two_corrections_cannot_share_one_admitted_head(event):
    root, app, event = event
    def writer(reason):
        return application_for(root).invoke(HistoryCorrectRequest(event['id'], event['revision'], reason, 'replacement'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, ('first', 'second')))
    assert sorted(row.status for row in results) == ['error', 'ok']
    assert ok(app.invoke(HistoryReadRequest(event['id']))).records[0]['interpretation']['state'] == 'corrected'


@pytest.mark.parametrize('shape', ['fork', 'cycle'])
def test_imported_correction_ambiguity_cannot_revive_original(event, shape):
    root, app, event = event
    store = Store(root)
    correction = ok(app.invoke(HistoryCorrectRequest(event['id'], event['revision'], 'reason', 'replacement'))).records[0]['event']
    if shape == 'fork':
        changed = {**correction, 'id': 'imported-fork'}
    else:
        changed = {**event, 'corrects': correction['id']}
    changed['revision'] = event_revision(changed)
    store.write([changed])
    store.refresh()
    interpreted = ok(app.invoke(HistoryReadRequest(event['id']))).records[0]['interpretation']
    assert interpreted['state'] == 'unresolved' and shape in interpreted['reason']


def test_rolled_back_state_journal_is_not_repaired_as_completed(graph, monkeypatch):
    root, app = graph
    original = Store.write
    def fail_state(self, records, removals=()):
        if any(row['kind'] == 'assertion' for row in records):
            raise OSError('injected pre-state rollback')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'write', fail_state)
    failed = app.invoke(create())
    assert failed.status == 'partial', failed
    assert Store(root).read('assertion', 'edge') is None
    journal = next(Store(root).read('operation', p.stem) for p in (root/'_Relationships/operation').glob('*.json') if Store(root).read('operation', p.stem)['state'] == 'pending')
    monkeypatch.setattr(Store, 'write', original)
    repaired = app.invoke(HistoryRepairRequest(journal['id']))
    assert repaired.status == 'error' and repaired.effects == 'none', repaired
    assert Store(root).read('operation', journal['id'])['state'] == 'pending'
    resolved = ok(app.invoke(HistoryRepairRequest(journal['id'], abandon=True, reason='State transaction rolled back; verified unchanged')))
    assert resolved.records[0]['state'] == 'abandoned'
    receipt = Store(root).read('operation', journal['id'])
    assert set(receipt) == {'schema', 'kind', 'id', 'state', 'resolution_reason'}
    assert Store(root).read('assertion', 'edge') is None
    assert app.invoke(HistoryRepairRequest(journal['id'])).status == 'error'
    from _application.relationship.check import RelationshipCheckRequest
    assert not ok(app.invoke(RelationshipCheckRequest())).findings


def test_index_failure_reports_partial_then_reconciles_without_duplicate_event(graph, monkeypatch):
    root, app = graph
    original = Store.reconcile
    once = True
    def fail(self, records, removals=()):
        nonlocal once
        if once and any(row['kind'] == 'event' for row in records):
            once = False
            raise OSError('injected index publication failure')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'reconcile', fail)
    result = app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active'))
    assert result.status == 'partial', result
    journal = next(Store(root).read('operation', p.stem) for p in (root/'_Relationships/operation').glob('*.json') if Store(root).read('operation', p.stem)['state'] == 'projection-pending')
    events_before = list((root/'_Relationships/event').glob('*.json'))
    assert len(events_before) == 1
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert list((root/'_Relationships/event').glob('*.json')) == events_before
    assert Store(root).generation()[1] is False


def test_custom_typed_payload_reference_follows_rekey_without_event_revision_change(graph):
    from _application.relationship._types import PayloadField, PayloadValue
    from _application.artefact.set_key import ArtefactSetKeyRequest
    from _relationships.model import Endpoint, NodeKind
    root, app = graph
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, 'evidence-used', 'Evidence used', 'Caller evidence reference',
        payload_fields=(PayloadField('evidence', 'reference', True),))))
    event = ok(app.invoke(HistoryWriteRequest('evidence-used', SUBJECT,
        payload=(PayloadValue('evidence', Endpoint(NodeKind.ARTEFACT, 'project/command-fixture')),)))).records[0]['event']
    ok(app.invoke(ArtefactSetKeyRequest('project/command-fixture', 'renamed-evidence')))
    current = ok(app.invoke(HistoryReadRequest(event['id']))).records[0]
    assert current['payload']['evidence']['reference'] == 'project/renamed-evidence'
    assert current['revision'] == event['revision']


def test_abandon_refuses_native_committed_state_after_event_failure(graph, monkeypatch):
    root, app = graph
    original = Store.write
    def fail(self, records, removals=()):
        if any(row['kind'] == 'event' for row in records):
            raise OSError('event publication failed')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'write', fail)
    assert app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active')).status == 'partial'
    journal = next(Store(root).read('operation', path.stem) for path in (root/'_Relationships/operation').glob('*.json')
                   if Store(root).read('operation', path.stem)['state'] != 'complete')
    monkeypatch.setattr(Store, 'write', original)
    refused = app.invoke(HistoryRepairRequest(journal['id'], abandon=True, reason='Cannot erase observed effects'))
    assert refused.status == 'error' and refused.effects == 'none'
    assert 'state changed' in refused.error.message
    ok(app.invoke(HistoryRepairRequest(journal['id'])))


def test_repair_captured_committed_event_after_newer_native_state_does_not_replay(graph, monkeypatch):
    from _application.relationship.refresh import RelationshipRefreshRequest
    from _common import parse_frontmatter
    from command_vault import load_command_vault_manifest
    root, app = graph
    original = Store.write
    def fail(self, records, removals=()):
        if any(row['kind'] == 'event' for row in records):
            raise OSError('event publication failed')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'write', fail)
    assert app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active')).status == 'partial'
    journal = next(Store(root).read('operation', path.stem) for path in (root/'_Relationships/operation').glob('*.json')
                   if Store(root).read('operation', path.stem)['state'] == 'state-committed')
    monkeypatch.setattr(Store, 'write', original)
    ok(app.invoke(RelationshipRefreshRequest()))
    ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'ready')))
    path = root/load_command_vault_manifest()['stable_artefacts']['owned_design']['path']
    before = path.read_bytes()
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert path.read_bytes() == before and parse_frontmatter(path.read_text())[0]['status'] == 'ready'
    assert len(Store(root).rows(kind='event')) == 2
    recovered = ok(app.invoke(HistoryReadRequest(journal['events'][0]['id']))).records[0]
    assert '"status":"active"' in recovered['payload']['after']


@pytest.mark.parametrize('fail', [False, True])
def test_real_sqlite_connections_close_after_success_and_exception(tmp_path, monkeypatch, fail):
    import sqlite3
    import _relationships.storage as storage
    original = sqlite3.connect
    opened = []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(storage.sqlite3, 'connect', connect)
    store = Store(tmp_path)
    if fail:
        with pytest.raises(ValueError, match='injected'):
            with store.connection() as connection:
                connection.execute('SELECT 1')
                raise ValueError('injected transaction exception')
    else:
        store.initialise()
        store.generation()
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_terminal_journals_drop_captures_and_typed_cleanup_cannot_replay_payload(graph):
    from _application.history.cleanup import HistoryCleanupRequest
    from _application.relationship._types import PayloadField, PayloadValue
    root, app = graph
    store = Store(root)
    secret = 'payload-selected-for-explicit-cleanup'
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, 'private-note', 'Private note', 'Caller note',
        payload_fields=(PayloadField('detail', 'string', True),))))
    event = ok(app.invoke(HistoryWriteRequest('private-note', SUBJECT, (PayloadValue('detail', secret),)))).records[0]['event']
    operation_ids = [row['id'] for row in store.rows(kind='operation')]
    ok(app.invoke(HistoryCleanupRequest((event['id'],))))
    for row in store.rows(kind='operation'):
        receipt = store.read('operation', row['id'])
        assert set(receipt) == {'schema', 'kind', 'id', 'state'}
        assert receipt['state'] == 'complete'
    assert all(secret not in path.read_text() for path in (root/'_Relationships').rglob('*.json'))
    before = {path: path.read_bytes() for path in (root/'_Relationships').rglob('*.json')}
    for operation in operation_ids:
        assert ok(app.invoke(HistoryRepairRequest(operation))).records[0]['repaired_events'] == 0
    assert {path: path.read_bytes() for path in before} == before
    assert store.read('event', event['id'])['retired']
