"""Avoid unused Git captures and unrelated recovery prevalidation blob reads."""

from collections import Counter
from dataclasses import dataclass
import hashlib
from typing import ClassVar

import pytest

from command_application import context_for
from _application.application import CommandApplication
from _application.catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationCatalogue, ApplicationEntry
from _application.preparation import LIVE_QUERY
from _application.results import Error, Ok
from _application.types import (
    Authority, DependencyTier, EffectClass, InitialAuthorisationClass, Locality, RetryClass,
)
from _bootstrap import branch_guard, upgrade_journal
from _bootstrap.file_lock import vault_mutation_lock
from _bootstrap.local_config import patch_local_config, read_local_config


@dataclass(frozen=True)
class ProbePayload:
    value: str = "ok"


def application(root, command_id, execute, *, dry_run=False):
    @dataclass(frozen=True)
    class ProbeRequest:
        COMMAND_ID: ClassVar[str] = command_id
        COMMAND_VERSION: ClassVar[int] = 1
        RESULT_TYPE: ClassVar[type] = ProbePayload

    entry = ApplicationEntry(
        ProbeRequest, execute, DependencyTier.PORTABLE, Locality.SELECTED_BRAIN_LOCAL,
        (), (), Authority.MAINTAINER, EffectClass.SELECTED_BRAIN_MUTATION,
        InitialAuthorisationClass.OBSERVATION, RetryClass.SAFE,
        ALL_APPLICATION_PROJECTIONS, preparation=LIVE_QUERY,
    )
    catalogue = ApplicationCatalogue((entry,))
    context = context_for(root, catalogue=catalogue, dry_run=dry_run)
    return CommandApplication(context, catalogue), ProbeRequest()


@pytest.mark.parametrize("command_id,dry_run", [
    ("artefact.read", False),
    ("relationship.refresh", False),
    ("result.read", False),
    ("command.list", False),
    ("vault.configure-branch-guard", False),
    ("document.write-body", True),
    ("shaping.render", True),
])
def test_nonprotected_invocations_skip_entry_capture(tmp_path, monkeypatch, command_id, dry_run):
    def forbidden(*args, **kwargs):
        pytest.fail("This invocation must not capture unused branch continuity")

    monkeypatch.setattr(branch_guard, "operation", forbidden)
    app, request = application(tmp_path, command_id,
        lambda context, request: Ok(request.COMMAND_ID, 1, ProbePayload()), dry_run=dry_run)
    assert isinstance(app.invoke(request), Ok)


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    state = {"branch": "dev"}
    patch_local_config(tmp_path, lambda document: document.update(
        branch_guard={"allowed_branches": ["dev", "other"]}, marker="current"))
    monkeypatch.setattr(branch_guard, "observe_git", lambda root:
        branch_guard.GitState(str(tmp_path), state["branch"]))
    return tmp_path, state


@pytest.mark.parametrize("command_id", ["document.write-body", "shaping.render"])
def test_protected_and_mixed_invocations_capture_before_executor(guarded, command_id):
    root, state = guarded
    target = root / "must-not-write.md"

    def execute(context, request):
        state["branch"] = "other"
        branch_guard.checkpoint(root)
        target.write_text("forbidden")
        return Ok(request.COMMAND_ID, 1, ProbePayload())

    app, request = application(root, command_id, execute)
    result = app.invoke(request)
    assert isinstance(result, Error)
    details = getattr(result.error.details, "branch_guard", result.error.details)
    assert details.reason == "branch-changed"
    assert not target.exists()


def test_recovery_prevalidation_reads_only_config_blobs(guarded, monkeypatch):
    root, _state = guarded
    config = root / ".brain/local/config.yaml"
    unrelated = root / "unrelated.bin"
    ledger = root / ".brain/local/migrations.json"
    reads = Counter()
    original_read = upgrade_journal.UpgradeJournal._blob_bytes

    def read_blob(journal, digest):
        reads[digest] += 1
        return original_read(journal, digest)

    with vault_mutation_lock(root), branch_guard.operation(root):
        journal = upgrade_journal.UpgradeJournal.open(str(root), "old", "new")
        for stage, content in (
            (upgrade_journal.STAGE_PRE_COMPILE, b"original unrelated payload"),
            (upgrade_journal.STAGE_POST_COMPILE, b"intermediate unrelated payload"),
        ):
            unrelated.write_bytes(content)
            journal.capture(stage, [str(config), str(unrelated)])
        unrelated.write_bytes(b"partial upgrade")
        monkeypatch.setattr(upgrade_journal.UpgradeJournal, "_blob_bytes", read_blob)
        report = upgrade_journal.restore_journal(
            journal, str(ledger), upgrade_journal.RecoveryStore.for_vault(str(root)))

    assert report.verified, report.errors
    for content in (b"original unrelated payload", b"intermediate unrelated payload"):
        assert reads[hashlib.sha256(content).hexdigest()] == 1
    assert unrelated.read_bytes() == b"original unrelated payload"
    assert read_local_config(root).document["branch_guard"]["allowed_branches"] == ["dev", "other"]


@pytest.mark.parametrize("tamper_after_patch", [False, True])
def test_config_restore_noop_retains_bytes_and_verifies_exactly(guarded, monkeypatch, tamper_after_patch):
    root, _state = guarded
    config = root / ".brain/local/config.yaml"
    before = config.read_bytes()
    identity = config.stat().st_ino
    revision = read_local_config(root).revision
    original_patch = upgrade_journal.patch_local_config
    patches = []

    def patch(*args, **kwargs):
        assert kwargs["expected_revision"] == revision
        result = original_patch(*args, **kwargs)
        assert result.revision == revision
        assert config.read_bytes() == before
        assert config.stat().st_ino == identity
        patches.append(result)
        if tamper_after_patch:
            # Even a later formatting-only change must fail exact verification.
            config.write_bytes(before + b"\n")
        return result

    monkeypatch.setattr(upgrade_journal, "patch_local_config", patch)
    with vault_mutation_lock(root), branch_guard.operation(root):
        report = upgrade_journal.restore_snapshots(
            {str(config): {"exists": True, "content": before}},
            store=upgrade_journal.RecoveryStore.for_vault(str(root)))

    assert len(patches) == 1
    if tamper_after_patch:
        assert not report.verified
        assert any("verify restored file" in error for error in report.errors)
    else:
        assert report.verified, report.errors
        assert config.read_bytes() == before
        assert config.stat().st_ino == identity


def test_config_restore_noop_still_requires_current_revision(guarded, monkeypatch):
    root, _state = guarded
    config = root / ".brain/local/config.yaml"
    before = config.read_bytes()
    original_patch = upgrade_journal.patch_local_config
    later = before + b"later_setting: retained\n"

    def patch(*args, **kwargs):
        config.write_bytes(later)
        return original_patch(*args, **kwargs)

    monkeypatch.setattr(upgrade_journal, "patch_local_config", patch)
    with vault_mutation_lock(root), branch_guard.operation(root):
        report = upgrade_journal.restore_snapshots(
            {str(config): {"exists": True, "content": before}},
            store=upgrade_journal.RecoveryStore.for_vault(str(root)))

    assert not report.verified
    assert any("Local configuration changed" in error for error in report.errors)
    assert config.read_bytes() == later


def test_all_config_snapshots_are_validated_before_ledger_rewind(guarded):
    root, _state = guarded
    config = root / ".brain/local/config.yaml"
    current = config.read_bytes()
    ledger = root / ".brain/local/migrations.json"
    ledger.write_text("original ledger")

    with vault_mutation_lock(root), branch_guard.operation(root):
        journal = upgrade_journal.UpgradeJournal.open(str(root), "old", "new")
        journal.capture(upgrade_journal.STAGE_PRE_COMPILE, [str(config), str(ledger)])
        config.write_text("invalid: [unterminated\n")
        journal.capture(upgrade_journal.STAGE_POST_COMPILE, [str(config)])
        config.write_bytes(current)
        ledger.write_text("current ledger")
        with pytest.raises(upgrade_journal.UpgradeJournalUnreadable, match="malformed local-config snapshot"):
            upgrade_journal.restore_journal(
                journal, str(ledger), upgrade_journal.RecoveryStore.for_vault(str(root)))

    assert ledger.read_text() == "current ledger"
    assert config.read_bytes() == current
    assert upgrade_journal.UpgradeJournal.load(str(root)) is not None


def test_recovery_preserves_policy_introduced_under_captured_root(guarded):
    root, _state = guarded
    config = root / ".brain/local/config.yaml"
    current = config.read_bytes()

    with vault_mutation_lock(root), branch_guard.operation(root):
        journal = upgrade_journal.UpgradeJournal.open(str(root), "old", "new")
        config.unlink()
        journal.capture_tree(upgrade_journal.STAGE_PRE_COMPILE, str(root / ".brain"))
        config.write_bytes(current)
        report = upgrade_journal.restore_journal(journal,
            str(root / ".brain/local/migrations.json"),
            upgrade_journal.RecoveryStore.for_vault(str(root)))

    assert report.verified, report.errors
    assert read_local_config(root).document["branch_guard"]["allowed_branches"] == ["dev", "other"]
