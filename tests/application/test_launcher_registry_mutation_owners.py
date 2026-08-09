"""Receipt-safe behaviour for machine Brain-registry mutation owners."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ErrorCode, ReceiptState
from _launcher.invocation import LauncherInvocation
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.registry import (
    BrainBackfillRequest,
    BrainClearDefaultRequest,
    BrainPruneRequest,
    BrainRegisterRequest,
    BrainSetDefaultRequest,
    BrainUnregisterRequest,
    RegistryMutationStatus,
)
import vault_registry


NOW = datetime.fromisoformat("2026-08-10T04:00:00+10:00")
MUTATION_COMMANDS = {
    "brain.backfill",
    "brain.clear-default",
    "brain.prune",
    "brain.register",
    "brain.set-default",
    "brain.unregister",
}


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"

    def __init__(self, available=True):
        self.available = available


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _invocation(
    tmp_path,
    *,
    provider=True,
    provider_available=True,
    dry_run=False,
    receipts=None,
):
    providers = (
        (_CallerFilesystem(provider_available),)
        if provider
        else ()
    )
    context = LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings(providers),
        correlation_id="corr-registry",
        invocation_id="inv-registry",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        cli_version="1.2.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _effect_subjects(result):
    return tuple(effect.subject for effect in result.committed_effects)


def test_registry_mutation_owners_match_machine_global_contract():
    entries = {entry.command_id: entry for entry in LAUNCHER_CATALOGUE.entries}
    owners = {
        owner.command_id: owner
        for owner in LAUNCHER_OWNERS.entries
        if owner.command_id in MUTATION_COMMANDS
    }

    assert set(owners) == MUTATION_COMMANDS
    assert len({owner.result_type for owner in owners.values()}) == len(
        MUTATION_COMMANDS
    )
    for command_id, owner in owners.items():
        entry = entries[command_id]
        assert entry.owner_ref == owner.owner_ref
        assert entry.command_version == owner.command_version
        assert entry.authority == "operator"
        assert entry.effect_class == "machine_mutation"
        assert entry.retry_class == "receipt_required"
        assert entry.required_providers == ("caller_filesystem",)
        assert {item.projection: item.supported for item in entry.projections} == {
            "cli": True,
            "launcher": True,
            "mcp": False,
            "python": False,
            "script": False,
        }


@pytest.mark.parametrize(
    "command_request",
    (
        BrainBackfillRequest(Path("/tmp/brain")),
        BrainClearDefaultRequest(),
        BrainPruneRequest(),
        BrainRegisterRequest(Path("/tmp/brain")),
        BrainSetDefaultRequest("brain"),
        BrainUnregisterRequest(Path("/tmp/brain")),
    ),
)
def test_registry_mutations_require_an_available_caller_filesystem(
    tmp_path,
    command_request,
):
    missing = _invocation(tmp_path, provider=False).invoke(command_request)
    unavailable = _invocation(
        tmp_path,
        provider_available=False,
    ).invoke(command_request)

    assert missing.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing.error.details.missing == ("provider:caller_filesystem",)
    assert missing.effects == "none"
    assert unavailable.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert unavailable.error.details.missing == ("capability:caller_filesystem",)
    assert unavailable.effects == "none"


def test_register_and_backfill_report_exact_change_state(vault, tmp_path):
    invocation = _invocation(tmp_path)

    registered = invocation.invoke(BrainRegisterRequest(vault.resolve(), "test-brain"))
    repeated = invocation.invoke(BrainRegisterRequest(vault.resolve(), "test-brain"))
    backfilled = invocation.invoke(BrainBackfillRequest(vault.resolve()))

    assert registered.result.status is RegistryMutationStatus.CHANGED
    assert registered.result.brain_id == "test-brain"
    assert _effect_subjects(registered) == ("machine-registry:test-brain",)
    assert repeated.result.status is RegistryMutationStatus.NOOP
    assert repeated.committed_effects == ()
    assert backfilled.result.status is RegistryMutationStatus.NOOP
    assert backfilled.result.brain_id == "test-brain"
    assert backfilled.committed_effects == ()


def test_register_rejects_a_non_brain_before_mutation(tmp_path):
    candidate = tmp_path / "not-a-brain"
    candidate.mkdir()

    result = _invocation(tmp_path).invoke(
        BrainRegisterRequest(candidate.resolve(), "not-a-brain")
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"
    assert vault_registry.list_entries() == []


def test_default_pointer_owners_report_only_default_effects(vault, tmp_path):
    vault_registry.register(vault, brain_id="test-brain")
    invocation = _invocation(tmp_path)

    selected = invocation.invoke(BrainSetDefaultRequest("test-brain"))
    repeated = invocation.invoke(BrainSetDefaultRequest("test-brain"))
    cleared = invocation.invoke(BrainClearDefaultRequest())
    absent = invocation.invoke(BrainClearDefaultRequest())

    assert selected.result.status is RegistryMutationStatus.CHANGED
    assert _effect_subjects(selected) == ("machine-default",)
    assert repeated.result.status is RegistryMutationStatus.NOOP
    assert repeated.committed_effects == ()
    assert cleared.result.status is RegistryMutationStatus.CHANGED
    assert cleared.result.brain_id == "test-brain"
    assert _effect_subjects(cleared) == ("machine-default",)
    assert absent.result.status is RegistryMutationStatus.NOOP
    assert absent.committed_effects == ()


def test_unregister_reports_registry_and_default_effects(vault, tmp_path):
    vault_registry.register(vault, brain_id="test-brain")
    vault_registry.set_default("test-brain")
    invocation = _invocation(tmp_path)

    removed = invocation.invoke(BrainUnregisterRequest(vault.resolve()))
    absent = invocation.invoke(BrainUnregisterRequest(vault.resolve()))

    assert removed.result.status is RegistryMutationStatus.CHANGED
    assert removed.result.default_pointer_affected is True
    assert _effect_subjects(removed) == (
        "machine-registry:test-brain",
        "machine-default",
    )
    assert vault_registry.get_default() is None
    assert absent.result.status is RegistryMutationStatus.NOOP
    assert absent.committed_effects == ()


def test_prune_removes_only_stale_registrations(vault, tmp_path):
    stale = tmp_path / "missing-brain"
    vault_registry.register(vault, brain_id="live-brain")
    vault_registry.register(stale, brain_id="stale-brain")

    result = _invocation(tmp_path).invoke(BrainPruneRequest())

    assert result.result.status is RegistryMutationStatus.CHANGED
    assert result.result.removed_brain_ids == ("stale-brain",)
    assert _effect_subjects(result) == ("machine-registry:stale-brain",)
    assert vault_registry.resolve("live-brain") == str(vault.resolve())
    assert vault_registry.resolve("stale-brain") is None


def test_registry_dry_run_is_explicit_and_does_not_mutate(vault, tmp_path):
    receipts = _Receipts()

    result = _invocation(
        tmp_path,
        dry_run=True,
        receipts=receipts,
    ).invoke(BrainRegisterRequest(vault.resolve(), "planned-brain"))

    assert result.result.status is RegistryMutationStatus.PLANNED
    assert result.result.brain_id == "planned-brain"
    assert result.committed_effects == ()
    assert vault_registry.resolve("planned-brain") is None
    assert receipts.values[-1].state is ReceiptState.COMMITTED


def test_registry_dry_run_uses_real_resolution_without_writing(vault, tmp_path):
    vault_registry.register(vault, brain_id="existing-brain")
    vault_registry.set_default("existing-brain")
    invocation = _invocation(tmp_path, dry_run=True)

    existing = invocation.invoke(BrainBackfillRequest(vault.resolve()))
    impossible = invocation.invoke(BrainSetDefaultRequest("missing-brain"))
    removal = invocation.invoke(BrainUnregisterRequest(vault.resolve()))

    assert existing.result.status is RegistryMutationStatus.NOOP
    assert existing.result.brain_id == "existing-brain"
    assert impossible.error.code is ErrorCode.CONFLICT
    assert removal.result.status is RegistryMutationStatus.PLANNED
    assert removal.result.removed_brain_ids == ("existing-brain",)
    assert removal.result.default_pointer_affected is True
    assert vault_registry.resolve("existing-brain") == str(vault.resolve())
    assert vault_registry.get_default() == "existing-brain"


def test_unregister_surfaces_registry_commit_when_default_cleanup_fails(
    vault,
    tmp_path,
    monkeypatch,
):
    vault_registry.register(vault, brain_id="test-brain")
    vault_registry.set_default("test-brain")
    monkeypatch.setattr(
        vault_registry,
        "_clear_default_unlocked",
        lambda: (_ for _ in ()).throw(
            vault_registry.RegistryReadError("default pointer is read-only")
        ),
    )
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        BrainUnregisterRequest(vault.resolve())
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert _effect_subjects(result) == ("machine-registry:test-brain",)
    assert vault_registry.resolve("test-brain") is None
    assert vault_registry.get_default() == "test-brain"
    assert receipts.values[-1].state is ReceiptState.KNOWN_PARTIAL


def test_unexpected_registry_failure_is_a_non_retryable_unknown_outcome(
    vault,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        vault_registry,
        "register_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk uncertain")),
    )
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        BrainRegisterRequest(vault.resolve(), "test-brain")
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert result.outcome_reference.invocation_id == "inv-registry"
    assert receipts.values[-1].state is ReceiptState.UNKNOWN


@pytest.mark.parametrize(
    "request_factory",
    (
        lambda: BrainRegisterRequest(Path("relative")),
        lambda: BrainRegisterRequest(Path("/tmp/brain"), "Invalid Brain"),
        lambda: BrainBackfillRequest(Path("relative")),
        lambda: BrainUnregisterRequest(Path("relative")),
        lambda: BrainSetDefaultRequest("Invalid Brain"),
    ),
)
def test_registry_mutation_requests_reject_invalid_intent(request_factory):
    with pytest.raises(ValueError):
        request_factory()
