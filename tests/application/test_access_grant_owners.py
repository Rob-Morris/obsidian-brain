"""Typed instance-authorisation controls expose scope without lease semantics."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from _application.access import prepare, reduce, request, status
from _application.access_contracts import (
    AccessReductionResult, AccessRequestDecision, CommandAuthorisationState,
    ConsentRecordState, PreparedOperation,
)
from _application.consent import ConsentScope
from _application.projection import request_schema, canonical_result_envelope
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import InitialAuthorisationClass, RetryClass


def test_consent_and_reduction_discriminators_are_constructor_owned():
    assert request.CommandConsent("artefact.delete", "review").scope == "command"
    assert request.OperationConsent("operation-one", "sha256:digest", "review").scope == "operation"
    assert reduce.RevokeGrants(("grant-one",)).kind == "grants"
    assert reduce.DiscardOperations(("operation-one",)).kind == "operations"
    assert reduce.NarrowInitial(("artefact.read",)).kind == "commands"
    assert reduce.ClearGrants().kind == "all-grants"
    with pytest.raises(TypeError):
        request.CommandConsent("artefact.delete", "review", scope="operation")
    with pytest.raises(TypeError):
        reduce.ClearGrants(kind="initial")


@pytest.mark.parametrize("command,payload", [
    ("access.request", {"commands": ["artefact.delete"]}),
    ("access.request", {"consent": {"scope": "command", "command_id": "artefact.delete", "review": "review", "duration_seconds": 60}}),
    ("access.request", {"consent": {"scope": "command", "command_id": "artefact.delete", "review": "review"}, "use_count": 1}),
    ("access.request", {"consent": {"scope": "operation", "operation_id": "op", "digest": "digest", "review": "review", "profile": "administrator"}}),
    ("access.reduce", {"reduction": {"kind": "initial"}}),
    ("access.reduce", {"reduction": {"kind": "leases", "lease_ids": ["lease"]}}),
    ("access.prepare", {"preparation": {"kind": "operation", "command_id": "artefact.read", "arguments": {"brain_operation": "op"}}}),
    ("access.prepare", {"preparation": {"kind": "operation", "command_id": "artefact.read", "arguments": {"value": float("nan")}}}),
    ("access.prepare", {"preparation": {"kind": "inspect", "operation_id": "op", "context_id": "other"}}),
])
def test_removed_lease_and_untrusted_context_inputs_are_rejected(command, payload):
    with pytest.raises(ValueError):
        current_request_resolver().resolve(command, payload)


@pytest.mark.parametrize("values", [(), ("b", "a"), ("a", "a"), ("",), (None,), tuple(str(i) for i in range(129))])
def test_reduction_identifiers_are_bounded_canonical_sets(values):
    with pytest.raises(ValueError):
        reduce.RevokeGrants(values)


@pytest.mark.parametrize("scope", ["command", "operation"])
def test_escaped_review_budget_measures_actual_utf8_request(scope):
    # Quotes double when encoded; both these reviews fit the old 7000-byte raw cap.
    review = '"\\' * 2200
    consent = (request.CommandConsent("artefact.delete", review) if scope == "command"
               else request.OperationConsent("operation-one", "sha256:" + "a" * 64, review))
    assert len(review.encode()) < 7000
    with pytest.raises(ValueError, match="8000-byte"):
        request.AccessRequestRequest(consent)
    consent = replace(consent, review="é" * 1500)
    request.AccessRequestRequest(consent)


@pytest.mark.parametrize("value", [0, 65, True, 1.5, "16"])
def test_access_status_bounds_page_size(value):
    with pytest.raises(ValueError):
        status.AccessStatusRequest(page_size=value)


def test_controls_have_strict_schemas_and_no_operation_selector():
    resolver = current_request_resolver()
    for module in (prepare, request, status, reduce):
        entry = module.catalogue_entry()
        schema = request_schema(entry.request_type)
        assert schema["additionalProperties"] is False
        assert "brain_operation" not in schema["properties"]
        assert entry.initial_class is InitialAuthorisationClass.CONTROL
        assert entry.preparation is None
        example = getattr(entry.request_type, "MINIMAL_EXAMPLE", {})
        resolver.resolve(entry.command_id, example)
    assert request.catalogue_entry().retry_class is RetryClass.RECEIPT_REQUIRED


def test_every_ordinary_command_has_explicit_class_and_preparation():
    catalogue = current_application_catalogue()
    controls = {entry.command_id for entry in catalogue.entries if entry.initial_class is InitialAuthorisationClass.CONTROL}
    assert controls == {"access.prepare", "access.request", "access.status", "access.reduce", "command.list", "command.describe", "invocation.read", "session.start"}
    assert catalogue.interface_epoch == 3
    for entry in catalogue.entries:
        assert (entry.preparation is None) == (entry.command_id in controls)
    exceptional = {entry.command_id for entry in catalogue.entries if entry.initial_class is InitialAuthorisationClass.EXCEPTIONAL}
    assert exceptional == {"artefact.delete", "skill.add-git", "skill.update", "type.sync", "retrieval.construct-benchmark", "retrieval.evaluate", "retrieval.rebuild-semantic", "retrieval.repair-semantic", "retrieval.enable", "workspace.bind", "workspace.configure-bootstrap", "workspace.register", "workspace.unregister", "workspace.setup", "workspace.update-metadata", "workspace.repair-registry"}
    by_id = {entry.command_id: entry for entry in catalogue.entries}
    assert by_id["shaping.render"].initial_class is InitialAuthorisationClass.CONTENT
    assert by_id["skill.status"].initial_class is InitialAuthorisationClass.OBSERVATION
    assert by_id["runtime.warmup"].initial_class is InitialAuthorisationClass.OBSERVATION


def test_request_owner_calls_only_explicit_selected_scope():
    calls = []
    class Access:
        def request(self, **values):
            calls.append(values)
            return AccessRequestDecision(CommandAuthorisationState.AUTHORISED, "artefact.delete", ConsentScope.COMMAND, True, grant_id="grant-one")
    typed = request.AccessRequestRequest(request.CommandConsent("artefact.delete", "canonical review"))
    result = request.execute(SimpleNamespace(access=Access()), typed)
    assert calls == [{"scope": ConsentScope.COMMAND, "command_id": "artefact.delete", "operation_id": None, "digest": None, "review": "canonical review"}]
    assert result.committed_effects[0].subject == "grant-one"
    assert canonical_result_envelope(result)["result"]["state"] == "authorised"


def test_preparation_owner_never_requests_or_enters_target():
    class Access:
        def prepare(self, command_id, arguments):
            assert (command_id, arguments) == ("artefact.delete", {"path": "Thoughts/Test.md"})
            return PreparedOperation("operation-one", command_id, 1, "sha256:" + "a" * 64, "canonical review", ConsentRecordState.PREPARED)
    result = prepare.execute(SimpleNamespace(access=Access()), prepare.decode(prepare.AccessPrepareRequest.MINIMAL_EXAMPLE))
    assert result.result.state is ConsentRecordState.PREPARED
    assert len(json.dumps(canonical_result_envelope(result), ensure_ascii=False).encode()) < 8000


@pytest.mark.parametrize("variant,expected", [
    (reduce.ClearGrants(), {"clear_grants": True}),
    (reduce.NarrowInitial(("artefact.read",)), {"commands": ("artefact.read",)}),
    (reduce.DiscardOperations(("operation-one",)), {"operation_ids": ("operation-one",)}),
    (reduce.RevokeGrants(("grant-one",)), {"grant_ids": ("grant-one",)}),
])
def test_reduction_owner_preserves_selected_narrowing_variant(variant, expected):
    class Access:
        def reduce(self, **values):
            assert {key: value for key, value in values.items() if value} == expected
            return AccessReductionResult(True)
    result = reduce.execute(SimpleNamespace(access=Access()), reduce.AccessReduceRequest(variant))
    assert result.result.changed


def test_new_ordinary_entries_cannot_omit_preparation_or_classification():
    from _application.catalogue import ApplicationCatalogue
    entry = next(item for item in current_application_catalogue().entries if item.command_id == "artefact.read")
    with pytest.raises(ValueError, match="explicit initial"):
        replace(entry, initial_class=None)
    with pytest.raises(ValueError, match="preparation strategy"):
        ApplicationCatalogue((replace(entry, preparation=None),))
    with pytest.raises(ValueError, match="control cannot"):
        ApplicationCatalogue((replace(entry, initial_class=InitialAuthorisationClass.CONTROL),))


def test_reserved_operation_selector_cannot_become_a_business_field():
    from dataclasses import dataclass
    from typing import ClassVar
    @dataclass(frozen=True)
    class Collision:
        COMMAND_ID: ClassVar[str] = "example.read"
        COMMAND_VERSION: ClassVar[int] = 1
        RESULT_TYPE: ClassVar[type] = str
        brain_operation: str
    entry = current_application_catalogue().entries[0]
    with pytest.raises(ValueError, match="reserved transport"):
        replace(entry, request_type=Collision)


@pytest.mark.parametrize("available,policy", [(True, "allowed"), (False, "allowed"), (True, "denied"), (True, "migration_required")])
def test_bootstrap_honestly_advertises_request_routes_within_byte_budget(tmp_path, available, policy):
    from command_application import context_for
    context = context_for(tmp_path, context_available=available, request_policy=policy)
    summary = context.access.summary()
    assert summary.authorisation.context_available is available
    assert summary.authorisation.request_policy == policy
    assert (summary.authorisation.request is not None) == (available and policy == "allowed")
    assert summary.authorisation.status == "access.status"
    assert len(json.dumps(asdict(summary), ensure_ascii=False, separators=(",", ":")).encode()) <= 768


def test_specific_read_preparation_request_and_success_spend_in_real_application(command_vault_clone):
    from command_application import application_for
    from _application.vault.read_file import VaultReadFileRequest
    from _application.results import ErrorCode
    catalogue = current_application_catalogue()
    controls = {entry.command_id for entry in catalogue.entries if entry.initial_class is InitialAuthorisationClass.CONTROL}
    app = application_for(command_vault_clone.vault_root, initial_commands=controls)
    target = VaultReadFileRequest(".brain-core/guide.md")
    denied = app.invoke(target)
    assert denied.error.code is ErrorCode.AUTHORISATION_REQUIRED
    prepared = app.invoke(prepare.AccessPrepareRequest(prepare.PrepareCommand(target.COMMAND_ID, {"path": target.path})))
    assert prepared.status == "ok", prepared
    operation = prepared.result
    listed = app.invoke(status.AccessStatusRequest(view=status.AccessStatusView.OPERATIONS))
    assert listed.result.entries[0].operation_id == operation.operation_id
    granted = app.invoke(request.AccessRequestRequest(request.OperationConsent(operation.operation_id, operation.digest, operation.review)))
    assert granted.status == "ok", granted
    app._context = replace(app._context, operation_id=operation.operation_id)
    read = app.invoke(target)
    assert read.status == "ok", read
    repeated = app.invoke(target)
    assert repeated.status == "error"
    assert app._context.authorisation.service.inspect(operation.operation_id)["state"] == "spent"
