"""A source inventory beyond one page remains repairable through native commands."""

from _application.relationship.refresh import RelationshipRefreshRequest
from _application.relationship.check import RelationshipCheckRequest
from _application.relationship.query import RelationshipQueryRequest
from _relationships.storage import Store
from test_relationship_commands import graph, ok, create, SUBJECT


def test_missing_projection_larger_than_max_page_rebuilds_and_scoped_checks_finish(graph):
    root, app = graph
    store = Store(root)
    # Historical tombstones remain shared data and count towards inventory work.
    store.write([{'kind': 'event', 'id': 'retired-' + str(index), 'retired': True, 'revision': 'historical'} for index in range(5001)])
    store.index.unlink()
    first = ok(app.invoke(RelationshipRefreshRequest(work_budget=1000)))
    assert not first.complete and first.records[0]['cursor']
    refused = app.invoke(create())
    assert refused.status == 'error' and refused.effects == 'none'
    from _application.artefact.set_status import ArtefactSetStatusRequest
    refused_native = app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active'))
    assert refused_native.status == 'error' and refused_native.effects == 'none'
    from _application.relationship.define import RelationshipDefineRequest
    from _application.relationship.expose import RelationshipExposeRequest
    from _application.relationship._types import DefinitionKind
    from _application.history.write import HistoryWriteRequest
    from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
    from _application.document._types import DocumentLocator, DocumentResource
    from _application._mutation_support import InlineContent
    from _common import document_revision_at
    from command_vault import load_command_vault_manifest
    document_path = root/load_command_vault_manifest()['stable_artefacts']['owned_design']['path']
    for request in (
        RelationshipDefineRequest(DefinitionKind.PREDICATE, 'blocked', 'Blocked', 'Blocked during refresh'),
        RelationshipExposeRequest(SUBJECT), HistoryWriteRequest('native-transition', SUBJECT),
        DocumentWriteBodyRequest(DocumentLocator(DocumentResource.ARTEFACT, SUBJECT.reference),
            document_revision_at(document_path), DocumentWriteBodyOperation.APPEND, InlineContent('Not admitted')),
    ):
        blocked = app.invoke(request)
        assert blocked.status == 'error' and blocked.effects == 'none'
        assert 'projection' in blocked.error.message
    cursor, pages = first.records[0]['cursor'], 1
    while cursor:
        page = ok(app.invoke(RelationshipRefreshRequest(work_budget=1000, cursor=cursor)))
        assert page.records[0]['visited'] <= 1000
        cursor = page.records[0]['cursor']
        pages += 1
    assert page.complete and pages == 6
    ok(app.invoke(create()))
    assert len(ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False))).records) == 1
    scoped = ok(app.invoke(RelationshipCheckRequest(identities=('relationship:edge',))))
    assert scoped.complete and not scoped.findings
    first_check = ok(app.invoke(RelationshipCheckRequest(work_budget=1000)))
    assert not first_check.complete and first_check.continuation
    cursor, inspected = first_check.continuation, 1
    while cursor:
        checked = ok(app.invoke(RelationshipCheckRequest(work_budget=1000, cursor=cursor)))
        assert checked.visited <= 1000
        cursor = checked.continuation
        inspected += 1
    assert checked.complete and inspected > 50


def test_rebuild_continuation_rejects_changed_inventory(graph):
    root, app = graph
    cursor = ok(app.invoke(RelationshipRefreshRequest(work_budget=1))).records[0]['cursor']
    assert cursor
    Store(root).write([{'kind': 'event', 'id': 'external', 'retired': True, 'revision': 'historical'}])
    changed = app.invoke(RelationshipRefreshRequest(work_budget=1, cursor=cursor))
    assert changed.status == 'error' and changed.effects == 'none'


def test_query_pages_beyond_one_work_budget_without_skipping_assertions(graph):
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship._types import BatchItem, Action
    from test_relationship_commands import VALUE
    root, app = graph
    ok(app.invoke(RelationshipBatchRequest(tuple(BatchItem(Action.CREATE, 'paged-' + str(index), SUBJECT, 'confidence', VALUE) for index in range(23)))))
    cursor, seen = None, []
    while True:
        page = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False, work_budget=5, limit=2, cursor=cursor)))
        assert page.visited <= 5
        seen.extend(row['id'] for row in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 23


def test_query_byte_cap_cursor_keeps_the_first_unreturned_record(graph):
    from _relationships.model import Endpoint, NodeKind
    root, app = graph
    for index in range(3):
        ok(app.invoke(create('large-' + str(index), value=Endpoint(NodeKind.LITERAL, value='x' * 140000))))
    cursor, seen = None, []
    for _ in range(4):
        page = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False, cursor=cursor)))
        seen.extend(row['id'] for row in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None and seen == ['large-0', 'large-1', 'large-2']


def test_definition_pages_cross_work_budget_and_preserve_builtin_overlay(graph):
    from _application.relationship.define import RelationshipDefineRequest
    from _application.relationship.definitions import RelationshipDefinitionsRequest
    from _application.relationship._types import DefinitionKind
    from _relationships.definitions import BUILTINS
    root, app = graph
    expected = set(BUILTINS)
    for index in range(12):
        identity = 'event-' + str(index)
        ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, identity, identity, 'Caller event ' + identity)))
        expected.add(identity)
    cursor, seen = None, []
    for _ in range(20):
        page = ok(app.invoke(RelationshipDefinitionsRequest(DefinitionKind.EVENT, work_budget=2, limit=5, cursor=cursor)))
        assert page.visited <= 2
        seen.extend(record['id'] for record in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None and len(seen) == len(set(seen)) and set(seen) == expected
