"""Independent native compatibility checks for semantic relationships."""

from dataclasses import asdict
import json

import pytest

from command_application import application_for
from command_vault import load_command_vault_manifest
from _common import document_revision_at, parse_frontmatter
from _application._mutation_support import FrontmatterField, InlineContent
from _application.artefact.create import ArtefactCreateRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.write_body import DocumentWriteBodyOperation, DocumentWriteBodyRequest
from _application.history.query import HistoryQueryRequest
from _application.relationship._types import Action, BatchItem, DefinitionKind, Disposition
from _application.relationship.batch import RelationshipBatchRequest
from _application.relationship.check import RelationshipCheckRequest
from _application.relationship.define import RelationshipDefineRequest
from _application.relationship.expose import RelationshipExposeRequest
from _application.relationship.query import RelationshipQueryRequest
from _application.relationship.read import RecordKind, RelationshipReadRequest
from _application.relationship.refresh import RelationshipRefreshRequest
from _application.workspace.create import WorkspaceCreateRequest
from _application.workspace_context import WorkspaceSelector
from _relationships.model import Endpoint, NodeKind, assertion_revision
from _relationships.policies import AssessmentPolicy
from _relationships.storage import Store


SUBJECT = Endpoint(NodeKind.ARTEFACT, "design/command-fixture-design")
HIGH = Endpoint(NodeKind.LITERAL, value="high")
WORKSPACE = "workspace/compatibility"


def ok(result):
    assert result.status == "ok", result
    return result.result


def create(identity, subject, predicate, obj, *, scope="global"):
    return BatchItem(Action.CREATE, identity, subject, predicate, obj, scope=scope)


def read(app, identity, kind=RecordKind.ASSERTION):
    return ok(app.invoke(RelationshipReadRequest(identity, kind))).records[0]


@pytest.fixture
def graph(command_vault_clone):
    root = command_vault_clone.vault_root
    app = application_for(root)
    ok(app.invoke(RelationshipDefineRequest(
        DefinitionKind.PREDICATE, "confidence", "Confidence", "Confidence in the subject",
    )))
    return root, app


@pytest.fixture
def temporal(graph):
    root, app = graph
    ok(app.invoke(WorkspaceCreateRequest("compatibility")))
    plan = ok(app.invoke(ArtefactCreateRequest(
        "temporal/plan", "Compatibility Verification", parent=None,
        workspace_context=WorkspaceSelector(WORKSPACE),
    )))
    assert parse_frontmatter((root / plan.path).read_text())[0]["workspace"] == WORKSPACE
    return root, app, Endpoint(NodeKind.ARTEFACT, plan.path)


def test_temporal_relationships_and_native_occurrences_use_actual_workspace(temporal):
    root, app, subject = temporal
    ok(app.invoke(RelationshipBatchRequest((create("temporal-confidence", subject, "confidence", HIGH),))))
    assert read(app, "temporal-confidence")["workspace"] == WORKSPACE
    native = ok(app.invoke(RelationshipQueryRequest(subject=subject, workspace=WORKSPACE)))
    assert any(row['predicate'] == 'native.workspace' and not row.get('materialised', True) for row in native.records)
    scoped = ok(app.invoke(RelationshipQueryRequest(subject=subject, workspace=WORKSPACE, include_native=False)))
    assert {row["id"] for row in scoped.records} == {"temporal-confidence"}
    assert not ok(app.invoke(RelationshipQueryRequest(subject=subject, workspace="global"))).records
    occurrences = ok(app.invoke(RelationshipExposeRequest(subject))).records
    membership = next(row for row in occurrences if row["predicate"] == "native.workspace")
    assert membership["object"]["reference"] == WORKSPACE
    assert all(row["workspace"] == WORKSPACE for row in occurrences)


def test_metadata_about_assertions_inherits_workspace_within_and_across_batches(temporal):
    _, app, subject = temporal
    ok(app.invoke(RelationshipBatchRequest((
        create("edge", subject, "confidence", HIGH),
        create("annotation", Endpoint(NodeKind.RELATIONSHIP, "edge"), "confidence", HIGH),
    ))))
    ok(app.invoke(RelationshipBatchRequest((
        create("nested", Endpoint(NodeKind.RELATIONSHIP, "annotation"), "confidence", HIGH),
    ))))
    records = {identity: read(app, identity) for identity in ("edge", "annotation", "nested")}
    assert {identity: row["workspace"] for identity, row in records.items()} == {
        "edge": WORKSPACE, "annotation": WORKSPACE, "nested": WORKSPACE,
    }
    assert {row["id"] for row in ok(app.invoke(RelationshipQueryRequest(workspace=WORKSPACE))).records} == set(records)
    assert not ok(app.invoke(RelationshipQueryRequest(workspace="global"))).records


def test_document_status_patch_routes_to_native_owner_with_disposition_and_history(graph):
    root, app = graph
    path = root / load_command_vault_manifest()["stable_artefacts"]["owned_design"]["path"]
    document = DocumentLocator(DocumentResource.ARTEFACT, SUBJECT.reference)
    status = next(row for row in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records
                  if row["predicate"] == "native.status")
    ok(app.invoke(RelationshipBatchRequest((
        create("status-review", Endpoint(NodeKind.OCCURRENCE, status["id"]), "confidence", HIGH),
    ))))
    annotation = read(app, "status-review")
    before = path.read_bytes()
    request = DocumentUpdateFrontmatterRequest(
        document, document_revision_at(path), (FrontmatterField("status", "active"),),
    )
    refused = app.invoke(request)
    assert refused.status == "error" and refused.effects == "none", refused
    assert "artefact.set-status" in refused.error.message
    assert path.read_bytes() == before
    assert not ok(app.invoke(HistoryQueryRequest())).records

    refused = app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, "active"))
    assert refused.status == "error" and refused.effects == "none", refused
    assert "closure" in refused.error.message
    assert path.read_bytes() == before
    assert not ok(app.invoke(HistoryQueryRequest())).records
    ok(app.invoke(ArtefactSetStatusRequest(
        SUBJECT.reference, "active",
        relationship_dispositions=(Disposition("relationship:status-review", annotation["revision"]),),
    )))
    assert parse_frontmatter(path.read_text())[0]["status"] == "active"
    assert read(app, status["id"], RecordKind.OCCURRENCE)["retired"]
    assert read(app, "status-review")["retired"]
    current = next(row for row in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records
                   if row["predicate"] == "native.status")
    assert current["id"] != status["id"] and current["object"]["value"] == "active"
    events = ok(app.invoke(HistoryQueryRequest(subject=SUBJECT, predicate="native-transition"))).records
    assert len(events) == 1
    event = events[0]
    assert event["provenance"] == "observed"
    assert event["payload"]["command"] == "artefact.set-status"
    assert json.loads(event["payload"]["before"])["status"] == "ready"
    assert json.loads(event["payload"]["after"])["status"] == "active"


def test_document_noop_and_body_edit_preserve_annotated_status_without_history(graph):
    root, app = graph
    path = root / load_command_vault_manifest()["stable_artefacts"]["owned_design"]["path"]
    document = DocumentLocator(DocumentResource.ARTEFACT, SUBJECT.reference)
    status = next(row for row in ok(app.invoke(RelationshipExposeRequest(SUBJECT))).records
                  if row["predicate"] == "native.status")
    ok(app.invoke(RelationshipBatchRequest((
        create("status-review", Endpoint(NodeKind.OCCURRENCE, status["id"]), "confidence", HIGH),
    ))))
    annotation = read(app, "status-review")
    refused = app.invoke(DocumentUpdateFrontmatterRequest(
        document, document_revision_at(path), (FrontmatterField("status", "ready"),),
    ))
    assert refused.status == "error" and refused.effects == "none", refused
    assert "artefact.set-status" in refused.error.message
    ok(app.invoke(ArtefactSetStatusRequest(SUBJECT.reference, "ready")))
    ok(app.invoke(DocumentUpdateFrontmatterRequest(
        document, document_revision_at(path),
        (FrontmatterField("tags", tuple(parse_frontmatter(path.read_text())[0]["tags"])),),
    )))
    ok(app.invoke(DocumentWriteBodyRequest(
        document, document_revision_at(path), DocumentWriteBodyOperation.APPEND,
        InlineContent("\nCompatibility review notes.\n"),
    )))
    assert "Compatibility review notes." in path.read_text()
    assert parse_frontmatter(path.read_text())[0]["status"] == "ready"
    retained = read(app, status["id"], RecordKind.OCCURRENCE)
    assert not retained["retired"] and retained["revision"] == status["revision"]
    assert read(app, "status-review")["revision"] == annotation["revision"]
    assert not read(app, "status-review")["retired"]
    assert not ok(app.invoke(HistoryQueryRequest())).records


@pytest.fixture
def assessed(graph):
    root, app = graph
    fixture = load_command_vault_manifest()["stable_artefacts"]
    criterion = ok(app.invoke(ArtefactCreateRequest(
        "living/design", "Compatibility Acceptance Criteria", key="compatibility-criteria",
        content=InlineContent("# Criteria\n\nThe implementation must preserve native ownership.\n"),
    )))
    policy = AssessmentPolicy(
        Endpoint(NodeKind.RELATIONSHIP, "edge"),
        Endpoint(NodeKind.ARTEFACT, "design/compatibility-criteria"), "compatibility",
    )
    predicates = {value for name, value in asdict(policy).items() if name.endswith("_predicate")}
    contracts = {}
    for predicate in sorted(predicates):
        ok(app.invoke(RelationshipDefineRequest(
            DefinitionKind.PREDICATE, predicate, predicate, "Compatibility assessment " + predicate,
        )))
        contracts[predicate] = read(app, predicate, RecordKind.PREDICATE)["contract_revision"]
    ok(app.invoke(RelationshipBatchRequest((create("edge", SUBJECT, "confidence", HIGH),))))
    edge = read(app, "edge")
    contracts[edge["predicate"]] = read(app, edge["predicate"], RecordKind.PREDICATE)["contract_revision"]
    claim = Endpoint(NodeKind.RELATIONSHIP, "claim")
    evidence = Endpoint(NodeKind.ARTEFACT, fixture["owned_plan"]["path"])
    reviewer = Endpoint(NodeKind.ARTEFACT, fixture["project"]["key"])
    values = (
        ("claim", reviewer, policy.claim_predicate, policy.subject),
        ("criterion", claim, policy.criterion_predicate, policy.criterion),
        ("verdict", claim, policy.verdict_predicate, Endpoint(NodeKind.LITERAL, value="accepted")),
        ("attribution", claim, policy.attribution_predicate, reviewer),
        ("evidence", claim, policy.evidence_predicate, evidence),
        ("subject-pin", claim, policy.subject_revision_predicate, Endpoint(NodeKind.LITERAL, value=edge["revision"])),
        ("criterion-pin", claim, policy.criterion_revision_predicate,
         Endpoint(NodeKind.LITERAL, value=document_revision_at(root / criterion.path))),
        ("evidence-pin", Endpoint(NodeKind.RELATIONSHIP, "evidence"), policy.evidence_revision_predicate,
         Endpoint(NodeKind.LITERAL, value=document_revision_at(root / evidence.reference))),
        ("definition-pins", claim, policy.definition_revisions_predicate,
         Endpoint(NodeKind.LITERAL, value=json.dumps(contracts))),
    )
    ok(app.invoke(RelationshipBatchRequest(tuple(create(*row, scope=policy.scope) for row in values))))
    checked = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    assert checked.complete and not checked.findings, checked
    assert checked.records[0]["state"] == "satisfied", checked
    return root, app, policy


def test_invalid_external_record_is_reported_and_cannot_establish_standing(assessed):
    root, app, policy = assessed
    path = Store(root).path("assertion", "verdict")
    record = json.loads(path.read_text())
    del record["object"]
    path.write_text(json.dumps(record), encoding="utf-8")

    checked = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    finding = next(row for row in checked.findings if row.get("identity") == "verdict")
    assert finding["state"] == "malformed" and finding["remediation"]
    assert checked.records[0]["state"] == "unknown"
    ok(app.invoke(RelationshipRefreshRequest()))
    checked = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    assert checked.records[0]["state"] == "unknown"
    assert any(row.get("identity") == "verdict" for row in checked.findings)


@pytest.mark.parametrize("changed_field", ("predicate", "object"))
def test_external_meaning_change_cannot_hide_behind_stale_indices(assessed, changed_field):
    root, app, policy = assessed
    path = Store(root).path("assertion", "edge")
    record = json.loads(path.read_text())
    if changed_field == "predicate":
        record["predicate"] = "verdict"
        old_filter, new_filter = {"predicate": "confidence"}, {"predicate": "verdict"}
    else:
        record["object"] = asdict(Endpoint(NodeKind.LITERAL, value="low"))
        old_filter, new_filter = {"object": HIGH}, {"object": Endpoint(NodeKind.LITERAL, value="low")}
    record["revision"] = assertion_revision(record)
    path.write_text(json.dumps(record), encoding="utf-8")

    for selectors in (old_filter, new_filter):
        queried = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, **selectors)))
        assert not queried.complete and queried.findings
        assert not queried.records
    checked = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    assert checked.complete and not checked.findings, checked
    assert checked.records[0]["state"] == "unknown"
    assert checked.records[0]["claims"][0]["reason"] == "subject revision changed"

    ok(app.invoke(RelationshipRefreshRequest()))
    assert not ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, **old_filter))).records
    refreshed = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, **new_filter)))
    assert {row["id"] for row in refreshed.records} == {"edge"}
    assert ok(app.invoke(RelationshipCheckRequest(assessment=policy))).records[0]["state"] == "unknown"


def test_reserved_predicates_cannot_author_competing_native_claims(temporal):
    root, app, subject = temporal
    before = (root / subject.reference).read_bytes()
    native = ok(app.invoke(RelationshipExposeRequest(subject))).records
    targets = {
        "native.status": Endpoint(NodeKind.LITERAL, value="completed"),
        "native.parent": Endpoint(NodeKind.ARTEFACT, "project/command-fixture"),
        "native.workspace": Endpoint(NodeKind.ARTEFACT, WORKSPACE),
    }
    for predicate, obj in targets.items():
        definition = app.invoke(RelationshipDefineRequest(
            DefinitionKind.PREDICATE, predicate, "Competing native claim", "Caller-owned native claim",
        ))
        assert definition.status == "error" and definition.effects == "none", definition
        assert "reserved" in definition.error.message
        authored = app.invoke(RelationshipBatchRequest((create("competing", subject, predicate, obj),)))
        assert authored.status == "error" and authored.effects == "none", authored
        assert Store(root).read("predicate", predicate) is None
        assert Store(root).read("assertion", "competing") is None
    assert (root / subject.reference).read_bytes() == before
    current = ok(app.invoke(RelationshipExposeRequest(subject))).records
    semantic_fields = ("id", "subject", "predicate", "object", "revision", "retired", "workspace")
    assert [{key: row[key] for key in semantic_fields} for row in current] == [
        {key: row[key] for key in semantic_fields} for row in native
    ]
    assert not ok(app.invoke(HistoryQueryRequest())).records
