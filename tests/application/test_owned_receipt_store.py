"""Owned admission intent and final outcome persistence contracts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from _application.receipts import (
    AdmissionIntent, CommittedEffect, ExecutionState, InvocationOutcome,
    OutcomeReceipt, OutcomeReference, ReceiptLookupState, ReceiptOwnership,
    ReceiptOwnershipError, ReceiptPolicy, ReceiptState,
    ReceiptIntentConflict,
)
from _command_interface import receipts


NOW = datetime.fromisoformat("2026-09-14T08:00:00+10:00")
OWNER = ReceiptOwnership("brain", "operator:rob", "mcp-instance", "context-one")


class Clock:
    def __init__(self):
        self.value = NOW

    def now(self):
        return self.value


@pytest.fixture
def root(tmp_path):
    root = tmp_path.resolve() / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core" / "VERSION").write_text("0.67.1\n")
    return root


def intent(identifier="one", **changes):
    value = AdmissionIntent(OutcomeReference(identifier), "retrieval.evaluate", 1,
                            NOW, "operation", "generation-one", "host-request",
                            "grant-one", "operation-one", "sha256:" + "a" * 64,
                            "request-one")
    return replace(value, **changes)


def outcome(identifier="one", execution=ExecutionState.SUCCEEDED, state=ReceiptState.NONE):
    effects = ((CommittedEffect("document", "Notes/One.md"),)
               if state in {ReceiptState.COMMITTED, ReceiptState.KNOWN_PARTIAL} else ())
    return InvocationOutcome(OutcomeReceipt(OutcomeReference(identifier), "retrieval.evaluate", 1,
                                            state, NOW + timedelta(seconds=1), effects), execution)


def store(root, owner=OWNER, clock=None, policy=None):
    return receipts.OwnedReceiptStore(root, clock or Clock(), owner, policy)


def path(root, identifier="one", suffix=".json"):
    return root / receipts.OWNED_RECEIPT_DIRECTORY / (hashlib.sha256(identifier.encode()).hexdigest() + suffix)


def test_intent_alone_survives_process_recreation_as_unknown(root):
    store(root).begin(intent())
    found = store(root).read(OutcomeReference("one"))
    assert found.state is ReceiptLookupState.STILL_UNKNOWN
    assert found.intent == intent()
    assert found.outcome is None
    assert not path(root, suffix=".outcome").exists()


@pytest.mark.parametrize("execution,state", [
    (ExecutionState.SUCCEEDED, ReceiptState.NONE),
    (ExecutionState.SUCCEEDED, ReceiptState.COMMITTED),
    (ExecutionState.FAILED, ReceiptState.NONE),
    (ExecutionState.PARTIAL, ReceiptState.KNOWN_PARTIAL),
    (ExecutionState.UNKNOWN, ReceiptState.UNKNOWN),
    (ExecutionState.UNKNOWN, ReceiptState.NONE),
])
def test_separate_immutable_outcome_round_trip(root, execution, state):
    saved = store(root)
    saved.begin(intent())
    before = path(root).read_bytes()
    result = outcome(execution=execution, state=state)
    saved.finalise(result)
    assert path(root).read_bytes() == before
    assert path(root, suffix=".outcome").is_file()
    lookup = store(root).read(OutcomeReference("one"))
    assert lookup.state is ReceiptLookupState.FOUND
    assert lookup.intent == intent()
    assert lookup.outcome == result


def test_identical_retries_are_idempotent_and_conflicts_refused(root):
    saved = store(root)
    assert saved.begin(intent()) is True
    saved.finalise(outcome())
    before = (path(root).read_bytes(), path(root, suffix=".outcome").read_bytes())
    assert saved.begin(intent()) is False
    saved.finalise(outcome())
    assert before == (path(root).read_bytes(), path(root, suffix=".outcome").read_bytes())
    with pytest.raises(ValueError, match="immutable"):
        saved.begin(intent(request_id="different"))
    with pytest.raises(ValueError, match="immutable"):
        saved.finalise(outcome(execution=ExecutionState.FAILED))


def test_concurrent_identical_intents_have_one_entry_claim(root):
    from threading import Barrier

    ready = Barrier(8)

    def claim(_):
        caller = store(root)
        ready.wait()
        return caller.begin(intent())

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(claim, range(8)))
    assert results.count(True) == 1
    assert results.count(False) == 7
    assert store(root).read(OutcomeReference('one')).intent == intent()


def test_later_timestamp_conflicts_with_owned_intent_without_replacing_it(root):
    saved = store(root)
    assert saved.begin(intent()) is True
    with pytest.raises(ReceiptIntentConflict, match='immutable'):
        saved.begin(intent(recorded_at=NOW + timedelta(seconds=1)))
    assert saved.read(OutcomeReference('one')).intent == intent()


@pytest.mark.parametrize("foreign", [
    replace(OWNER, brain_id="other-brain"),
    replace(OWNER, principal="operator:other"),
    replace(OWNER, context_id="context-other"),
    replace(OWNER, kind="cli-job"),
    ReceiptOwnership("brain", "operator:rob", "standalone"),
])
def test_foreign_read_begin_and_finalisation_are_denied(root, foreign):
    store(root).begin(intent())
    caller = store(root, foreign)
    for action in (lambda: caller.read(OutcomeReference("one")),
                   lambda: caller.begin(intent()), lambda: caller.finalise(outcome())):
        with pytest.raises(ReceiptOwnershipError):
            action()


def test_standalone_receipts_recover_without_a_reusable_context(root):
    owner = ReceiptOwnership("brain", "operator:rob", "standalone")
    store(root, owner).begin(intent(basis="initial", grant_id=None, operation_id=None,
                                    operation_digest=None, source="cli-request"))
    store(root, owner).finalise(outcome())
    assert store(root, owner).read(OutcomeReference("one")).outcome == outcome()
    with pytest.raises(ValueError, match="reusable"):
        replace(owner, context_id="pretend-job")


def test_legacy_receipts_cannot_be_read_or_adopted(root):
    receipts.FileReceiptStore(root, Clock()).write(outcome(state=ReceiptState.COMMITTED).receipt)
    owned = store(root)
    for action in (lambda: owned.read(OutcomeReference("one")),
                   lambda: owned.begin(intent()), lambda: owned.finalise(outcome())):
        with pytest.raises(ReceiptOwnershipError, match="Legacy"):
            action()


def test_missing_read_creates_no_ledger_and_never_claims_no_effects(root):
    result = store(root).read(OutcomeReference("missing"))
    assert result.state is ReceiptLookupState.STILL_UNKNOWN
    assert result.intent is result.outcome is None
    assert not (root / receipts.RECEIPT_DIRECTORY).exists()


def test_null_record_is_corruption_not_absence(root):
    saved = store(root)
    saved.begin(intent())
    path(root).write_text("null")
    with pytest.raises(ValueError, match="invalid outcome receipt"):
        saved.read(OutcomeReference("one"))


def test_final_requires_matching_unexpired_intent(root):
    saved = store(root)
    with pytest.raises(ValueError, match="existing admission"):
        saved.finalise(outcome())
    saved.begin(intent())
    wrong = replace(outcome(), receipt=replace(outcome().receipt, command_id="artefact.delete"))
    with pytest.raises(ValueError, match="differs"):
        saved.finalise(wrong)
    early = replace(outcome(), receipt=replace(outcome().receipt, recorded_at=NOW - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="predates"):
        saved.finalise(early)
    clock = Clock()
    clock.value += timedelta(days=8)
    with pytest.raises(ValueError, match="expired"):
        store(root, clock=clock).finalise(outcome())


def test_retention_and_capacity_are_global_per_invocation_across_owners(root):
    clock = Clock()
    policy = ReceiptPolicy(retention=timedelta(minutes=5), max_records=2)
    for number in range(3):
        clock.value = NOW + timedelta(minutes=number)
        saved = store(root, replace(OWNER, context_id=f"context-{number}"), clock, policy)
        saved.begin(intent(str(number), recorded_at=clock.value))
        result = outcome(str(number))
        saved.finalise(replace(result, receipt=replace(result.receipt, recorded_at=clock.value)))
    assert not path(root, "0").exists()
    assert not path(root, "0", ".outcome").exists()
    assert len(list((root / receipts.OWNED_RECEIPT_DIRECTORY).glob("*.json"))) == 2
    clock.value = NOW + timedelta(minutes=8)
    caller = store(root, replace(OWNER, context_id="context-2"), clock, policy)
    assert caller.read(OutcomeReference("2")).state is ReceiptLookupState.STILL_UNKNOWN
    assert caller.cleanup() == 2
    assert not list((root / receipts.OWNED_RECEIPT_DIRECTORY).glob("*.outcome"))


def test_rebuild_index_preserves_pairs_and_normal_begin_avoids_inventory(root, monkeypatch):
    saved = store(root)
    saved.begin(intent())
    saved.finalise(outcome())
    index = root / receipts.OWNED_RECEIPT_DIRECTORY / receipts.RECEIPT_INDEX_NAME
    index.unlink()
    assert saved.cleanup() == 0
    assert saved.read(OutcomeReference("one")).outcome == outcome()
    monkeypatch.setattr(saved._ledger, "_records_locked", lambda: pytest.fail("unexpected full inventory"))
    saved.begin(intent("two"))


def test_independent_adapters_racing_conflicting_finalisation_have_one_winner(root):
    store(root).begin(intent())
    choices = [outcome(), outcome(execution=ExecutionState.FAILED)]
    def finish(result):
        try:
            store(root).finalise(result)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(finish, choices))
    assert sorted(results) == [False, True]
    assert store(root).read(OutcomeReference("one")).outcome in choices


@pytest.mark.parametrize("fail_at", ["intent", "outcome"])
def test_storage_failure_leaves_honest_unknown_state(root, monkeypatch, fail_at):
    saved = store(root)
    original = receipts.safe_write_json
    def fail(path_, value, **kwargs):
        if value.get("schema") == ("brain.admission-intent/1" if fail_at == "intent" else "brain.owned-invocation-outcome/1"):
            raise OSError("storage unavailable")
        return original(path_, value, **kwargs)
    monkeypatch.setattr(receipts, "safe_write_json", fail)
    if fail_at == "intent":
        with pytest.raises(OSError):
            saved.begin(intent())
    else:
        saved.begin(intent())
        with pytest.raises(OSError):
            saved.finalise(outcome())
    result = saved.read(OutcomeReference("one"))
    assert result.state is ReceiptLookupState.STILL_UNKNOWN
    assert result.outcome is None
    assert (result.intent is not None) == (fail_at == "outcome")


def test_final_ownership_tampering_is_rejected(root):
    saved = store(root)
    saved.begin(intent())
    saved.finalise(outcome())
    file = path(root, suffix=".outcome")
    raw = json.loads(file.read_text())
    raw["ownership"]["context_id"] = "different"
    file.write_text(json.dumps(raw))
    with pytest.raises(ReceiptOwnershipError):
        saved.read(OutcomeReference("one"))


def test_index_failure_cannot_publish_an_untracked_intent(root, monkeypatch):
    saved = store(root)
    saved.begin(intent("existing"))
    def fail(_index):
        raise OSError("index unavailable")
    monkeypatch.setattr(saved._ledger, "_write_index_locked", fail)
    with pytest.raises(OSError):
        saved.begin(intent("untracked"))
    assert not path(root, "untracked").exists()
    assert saved.read(OutcomeReference("untracked")).state is ReceiptLookupState.STILL_UNKNOWN


def test_crash_after_intent_publication_recovers_from_a_fresh_process(root):
    code = """
import os, sys
sys.path.insert(0, sys.argv[2])
from datetime import datetime
from pathlib import Path
from _application.receipts import AdmissionIntent, OutcomeReference, ReceiptOwnership
from _command_interface.receipts import OwnedReceiptStore
class Clock:
    def now(self):
        return datetime.fromisoformat('2026-09-14T08:00:00+10:00')
store = OwnedReceiptStore(Path(sys.argv[1]), Clock(), ReceiptOwnership('brain', 'operator:rob', 'mcp-instance', 'context-one'))
store.begin(AdmissionIntent(OutcomeReference('crashed'), 'retrieval.evaluate', 1, Clock().now(), 'initial', 'generation-one', 'host-request'))
os._exit(0)
"""
    scripts = Path(__file__).resolve().parents[2] / "src/brain-core/scripts"
    completed = subprocess.run([sys.executable, "-c", code, str(root), str(scripts)],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    lookup = store(root).read(OutcomeReference("crashed"))
    assert lookup.state is ReceiptLookupState.STILL_UNKNOWN
    assert lookup.intent.reference.invocation_id == "crashed"
    assert lookup.outcome is None


def test_intent_write_failure_stays_inside_global_inventory_capacity(root, monkeypatch):
    saved = store(root, policy=ReceiptPolicy(max_records=2))
    original = receipts.safe_write_json
    def fail(path_, value, **kwargs):
        if value.get("schema") == "brain.admission-intent/1":
            raise OSError("intent unavailable")
        return original(path_, value, **kwargs)
    monkeypatch.setattr(receipts, "safe_write_json", fail)
    for number in range(5):
        with pytest.raises(OSError):
            saved.begin(intent(str(number), recorded_at=NOW + timedelta(seconds=number)))
    index = json.loads((root / receipts.OWNED_RECEIPT_DIRECTORY / receipts.RECEIPT_INDEX_NAME).read_text())
    assert len(index["records"]) == 2
    assert not list((root / receipts.OWNED_RECEIPT_DIRECTORY).glob("*.json"))


def test_audit_records_have_only_compact_contract_fields(root):
    saved = store(root)
    saved.begin(intent())
    saved.finalise(outcome())
    raw = json.loads(path(root).read_text())
    assert set(raw) == {"schema", "ownership", "intent"}
    assert set(raw["intent"]) == {"reference", "command_id", "command_version", "recorded_at",
                                  "basis", "generation", "source", "grant_id", "operation_id",
                                  "operation_digest", "request_id"}
    assert raw["intent"]["source"] == "host-request"
    assert len(path(root).read_bytes()) < 2000


@pytest.mark.parametrize("execution,state", [
    (ExecutionState.SUCCEEDED, ReceiptState.UNKNOWN),
    (ExecutionState.FAILED, ReceiptState.COMMITTED),
    (ExecutionState.PARTIAL, ReceiptState.NONE),
])
def test_execution_state_cannot_contradict_effects(execution, state):
    with pytest.raises(ValueError, match="contradicts"):
        outcome(execution=execution, state=state)


def test_permission_audit_round_trips_separate_immutable_before_and_after(root):
    from _application.receipts import PermissionChange
    change = PermissionChange('rob', 'operator', 'reader', (), ('artefact.create',), 'sha256:' + 'b' * 64)
    requested = intent(command_id='permission.set-profile', permission_change=change)
    finished = replace(outcome(), receipt=replace(outcome().receipt, command_id='permission.set-profile'),
                       config_revision='sha256:' + 'c' * 64)
    saved = store(root)
    saved.begin(requested)
    saved.finalise(finished)
    found = store(root).read(requested.reference)
    assert found.intent == requested
    assert found.outcome == finished
    with pytest.raises(ValueError, match='immutable'):
        saved.begin(replace(requested, permission_change=replace(change, after_profile='administrator')))
    with pytest.raises(ValueError, match='immutable'):
        saved.finalise(replace(finished, config_revision='sha256:' + 'd' * 64))
    assert 'hash' not in json.dumps(json.loads(path(root).read_text()))
