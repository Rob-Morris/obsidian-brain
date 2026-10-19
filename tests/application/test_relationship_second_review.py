"""Native entry paths and source-preserving recovery from the second design review."""

import pytest
from test_relationship_commands import graph, ok, SUBJECT, VALUE
from test_relationship_compatibility import assessed
from _application.relationship.define import RelationshipDefineRequest
from _application.relationship.undefine import RelationshipUndefineRequest
from _application.relationship.read import RelationshipReadRequest, RecordKind
from _application.relationship.check import RelationshipCheckRequest
from _application.relationship._types import DefinitionKind, Cardinality
from _application.history.repair import HistoryRepairRequest
from _relationships.storage import Store
from _relationships.model import Endpoint, NodeKind


def test_assessed_predicate_constraint_change(assessed):
    root, app, policy = assessed
    old = ok(app.invoke(RelationshipReadRequest('confidence', RecordKind.PREDICATE))).records[0]
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, 'confidence', old['name'], old['meaning'],
        cardinality=Cardinality.ONE, expected_revision=old['document_revision'])))
    checked = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    assert checked.records[0]['state'] != 'satisfied'


def test_repair_removal_projection(graph, monkeypatch):
    root, app = graph
    store = Store(root)
    old = ok(app.invoke(RelationshipReadRequest('confidence', RecordKind.PREDICATE))).records[0]
    original = Store.reconcile
    def fail(self, records, removals=()):
        if removals:
            raise OSError('injected removal reconciliation failure')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'reconcile', fail)
    failed = app.invoke(RelationshipUndefineRequest(DefinitionKind.PREDICATE, 'confidence', old['document_revision']))
    assert failed.status == 'partial', failed
    monkeypatch.setattr(Store, 'reconcile', original)
    journal = next(store.read('operation', p.stem) for p in (root/'_Relationships/operation').glob('*.json') if store.read('operation', p.stem)['state'] != 'complete')
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert store.rows(kind='predicate', id='confidence') == []


def test_shaping_transition_disposition_is_applied(graph):
    from _application.relationship.expose import RelationshipExposeRequest
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship._types import Action, BatchItem, Disposition
    from _application.shaping.start import ShapingStartRequest, ShapingMode
    root, app = graph
    store = Store(root)
    occurrence = next(row for row in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records if row['predicate'] == 'native.status')
    annotation = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, 'annotation',
        Endpoint(NodeKind.OCCURRENCE, occurrence['id']), 'confidence', VALUE),)))).records[0]
    result = app.invoke(ShapingStartRequest(SUBJECT.reference, ShapingMode.REFINE,
        relationship_dispositions=(Disposition('relationship:annotation', annotation['revision']),)))
    assert result.status == 'ok', result
    assert store.read('assertion', 'annotation')['retired']
    assert store.read('occurrence', occurrence['id'])['retired']
    events = [store.read('event', row['id']) for row in store.rows(kind='event')]
    assert {row['type'] for row in events} == {'native-transition', 'relationship-change'}
    assert len(events) == 2
    assert next(row for row in events if row['type'] == 'native-transition')['payload']['command'] == 'shaping.start'
    assert not ok(app.invoke(RelationshipCheckRequest())).findings


def test_repair_captured_event_follows_rekey(graph, monkeypatch):
    from _application.artefact.set_status import ArtefactSetStatusRequest
    from _application.artefact.set_key import ArtefactSetKeyRequest
    from _application.relationship.refresh import RelationshipRefreshRequest
    from _relationships.model import Endpoint, NodeKind
    root, app = graph
    store, original = Store(root), Store.write
    def fail(self, records, removals=()):
        if any(row['kind'] == 'event' for row in records):
            raise OSError('event publication failed')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'write', fail)
    assert app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active')).status == 'partial'
    journal = next(store.read('operation', p.stem) for p in (root/'_Relationships/operation').glob('*.json') if store.read('operation', p.stem)['state'] == 'state-committed')
    monkeypatch.setattr(Store, 'write', original)
    ok(app.invoke(RelationshipRefreshRequest()))
    ok(app.invoke(ArtefactSetKeyRequest(SUBJECT.reference, 'renamed-design')))
    native_before = {path: path.read_bytes() for path in root.rglob('*.md')}
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert native_before == {path: path.read_bytes() for path in root.rglob('*.md')}
    event = store.read('event', journal['events'][0]['id'])
    assert event['subject']['reference'] == 'design/renamed-design'
    assert event['subject']['identity'] == journal['events'][0]['subject']['identity']
    assert event['revision'] == journal['events'][0]['revision']
    assert event['payload'] == journal['events'][0]['payload']
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert store.read('event', event['id']) == event


def test_naming_migration_updates_binding_and_metadata(graph):
    import compile_router
    from command_vault import load_command_vault_manifest
    from _application.artefact.migrate_naming import ArtefactMigrateNamingRequest
    root, app = graph
    path = load_command_vault_manifest()['stable_artefacts']['owned_plan']['path']
    canonical = root/path
    legacy = canonical.with_name('20260809-plan--renamed-legacy-plan.md')
    canonical.rename(legacy)
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    subject = Endpoint(NodeKind.ARTEFACT, legacy.relative_to(root).as_posix())
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship._types import Action, BatchItem
    original = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, 'temporal', subject, 'confidence', VALUE),)))).records[0]
    original = Store(root).read('assertion', 'temporal')
    result = app.invoke(ArtefactMigrateNamingRequest())
    record = Store(root).read('assertion', 'temporal')
    assert result.status == 'ok', result
    assert (root/record['subject']['reference']).exists()
    assert record['subject']['reference'] != subject.reference
    assert record['subject']['identity'] == original['subject']['identity']
    assert record['revision'] == original['revision']
    assert not ok(app.invoke(RelationshipCheckRequest())).findings


def test_repair_removed_definition_preserves_later_recreation(graph, monkeypatch):
    from _application.relationship.refresh import RelationshipRefreshRequest
    root, app = graph
    store = Store(root)
    old = ok(app.invoke(RelationshipReadRequest('confidence', RecordKind.PREDICATE))).records[0]
    original = Store.reconcile
    def fail(self, records, removals=()):
        if removals:
            raise OSError('injected removal reconciliation failure')
        return original(self, records, removals)
    monkeypatch.setattr(Store, 'reconcile', fail)
    failed = app.invoke(RelationshipUndefineRequest(DefinitionKind.PREDICATE, 'confidence', old['document_revision']))
    assert failed.status == 'partial', failed
    journal = next(store.read('operation', p.stem) for p in (root/'_Relationships/operation').glob('*.json')
                   if store.read('operation', p.stem)['state'] == 'projection-pending')
    monkeypatch.setattr(Store, 'reconcile', original)
    ok(app.invoke(RelationshipRefreshRequest()))
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, 'confidence', 'New Confidence', 'Deliberately recreated contract')))
    path = store.path('predicate', 'confidence')
    before = path.read_bytes()
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert path.read_bytes() == before
    row, = store.rows(kind='predicate', id='confidence')
    assert store.checked(row)['meaning'] == 'Deliberately recreated contract'
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert path.read_bytes() == before


def test_assessment_requires_explicit_assessed_predicate_contract_pin(assessed):
    import json
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship._types import Action, BatchItem
    root, app, policy = assessed
    pin = Store(root).read('assertion', 'definition-pins')
    contracts = json.loads(pin['object']['value'])
    del contracts['confidence']
    ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.REPLACE, 'definition-pins',
        subject=Endpoint(NodeKind.RELATIONSHIP, 'claim'), predicate=pin['predicate'], scope=pin['scope'], object=Endpoint(NodeKind.LITERAL, value=json.dumps(contracts)), expected_revision=pin['revision']),))))
    result = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    assert result.records[0]['state'] == 'unknown'
    assert result.records[0]['claims'][0]['reason'] == 'semantic definition contract revisions changed'


def legacy_temporal(graph):
    import compile_router
    from command_vault import load_command_vault_manifest
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship._types import Action, BatchItem
    root, app = graph
    canonical = root / load_command_vault_manifest()['stable_artefacts']['owned_plan']['path']
    legacy = canonical.with_name('20260809-plan--renamed-legacy-plan.md')
    canonical.rename(legacy)
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    subject = Endpoint(NodeKind.ARTEFACT, legacy.relative_to(root).as_posix())
    edge = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, 'temporal', subject, 'confidence', VALUE),)))).records[0]
    return legacy, Store(root).read('assertion', 'temporal')


@pytest.mark.parametrize('owner', ['shaping', 'naming'])
@pytest.mark.parametrize('phase', ['before-semantic-state', 'projection'])
def test_native_executor_publication_partial_repairs_without_native_replay(graph, monkeypatch, owner, phase):
    from _application.shaping.start import ShapingStartRequest, ShapingMode
    from _application.artefact.migrate_naming import ArtefactMigrateNamingRequest
    from _common import parse_frontmatter
    from command_vault import load_command_vault_manifest
    root, app = graph
    if owner == 'shaping':
        request = ShapingStartRequest(SUBJECT.reference, ShapingMode.REFINE)
    else:
        legacy, edge = legacy_temporal(graph)
        request = ArtefactMigrateNamingRequest()
    method = 'publish' if phase == 'before-semantic-state' else 'reconcile'
    original = getattr(Store, method)
    def fail(*args, **kwargs):
        raise OSError('injected semantic publication failure')
    monkeypatch.setattr(Store, method, fail)
    result = app.invoke(request)
    assert result.status == 'partial', result
    assert 'history.repair' in result.error.message
    operation, = [effect.subject for effect in result.committed_effects if effect.kind == 'semantic.recovery']
    store = Store(root)
    journal = store.read('operation', operation)
    assert journal['state'] == ('native-pending' if phase == 'before-semantic-state' else 'projection-pending')
    if owner == 'shaping':
        path = root / load_command_vault_manifest()['stable_artefacts']['owned_design']['path']
        assert parse_frontmatter(path.read_text())[0]['status'] == 'shaping'
    else:
        assert not legacy.exists()
    native_before = {path: path.read_bytes() for path in root.rglob('*.md')}
    monkeypatch.setattr(Store, method, original)
    ok(app.invoke(HistoryRepairRequest(operation)))
    assert native_before == {path: path.read_bytes() for path in root.rglob('*.md')}
    assert store.read('operation', operation)['state'] == 'complete'
    events = [store.read('event', row['id']) for row in store.rows(kind='event')]
    assert len(events) == len(journal['events'])
    ok(app.invoke(HistoryRepairRequest(operation)))
    assert [store.read('event', row['id']) for row in store.rows(kind='event')] == events
    if owner == 'naming':
        record = store.read('assertion', 'temporal')
        assert (root / record['subject']['reference']).exists()
        assert record['revision'] == edge['revision']
    assert not ok(app.invoke(RelationshipCheckRequest())).findings


@pytest.mark.parametrize('owner', ['shaping', 'naming'])
@pytest.mark.parametrize('failure', ['classified', 'unexpected'])
def test_native_executor_post_commit_failure_keeps_honest_outcome_and_recovery(graph, monkeypatch, owner, failure):
    import start_shaping_session
    import migrate_naming
    import rename
    from _common import PartialApplyError
    from _application.results import ErrorCode
    from _application.shaping.start import ShapingStartRequest, ShapingMode
    from _application.artefact.migrate_naming import ArtefactMigrateNamingRequest
    root, app = graph
    if owner == 'shaping':
        request = ShapingStartRequest(SUBJECT.reference, ShapingMode.REFINE)
        module = start_shaping_session
        method = '_add_transcript_link' if failure == 'classified' else 'start_shaping_session'
    else:
        legacy_temporal(graph)
        request = ArtefactMigrateNamingRequest()
        module = rename if failure == 'classified' else migrate_naming
        method = 'apply_move_and_links' if failure == 'classified' else 'apply_naming_migration'
    original = getattr(module, method)
    def fail_after_commit(*args, **kwargs):
        original(*args, **kwargs)
        error = PartialApplyError if failure == 'classified' else OSError
        raise error('injected native post-commit failure')
    monkeypatch.setattr(module, method, fail_after_commit)
    result = app.invoke(request)
    if failure == 'classified':
        assert result.status == 'partial', result
        assert 'history.repair' in result.error.message
        assert any(effect.kind == 'semantic.recovery' for effect in result.committed_effects)
    else:
        assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN, result
        assert result.effects == 'unknown'
    store = Store(root)
    journal, = [store.read('operation', path.stem) for path in (root/'_Relationships/operation').glob('*.json')
                if store.read('operation', path.stem)['state'] == 'native-pending']
    native_before = {path: path.read_bytes() for path in root.rglob('*.md')}
    monkeypatch.setattr(module, method, original)
    ok(app.invoke(HistoryRepairRequest(journal['id'])))
    assert native_before == {path: path.read_bytes() for path in root.rglob('*.md')}
    assert store.read('operation', journal['id'])['state'] == 'complete'
