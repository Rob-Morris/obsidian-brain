"""Native command-boundary acceptance for semantic assertions and history."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from command_application import application_for
from _application.relationship.define import RelationshipDefineRequest
from _application.relationship.batch import RelationshipBatchRequest
from _application.relationship.read import RelationshipReadRequest, RecordKind
from _application.relationship.query import RelationshipQueryRequest
from _application.relationship._types import (DefinitionKind, Cardinality, Action, BatchItem,
                                             PayloadField, PayloadValue, Disposition)
from _application.history.write import HistoryWriteRequest
from _application.history.read import HistoryReadRequest
from _application.history.correct import HistoryCorrectRequest
from _application.history.cleanup import HistoryCleanupRequest
from _relationships.model import Endpoint, NodeKind
from _relationships.storage import Store


SUBJECT = Endpoint(NodeKind.ARTEFACT, "design/command-fixture-design")
VALUE = Endpoint(NodeKind.LITERAL, value="high")


def ok(result):
    assert result.status == "ok", getattr(getattr(result, "error", None), "message", result)
    return result.result


@pytest.fixture
def graph(command_vault_clone):
    root = command_vault_clone.vault_root
    app = application_for(root)
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, "confidence", "Confidence", "Subject confidence")))
    return root, app


def create(identity="edge", *, value=VALUE):
    return RelationshipBatchRequest((BatchItem(Action.CREATE, identity, SUBJECT, "confidence", value),))


def test_equal_triples_have_distinct_identity_and_external_metadata_does_not_change_revision(graph):
    root, app = graph
    first = ok(app.invoke(create())).records[0]
    second = ok(app.invoke(create("second"))).records[0]
    assert first["id"] != second["id"]
    before = ok(app.invoke(RelationshipReadRequest("edge"))).records[0]
    ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "annotation", Endpoint(NodeKind.RELATIONSHIP, "edge"), "confidence", VALUE),))))
    after = ok(app.invoke(RelationshipReadRequest("edge"))).records[0]
    assert before["revision"] == after["revision"]
    assert before["document_revision"] == after["document_revision"]
    queried = ok(app.invoke(RelationshipQueryRequest(subject=SUBJECT, include_native=False)))
    assert {item["id"] for item in queried.records} == {"edge", "second"}
    assert queried.complete is False and queried.source_freshness == "observed-projection-only"


def test_single_empty_slot_admission_prevents_competing_creates(graph):
    root, app = graph
    old = ok(app.invoke(RelationshipReadRequest("confidence", RecordKind.PREDICATE))).records[0]
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, "confidence", "Confidence", "Subject confidence",
        cardinality=Cardinality.ONE, expected_revision=old["document_revision"])))
    def writer(identity):
        return application_for(root).invoke(create(identity))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, ("one", "two")))
    assert sorted(item.status for item in results) == ["error", "ok"]
    assert "slot" in next(item.error.message for item in results if item.status == "error")
    rows = Store(root).rows(kind="assertion", retired=0)
    assert len(rows) == 1


def test_replacement_requires_explicit_revision_bound_dependency_disposition(graph):
    root, app = graph
    edge = ok(app.invoke(create())).records[0]
    annotation = ok(app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "annotation", Endpoint(NodeKind.RELATIONSHIP, "edge"), "confidence", VALUE),)))).records[0]
    item = BatchItem(Action.REPLACE, "edge", SUBJECT, "confidence", Endpoint(NodeKind.LITERAL, value="low"), expected_revision=edge["revision"])
    failed = app.invoke(RelationshipBatchRequest((item,)))
    assert failed.status == "error" and failed.effects == "none"
    assert "closure" in failed.error.message
    item = replace(item, dispositions=(Disposition("relationship:annotation", annotation["revision"]),))
    updated = ok(app.invoke(RelationshipBatchRequest((item,)))).records[0]
    assert updated["id"] != "edge"
    assert ok(app.invoke(RelationshipReadRequest("edge"))).records[0]["retired"]
    assert ok(app.invoke(RelationshipReadRequest("annotation"))).records[0]["retired"]
    assert len(Store(root).rows(kind="event")) == 1


def register_event(app):
    return ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, "milestone", "Milestone", "Caller milestone",
        payload_fields=(PayloadField("detail", "string", True),))))


def test_original_read_shows_corrections_and_deleted_correction_does_not_revive_it(graph):
    root, app = graph
    register_event(app)
    event = ok(app.invoke(HistoryWriteRequest("milestone", SUBJECT, (PayloadValue("detail", "initial account"),)))).records[0]["event"]
    corrected = ok(app.invoke(HistoryCorrectRequest(event["id"], event["revision"], "Clarification", "corrected account"))).records[0]["event"]
    read = ok(app.invoke(HistoryReadRequest(event["id"]))).records[0]
    assert read["payload"] == {"detail": "initial account"}
    assert read["interpretation"] == {"state": "corrected", "head": corrected["id"], "chain": [corrected["id"]]}
    second = ok(app.invoke(HistoryCorrectRequest(event["id"], corrected["revision"], "More evidence", "second account"))).records[0]["event"]
    conflict = app.invoke(HistoryCorrectRequest(event["id"], corrected["revision"], "Competing", "stale account"))
    assert conflict.status == "error" and "head" in conflict.error.message
    ok(app.invoke(HistoryCleanupRequest((second["id"],))))
    assert ok(app.invoke(HistoryReadRequest(event["id"]))).records[0]["interpretation"]["state"] == "unresolved"


def test_old_event_pins_original_contract_after_constraints_change(graph):
    root, app = graph
    register_event(app)
    event = ok(app.invoke(HistoryWriteRequest("milestone", SUBJECT, (PayloadValue("detail", "initial"),)))).records[0]["event"]
    previous = ok(app.invoke(RelationshipReadRequest("milestone", RecordKind.EVENT_TYPE))).records[0]
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.EVENT, "milestone", "Milestone", "Caller milestone",
        payload_fields=(PayloadField("detail", "string", True), PayloadField("reason", "string", True)), expected_revision=previous["document_revision"])))
    read = ok(app.invoke(HistoryReadRequest(event["id"]))).records[0]
    assert read["historical_validation"] == "valid"
    assert "requires reason" in read["present_conformance"]
    assert read["provenance"] == "caller"


def test_index_symlinked_ancestor_cannot_escape_selected_brain(tmp_path):
    root, outside = tmp_path / "vault", tmp_path / "elsewhere"
    root.mkdir(); outside.mkdir()
    (root / ".brain").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="selected Brain"):
        Store(root).initialise()
    assert list(outside.iterdir()) == []


def test_boolean_is_not_numeric_enumeration(graph):
    root, app = graph
    ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, "numeric", "Numeric", "One numeric value", values=(1,))))
    result = app.invoke(RelationshipBatchRequest((BatchItem(Action.CREATE, "bad", SUBJECT, "numeric", Endpoint(NodeKind.LITERAL, value=True)),)))
    assert result.status == "error" and result.effects == "none"
    assert Store(root).read("assertion", "bad") is None
