"""The maintenance pass, its invoker gate, list, advisory and log (DD-082, phases 2 and 3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from brain_test_support import folder_tree, link_folder, manifest_text, register_other_brain

from command_application import application_for, context_for
from _application.application import CommandApplication
from _application.maintenance import _detection, run as maintenance_run
from _bootstrap.maintenance_summary import GroupOutcome
from _application.maintenance.list import MaintenanceListRequest
from _application.maintenance.run import MaintenanceRunRequest, classify_result
from _application.receipts import CommittedEffect, OutcomeReference
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import CommandError, Error, ErrorCode, Ok, OutcomeUnknownDetails, Partial
from _application.runtime.remove_temporaries import RuntimeRemoveTemporariesRequest, TemporariesRemovalStatus
from _application.runtime.status import RuntimeStatusRequest
from _application.types import Authority, EffectClass, InitialAuthorisationClass, Projection, RetryClass
from _bootstrap import maintenance_summary, stranded_temporaries
from _bootstrap.maintenance_findings import Disposition, MaintenanceFinding, Owner, family_key
from _common import vault_mutation_lock
from _repair_common import REPAIR_SCOPES
import _command_interface.direct as direct_context


MAINTENANCE = ".brain/local/maintenance"
ROUTER = ".brain/local/compiled-router.json"
INDEX = ".brain/local/retrieval-index.json"
REGISTRY = ".brain/local/workspaces.json"
AUTOMATIC = ["router", "lexical", "temporaries", "registry"]


class _RealClock:
    def now(self):
        return datetime.now(timezone.utc)


class _Recorder:
    """A diagnostics port that keeps every maintenance record."""

    def __init__(self):
        self.records = []
        self.failures = []

    def report_failure(self, **failure):
        self.failures.append(failure)

    def record(self, event, *, family, **fields):
        self.records.append((event, family, fields))


class _Sibling:
    """Invoke each automatic family through the real application boundary."""

    def __init__(self, root, **options):
        self.root, self.options, self.calls = root, options, []

    def repair(self, family, *, invocation_id):
        self.calls.append((family.command_id, invocation_id))
        request = current_request_resolver().resolve(family.command_id, dict(family.request))
        return application_for(self.root, invocation_id=invocation_id, context_kind="standalone",
                               clock=_RealClock(), **self.options).invoke(request)


class _Scripted:
    """Return scripted results per scope without touching the vault."""

    def __init__(self, results):
        self.results, self.calls = results, []

    def repair(self, family, *, invocation_id):
        scope = next(scope for scope, item in REPAIR_SCOPES.items() if item is family)
        self.calls.append(scope)
        value = self.results[scope]
        return value(invocation_id) if callable(value) else value


def _invoke(root, request, invoker=None, *, diagnostics=None, dry_run=False, **options):
    context = context_for(root, context_kind="standalone", invocation_id=f"inv-{uuid.uuid4().hex[:8]}",
                          dry_run=dry_run, **options)
    context = replace(context, maintenance=invoker, clock=_RealClock())
    if diagnostics is not None:
        context = replace(context, diagnostics=diagnostics)
    context = replace(context, access=context.authorisation.bind(context))
    return CommandApplication(context, context.authorisation.catalogue).invoke(request)


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if ".brain" in path.parts or path.is_dir():
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    for cache in (ROUTER, INDEX):
        digest.update((root / cache).read_bytes())
    return digest.hexdigest()


def _strand(root: Path, name="retrieval-index.json.ab12cd34.tmp") -> Path:
    path = root / ".brain" / "local" / name
    path.write_text("stranded")
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (stale, stale))
    return path


@pytest.fixture
def young_temporaries_count(monkeypatch):
    """ctime cannot be aged in a fixture, so collapse the age rule."""
    monkeypatch.setattr(stranded_temporaries, "MINIMUM_AGE", timedelta(0))


def _last_pass(root):
    return json.loads((root / MAINTENANCE / "last-pass.json").read_text())


# ---------------------------------------------------------------------------
# Current-state admission (D8): one parametrised suite over the automatic set
# ---------------------------------------------------------------------------

def _seed(root: Path, scope: str) -> None:
    if scope == "router":
        (root / ROUTER).unlink()
    elif scope == "lexical":
        (root / INDEX).unlink()
    elif scope == "temporaries":
        _strand(root)
    elif scope == "registry":
        # A registered Brain with one agreeing link, one row its manifest contradicts and one invalid row.
        parent = root.parent / f"{root.name}-linked"
        parent.mkdir()
        _linked_folders(root, parent, {"agrees": manifest_text("agrees"), "rekeyed": manifest_text("another")})
        rows = json.loads((root / REGISTRY).read_text())
        rows["workspaces"]["relative"] = "foreign"
        (root / REGISTRY).write_text(json.dumps(rows))
    else:
        raise AssertionError(scope)


@pytest.mark.parametrize("scope", AUTOMATIC)
def test_run_twice_repairs_then_finds_nothing(command_vault_clone, young_temporaries_count, scope):
    root = command_vault_clone.vault_root
    _seed(root, scope)
    invoker = _Sibling(root)

    first = _invoke(root, MaintenanceRunRequest(), invoker)
    assert first.status == "ok", getattr(first, "error", None)
    assert {item.scope: item.outcome for item in first.result.groups}[scope] is GroupOutcome.REPAIRED
    assert any(effect.kind == REPAIR_SCOPES[scope].command_id for effect in first.committed_effects)
    assert _last_pass(root)["groups"][scope] == "repaired"

    before = _digest(root)
    calls = len(invoker.calls)
    second = _invoke(root, MaintenanceRunRequest(), invoker)

    assert second.status == "ok"
    assert all(item.outcome is GroupOutcome.ALREADY_CLEAN for item in second.result.groups)
    assert all(effect.kind == "maintenance.summary" for effect in second.committed_effects)
    assert _digest(root) == before
    assert len(invoker.calls) in {calls, calls + 1}, "a second pass either skips the family or its sibling is a noop"


@pytest.mark.parametrize("scope", AUTOMATIC)
def test_direct_call_on_a_clean_fixture_is_a_noop(command_vault_clone, young_temporaries_count, scope):
    root = command_vault_clone.vault_root
    if scope == "registry":
        # Clean means verified, not empty: a registered Brain with one agreeing link.
        parent = root.parent / f"{root.name}-linked"
        parent.mkdir()
        _linked_folders(root, parent, {"agrees": manifest_text("agrees")})
    family = REPAIR_SCOPES[scope]
    request = current_request_resolver().resolve(family.command_id, dict(family.request))

    result = application_for(root, clock=_RealClock()).invoke(request)

    assert result.status == "ok"
    assert result.committed_effects == ()


@pytest.mark.parametrize("scope", AUTOMATIC)
def test_state_change_between_detection_and_repair_is_honoured(command_vault_clone, young_temporaries_count, scope):
    root = command_vault_clone.vault_root
    _seed(root, scope)
    findings = _detection.detect(_invoke_context(root))
    assert any(item.scope == scope for item in findings)
    # Someone else fixes the condition after detection and before the repair runs.
    if scope == "temporaries":
        (root / ".brain/local/retrieval-index.json.ab12cd34.tmp").unlink()
    else:
        family = REPAIR_SCOPES[scope]
        request = current_request_resolver().resolve(family.command_id, dict(family.request))
        assert application_for(root, clock=_RealClock()).invoke(request).status == "ok"

    invoker = _Sibling(root)
    repaired = invoker.repair(REPAIR_SCOPES[scope], invocation_id="late")

    assert repaired.status == "ok"
    assert repaired.committed_effects == (), "the repair re-plans under its lock and changes nothing"


class _Mutating:
    """A sibling that changes the vault after detection and before each repair."""

    def __init__(self, root, mutate):
        self.inner, self.mutate = _Sibling(root), mutate

    def repair(self, family, *, invocation_id):
        self.mutate(family.scope)
        return self.inner.repair(family, invocation_id=invocation_id)


@pytest.mark.parametrize("scope", AUTOMATIC)
def test_a_condition_that_vanishes_during_the_pass_is_already_clean(command_vault_clone, young_temporaries_count, scope):
    root = command_vault_clone.vault_root
    _seed(root, scope)

    def fix(_scope):
        if scope == "temporaries":
            (root / ".brain/local/retrieval-index.json.ab12cd34.tmp").unlink()
        else:
            family = REPAIR_SCOPES[scope]
            request = current_request_resolver().resolve(family.command_id, dict(family.request))
            assert application_for(root, clock=_RealClock()).invoke(request).status == "ok"

    result = _invoke(root, MaintenanceRunRequest(), _Mutating(root, fix))

    assert result.status == "ok"
    assert {item.scope: item.outcome for item in result.result.groups}[scope] is GroupOutcome.ALREADY_CLEAN
    assert all(effect.kind == "maintenance.summary" for effect in result.committed_effects)


def _linked_folders(root: Path, tmp_path: Path, manifests: dict) -> dict:
    """Register this Brain and one linked folder per key; ``None`` means no manifest."""
    import vault_registry

    vault_registry.register(root, "brain")
    return {key: link_folder(root, tmp_path / f"linked-{key}", key, manifest) for key, manifest in manifests.items()}


def test_the_registry_pass_drops_only_rows_its_manifest_contradicts_and_writes_in_no_folder(command_vault_clone, tmp_path):
    import shutil
    import workspace_registry

    root = command_vault_clone.vault_root
    register_other_brain(tmp_path)
    folders = _linked_folders(root, tmp_path, {
        "agrees": manifest_text("agrees"),
        "rekeyed": manifest_text("another"),
        "elsewhere": manifest_text("elsewhere", brain="other"),
        "ghost": manifest_text("ghost", brain="not-on-this-machine"),
        "badkey": "brain: brain\nlinks:\n  workspace: ''\n",
        "unverifiable": None,
        "away": manifest_text("away"),
    })
    shutil.rmtree(folders["away"])
    before = {key: folder_tree(folder) for key, folder in folders.items()}

    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "ok", getattr(result, "error", None)
    assert {item.scope: item.outcome for item in result.result.groups}["registry"] is GroupOutcome.REPAIRED
    assert sorted(workspace_registry.load_registry(root)) == ["agrees", "away", "badkey", "ghost", "unverifiable"]
    assert {key: folder_tree(folder) for key, folder in folders.items()} == before, "the pass created nothing in any linked folder"
    second = _invoke(root, MaintenanceRunRequest(), _Sibling(root))
    assert "registry" not in {item.scope for item in second.result.groups}, "a second pass finds nothing to repair"


@pytest.mark.parametrize("change", ["now-agrees", "other-bytes", "other-brain-gone", "row-moved"])
def test_a_row_is_dropped_only_if_it_still_disagrees_under_the_lock(command_vault_clone, tmp_path, monkeypatch, change):
    """The repair classifies outside the lock; a change before its locked recheck keeps the row."""
    import shutil
    import workspace_registry
    from _portable import registry_maintenance

    root = command_vault_clone.vault_root
    other = register_other_brain(tmp_path)
    brain = "other" if change == "other-brain-gone" else "brain"
    folders = _linked_folders(root, tmp_path, {"stale": manifest_text("another", brain=brain)})
    manifest = folders["stale"] / ".brain" / "local" / "workspace.yaml"
    moved = (tmp_path / "moved").resolve()
    real = registry_maintenance._disagreeing

    def then_change(verification):
        planned = real(verification)
        assert [row.key for row in planned] == ["stale"], "the repair's own classification saw the disagreement"
        if change == "now-agrees":
            manifest.write_text(manifest_text("stale"))
        elif change == "other-bytes":
            manifest.write_text(manifest_text("yet-another"))
        elif change == "other-brain-gone":
            shutil.rmtree(other / ".brain-core")
        else:
            moved.mkdir()
            workspace_registry.save_registry(root, {"stale": {"path": str(moved)}})
        return planned

    monkeypatch.setattr(registry_maintenance, "_disagreeing", then_change)
    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "ok", getattr(result, "error", None)
    assert {item.scope: item.outcome for item in result.result.groups}["registry"] is GroupOutcome.ALREADY_CLEAN
    assert "stale" in workspace_registry.load_registry(root)


def test_a_held_vault_lock_defers_the_registry_repair(command_vault_clone, tmp_path, monkeypatch):
    import _bootstrap.file_lock as file_lock
    import _common
    import workspace_registry

    root = command_vault_clone.vault_root
    _linked_folders(root, tmp_path, {"rekeyed": "brain: brain\nlinks:\n  workspace: another\n"})
    real = file_lock.vault_mutation_lock
    monkeypatch.setattr(file_lock, "vault_mutation_lock",
                        lambda vault_root, *, timeout=30.0, create_parent=True: real(vault_root, timeout=0.1))
    monkeypatch.setattr(_common, "vault_mutation_lock", file_lock.vault_mutation_lock)
    with real(root):
        result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert {item.scope: item.outcome for item in result.result.groups} == {"registry": GroupOutcome.DEFERRED}
    assert "rekeyed" in workspace_registry.load_registry(root)


def test_a_condition_that_changes_during_the_pass_is_repaired_as_it_is_now(command_vault_clone, young_temporaries_count):
    root = command_vault_clone.vault_root
    _strand(root, "retrieval-index.json.ab12cd34.tmp")

    def swap(_scope):
        (root / ".brain/local/retrieval-index.json.ab12cd34.tmp").unlink()
        _strand(root, "compiled-router.json.ef56ab78.tmp")

    result = _invoke(root, MaintenanceRunRequest(), _Mutating(root, swap))

    assert result.status == "ok"
    assert {item.scope: item.outcome for item in result.result.groups} == {"temporaries": GroupOutcome.REPAIRED}
    assert [effect.subject for effect in result.committed_effects if effect.kind == "runtime.remove-temporaries"] == [
        ".brain/local/compiled-router.json.ef56ab78.tmp"], "the repair removed the file that is stranded now"


def _invoke_context(root):
    context = context_for(root, context_kind="standalone")
    return replace(context, clock=_RealClock())


def test_detection_matches_vault_check_and_identity_ignores_prose(command_vault_clone, young_temporaries_count):
    from _application.vault.check import VaultCheckRequest

    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()
    _strand(root)
    assert VaultCheckRequest.COMMAND_VERSION == 3

    detected = _detection.detect(_invoke_context(root))
    checked = application_for(root, clock=_RealClock()).invoke(VaultCheckRequest()).result.findings

    def shape(check, severity, file, scope):
        return (check, severity, file, scope)

    assert sorted(shape(item.check, item.severity, item.file, item.scope) for item in detected) == sorted(
        shape(item.check, item.severity.value, item.file, item.repair.scope if item.repair else None) for item in checked)

    raw = {"check": "workspace_contract", "code": "workspace_reference_missing", "severity": "error", "file": "Notes/a.md",
           "message": "first wording", "evidence": {"workspace": "workspace/x"}}
    first = _detection.classify((raw,))[0]
    second = _detection.classify(({**raw, "message": "second wording"},))[0]
    assert first.key == second.key
    from _bootstrap.maintenance_findings import group_by_family
    assert group_by_family((first,))[0].fingerprint == group_by_family((second,))[0].fingerprint


def test_held_lock_defers_the_group_and_every_later_lock_taking_group(command_vault_clone, young_temporaries_count, monkeypatch):
    root = command_vault_clone.vault_root
    (root / INDEX).unlink()
    _strand(root)
    import _bootstrap.file_lock as file_lock

    real = file_lock.vault_mutation_lock
    monkeypatch.setattr(file_lock, "vault_mutation_lock", lambda vault_root, *, timeout=30.0: real(vault_root, timeout=0.1))
    import _common
    monkeypatch.setattr(_common, "vault_mutation_lock", file_lock.vault_mutation_lock)
    invoker = _Sibling(root)
    with real(root):
        result = _invoke(root, MaintenanceRunRequest(), invoker)

    assert result.status == "ok"
    outcomes = {item.scope: item.outcome for item in result.result.groups}
    assert outcomes == {"lexical": GroupOutcome.DEFERRED, "temporaries": GroupOutcome.DEFERRED}
    assert [call[1].rsplit("-", 1)[1] for call in invoker.calls] == ["lexical"], (
        "after the first busy conflict no later lock-taking group is invoked")
    assert result.result.counts.deferred == 2
    assert _last_pass(root)["counts"]["deferred"] == 2


def test_clean_vault_runs_nothing_and_records_a_finalised_receipt(command_vault_clone):
    from command_application import _Receipts as ReceiptStore

    root = command_vault_clone.vault_root
    invoker = _Sibling(root)
    diagnostics = _Recorder()
    receipts = ReceiptStore()

    result = _invoke(root, MaintenanceRunRequest(), invoker, diagnostics=diagnostics, receipts=receipts)
    pass_receipts = [(intent.command_id, receipts.outcomes[key].execution.value if key in receipts.outcomes else None)
                     for key, intent in receipts.intents.items()]
    assert pass_receipts == [("maintenance.run", "succeeded")], "one finalised receipt for the pass itself"
    dry = _invoke(root, MaintenanceRunRequest(), invoker, dry_run=True, receipts=receipts)
    assert dry.status == "ok" and len(receipts.outcomes) == 2

    assert result.status == "ok" and invoker.calls == []
    assert result.result.groups == () and result.result.planned == ()
    assert [effect.kind for effect in result.committed_effects] == ["maintenance.summary"]
    assert _last_pass(root)["outcome"] == "ok"
    events = [event for event, _family, _fields in diagnostics.records]
    assert events == ["maintenance.pass_started", "maintenance.pass_finished"]
    assert all(family == "maintenance" for _event, family, _fields in diagnostics.records)

    dry = _invoke(root, MaintenanceRunRequest(), invoker, dry_run=True)
    assert dry.status == "ok" and dry.result.dry_run is True and dry.committed_effects == ()


def test_dry_run_reports_the_plan_and_writes_nothing(command_vault_clone, young_temporaries_count):
    root = command_vault_clone.vault_root
    (root / INDEX).unlink()
    _strand(root)
    invoker = _Sibling(root)

    result = _invoke(root, MaintenanceRunRequest(), invoker, dry_run=True)

    assert result.status == "ok"
    assert result.result.planned == ("lexical", "temporaries")
    assert result.result.groups == () and invoker.calls == []
    assert not (root / MAINTENANCE / "last-pass.json").exists()
    assert not (root / INDEX).exists()


def test_a_missing_router_short_circuits_every_other_check(command_vault_clone, young_temporaries_count):
    """Detection depends on the router, which is why router is never held (D4)."""
    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()
    _strand(root)

    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root), dry_run=True)

    assert result.result.planned == ("router",)


# ---------------------------------------------------------------------------
# Outcome classification and pass results
# ---------------------------------------------------------------------------

def _error(code, *, retryable=False, unknown=False):
    if unknown:
        reference = OutcomeReference("maint-x-router")
        return Error("runtime.refresh-router", 1,
                     CommandError(ErrorCode.COMMAND_OUTCOME_UNKNOWN, "lost", OutcomeUnknownDetails(reference)),
                     effects="unknown", outcome_reference=reference)
    return Error("runtime.refresh-router", 1, CommandError(code, "m"), retryable=retryable)


def test_outcome_classification_covers_all_seven_outcomes():
    effect = CommittedEffect("runtime.refresh-router", ROUTER)
    cases = {
        GroupOutcome.REPAIRED: Ok("runtime.refresh-router", 1, object(), (effect,)),
        GroupOutcome.ALREADY_CLEAN: Ok("runtime.refresh-router", 1, object()),
        GroupOutcome.PARTIAL: Partial("runtime.refresh-router", 1, CommandError(ErrorCode.CONFLICT, "half"), (effect,)),
        GroupOutcome.DEFERRED: _error(ErrorCode.CONFLICT, retryable=True),
        GroupOutcome.NEEDS_PERSON: _error(ErrorCode.AUTHORITY_DENIED),
        GroupOutcome.UNKNOWN: _error(None, unknown=True),
        GroupOutcome.FAILED: _error(ErrorCode.CONFLICT),
    }
    assert {classify_result(result)[0]: outcome for outcome, result in cases.items()} == {
        outcome: outcome for outcome in cases
    }
    assert classify_result(_error(ErrorCode.AUTHORISATION_REQUIRED))[0] is GroupOutcome.NEEDS_PERSON
    assert classify_result(_error(ErrorCode.INTERNAL_ERROR))[0] is GroupOutcome.FAILED


def test_partial_sibling_makes_the_pass_partial_with_every_effect(command_vault_clone):
    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()
    effect = CommittedEffect("runtime.refresh-router", ROUTER)
    invoker = _Scripted({"router": Partial("runtime.refresh-router", 1, CommandError(ErrorCode.CONFLICT, "verify"), (effect,))})

    result = _invoke(root, MaintenanceRunRequest(), invoker)

    assert result.status == "partial"
    assert "router partial" in result.error.message
    assert result.committed_effects[0] == effect
    assert result.committed_effects[-1].kind == "maintenance.summary"
    assert _last_pass(root)["outcome"] == "partial"
    assert _last_pass(root)["groups"]["router"] == "partial"


def test_failed_sibling_is_counted_and_simply_found_again(command_vault_clone):
    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()
    invoker = _Scripted({"router": _error(ErrorCode.CONFLICT)})

    result = _invoke(root, MaintenanceRunRequest(), invoker)

    assert result.status == "partial"
    assert _last_pass(root)["counts"]["failed"] == 1
    assert not (root / ROUTER).exists()
    # Nothing records the failure for correctness: the next pass just repairs it.
    repaired = _invoke(root, MaintenanceRunRequest(), _Sibling(root))
    assert repaired.status == "ok" and (root / ROUTER).exists()


def test_unknown_sibling_is_reported_unknown_naming_the_sibling_and_never_followed_up(command_vault_clone, young_temporaries_count):
    root = command_vault_clone.vault_root
    (root / INDEX).unlink()
    _strand(root)

    def unknown(invocation_id):
        reference = OutcomeReference(invocation_id)
        return Error("retrieval.refresh-lexical", 1,
                     CommandError(ErrorCode.COMMAND_OUTCOME_UNKNOWN, "lost", OutcomeUnknownDetails(reference)),
                     effects="unknown", outcome_reference=reference)

    temp_effect = CommittedEffect("runtime.remove-temporaries", ".brain/local/retrieval-index.json.ab12cd34.tmp")
    invoker = _Scripted({"lexical": unknown, "temporaries": Ok("runtime.remove-temporaries", 1, object(), (temp_effect,))})

    result = _invoke(root, MaintenanceRunRequest(), invoker)

    assert result.status == "error"
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN and result.effects == "unknown"
    assert result.outcome_reference.invocation_id.endswith("-lexical")
    assert temp_effect.subject in result.error.message
    summary = _last_pass(root)
    assert summary["outcome"] == "error" and summary["groups"]["lexical"] == "unknown"
    assert summary["counts"]["failed"] == 1

    # The next pass detects whatever is still wrong and repairs it; nothing settles the unknown.
    repaired = _invoke(root, MaintenanceRunRequest(), _Sibling(root))
    assert repaired.status == "ok"
    assert {item.scope: item.outcome for item in repaired.result.groups}["lexical"] is GroupOutcome.REPAIRED


def test_last_pass_write_failure_after_a_commit_is_partial_with_a_warning(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()

    def fail(_path, _summary):
        raise OSError("disk full")

    monkeypatch.setattr(maintenance_run, "write_last_pass", fail)
    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "partial"
    assert result.warnings[0].code.value == "follow_up_required"
    assert "disk full" in result.warnings[0].message
    assert [effect.kind for effect in result.committed_effects] == ["runtime.refresh-router"]


def test_detection_failure_blocks_last_pass_and_re_emits_conflict(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root

    def broken(_context):
        raise _detection.DetectionFailed("router payload is corrupt")

    monkeypatch.setattr("_application.maintenance._items.detect", broken)
    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "error" and result.error.code is ErrorCode.CONFLICT
    assert "corrupt" in result.error.message
    summary = _last_pass(root)
    assert summary["blocked"] == "detection_failed" and summary["groups"] == {}
    assert summary["counts"] == {"needs_person": 0, "claim_expired": 0, "failed": 0, "deferred": 0}


def test_unreadable_decisions_block_a_real_pass_but_a_dry_run_writes_nothing(command_vault_clone):
    root = command_vault_clone.vault_root
    (root / MAINTENANCE).mkdir(parents=True)
    (root / MAINTENANCE / "decisions.json").write_text("{nope")

    dry = _invoke(root, MaintenanceRunRequest(), _Sibling(root), dry_run=True)
    assert dry.status == "error" and dry.error.code is ErrorCode.INTERNAL_ERROR
    assert "decisions.json" in dry.error.message
    assert not (root / MAINTENANCE / "last-pass.json").exists()

    real = _invoke(root, MaintenanceRunRequest(), _Sibling(root))
    assert real.status == "error"
    assert _last_pass(root)["blocked"] == "decisions_unreadable"


def test_refused_sibling_is_needs_person(command_vault_clone, young_temporaries_count):
    root = command_vault_clone.vault_root
    _strand(root)
    allowed = {entry.command_id for entry in current_application_catalogue().entries} - {"runtime.remove-temporaries"}
    invoker = _Sibling(root, allowed_commands=allowed)

    result = _invoke(root, MaintenanceRunRequest(), invoker)

    assert result.status == "ok"
    assert {item.scope: item.outcome for item in result.result.groups} == {"temporaries": GroupOutcome.NEEDS_PERSON}
    assert result.result.counts.needs_person == 1
    assert [item.scope for item in result.result.attention] == ["temporaries"]
    assert (root / ".brain/local/retrieval-index.json.ab12cd34.tmp").exists()


def test_overlapping_passes_exit_busy(command_vault_clone):
    root = command_vault_clone.vault_root
    from _bootstrap.file_lock import exclusive_file_lock

    (root / MAINTENANCE).mkdir(parents=True)
    with exclusive_file_lock(root / MAINTENANCE / "pass.lock"):
        result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "error" and result.error.code is ErrorCode.CONFLICT and result.retryable


def test_pass_without_the_invoker_is_capability_unavailable(command_vault_clone):
    root = command_vault_clone.vault_root

    result = _invoke(root, MaintenanceRunRequest(), None)

    assert result.status == "error" and result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert "maintenance run" in result.error.next_action.instruction
    assert result.error.details.missing == ("provider:maintenance_invoker",)


def test_semantic_degradation_is_reported_once_after_a_cache_rebuild(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    from _semantic.config import set_semantic_engine_installed

    (root / ROUTER).unlink()
    set_semantic_engine_installed(root, installed=True)
    assert _detection.semantic_retrieval_configured(root) is True

    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "ok" and result.result.semantic_degraded is True
    semantic = [item for item in result.result.attention if item.scope == "semantic"]
    assert len(semantic) == 1 and semantic[0].code == "pass_cleared_embeddings"
    assert semantic[0].key == family_key("brain", "semantic")
    assert result.result.counts.needs_person == 1

    # With a detected semantic group the pass reports it once, under the same key.
    real_detect = _detection.detect
    semantic_finding = MaintenanceFinding("semantic:x", "warning", None, "degraded", Disposition.JUDGEMENT, scope="semantic",
                                          owner=Owner.BRAIN, key=family_key("brain", "semantic"))
    monkeypatch.setattr("_application.maintenance._items.detect", lambda context: (*real_detect(context), semantic_finding))
    (root / ROUTER).unlink()
    again = _invoke(root, MaintenanceRunRequest(), _Sibling(root))
    assert again.status == "ok"
    assert [item.key for item in again.result.attention if item.scope == "semantic"] == [family_key("brain", "semantic")]
    assert again.result.counts.needs_person == 1


def test_host_change_warns(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    monkeypatch.setattr(maintenance_run, "host_name", lambda: "laptop")
    assert _invoke(root, MaintenanceRunRequest(), _Sibling(root)).warnings == ()
    monkeypatch.setattr(maintenance_run, "host_name", lambda: "desktop")

    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))

    assert result.status == "ok"
    assert "laptop" in result.warnings[0].message and "desktop" in result.warnings[0].message


def test_machine_owned_findings_are_listed_as_see_doctor_and_not_counted(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    real_detect = _detection.detect
    machine = MaintenanceFinding("runtime:runtime-missing", "warning", None, "no runtime", Disposition.JUDGEMENT, scope="runtime",
                                 owner=Owner.MACHINE, key=family_key("brain", "runtime"))
    monkeypatch.setattr("_application.maintenance._items.detect", lambda context: (*real_detect(context), machine))

    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root))
    listed = _invoke(root, MaintenanceListRequest())

    assert result.result.counts.needs_person == 0
    assert [(item.owner, item.command) for item in result.result.attention] == [("machine", "see the machine pass: brain machine-maintenance list")]
    assert [(item.owner, item.command) for item in listed.result.items] == [("machine", "see the machine pass: brain machine-maintenance list")]


# ---------------------------------------------------------------------------
# Context gate: who gets the invoker
# ---------------------------------------------------------------------------

def _gate_vault(tmp_path, monkeypatch):
    vault = (tmp_path / "Gate").resolve()
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.70.10\n")
    (vault / ".brain").mkdir()
    import config as brain_config

    (vault / ".brain" / "config.yaml").write_text(
        "vault:\n  profiles:\n    operator:\n      allow: [maintenance.run, runtime.refresh-router]\n"
        "  operators:\n    - id: rob\n      profile: operator\n      auth:\n        type: key\n"
        f"        hash: {brain_config.hash_key('secret')}\n"
        "defaults:\n  default_profile: operator\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    return vault


def test_only_a_standalone_keyless_script_context_gets_the_invoker(tmp_path, monkeypatch):
    from _bootstrap.owner_attachment import ProcessIdentity

    vault = _gate_vault(tmp_path, monkeypatch)
    catalogue = current_application_catalogue()

    standalone = direct_context.compose_direct_context(vault_root=vault, command_id="maintenance.run",
                                                       catalogue=catalogue, maintenance_invoker=True)
    assert isinstance(standalone.maintenance, direct_context.DirectMaintenanceInvoker)

    not_opted_in = direct_context.compose_direct_context(vault_root=vault, command_id="maintenance.run", catalogue=catalogue)
    assert not_opted_in.maintenance is None

    keyed = direct_context.compose_direct_context(vault_root=vault, command_id="maintenance.run", catalogue=catalogue,
                                                  operator_key="secret", maintenance_invoker=True)
    assert keyed.maintenance is None

    job = direct_context.DirectContextComposer(vault_root=vault, catalogue=catalogue,
                                               transport_identity=ProcessIdentity("cli-job", "ctx-1"))
    assert job.compose(command_id="maintenance.run", maintenance_invoker=True).maintenance is None
    keyed_job = direct_context.DirectContextComposer(vault_root=vault, catalogue=catalogue, operator_key="secret",
                                                     transport_identity=ProcessIdentity("cli-job", "ctx-3"))
    assert keyed_job.compose(command_id="maintenance.run", maintenance_invoker=True).maintenance is None
    instance = direct_context.DirectContextComposer(vault_root=vault, catalogue=catalogue,
                                                    transport_identity=ProcessIdentity("mcp-instance", "ctx-2"))
    assert instance.compose(command_id="maintenance.run", maintenance_invoker=True).maintenance is None


def test_list_and_dismiss_work_inside_a_cli_job_where_the_pass_is_refused(command_vault_clone, monkeypatch):
    from _bootstrap.owner_attachment import ProcessIdentity
    from _application.maintenance.dismiss import MaintenanceDismissRequest

    root = command_vault_clone.vault_root
    judgement = MaintenanceFinding("workspace_contract", "error", "Notes/a.md", "gone", Disposition.JUDGEMENT,
                                   code="workspace_reference_missing", owner=Owner.BRAIN,
                                   key=_detection.finding_key("brain", "workspace_contract:workspace_reference_missing",
                                                              {"file": "Notes/a.md"}),
                                   subject={"file": "Notes/a.md"}, evidence={"workspace": "workspace/x"})
    monkeypatch.setattr("_application.maintenance._items.detect", lambda context: (judgement,))
    composer = direct_context.DirectContextComposer(vault_root=root, catalogue=current_application_catalogue(),
                                                    transport_identity=ProcessIdentity("cli-job", "job-1"))

    def run(request, **kwargs):
        context = composer.compose(command_id=request.COMMAND_ID, invocation_id=f"job-{uuid.uuid4().hex[:8]}",
                                   maintenance_invoker=True, **kwargs)
        return CommandApplication(context, composer.catalogue).invoke(request)

    listed = run(MaintenanceListRequest())
    assert listed.status == "ok", getattr(listed, "error", None)
    item = listed.result.items[0]
    dismissed = run(MaintenanceDismissRequest(item.key, item.fingerprint, "known", "job"))
    assert dismissed.status == "ok" and dismissed.result.item.state.value == "quiet"
    refused = run(MaintenanceRunRequest())
    assert refused.status == "error" and refused.error.code is ErrorCode.CAPABILITY_UNAVAILABLE


def test_invoker_refuses_non_automatic_and_unknown_families(tmp_path, monkeypatch):
    from _repair_common import Disposition, Owner, RepairFamily

    vault = _gate_vault(tmp_path, monkeypatch)
    context = direct_context.compose_direct_context(vault_root=vault, command_id="maintenance.run",
                                                    catalogue=current_application_catalogue(), maintenance_invoker=True)
    invoker = context.maintenance

    judgement = invoker.repair(REPAIR_SCOPES["ownership"], invocation_id="x")
    unknown = invoker.repair(RepairFamily("router", "runtime.refresh-router", {}, Disposition.AUTOMATIC, Owner.BRAIN, False, "d"),
                             invocation_id="y")
    foreign = invoker.repair(RepairFamily("mcp", "mcp.repair", {}, Disposition.AUTOMATIC, Owner.MACHINE, True, "d"),
                             invocation_id="z")

    assert all(item.status == "error" and item.error.code is ErrorCode.INVALID_REQUEST
               for item in (judgement, unknown, foreign))
    assert all(item.command_id == "maintenance.run" for item in (judgement, unknown, foreign))
    assert "not an automatic" in judgement.error.message
    assert "repair table" in unknown.error.message and "repair table" in foreign.error.message


def test_siblings_carry_no_invoker_and_run_the_real_command(tmp_path, monkeypatch):
    vault = _gate_vault(tmp_path, monkeypatch)
    composer = direct_context.DirectContextComposer(vault_root=vault, catalogue=current_application_catalogue())
    composed = []
    real = composer.compose

    def spy(**kwargs):
        context = real(**kwargs)
        composed.append(context)
        return context

    monkeypatch.setattr(composer, "compose", spy)
    outer = composer.compose(command_id="maintenance.run", maintenance_invoker=True)

    result = outer.maintenance.repair(REPAIR_SCOPES["router"], invocation_id="maint-t-router")

    assert composed[-1].maintenance is None and composed[-1].invocation_id == "maint-t-router"
    assert composed[-1].dry_run is False
    assert result.command_id == "runtime.refresh-router"


# ---------------------------------------------------------------------------
# list, advisory, log
# ---------------------------------------------------------------------------

def test_list_shows_judgement_items_and_only_unsettled_automatic_groups(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    real_detect = _detection.detect
    judgement = MaintenanceFinding("workspace_contract", "error", "Notes/a.md", "gone", Disposition.JUDGEMENT,
                                   code="workspace_reference_missing", owner=Owner.BRAIN,
                                   key="aaaaaaaaaaaaaaaa", evidence={"workspace": "workspace/x"})
    monkeypatch.setattr("_application.maintenance._items.detect", lambda context: (*real_detect(context), judgement))
    (root / ROUTER).unlink()
    assert _invoke(root, MaintenanceRunRequest(), _Scripted({"router": _error(ErrorCode.CONFLICT)})).status == "partial"

    listed = _invoke(root, MaintenanceListRequest())

    items = {item.key: item for item in listed.result.items}
    assert items["aaaaaaaaaaaaaaaa"].kind == "finding" and items["aaaaaaaaaaaaaaaa"].state.value == "open"
    router = items[family_key("brain", "router")]
    assert router.last_outcome is GroupOutcome.FAILED and "runtime refresh-router" in router.command
    assert listed.result.last_pass.outcome == "partial"

    assert _invoke(root, MaintenanceRunRequest(), _Sibling(root)).status == "ok"
    after = _invoke(root, MaintenanceListRequest())
    assert [item.key for item in after.result.items] == ["aaaaaaaaaaaaaaaa"], "a repaired group drops off the list"
    everything = _invoke(root, MaintenanceListRequest(all=True))
    assert len(everything.result.items) == 1, "all=True reveals quiet items, not clean automatic groups"


def test_advisory_reaches_runtime_status_and_session_start(command_vault_clone):
    root = command_vault_clone.vault_root
    assert application_for(root).invoke(RuntimeStatusRequest()).result.maintenance is None

    assert _invoke(root, MaintenanceRunRequest(), _Sibling(root)).status == "ok"

    status = application_for(root, clock=_RealClock()).invoke(RuntimeStatusRequest())
    assert status.status == "ok", status
    advisory = status.result.maintenance
    assert advisory is not None and advisory.needs_person == 0 and advisory.blocked is None
    assert advisory.age_seconds >= 0
    from _application.session.start import SessionStartRequest
    from _application.types import DependencyTier
    from test_session_start_owner import _mark_ready

    _mark_ready(root)
    session = application_for(root, clock=_RealClock(), dependency_tier=DependencyTier.MANAGED).invoke(SessionStartRequest())
    assert session.status == "ok", session
    assert session.result.maintenance.finished_at == advisory.finished_at
    mirror = (root / ".brain" / "local" / "session.md").read_text()
    assert "## Maintenance" in mirror and "brain maintenance list" in mirror


def test_log_records_go_through_the_diagnostics_port_only(command_vault_clone, monkeypatch, tmp_path):
    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()
    diagnostics = _Recorder()
    from _common import _operational_log
    monkeypatch.setattr(_operational_log, "append_record", lambda *a, **k: pytest.fail("executors never write the log directly"))

    result = _invoke(root, MaintenanceRunRequest(), _Sibling(root), diagnostics=diagnostics)

    assert result.status == "ok"
    events = [(event, fields.get("outcome")) for event, _family, fields in diagnostics.records]
    assert events == [("maintenance.pass_started", None), ("maintenance.repair_invoked", "repaired"),
                      ("maintenance.pass_finished", "ok")]
    invoked = diagnostics.records[1][2]
    assert invoked["command_id"] == "runtime.refresh-router" and invoked["invocation_id"].endswith("-router")

    # The real reporter writes the family, with closed fields enforced by the log.
    from _command_interface.context import OperationalDiagnosticReporter
    reporter = OperationalDiagnosticReporter(tmp_path)
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.70.10\n")
    monkeypatch.undo()
    for event, _family, fields in diagnostics.records:
        reporter.record(event, family="maintenance", **fields)
    lines = (tmp_path / ".brain/local/diagnostics/maintenance.log").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [item["event"] for item in records] == [event for event, _f, _x in diagnostics.records]
    assert not any(key in record for record in records for key in ("message", "path", "file"))
    assert not (tmp_path / ".brain/local/diagnostics/command.log").exists()


def test_maintenance_events_are_refused_outside_their_family(tmp_path):
    from _common import _operational_log
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.70.10\n")

    assert _operational_log.append_record(tmp_path, "script", "maintenance.pass_started", pass_id="abc", dry_run=False) is False
    assert _operational_log.append_record(tmp_path, "script", "maintenance.pass_started", family="maintenance",
                                          pass_id="abc", dry_run=False) is True
    assert _operational_log.append_record(tmp_path, "script", "maintenance.pass_finished", family="maintenance",
                                          pass_id="abc", outcome="done", needs_person=0, claim_expired=0, failed=0,
                                          deferred=0, duration_ms=1) is False


def test_temporaries_removal_honours_dry_run_and_re_lists_under_the_lock(command_vault_clone, young_temporaries_count):
    root = command_vault_clone.vault_root
    stranded = _strand(root)
    (root / ".brain/local/nested").mkdir()
    nested = root / ".brain/local/nested/x.json.ab12cd34.tmp"
    nested.write_text("deep")

    dry = application_for(root, clock=_RealClock(), dry_run=True).invoke(RuntimeRemoveTemporariesRequest())
    assert dry.result.status is TemporariesRemovalStatus.PLANNED and stranded.exists()
    assert dry.result.removed == (".brain/local/retrieval-index.json.ab12cd34.tmp",)

    stranded.unlink()
    noop = application_for(root, clock=_RealClock()).invoke(RuntimeRemoveTemporariesRequest())
    assert noop.result.status is TemporariesRemovalStatus.NOOP and noop.committed_effects == ()

    stranded = _strand(root)
    done = application_for(root, clock=_RealClock()).invoke(RuntimeRemoveTemporariesRequest())
    assert done.result.status is TemporariesRemovalStatus.CHANGED and not stranded.exists() and nested.exists()
    assert done.committed_effects == (CommittedEffect("runtime.remove-temporaries", ".brain/local/retrieval-index.json.ab12cd34.tmp"),)


def test_new_commands_declare_portable_selected_brain_cli_only_contracts():
    catalogue = current_application_catalogue()
    expected = {
        "maintenance.run": (Authority.MAINTAINER, InitialAuthorisationClass.OBSERVATION, EffectClass.DERIVED_CACHE_WRITE, RetryClass.SAFE),
        "maintenance.list": (Authority.READER, InitialAuthorisationClass.OBSERVATION, EffectClass.NONE, RetryClass.SAFE),
        "maintenance.claim": (Authority.MAINTAINER, InitialAuthorisationClass.CONTENT, EffectClass.SELECTED_BRAIN_MUTATION, RetryClass.RECEIPT_REQUIRED),
        "maintenance.dismiss": (Authority.MAINTAINER, InitialAuthorisationClass.CONTENT, EffectClass.SELECTED_BRAIN_MUTATION, RetryClass.RECEIPT_REQUIRED),
        "maintenance.release": (Authority.MAINTAINER, InitialAuthorisationClass.CONTENT, EffectClass.SELECTED_BRAIN_MUTATION, RetryClass.RECEIPT_REQUIRED),
        "runtime.remove-temporaries": (Authority.MAINTAINER, InitialAuthorisationClass.OBSERVATION, EffectClass.DERIVED_CACHE_WRITE, RetryClass.SAFE),
    }
    for command_id, contract in expected.items():
        entry = next(item for item in catalogue.entries if item.command_id == command_id)
        assert (entry.authority, entry.initial_class, entry.effect_class, entry.retry_class) == contract, command_id
        assert entry.dependency_tier.value == "portable" and entry.locality.value == "selected_brain_local"
        assert Projection.MCP not in entry.eligible_projections and Projection.SCRIPT in entry.eligible_projections


def test_a_scheduler_can_run_the_pass_through_the_launcher_from_an_empty_environment(command_vault_clone, tmp_path, monkeypatch):
    """What cron or launchd sees: no shell profile, a minimal PATH, the launcher and a selector."""
    import vault_registry

    repo = Path(__file__).resolve().parents[2]
    root = command_vault_clone.vault_root
    (tmp_path / "home").mkdir()
    env = {
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "BRAIN_CLI_BUNDLE": str(repo),
        **command_vault_clone.environment,
    }
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    vault_registry.register(str(root), "clone-brain")

    def launcher(*selector):
        argv = ["env", "-i", *[f"{key}={value}" for key, value in env.items()],
                "bash", str(repo / "cli" / "brain"), *selector, "maintenance", "run", "--json"]
        return subprocess.run(argv, capture_output=True, text=True, cwd=str(tmp_path), timeout=300, check=False)

    (root / ROUTER).unlink()
    by_vault = launcher("--vault", str(root))
    assert by_vault.returncode == 0, by_vault.stderr
    envelope = json.loads(by_vault.stdout)
    assert envelope["status"] == "ok" and [group["scope"] for group in envelope["result"]["groups"]] == ["router"]
    assert (root / ROUTER).exists() and _last_pass(root)["groups"] == {"router": "repaired"}

    (root / ROUTER).unlink()
    by_brain = launcher("--brain", "clone-brain")
    assert by_brain.returncode == 0, by_brain.stderr
    assert [group["scope"] for group in json.loads(by_brain.stdout)["result"]["groups"]] == ["router"]

    (root / ROUTER).unlink()
    unselected = launcher()
    assert unselected.returncode != 0, "outside a vault with no registered default there is nothing to run against"
    assert not (root / ROUTER).exists(), "no pass ran"


def test_direct_script_runs_a_keyless_pass_and_refuses_a_keyed_one(command_vault_clone):
    root = command_vault_clone.vault_root
    (root / ROUTER).unlink()
    scripts = Path(__file__).resolve().parents[2] / "src" / "brain-core" / "scripts"
    env = {**os.environ, **command_vault_clone.environment}

    def run(*extra):
        return subprocess.run(
            [sys.executable, str(scripts / "command.py"), "maintenance", "run", "--vault", str(root), "--json", *extra],
            capture_output=True, text=True, env=env, cwd=str(root.parent), timeout=180, check=False,
        )

    completed = run()
    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["status"] == "ok"
    assert [group["scope"] for group in envelope["result"]["groups"]] == ["router"]
    assert (root / ROUTER).exists()
    assert _last_pass(root)["groups"] == {"router": "repaired"}

    listed = subprocess.run(
        [sys.executable, str(scripts / "command.py"), "maintenance", "list", "--vault", str(root), "--json"],
        capture_output=True, text=True, env=env, timeout=180, check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["result"]["last_pass"]["outcome"] == "ok"

    # A keyed call is refused before anything runs: the pass is keyless by design (D7).
    import config as brain_config

    (root / ".brain" / "config.yaml").write_text(
        "vault:\n  operators:\n    - id: rob\n      profile: operator\n      auth:\n        type: key\n"
        f"        hash: {brain_config.hash_key('secret')}\n")
    (root / ROUTER).unlink()
    keyed = run("--operator-key", "secret")
    assert keyed.returncode == 3, keyed.stderr
    assert json.loads(keyed.stdout)["error"]["code"] == "capability_unavailable"
    assert not (root / ROUTER).exists(), "nothing ran"
