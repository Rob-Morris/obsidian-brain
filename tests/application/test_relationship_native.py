"""Native lifecycle reference, occurrence and selected-history acceptance."""

from dataclasses import replace

from command_application import application_for
from _application.artefact.set_key import ArtefactSetKeyRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.artefact.reparent import ArtefactReparentRequest
from _application.artefact.archive import ArtefactArchiveRequest
from _application.artefact.unarchive import ArtefactUnarchiveRequest
from _application.relationship.expose import RelationshipExposeRequest
from _application.relationship.batch import RelationshipBatchRequest
from _application.relationship.read import RelationshipReadRequest, RecordKind
from _application.relationship._types import Action, BatchItem, Disposition
from _application.history.read import HistoryReadRequest
from _application.history.repair import HistoryRepairRequest
from _relationships.model import Endpoint, NodeKind
from _relationships.storage import Store
from test_relationship_commands import graph, ok, create, SUBJECT, VALUE


def test_rekey_preserves_assertion_and_native_parent_occurrence_identity(graph):
    root, app = graph
    edge = ok(app.invoke(create())).records[0]
    exposed = ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records
    parent = next(item for item in exposed if item["predicate"] == "native.parent")
    annotation = BatchItem(Action.CREATE, "annotation", Endpoint(NodeKind.OCCURRENCE, parent["id"]), "confidence", VALUE)
    ok(app.invoke(RelationshipBatchRequest((annotation,))))
    ok(app.invoke(ArtefactSetKeyRequest("project/command-fixture", "renamed-owner")))
    retained = ok(app.invoke(RelationshipReadRequest(parent["id"], RecordKind.OCCURRENCE))).records[0]
    assert not retained["retired"]
    assert retained["object"]["reference"] == "project/renamed-owner"
    assert retained["revision"] == parent["revision"]
    ok(app.invoke(ArtefactSetKeyRequest("design/command-fixture-design", "renamed-design")))
    renamed = ok(app.invoke(RelationshipReadRequest(edge["id"]))).records[0]
    assert renamed["subject"]["reference"] == "design/renamed-design"
    assert renamed["revision"] == edge["revision"]


def test_reparent_a_b_a_retires_old_occurrence_and_requires_annotation_disposition(graph):
    root, app = graph
    old = next(item for item in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records if item["predicate"] == "native.parent")
    annotation = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "annotation",
        Endpoint(NodeKind.OCCURRENCE, old["id"]), "confidence", VALUE),)))).records[0]
    refused = app.invoke(ArtefactReparentRequest(SUBJECT.reference, None))
    assert refused.status == "error" and refused.effects == "none"
    moved = ok(app.invoke(ArtefactReparentRequest(SUBJECT.reference, None,
        relationship_dispositions=(Disposition("relationship:annotation", annotation["revision"]),))))
    assert ok(app.invoke(RelationshipReadRequest(old["id"], RecordKind.OCCURRENCE))).records[0]["retired"]
    ok(app.invoke(ArtefactReparentRequest(SUBJECT.reference, "project/command-fixture")))
    recreated = next(item for item in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records if item["predicate"] == "native.parent")
    assert recreated["id"] != old["id"]
    assert ok(app.invoke(RelationshipReadRequest("annotation"))).records[0]["retired"]


def test_archive_unarchive_preserves_typed_subject_and_records_only_real_transitions(graph):
    root, app = graph
    edge = ok(app.invoke(create())).records[0]
    ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, "ready")))
    count = len(Store(root).rows(kind="event"))
    ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, "ready")))
    assert len(Store(root).rows(kind="event")) == count
    archived = ok(app.invoke(ArtefactArchiveRequest(SUBJECT.reference)))
    assert ok(app.invoke(RelationshipReadRequest(edge["id"]))).records[0]["revision"] == edge["revision"]
    restored = ok(app.invoke(ArtefactUnarchiveRequest(archived.new_path)))
    assert restored.new_path == archived.old_path
    assert len(Store(root).rows(kind="event")) == count + 2


def test_failed_native_event_publication_repairs_captured_event_once(graph, monkeypatch):
    root, app = graph
    original = Store.write
    failed = False
    def injected(self, records, removals=()):
        nonlocal failed
        if any(item["kind"] == "event" for item in records) and not failed:
            failed = True
            raise OSError("injected history publication failure")
        return original(self, records, removals)
    monkeypatch.setattr(Store, "write", injected)
    result = app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, "active"))
    assert result.status == "partial", result
    journals = [Store(root).read_path(path) for path in (root / "_Relationships/operation").glob("*.json")]
    journal = next(item for item in journals if item["state"] != "complete")
    assert journal["native_guards"]
    repaired = ok(app.invoke(HistoryRepairRequest(journal["id"])))
    events = Store(root).rows(kind="event")
    assert len(events) == 1
    ok(app.invoke(HistoryRepairRequest(journal["id"])))
    assert len(Store(root).rows(kind="event")) == 1
    event = ok(app.invoke(HistoryReadRequest(events[0]["id"]))).records[0]
    assert event["provenance"] == "observed" and '"status":"active"' in event["payload"]["after"]


def test_historic_events_do_not_become_current_obligations_on_native_delete(graph):
    from _application.artefact.delete import ArtefactDeleteRequest
    root, app = graph
    changed = ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, "active")))
    ok(app.invoke(ArtefactDeleteRequest(changed.path)))


def test_temporal_rename_preserves_reference_and_conversion_dispositions(graph):
    from _application.artefact.rename import ArtefactRenameRequest
    from _application.artefact.convert import ArtefactConvertRequest
    from command_vault import load_command_vault_manifest
    root, app = graph
    path = load_command_vault_manifest()["stable_artefacts"]["owned_plan"]["path"]
    subject = Endpoint(NodeKind.ARTEFACT, path)
    edge = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "temporal", subject, "confidence", VALUE),)))).records[0]
    destination = path.replace(".md", " Renamed.md")
    moved = ok(app.invoke(ArtefactRenameRequest(path, destination)))
    retained = ok(app.invoke(RelationshipReadRequest("temporal"))).records[0]
    assert retained["subject"]["reference"] == moved.new_path
    assert retained["revision"] == edge["revision"]
    refused = app.invoke(ArtefactConvertRequest(moved.new_path, "temporal/report"))
    assert refused.status == "error" and "closure" in refused.error.message
    ok(app.invoke(ArtefactConvertRequest(moved.new_path, "temporal/report",
        relationship_dispositions=(Disposition("relationship:temporal", edge["revision"]),))))
    assert ok(app.invoke(RelationshipReadRequest("temporal"))).records[0]["retired"]


def test_native_preparation_refuses_new_dependant_before_first_write(graph):
    from _application.registry import current_application_catalogue
    from test_workspace_skill_preparation import MatchingAdmission
    root, app = graph
    occurrence = next(item for item in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records if item["predicate"] == "native.parent")
    request = ArtefactReparentRequest(SUBJECT.reference, None)
    owner = current_application_catalogue().resolve(request)
    binding = owner.preparation.prepare(app._context, request)
    ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "late", Endpoint(NodeKind.OCCURRENCE, occurrence["id"]), "confidence", VALUE),))))
    admission = MatchingAdmission(binding)
    result = owner.executor(replace(app._context, admission=admission), request)
    assert result.status == "error" and result.effects == "none" and admission.calls == 0
    assert "closure" in result.error.message
    assert not ok(app.invoke(RelationshipReadRequest(occurrence["id"], RecordKind.OCCURRENCE))).records[0]["retired"]


def test_reassign_parent_and_membership_accepts_one_unioned_disposition(graph):
    from _application.workspace.create import WorkspaceCreateRequest
    from _application.workspace_context import WorkspaceSelector
    from _application.artefact.create import ArtefactCreateRequest
    from _application.artefact.set_workspace import ArtefactSetWorkspaceRequest
    root, app = graph
    for name in ("old", "new"):
        ok(app.invoke(WorkspaceCreateRequest(name)))
    created = ok(app.invoke(ArtefactCreateRequest("living/design", "Scoped", key="scoped", parent="workspace/old", workspace_context=WorkspaceSelector("workspace/old"))))
    subject = Endpoint(NodeKind.ARTEFACT, "design/scoped")
    occurrences = ok(app.invoke(RelationshipExposeRequest(subject))).records
    selected = [row for row in occurrences if row["predicate"] in ("native.parent", "native.workspace")]
    items = tuple(BatchItem(Action.CREATE, "annotation-" + str(i), Endpoint(NodeKind.OCCURRENCE, row["id"]), "confidence", VALUE) for i, row in enumerate(selected))
    annotations = ok(app.invoke(RelationshipBatchRequest(items))).records
    direct = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "scoped-edge", subject, "confidence", VALUE),)))).records[0]
    ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "nested-scope", Endpoint(NodeKind.RELATIONSHIP, "scoped-edge"), "confidence", VALUE),))))
    ok(app.invoke(ArtefactSetWorkspaceRequest(created.path, parent="workspace/new", workspace_context=WorkspaceSelector("workspace/new"),
        relationship_dispositions=tuple(Disposition("relationship:" + row["id"], row["revision"]) for row in annotations))))
    assert all(ok(app.invoke(RelationshipReadRequest(row["id"], RecordKind.OCCURRENCE))).records[0]["retired"] for row in selected)
    updated = ok(app.invoke(RelationshipReadRequest("scoped-edge"))).records[0]
    assert updated["workspace"] == "workspace/new" and updated["revision"] == direct["revision"]
    assert ok(app.invoke(RelationshipReadRequest("nested-scope"))).records[0]["workspace"] == "workspace/new"


def test_bound_body_and_noop_leave_semantic_generation_and_recovery_records_unchanged(graph):
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
    from _application._mutation_support import InlineContent
    from _common import document_revision_at
    from command_vault import load_command_vault_manifest
    root, app = graph
    ok(app.invoke(create()))
    before = {path: path.read_bytes() for path in (root/'_Relationships').glob('*/*.json')}
    generation = Store(root).generation()
    from _application.relationship.query import RelationshipQueryRequest
    cursor = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, limit=1))).next_cursor
    assert cursor is not None
    path = load_command_vault_manifest()['stable_artefacts']['owned_design']['path']
    ok(app.invoke(DocumentWriteBodyRequest(DocumentLocator(DocumentResource.ARTEFACT, SUBJECT.reference),
        document_revision_at(root/path), DocumentWriteBodyOperation.APPEND, InlineContent('\nBody only.\n'))))
    ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'ready')))
    assert {path: path.read_bytes() for path in (root/'_Relationships').glob('*/*.json')} == before
    assert Store(root).generation() == generation
    ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, limit=1, cursor=cursor)))


def test_subject_query_reads_native_owner_facts_without_materialising_identity(graph):
    from _application.relationship.query import RelationshipQueryRequest
    root, app = graph
    store = Store(root)
    before = {path: path.read_bytes() for path in (root/'_Relationships').rglob('*.json')}
    generation = store.generation()
    page = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT)))
    native = [record for record in page.records if record['kind'] == 'native-fact']
    assert {'native.parent', 'native.status'} <= {record['predicate'] for record in native}
    assert all(not record['materialised'] and 'id' not in record for record in native)
    assert not ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False))).records
    assert {path: path.read_bytes() for path in (root/'_Relationships').rglob('*.json')} == before
    assert store.generation() == generation
    exposed = ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records
    current = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT))).records
    assert {row['id'] for row in current} == {row['id'] for row in exposed}
    cursor = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, limit=1))).next_cursor
    ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, 'active')))
    stale = app.invoke(RelationshipQueryRequest(subject=SUBJECT, limit=1, cursor=cursor))
    assert stale.status == 'error' and 'cursor' in stale.error.message
    broad = ok(app.invoke(RelationshipQueryRequest()))
    assert any('Native scope' in row['reason'] for row in broad.findings)


def test_deleted_native_key_can_bind_new_metadata_without_transferring_old_identity(graph):
    from _application.artefact.create import ArtefactCreateRequest
    from _application.artefact.delete import ArtefactDeleteRequest
    from _relationships.endpoints import Endpoints
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import pytest
    root, app = graph
    made = ok(app.invoke(ArtefactCreateRequest('living/design', 'Reusable design', key='reused-key')))
    subject = Endpoint(NodeKind.ARTEFACT, 'design/reused-key')
    old = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, 'old-life', subject, 'confidence', VALUE),)))).records[0]
    old = ok(app.invoke(RelationshipReadRequest('old-life'))).records[0]
    ok(app.invoke(ArtefactDeleteRequest(made.path,
        relationship_dispositions=(Disposition('relationship:old-life', old['revision']),))))
    ok(app.invoke(ArtefactCreateRequest('living/design', 'Reusable design', key='reused-key')))
    new = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, 'new-life', subject, 'confidence', VALUE),)))).records[0]
    new = ok(app.invoke(RelationshipReadRequest('new-life'))).records[0]
    assert new['subject']['identity'] != old['subject']['identity']
    retained = ok(app.invoke(RelationshipReadRequest('old-life'))).records[0]
    assert retained['retired'] and 'subject' not in retained
    with pytest.raises(ValueError, match='identity changed'):
        Endpoints(Store(root), require_fresh_compiled_router(root)).resolve(old['subject'])


def test_valid_native_artefact_without_created_accepts_metadata_without_content_rewrite(graph):
    from _common import parse_frontmatter, serialize_frontmatter
    from command_vault import load_command_vault_manifest
    root, app = graph
    path = root/load_command_vault_manifest()['stable_artefacts']['owned_design']['path']
    # Simulate a valid pre-existing document: created is optional in its schema.
    fields, body = parse_frontmatter(path.read_text())
    fields.pop('created', None)
    path.write_text(serialize_frontmatter(fields, body=body))
    before = path.read_bytes()
    assert 'created' not in parse_frontmatter(path.read_text())[0]
    ok(app.invoke(create()))
    first = ok(app.invoke(RelationshipReadRequest('edge'))).records[0]
    ok(app.invoke(create('another')))
    second = ok(app.invoke(RelationshipReadRequest('another'))).records[0]
    assert first['subject']['identity'] == second['subject']['identity']
    assert path.read_bytes() == before


def test_native_semantic_preparation_reuses_selected_lossless_source_once(graph, monkeypatch):
    from collections import Counter
    from pathlib import Path
    import builtins
    import edit
    from _common import _artefacts, document_revision, load_compiled_router
    from _application.relationship import _native
    root, app = graph
    router = load_compiled_router(root)
    path = router['artefact_index'][SUBJECT.reference]['path']
    plan = edit.plan_lifecycle_field(str(root), router, path, 'status', 'active',
                                     effective_at=app._context.clock.now().isoformat())
    raw = (root/path).read_text().encode('utf-16')
    (root/path).write_bytes(raw)
    reads = Counter()
    original = builtins.open
    def counted(source, mode, *args, **kwargs):
        if mode == 'rb':
            reads[Path(source).relative_to(root).as_posix()] += 1
        return original(source, mode, *args, **kwargs)
    monkeypatch.setattr(_artefacts, 'open', counted, raising=False)
    semantic, observations, _ = _native.prepare(app._context,
        ArtefactSetStatusRequest(SUBJECT.reference, 'active'), plan, router)
    assert reads[path] == 1
    assert semantic.events
    assert any(item.identity == path and item.revision == document_revision(raw) for item in observations)
    assert (root/path).read_bytes() == raw


def test_generic_metadata_does_not_consume_lossless_native_source(graph, monkeypatch):
    from _common import load_compiled_router
    root, app = graph
    router = load_compiled_router(root)
    path = root/router['artefact_index'][SUBJECT.reference]['path']
    import _lifecycle.derived_cache_state as cache
    monkeypatch.setattr(cache, 'require_fresh_compiled_router', lambda _root: router)
    raw = path.read_text().encode('utf-16')
    path.write_bytes(raw)
    refused = app.invoke(create())
    assert refused.status == 'error' and refused.effects == 'none', refused
    assert 'utf16_bom' in refused.error.message
    assert path.read_bytes() == raw
    assert Store(root).read('assertion', 'edge') is None
