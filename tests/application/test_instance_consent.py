"""Consent is owned by a context and spent by operation entry, not timers."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from _application.consent import (
    ConsentError, ConsentIdentity, ConsentPolicy, ConsentScope, ConsentService,
)
from _application.preparation import OperationBinding, ObservedResource, canonical_json
from _bootstrap.consent_state import MemoryStateStore, StoreClosedError


VERSIONS = {"artefact.read": 1, "document.write-body": 1,
            "artefact.delete": 1, "retrieval.evaluate": 1, "access.status": 1}


def service(*, store=None, context="test-context", generation="config-1", initial=None,
            kind="mcp-instance", versions=None, refresh=None):
    identity = ConsentIdentity("brain", "/selected/brain", "operator:test", context,
                               kind, generation, "3")
    policy = ConsentPolicy("operator", frozenset(VERSIONS),
                           frozenset({"artefact.read", "access.status"} if initial is None else initial),
                           controls=frozenset({"access.status"}))
    return ConsentService(store or MemoryStateStore(), identity, policy,
                          versions or VERSIONS, refresh=refresh)


def binding(command="document.write-body", *, value="one", revision="sha256:old"):
    return OperationBinding(command, 1, canonical_json({"value": value}), "{}",
                            (ObservedResource("file", "note.md", revision),),
                            canonical_json({"action": command, "target": "note.md", "value": value}))


def granted(svc, *, command="document.write-body", request_id="request-1", operation=None):
    bound = operation or binding(command)
    prepared = svc.prepare(bound, request_id="prepare-" + request_id)
    decision = svc.request(request_id=request_id, scope=ConsentScope.OPERATION,
                           operation_id=prepared["operation_id"], digest=prepared["digest"],
                           review=prepared["review"])
    return bound, prepared, decision


def test_normal_content_is_initial_without_a_lease():
    svc = service(initial=VERSIONS)
    proof = svc.admit("document.write-body", 1, "ordinary")
    assert proof.basis == "initial"
    assert svc.grant_page()["items"] == []
    assert len(json.dumps(svc.summary(), ensure_ascii=False, separators=(",", ":")).encode()) <= 768


def test_specific_scope_binds_arguments_revision_and_selector():
    svc = service()
    bound, op, decision = granted(svc)
    assert svc.state("document.write-body") == "authorisation_required"
    assert svc.state("document.write-body", operation_id=op["operation_id"]) == "authorised"
    for wrong in [binding(value="two"), binding(revision="sha256:new")]:
        with pytest.raises(ConsentError, match="changed"):
            svc.admit("document.write-body", 1, "wrong", operation_id=op["operation_id"], binding=wrong)
    with pytest.raises(ConsentError):
        svc.admit("document.write-body", 1, "missing-selector")
    proof = svc.admit("document.write-body", 1, "correct", operation_id=op["operation_id"], binding=bound)
    assert proof.grant_id == decision.grant_id
    svc.finish(proof)
    assert svc.inspect(op["operation_id"])["state"] == "spent"


def test_observation_success_spends_specific_consent():
    svc = service()
    bound, op, _ = granted(svc, command="retrieval.evaluate")
    proof = svc.admit("retrieval.evaluate", 1, "evaluation", operation_id=op["operation_id"], binding=bound)
    svc.finish(proof)
    with pytest.raises(ConsentError):
        svc.admit("retrieval.evaluate", 1, "repeat", operation_id=op["operation_id"], binding=bound)


def test_reservation_without_completion_never_allows_replay():
    svc = service()
    bound, op, _ = granted(svc)
    svc.admit("document.write-body", 1, "interrupted", operation_id=op["operation_id"], binding=bound)
    with pytest.raises(ConsentError):
        svc.admit("document.write-body", 1, "interrupted", operation_id=op["operation_id"], binding=bound)


def test_two_racing_invocations_enter_once():
    svc = service()
    bound, op, _ = granted(svc)

    def invoke(number):
        try:
            return svc.admit("document.write-body", 1, f"race-{number}",
                             operation_id=op["operation_id"], binding=bound)
        except ConsentError:
            return None

    with ThreadPoolExecutor(max_workers=8) as workers:
        proofs = list(workers.map(invoke, range(20)))
    assert sum(proof is not None for proof in proofs) == 1


def test_repeated_request_cannot_replenish_spent_grant():
    svc = service()
    bound, op, decision = granted(svc)
    args = dict(request_id="request-1", scope=ConsentScope.OPERATION,
                operation_id=op["operation_id"], digest=op["digest"], review=op["review"])
    assert svc.request(**args).grant_id == decision.grant_id
    svc.finish(svc.admit("document.write-body", 1, "enter", operation_id=op["operation_id"], binding=bound))
    assert svc.request(**args).state == "denied"
    with pytest.raises(ConsentError):
        svc.request(**dict(args, request_id="new-request"))


def test_request_retry_cannot_change_review_or_scope():
    svc = service()
    _, op, _ = granted(svc)
    with pytest.raises(ConsentError, match="exact"):
        svc.request(request_id="another", scope=ConsentScope.OPERATION,
                    operation_id=op["operation_id"], digest=op["digest"], review="some other action")
    with pytest.raises(ConsentError, match="scope"):
        svc.request(request_id="request-1", scope=ConsentScope.COMMAND,
                    command_id="artefact.delete", review=svc.command_review("artefact.delete"))


def test_blanket_covers_one_exact_command_only():
    svc = service()
    svc.request(request_id="blanket", scope=ConsentScope.COMMAND,
                command_id="artefact.delete", review=svc.command_review("artefact.delete"))
    for i in range(3):
        assert svc.admit("artefact.delete", 1, f"delete-{i}").basis == "command"
    with pytest.raises(ConsentError):
        svc.admit("document.write-body", 1, "unrelated")


def test_shared_principal_does_not_share_distinct_owner_stores():
    first, second = service(context="first"), service(context="second")
    _, op, _ = granted(first)
    with pytest.raises(ConsentError):
        second.inspect(op["operation_id"])


def test_reusing_store_with_forged_context_identity_fails():
    store = MemoryStateStore()
    granted(service(store=store))
    with pytest.raises(ConsentError, match="another"):
        service(store=store, context="forged").summary()


def test_child_replacement_keeps_owner_state_but_owner_close_ends_it():
    store = MemoryStateStore()
    _, op, _ = granted(service(store=store))
    assert service(store=store).state("document.write-body", operation_id=op["operation_id"]) == "authorised"
    store.close()
    with pytest.raises(StoreClosedError):
        service(store=store).summary()


def test_permission_generation_changes_cannot_resurrect_grants():
    store = MemoryStateStore()
    first = service(store=store)
    _, op, _ = granted(first)
    service(store=store, generation="config-2").summary()
    restored = service(store=store, generation="config-1")
    assert restored.state("document.write-body", operation_id=op["operation_id"]) == "authorisation_required"


def test_blanket_invalidates_on_command_version_change():
    store = MemoryStateStore()
    svc = service(store=store)
    svc.request(request_id="blanket", scope=ConsentScope.COMMAND,
                command_id="artefact.delete", review=svc.command_review("artefact.delete"))
    changed = service(store=store, versions=dict(VERSIONS, **{"artefact.delete": 2}))
    assert changed.state("artefact.delete") == "authorisation_required"


def test_permission_checked_before_any_preparation_is_retained():
    svc = service()
    svc.policy = replace(svc.policy, permissions=frozenset({"artefact.read"}),
                         initial=frozenset({"artefact.read"}))
    with pytest.raises(ConsentError, match="administrator"):
        svc.prepare(binding(), request_id="forbidden")
    assert svc.store.list_keys(prefix="consent/operation/").keys == ()


def test_standalone_and_reserved_controls_cannot_gain_exceptional_grants():
    with pytest.raises(ConsentError, match="session run"):
        granted(service(kind="standalone"))
    with pytest.raises(ConsentError, match="Session controls"):
        granted(service(), command="access.status")


def test_audit_intent_failure_prevents_admission_and_preserves_consent():
    svc = service()
    bound, op, _ = granted(svc)

    def fail(_proof):
        raise OSError("audit unavailable")

    with pytest.raises(OSError):
        svc.admit("document.write-body", 1, "failed", operation_id=op["operation_id"],
                  binding=bound, before_enter=fail)
    assert svc.state("document.write-body", operation_id=op["operation_id"]) == "authorised"


def test_reduce_never_restores_initial_access():
    svc = service()
    assert svc.reduce(commands=("artefact.read",))
    assert svc.state("artefact.read") == "authorisation_required"
    assert not svc.reduce()
    assert svc.state("artefact.read") == "authorisation_required"


def test_repeated_preparation_is_immutable_and_does_not_allocate_again():
    svc = service()
    one = svc.prepare(binding(), request_id="same")
    assert svc.prepare(binding(), request_id="same") == one
    with pytest.raises(ConsentError):
        svc.prepare(binding(value="other"), request_id="same")
    assert len(svc.store.list_keys(prefix="consent/operation/").keys) == 1


def test_prepared_capacity_refuses_without_evicting_descriptors():
    svc = service()
    first = svc.prepare(binding(), request_id="0")
    for index in range(1, 128):
        svc.prepare(binding(), request_id=str(index))
    with pytest.raises(ConsentError, match="limit"):
        svc.prepare(binding(), request_id="overflow")
    assert svc.inspect(first["operation_id"])["digest"] == first["digest"]
    svc.reduce(operation_ids=(first["operation_id"],))
    svc.prepare(binding(), request_id="after-discard")


def test_large_required_review_is_rejected_not_truncated():
    svc = service()
    too_large = replace(binding(), review_json=canonical_json({"review": "x" * 8000}))
    with pytest.raises(ConsentError, match="split"):
        svc.prepare(too_large, request_id="large")


@pytest.mark.parametrize("specific", [False, True])
def test_command_version_restore_never_restores_consent(specific):
    store = MemoryStateStore()
    svc = service(store=store)
    selector = {}
    if specific:
        _, op, _ = granted(svc, command="artefact.delete")
        selector = {"operation_id": op["operation_id"]}
    else:
        svc.request(request_id="blanket", scope=ConsentScope.COMMAND,
                    command_id="artefact.delete", review=svc.command_review("artefact.delete"))
    service(store=store, versions=dict(VERSIONS, **{"artefact.delete": 2})).summary()
    restored = service(store=store)
    assert restored.state("artefact.delete", **selector) == "authorisation_required"
    if not specific:
        assert restored.request(request_id="blanket", scope=ConsentScope.COMMAND,
                                command_id="artefact.delete",
                                review=restored.command_review("artefact.delete")).state == "denied"


@pytest.mark.parametrize("change", ["policy", "permissions"])
def test_request_revalidates_policy_at_commit(change):
    svc = service()
    original = svc.policy
    calls = 0

    def refresh():
        nonlocal calls
        calls += 1
        policy = original
        if calls >= 2:
            policy = (replace(original, request_policy="denied") if change == "policy" else
                      replace(original, permissions=original.permissions - {"artefact.delete"}))
        return replace(svc.identity, permission_generation=str(calls >= 2)), policy

    svc._refresh = refresh
    with pytest.raises(ConsentError):
        svc.request(request_id="racing-policy", scope=ConsentScope.COMMAND,
                    command_id="artefact.delete", review=svc.command_review("artefact.delete"))
    assert svc.grant_page()["items"] == []


@pytest.mark.parametrize("clear", [False, True])
def test_revoked_descriptor_can_be_discarded_and_retry_is_compact(clear):
    svc = service()
    _, op, grant = granted(svc)
    if clear:
        svc.reduce(clear_grants=True)
    else:
        svc.reduce(grant_ids=(grant.grant_id, grant.grant_id))
    svc.reduce(operation_ids=(op["operation_id"], op["operation_id"]))
    context = svc.store.snapshot(("consent/context",)).values["consent/context"]
    assert context["grant_count"] == context["prepared_count"] == 0
    replay = svc.prepare(binding(), request_id="prepare-request-1")
    assert replay == {"operation_id": op["operation_id"], "digest": op["digest"], "state": "discarded"}
    keys = svc.store.list_keys(prefix="consent/preparation-request/").keys
    assert "review" not in next(iter(svc.store.snapshot(keys).values.values()))


def test_intent_runs_once_after_reservation_wins_cas(monkeypatch):
    svc = service()
    bound, op, _ = granted(svc)
    original = svc.store.compare_exchange
    failed = False

    def conflict_once(expected, writes):
        nonlocal failed
        if not failed and any(isinstance(value, dict) and value.get("state") == "reserved"
                              for value in writes.values()):
            failed = True
            return False
        return original(expected, writes)

    monkeypatch.setattr(svc.store, "compare_exchange", conflict_once)
    intents = []
    svc.admit("document.write-body", 1, "one-intent", operation_id=op["operation_id"],
              binding=bound, before_enter=intents.append)
    assert failed and len(intents) == 1


def test_losing_contenders_do_not_write_admission_intents():
    svc = service()
    bound, op, _ = granted(svc)
    intents = []

    def invoke(number):
        try:
            svc.admit("document.write-body", 1, str(number), operation_id=op["operation_id"],
                      binding=bound, before_enter=intents.append)
        except ConsentError:
            pass

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(invoke, range(20)))
    assert len(intents) == 1


def test_global_revocation_preserves_reserved_work_and_removes_unused_authority():
    svc = service()
    bound, op, _ = granted(svc)
    proof = svc.admit("document.write-body", 1, "interrupted", operation_id=op["operation_id"], binding=bound)
    svc.request(request_id="blanket", scope=ConsentScope.COMMAND,
                command_id="artefact.delete", review=svc.command_review("artefact.delete"))
    assert svc.reduce(clear_grants=True)
    assert svc.state("artefact.delete") == "authorisation_required"
    assert svc.inspect(op["operation_id"])["state"] == "entered"
    svc.finish(proof)
    svc.reduce(clear_grants=True, operation_ids=(op["operation_id"],))
    assert svc.grant_page()["items"] == []


def test_pre_entry_refund_cannot_undo_concurrent_revocation():
    svc = service()
    bound, op, _ = granted(svc)

    def revoke_then_fail(_proof):
        svc.reduce(clear_grants=True)
        raise OSError("intent unavailable")

    with pytest.raises(OSError):
        svc.admit("document.write-body", 1, "revoked-before-entry",
                  operation_id=op["operation_id"], binding=bound,
                  before_enter=revoke_then_fail)
    assert svc.state("document.write-body", operation_id=op["operation_id"]) == "authorisation_required"
    assert svc.inspect(op["operation_id"])["state"] == "revoked"


@pytest.mark.parametrize("basis", ["operation", "command", "initial"])
def test_successful_intent_cannot_cross_a_new_revocation(basis):
    svc = service(initial=VERSIONS if basis == "initial" else None)
    selectors = {}
    command = "artefact.delete"
    if basis == "operation":
        bound, op, _ = granted(svc, command=command)
        selectors = {"operation_id": op["operation_id"], "binding": bound}
    elif basis == "command":
        svc.request(request_id="blanket", scope=ConsentScope.COMMAND,
                    command_id=command, review=svc.command_review(command))
    def revoke(_proof):
        svc.reduce(clear_grants=True, commands=(command,))
    with pytest.raises(ConsentError, match="revoked|reduced"):
        svc.admit(command, 1, "revoked-after-intent", before_enter=revoke, **selectors)
    assert svc.state(command) == "authorisation_required"


def test_owner_close_during_intent_never_returns_admission():
    svc = service()
    bound, op, _ = granted(svc)
    with pytest.raises(StoreClosedError):
        svc.admit("document.write-body", 1, "owner-ended", operation_id=op["operation_id"],
                  binding=bound, before_enter=lambda _: svc.store.close())


def test_review_budget_counts_escaping_in_the_actual_tool_string():
    svc = service()
    escaped = replace(binding(), review_json=canonical_json({"quoted": "\\" * 2000}))
    assert len(escaped.review_json.encode()) < 7000
    with pytest.raises(ConsentError, match="too large"):
        svc.prepare(escaped, request_id="escaped")


def test_preparation_retry_reuses_descriptor_without_resolving_changed_resources():
    from _application.preparation import content_digest
    svc = service()
    bound = binding()
    created = svc.prepare(bound, request_id="prepared", pin_namespace="private-copy")
    assert svc.preparation_retry("prepared", content_digest(bound.request_json)) == created
    assert svc.inspect(created["operation_id"])["pin_namespace"] == "private-copy"
    svc.reduce(operation_ids=(created["operation_id"],))
    assert svc.preparation_retry("prepared", content_digest(bound.request_json))["state"] == "discarded"
    with pytest.raises(ConsentError, match="differs"):
        svc.preparation_retry("prepared", content_digest(binding(value="different").request_json))


def test_explicit_specific_selector_spends_even_when_command_is_initial():
    svc = service(initial=VERSIONS)
    bound, operation, decision = granted(svc)
    proof = svc.admit(bound.command_id, bound.command_version, "specific-with-initial",
                      operation_id=operation["operation_id"], binding=bound)
    assert proof.basis == "operation"
    assert proof.grant_id == decision.grant_id
    svc.finish(proof)
    with pytest.raises(ConsentError):
        svc.admit(bound.command_id, bound.command_version, "repeat-specific",
                  operation_id=operation["operation_id"], binding=bound)
    assert svc.admit(bound.command_id, bound.command_version, "ordinary").basis == "initial"
