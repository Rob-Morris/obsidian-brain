"""Native regression evidence adapted from the confirmed first Sol review findings."""

import json
import pytest

from test_relationship_commands import graph, ok, create, SUBJECT, VALUE
from _relationships.storage import Store
from _relationships.model import Endpoint, NodeKind, assertion_revision, event_revision
from _application.relationship.batch import RelationshipBatchRequest
from _application.relationship.query import RelationshipQueryRequest
from _application.relationship.check import RelationshipCheckRequest
from _application.relationship.refresh import RelationshipRefreshRequest
from _application.relationship.define import RelationshipDefineRequest
from _application.relationship.read import RelationshipReadRequest, RecordKind
from _application.relationship._types import Action, BatchItem, Cardinality, DefinitionKind
from _application.history.write import HistoryWriteRequest
from _application.history.correct import HistoryCorrectRequest
from _application.history.cleanup import HistoryCleanupRequest
from _application.history.repair import HistoryRepairRequest


@pytest.mark.parametrize('work_budget', [3, 20])
def test_depth_query_continues_roots_and_descendants_across_work_and_byte_caps(graph, work_budget):
    root, app = graph
    items = [BatchItem(Action.CREATE, f'edge-{i}', SUBJECT, 'confidence', VALUE) for i in range(5)]
    items += [BatchItem(Action.CREATE, f'child-{i}', Endpoint(NodeKind.RELATIONSHIP, f'edge-{i}'),
                        'confidence', Endpoint(NodeKind.LITERAL, value='x' * 140000)) for i in range(2)]
    # Large records are separate native batches so the recovery journal also stays bounded.
    ok(app.invoke(RelationshipBatchRequest(tuple(items[:5]))))
    for item in items[5:]:
        ok(app.invoke(RelationshipBatchRequest((item,))))
    cursor, seen, findings = None, [], []
    for _ in range(20):
        page = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False,
            depth=2, work_budget=work_budget, limit=50, cursor=cursor)))
        assert page.visited <= work_budget
        findings.extend(page.findings)
        seen.extend(row['id'] for row in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None and len(seen) == len(set(seen)) == 7
    assert set(seen) == {item.identity for item in items}
    if work_budget == 20:
        assert any('256 KiB' in row['reason'] for row in findings)
    cursor = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False, depth=2, work_budget=3))).next_cursor
    ok(app.invoke(create('new-root')))
    stale = app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False, depth=2, work_budget=3, cursor=cursor))
    assert stale.status == 'error' and 'cursor' in stale.error.message


def test_cross_page_single_slot_conflict_uses_source_without_refresh(graph):
    root, app = graph
    old = ok(app.invoke(RelationshipReadRequest('confidence', RecordKind.PREDICATE))).records[0]
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, 'confidence', 'Confidence',
        'Subject confidence', cardinality=Cardinality.ONE, expected_revision=old['document_revision'])))
    ok(app.invoke(create('a-first')))
    store = Store(root)
    first = store.read('assertion', 'a-first')
    rows = [{**first, 'id': 'm-middle', 'scope': 'separate'}, {**first, 'id': 'z-last'}]
    for row in rows:
        row['revision'] = assertion_revision(row)
    store.write(rows)
    cursor, findings = None, []
    for _ in range(20):
        page = ok(app.invoke(RelationshipCheckRequest(work_budget=16, cursor=cursor)))
        findings.extend(page.findings)
        cursor = page.continuation
        if cursor is None:
            break
    assert cursor is None
    assert any(row['state'] == 'conflicting' for row in findings)


@pytest.mark.parametrize('field,value', [('identity', []), ('reference', []), ('identity', None)])
def test_malformed_nested_endpoint_has_contextual_check_and_refresh_findings(graph, field, value):
    root, app = graph
    ok(app.invoke(create()))
    path = Store(root).path('assertion', 'edge')
    record = json.loads(path.read_text())
    record['subject'][field] = value
    path.write_text(json.dumps(record))
    checked = ok(app.invoke(RelationshipCheckRequest()))
    assert any(row['state'] == 'malformed' and 'assertion:edge' in row['reason'] for row in checked.findings)
    refreshed = ok(app.invoke(RelationshipRefreshRequest()))
    assert not refreshed.complete
    assert any('assertion:edge' in row['reason'] for row in refreshed.records[0]['findings'])


@pytest.mark.parametrize('field,value', [('fields', None), ('fields', [None]), ('kinds', None), ('values', [{}])])
def test_malformed_nested_definition_has_contextual_check_and_refresh_findings(graph, field, value):
    root, app = graph
    path = Store(root).path('predicate', 'confidence')
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    checked = ok(app.invoke(RelationshipCheckRequest()))
    assert any('predicate:confidence' in row['reason'] for row in checked.findings)
    refreshed = ok(app.invoke(RelationshipRefreshRequest()))
    assert not refreshed.complete and refreshed.records[0]['findings']


def test_source_check_discovers_unindexed_correction_fork(graph):
    root, app = graph
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, 'milestone', 'Milestone', 'Caller milestone')))
    event = ok(app.invoke(HistoryWriteRequest('milestone', SUBJECT))).records[0]['event']
    correction = ok(app.invoke(HistoryCorrectRequest(event['id'], event['revision'], 'reason', 'replacement'))).records[0]['event']
    fork = {**correction, 'id': 'external-fork'}
    fork['revision'] = event_revision(fork)
    Store(root).write([fork])
    checked = ok(app.invoke(RelationshipCheckRequest()))
    assert any('fork' in row['reason'] for row in checked.findings)


def test_cleanup_refuses_unfinished_publication_then_repair_and_cleanup_finish(graph, monkeypatch):
    root, app = graph
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, 'milestone', 'Milestone', 'Caller milestone')))
    store = Store(root)
    original = Store.reconcile
    def fail(self, records, removals=()):
        if any(row['kind'] == 'event' for row in records):
            raise OSError('injected projection failure')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'reconcile', fail)
    assert app.invoke(HistoryWriteRequest('milestone', SUBJECT)).status == 'partial'
    monkeypatch.setattr(Store, 'reconcile', original)
    journal = next(store.read('operation', p.stem) for p in (root/'_Relationships/operation').glob('*.json')
                   if store.read('operation', p.stem)['state'] != 'complete')
    event_id = journal['events'][0]['id']
    ok(app.invoke(RelationshipRefreshRequest()))
    refused = app.invoke(HistoryCleanupRequest((event_id,)))
    assert refused.status == 'error' and refused.effects == 'none'
    assert journal['id'] in refused.error.message and 'history.repair' in refused.error.message
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    ok(app.invoke(HistoryCleanupRequest((event_id,))))
    assert store.read('event', event_id)['retired']
    assert set(store.read('operation', journal['id'])) == {'schema', 'kind', 'id', 'state'}
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert store.read('event', event_id)['retired']
    assert not ok(app.invoke(RelationshipCheckRequest())).findings


def test_unclassified_native_commit_failure_remains_unknown_with_repair_route(graph, monkeypatch):
    import edit
    from _application.artefact.set_status import ArtefactSetStatusRequest
    from _application.results import ErrorCode
    root, app = graph
    original = edit.apply_artefact_transition
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('unclassified response failure after native commit')
    monkeypatch.setattr(edit, 'apply_artefact_transition', commit_then_fail)
    failed = app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active'))
    assert failed.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN and failed.effects == 'unknown'
    store = Store(root)
    journal = next(store.read('operation', path.stem) for path in (root/'_Relationships/operation').glob('*.json')
                   if store.read('operation', path.stem)['state'] == 'native-pending')
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert store.read('operation', journal['id'])['state'] == 'complete'


def test_depth_query_broad_roots_and_cycles_do_not_duplicate_or_loop(graph):
    root, app = graph
    ok(app.invoke(RelationshipBatchRequest((
        BatchItem(Action.CREATE, 'cycle-a', Endpoint(NodeKind.RELATIONSHIP, 'cycle-b'), 'confidence', VALUE),
        BatchItem(Action.CREATE, 'cycle-b', Endpoint(NodeKind.RELATIONSHIP, 'cycle-a'), 'confidence', VALUE),
    ))))
    for subject in (None, Endpoint(NodeKind.RELATIONSHIP, 'cycle-a')):
        cursor, seen = None, []
        for _ in range(10):
            page = ok(app.invoke(RelationshipQueryRequest(subject=subject, include_native=False,
                depth=4, work_budget=1, cursor=cursor)))
            seen.extend(row['id'] for row in page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert cursor is None and sorted(seen) == ['cycle-a', 'cycle-b']


def test_cross_page_adjacency_too_large_is_explicitly_unresolved(graph):
    root, app = graph
    old = ok(app.invoke(RelationshipReadRequest('confidence', RecordKind.PREDICATE))).records[0]
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, 'confidence', 'Confidence',
        'Subject confidence', cardinality=Cardinality.ONE, expected_revision=old['document_revision'])))
    ok(app.invoke(create('a-first')))
    store = Store(root)
    original = store.read('assertion', 'a-first')
    copies = [{**original, 'id': 'copy-' + str(index)} for index in range(30)]
    store.write(copies)
    first = ok(app.invoke(RelationshipCheckRequest(work_budget=16)))
    assert not first.complete
    assert any(row['state'] == 'not-evaluated' and 'adjacency' in row['reason'] for row in first.findings)
