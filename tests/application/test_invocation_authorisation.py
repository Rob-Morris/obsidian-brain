"""Real owner preparation, admission and receipt behaviour through application ports."""

from dataclasses import replace
import json
import time

import pytest

from _application.authorisation import InvocationAuthorisation, PreparationCoordinator
from _application.consent import ConsentError, ConsentIdentity, ConsentPolicy, ConsentScope, ConsentService
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
from _application._mutation_support import InlineContent, StagedContent
from _application.registry import current_application_catalogue
from _application.receipts import ExecutionState, OutcomeReference, ReceiptOwnership, ReceiptState
from _application.results import CommandError, Error, ErrorCode, Ok
from _bootstrap.consent_state import MemoryStateStore
from _command_interface.consent_staging import ConsentContentPins
from _command_interface.receipts import OwnedReceiptStore
from _common import document_revision_at
from _staging import stage_body, sweep_staged_bodies, STAGING_TTL_SECONDS
from command_application import application_for


TARGET = "Designs/project~command-fixture/Command Fixture Design.md"


@pytest.fixture
def system(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    context = application_for(root)._context
    catalogue = current_application_catalogue()
    versions = {entry.command_id: entry.command_version for entry in catalogue.entries}
    state = MemoryStateStore()
    identity = ConsentIdentity(context.selected_brain.brain_id, str(root.resolve()), "operator:test",
                               "instance-one", "mcp-instance", "policy-one", "3")
    service = ConsentService(state, identity, ConsentPolicy("operator", frozenset(versions), frozenset()), versions)
    private = tmp_path / "private"
    private.mkdir()
    def content_for(namespace):
        return ConsentContentPins(private, state, namespace=namespace, coordination_path=tmp_path / "pins.lock")
    preparation = PreparationCoordinator(service, catalogue, content_for)
    receipts = OwnedReceiptStore(root, context.clock, ReceiptOwnership(identity.brain_id, identity.principal,
                                                                     identity.kind, identity.context_id))
    request = DocumentWriteBodyRequest(DocumentLocator(DocumentResource.ARTEFACT, TARGET),
                                      document_revision_at(root / TARGET), DocumentWriteBodyOperation.REPLACE,
                                      InlineContent("A specifically authorised replacement.\n"))
    return root, context, catalogue, service, preparation, receipts, content_for, request


def grant(service, descriptor):
    return service.request(request_id="consent-one", scope=ConsentScope.OPERATION,
                           operation_id=descriptor["operation_id"], digest=descriptor["digest"],
                           review=descriptor["review"])


def invocation(system, descriptor, *, context=None, request=None):
    _, original, catalogue, service, _, receipts, content_for, original_request = system
    context, request = context or original, request or original_request
    entry = catalogue.resolve(request)
    admission = InvocationAuthorisation(service, entry, context, receipts,
                                         operation_id=descriptor["operation_id"], content_for=content_for)
    return entry, replace(context, admission=admission), admission


def test_real_document_entry_persists_attribution_and_spends_consent(system):
    root, context, _, service, preparation, receipts, _, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    decision = grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor)
    result = entry.executor(execution, request)
    assert isinstance(result, Ok)
    admission.finalise(result)
    assert request.content.content in (root / TARGET).read_text()
    found = receipts.read(OutcomeReference(context.invocation_id))
    assert found.intent.grant_id == decision.grant_id
    assert found.intent.request_id == "consent-one"
    assert found.outcome.execution is ExecutionState.SUCCEEDED
    assert service.inspect(descriptor["operation_id"])["state"] == "spent"


def test_preparation_retry_does_not_reread_changed_document(system):
    root, context, _, _, preparation, _, _, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    (root / TARGET).write_text((root / TARGET).read_text() + "\nConcurrent change.\n")
    assert preparation.prepare(context, request, request_id="prepare-one") == descriptor


def test_dry_run_review_cannot_authorise_real_execution(system):
    root, context, _, service, preparation, _, _, request = system
    descriptor = preparation.prepare(replace(context, dry_run=True), request, request_id="prepare-one")
    assert json.loads(descriptor["review"])["execution"]["dry_run"] is True
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor)
    before = (root / TARGET).read_bytes()
    with pytest.raises(ConsentError, match="changed"):
        entry.executor(execution, request)
    assert not admission.entered
    assert (root / TARGET).read_bytes() == before
    with pytest.raises(ConsentError, match="differs"):
        preparation.prepare(context, request, request_id="prepare-one")


def test_private_stage_survives_source_sweep(system):
    root, context, _, service, preparation, _, _, request = system
    handle = stage_body(str(root), "Pinned replacement.")["handle"]
    request = replace(request, content=StagedContent(handle))
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    sweep_staged_bodies(str(root), now=time.time() + STAGING_TTL_SECONDS + 10)
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor, request=request)
    result = entry.executor(execution, request)
    assert isinstance(result, Ok)
    admission.finalise(result)
    assert "Pinned replacement." in (root / TARGET).read_text()


def test_failed_final_receipt_never_refunds_entered_consent(system, monkeypatch):
    _, context, _, service, preparation, receipts, _, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor)
    result = entry.executor(execution, request)
    def fail(_):
        raise OSError("receipt disk unavailable")
    monkeypatch.setattr(receipts, "finalise", fail)
    with pytest.raises(OSError, match="unavailable"):
        admission.finalise(result)
    assert service.inspect(descriptor["operation_id"])["state"] == "spent"
    assert receipts.read(OutcomeReference(context.invocation_id)).outcome is None


def test_pin_cleanup_failure_can_be_retried_without_reopening_consent(system, monkeypatch):
    _, context, _, service, preparation, _, content_for, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    original = ConsentContentPins.discard
    def fail(_):
        raise OSError("temporary cleanup failure")
    monkeypatch.setattr(ConsentContentPins, "discard", fail)
    with pytest.raises(OSError, match="cleanup"):
        preparation.discard((descriptor["operation_id"],))
    assert service.inspect(descriptor["operation_id"])["state"] == "discarding"
    with pytest.raises(ConsentError):
        grant(service, descriptor)
    monkeypatch.setattr(ConsentContentPins, "discard", original)
    assert preparation.discard((descriptor["operation_id"],))
    assert preparation.prepare(context, request, request_id="prepare-one")["state"] == "discarded"


@pytest.mark.parametrize("lost", [False, True])
def test_read_execution_is_recorded_and_spent_even_without_effects(system, lost):
    from _application.artefact.read import ArtefactReadRequest
    _, context, _, service, preparation, receipts, _, _ = system
    request = ArtefactReadRequest(TARGET)
    descriptor = preparation.prepare(context, request, request_id="prepare-read")
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor, request=request)
    result = entry.executor(execution, request)
    assert isinstance(result, Ok)
    admission.finalise(None if lost else result)
    outcome = receipts.read(OutcomeReference(context.invocation_id)).outcome
    assert outcome.execution is (ExecutionState.UNKNOWN if lost else ExecutionState.SUCCEEDED)
    assert outcome.receipt.state is ReceiptState.NONE
    assert service.inspect(descriptor["operation_id"])["state"] == "spent"


def test_intent_failure_prevents_entry_and_preserves_specific_consent(system, monkeypatch):
    root, context, _, service, preparation, receipts, _, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor)
    before = (root / TARGET).read_bytes()
    def fail(_):
        raise OSError("intent unavailable")
    monkeypatch.setattr(receipts, "begin", fail)
    with pytest.raises(OSError, match="intent unavailable"):
        entry.executor(execution, request)
    assert not admission.entered
    assert (root / TARGET).read_bytes() == before
    assert service.inspect(descriptor["operation_id"])["state"] == "authorised"


def test_revocation_after_intent_prevents_entry_and_records_failure(system, monkeypatch):
    root, context, _, service, preparation, receipts, _, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    decision = grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor)
    before = (root / TARGET).read_bytes()
    begin = receipts.begin
    def revoke(intent):
        begin(intent)
        service.reduce(grant_ids=(decision.grant_id,))
    monkeypatch.setattr(receipts, "begin", revoke)
    with pytest.raises(ConsentError, match="revoked"):
        entry.executor(execution, request)
    admission.finalise(Error(entry.command_id, entry.command_version,
                             CommandError(ErrorCode.AUTHORITY_DENIED, "Revoked before entry.")))
    assert not admission.entered
    assert (root / TARGET).read_bytes() == before
    outcome = receipts.read(OutcomeReference(context.invocation_id)).outcome
    assert outcome.execution is ExecutionState.FAILED
    assert outcome.receipt.state is ReceiptState.NONE


def test_foreign_selected_brain_rejected_before_planning(system):
    _, context, _, _, preparation, _, _, request = system
    foreign = replace(context, selected_brain=replace(context.selected_brain, brain_id="another-brain"))
    with pytest.raises(ValueError, match="selected Brain"):
        preparation.prepare(foreign, request, request_id="prepare-one")


def test_concurrent_preparation_retries_keep_winner_pins(system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from _application.preparation import OperationPreparation
    root, context, catalogue, service, _, receipts, content_for, request = system
    handle = stage_body(str(root), "Concurrent retained input.")["handle"]
    request = replace(request, content=StagedContent(handle))
    entry = catalogue.resolve(request)
    barrier = Barrier(2)
    def planner(context, request, *, frozen_inputs=None):
        binding = entry.preparation.prepare(context, request, frozen_inputs=frozen_inputs)
        barrier.wait(timeout=10)
        return binding
    coordinated_entry = replace(entry, preparation=OperationPreparation(planner))
    coordinated_catalogue = replace(catalogue, entries=tuple(
        coordinated_entry if item.command_id == entry.command_id else item for item in catalogue.entries))
    coordinator = PreparationCoordinator(service, coordinated_catalogue, content_for)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: coordinator.prepare(context, request, request_id="same-prepare"), range(2)))
    assert results[0] == results[1]
    descriptor = results[0]
    sweep_staged_bodies(str(root), now=time.time() + STAGING_TTL_SECONDS + 10)
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor, request=request)
    result = entry.executor(execution, request)
    assert isinstance(result, Ok)
    admission.finalise(result)
    assert "Concurrent retained input." in (root / TARGET).read_text()


def test_selector_does_not_import_another_instance_descriptor(system):
    _, context, catalogue, service, preparation, receipts, content_for, request = system
    descriptor = preparation.prepare(context, request, request_id="prepare-one")
    grant(service, descriptor)
    foreign = ConsentService(MemoryStateStore(), replace(service.identity, context_id="other-instance"),
                              service.policy, service.versions)
    with pytest.raises(ConsentError, match="absent"):
        InvocationAuthorisation(foreign, catalogue.resolve(request), context, receipts,
                                operation_id=descriptor["operation_id"], content_for=content_for)


def test_successful_render_preview_has_no_effect_receipt_and_spends_consent(system):
    from _application.shaping.render import ShapingRenderRequest, PresentationOutput
    _, context, _, service, preparation, receipts, _, _ = system
    context = replace(context, dry_run=True)
    request = ShapingRenderRequest(TARGET, "consent-preview", PresentationOutput("presentation"), render=False)
    descriptor = preparation.prepare(context, request, request_id="prepare-preview")
    grant(service, descriptor)
    entry, execution, admission = invocation(system, descriptor, context=context, request=request)
    result = entry.executor(execution, request)
    assert isinstance(result, Ok)
    assert not result.committed_effects
    admission.finalise(result)
    outcome = receipts.read(OutcomeReference(context.invocation_id)).outcome
    assert outcome.execution is ExecutionState.SUCCEEDED
    assert outcome.receipt.state is ReceiptState.NONE
    assert service.inspect(descriptor["operation_id"])["state"] == "spent"


def test_discard_batch_rejects_active_member_before_closing_any_descriptor(system):
    _, context, _, service, preparation, _, _, request = system
    first = preparation.prepare(context, request, request_id="prepare-one")
    second = preparation.prepare(context, request, request_id="prepare-two")
    grant(service, second)
    with pytest.raises(ConsentError, match="Revoke"):
        preparation.discard((first["operation_id"], second["operation_id"]))
    assert service.inspect(first["operation_id"])["state"] == "prepared"
    assert service.inspect(second["operation_id"])["state"] == "authorised"
