"""Lifecycle effects retain branch continuity and recovery evidence.

These tests use disposable vaults and deterministic observation interleavings;
they do not need a managed runtime, network, or the developer's live vault.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from _bootstrap import branch_guard
from _bootstrap.file_lock import vault_mutation_lock
from _bootstrap.local_config import patch_local_config, read_local_config
from _bootstrap.upgrade_journal import (
    RecoveryStore, UpgradeJournal, restore_journal, restore_snapshots,
    STAGE_PRE_COMPILE,
)
import upgrade


@pytest.fixture
def guarded_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.76.2\n")
    patch_local_config(vault, lambda doc: doc.update(
        branch_guard={"allowed_branches": ["dev", "other"]}, marker="current"))
    observed = {"branch": "dev"}
    monkeypatch.setattr(branch_guard, "observe_git", lambda root:
                        branch_guard.GitState(str(vault), observed["branch"]))
    return vault, observed


def test_upgrade_refuses_before_core_or_journal_effects(guarded_vault, tmp_path):
    vault, observed = guarded_vault
    observed["branch"] = "main"
    source = tmp_path / "source"
    source.mkdir()
    (source / "VERSION").write_text("0.76.3\n")
    (source / "new.md").write_text("new content")
    lock_path = vault / ".brain/local/mutation.lock"
    before = {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*")
              if p.is_file() and p != lock_path}
    result = upgrade.upgrade(str(vault), str(source), force=True, sync=False, sync_deps=False)
    assert result["status"] == "error"
    assert result["reason"] == "branch-not-allowed"
    after = {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*")
             if p.is_file() and p != lock_path}
    assert after == before
    assert not (vault / ".brain-core/new.md").exists()
    assert UpgradeJournal.load(str(vault)) is None


def test_copy_stops_between_allowed_branches_without_rollback(guarded_vault, tmp_path, monkeypatch):
    vault, observed = guarded_vault
    source = tmp_path / "source"
    source.mkdir()
    for name in ("first", "second"):
        (source / name).write_text(name)
    copy = shutil.copy2

    def switch_after_copy(src, dst, **kwargs):
        result = copy(src, dst, **kwargs)
        observed["branch"] = "other"
        return result

    monkeypatch.setattr(upgrade.shutil, "copy2", switch_after_copy)
    with branch_guard.operation(vault):
        with pytest.raises(branch_guard.BranchGuardError, match="branch-changed"):
            upgrade._copy_core_except_version(str(source), str(vault / ".brain-core"),
                {"files_added": ["first", "second"], "files_modified": [], "files_removed": []})
        observed["branch"] = "dev"
        with pytest.raises(branch_guard.BranchGuardError):
            upgrade._restore_brain_core(str(tmp_path / "unused"), str(vault / ".brain-core"))
    assert (vault / ".brain-core/first").read_text() == "first"
    assert not (vault / ".brain-core/second").exists()


def test_journal_records_sticky_loss_and_refuses_later_replay(guarded_vault):
    vault, observed = guarded_vault
    content = vault / "note.md"
    content.write_text("before")
    with branch_guard.operation(vault):
        journal = UpgradeJournal.open(str(vault), "0.76.2", "0.76.3")
        journal.capture(STAGE_PRE_COMPILE, [str(content)])
        content.write_text("partial effect")
        observed["branch"] = "other"
        with pytest.raises(branch_guard.BranchGuardError):
            restore_journal(journal, str(vault / ".brain/local/migrations.json"), RecoveryStore.for_vault(str(vault)))
    assert UpgradeJournal.load(str(vault)).header["branch_guard_context"]["lost"] is True
    observed["branch"] = "dev"
    with pytest.raises(branch_guard.BranchGuardError):
        restore_journal(UpgradeJournal.load(str(vault)), str(vault / ".brain/local/migrations.json"), RecoveryStore.for_vault(str(vault)))
    assert content.read_text() == "partial effect"


def test_legacy_journal_is_not_blessed_by_matching_version(guarded_vault):
    vault, _ = guarded_vault
    with branch_guard.operation(vault):
        journal = UpgradeJournal.open(str(vault), "0.76.2", "0.76.3")
    journal.header.pop("branch_guard_context")
    Path(journal.header_path).write_text(json.dumps(journal.header))
    # This matches a completed VERSION witness, but cannot establish continuity.
    with branch_guard.operation(vault):
        with pytest.raises(branch_guard.BranchGuardError):
            upgrade._recover_leftover_journal(str(vault), "0.76.3", "0.76.3", None, dry_run=False)
    assert Path(journal.header_path).exists()


@pytest.mark.parametrize("old", [None, b"marker: old\n", b"branch_guard:\n  allowed_branches:\n    - obsolete\nmarker: old\n"])
def test_snapshot_restore_preserves_current_guard(guarded_vault, old):
    vault, _ = guarded_vault
    config = vault / ".brain/local/config.yaml"
    snapshots = {str(config): {"exists": False} if old is None else {"exists": True, "content": old}}
    with vault_mutation_lock(vault), branch_guard.operation(vault):
        report = restore_snapshots(snapshots, store=RecoveryStore.for_vault(str(vault)))
    assert report.verified, report.errors
    restored = read_local_config(vault).document
    assert restored["branch_guard"] == {"allowed_branches": ["dev", "other"]}
    assert restored.get("marker") == (None if old is None else "old")


def test_snapshot_does_not_resurrect_explicitly_removed_guard(guarded_vault):
    vault, _ = guarded_vault
    patch_local_config(vault, lambda doc: doc.pop("branch_guard"))
    config = vault / ".brain/local/config.yaml"
    snapshot = b"branch_guard:\n  allowed_branches:\n    - obsolete\nmarker: old\n"
    with vault_mutation_lock(vault), branch_guard.operation(vault):
        report = restore_snapshots({str(config): {"exists": True, "content": snapshot}},
                                   store=RecoveryStore.for_vault(str(vault)))
    assert report.verified, report.errors
    assert read_local_config(vault).document == {"marker": "old"}


def test_fresh_compiler_receives_parent_context(guarded_vault, monkeypatch):
    vault, observed = guarded_vault
    (vault / ".brain-core/scripts").mkdir()
    (vault / ".brain-core/scripts/compile_router.py").write_text("")
    captured = {}
    def run(*args, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(upgrade.subprocess, "run", run)
    with branch_guard.operation(vault) as operation:
        observed["branch"] = "other"
        assert upgrade._validate_compile(str(vault)) is None
        assert json.loads(captured[branch_guard.CONTEXT_ENV]) == operation.evidence()
    assert json.loads(captured[branch_guard.CONTEXT_ENV])["branch"] == "dev"


def _launcher():
    cli = str(Path(__file__).resolve().parents[1] / "cli")
    if cli not in sys.path:
        sys.path.insert(0, cli)
    from _launcher import lifecycle
    return lifecycle


def test_uninstall_refuses_before_cleanup_or_unregister(guarded_vault, monkeypatch):
    vault, observed = guarded_vault
    observed["branch"] = "main"
    lifecycle = _launcher()
    def forbidden(*args):
        pytest.fail("cleanup must not run before the guard")
    monkeypatch.setattr(lifecycle, "_mcp_cleanup", forbidden)
    result = lifecycle.execute_uninstall(SimpleNamespace(current_vault=vault, dry_run=False),
                                         lifecycle.BrainUninstallRequest())
    assert isinstance(result, lifecycle.Error)
    assert (vault / ".brain-core/VERSION").exists()
    assert read_local_config(vault).document["branch_guard"]


def test_eligible_uninstall_removes_policy_last(guarded_vault, monkeypatch):
    vault, _ = guarded_vault
    lifecycle = _launcher()
    from _launcher import registry
    from _launcher.contracts import Ok
    monkeypatch.setattr(lifecycle, "_mcp_cleanup", lambda context: ([], None))
    monkeypatch.setattr(registry, "execute_unregister", lambda *args, **kwargs:
        Ok("brain.unregister", 1, SimpleNamespace(removed_brain_ids=())))
    removed = []
    remove = shutil.rmtree
    def delete(path, **kwargs):
        assert read_local_config(vault).document["branch_guard"]
        removed.append(Path(path).name)
        remove(path, **kwargs)
    monkeypatch.setattr(lifecycle.shutil, "rmtree", delete)
    context = SimpleNamespace(current_vault=vault, dry_run=False, invocation_id="uninstall-test")
    result = lifecycle.execute_uninstall(context, lifecycle.BrainUninstallRequest())
    assert isinstance(result, lifecycle.Ok)
    assert removed[-1] == ".brain"
    assert not (vault / ".brain").exists()


def test_nested_journal_recovery_retains_original_operation(guarded_vault):
    vault, _ = guarded_vault
    with branch_guard.operation(vault) as outer:
        journal = UpgradeJournal.open(str(vault), "0.76.2", "0.76.3")
        with branch_guard.operation(vault, recovery=journal.header["branch_guard_context"]) as nested:
            assert nested is outer
            branch_guard.checkpoint(vault)
        wrong = {**outer.evidence(), "branch": "other"}
        with branch_guard.operation(vault, recovery=wrong) as nested:
            assert nested is outer
            with pytest.raises(branch_guard.BranchGuardError):
                branch_guard.checkpoint(vault)
        with pytest.raises(branch_guard.BranchGuardError):
            branch_guard.checkpoint(vault)


def test_installer_refuses_existing_policy_without_core(guarded_vault, tmp_path):
    import install
    vault, observed = guarded_vault
    shutil.rmtree(vault / ".brain-core")
    observed["branch"] = "main"
    source = tmp_path / "source"
    (source / "src/brain-core").mkdir(parents=True)
    (source / "src/brain-core/VERSION").write_text("0.76.3\n")
    (source / "template-vault").mkdir()
    config_before = (vault / ".brain/local/config.yaml").read_bytes()
    result = install.install_vault_action(vault, source_root=source, mcp_scope="skip")
    assert result["status"] == "error"
    assert not (vault / ".brain-core").exists()
    assert (vault / ".brain/local/config.yaml").read_bytes() == config_before


def test_native_fresh_scaffold_never_copies_or_scrubs_policy(guarded_vault, tmp_path):
    import install
    vault, _ = guarded_vault
    shutil.rmtree(vault / ".brain-core")
    source = tmp_path / "source"
    template = source / "template-vault"
    (template / ".brain/local").mkdir(parents=True)
    (template / ".brain/local/config.yaml").write_text("branch_guard: null\n")
    (source / "src/brain-core").mkdir(parents=True)
    (source / "src/brain-core/VERSION").write_text("0.76.3\n")
    original = (vault / ".brain/local/config.yaml").read_bytes()
    install._scaffold_fresh_vault(vault, source)
    assert (vault / ".brain-core/VERSION").read_text() == "0.76.3\n"
    assert (vault / ".brain/local/config.yaml").read_bytes() == original


def test_native_install_accepts_nonexistent_root_without_git_probe(tmp_path, monkeypatch):
    import install
    vault = tmp_path / "new-vault"
    source = tmp_path / "source"
    source.mkdir()
    (source / "VERSION").write_text("0.76.3\n")
    monkeypatch.setattr(branch_guard, "observe_git", lambda root: pytest.fail("unconfigured install must not probe Git"))
    install._install_brain_core(vault, source)
    assert (vault / ".brain-core/VERSION").read_text() == "0.76.3\n"


def test_authorisation_migration_patches_local_config_under_upgrade_lock(guarded_vault, monkeypatch):
    import importlib.util
    vault, _ = guarded_vault
    path = Path(upgrade.__file__).parent / "migrations/migrate_to_0_68_0.py"
    spec = importlib.util.spec_from_file_location("branch_guard_auth_migration_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "plan_authorisation_migration", lambda *args:
        SimpleNamespace(writes=((".brain/local/config.yaml", "marker: migrated\n"),),
                        report={"conflicts": [], "changes": [], "status": "converted"}))
    with vault_mutation_lock(vault), branch_guard.operation(vault):
        result = migration.patch_pre_compile(vault)
        branch_guard.checkpoint(vault)
    assert result["status"] == "ok"
    assert read_local_config(vault).document == {
        "marker": "migrated", "branch_guard": {"allowed_branches": ["dev", "other"]}}


def test_deployed_resolution_runtime_contains_bootstrap_guard_closure(tmp_path):
    from _machine.resolution_runtime import ensure_resolution_runtime
    scripts = Path(upgrade.__file__).parent
    runtime = tmp_path / "resolution-runtime"
    result = ensure_resolution_runtime(scripts, runtime_root=runtime)
    assert result["status"] == "changed"
    for name in ("branch_guard.py", "local_config.py", "yaml_engine.py", "owned_paths.py", "file_lock.py", "paths.py"):
        assert (runtime / "_bootstrap" / name).read_bytes() == (scripts / "_bootstrap" / name).read_bytes()
    assert not (runtime / "_common/__init__.py").exists()


def test_final_version_guard_retains_journal_and_backup(guarded_vault, tmp_path, monkeypatch):
    vault, observed = guarded_vault
    source = tmp_path / "source"
    source.mkdir()
    (source / "VERSION").write_text("0.76.3\n")
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "evidence").write_text("retain")
    with branch_guard.operation(vault) as operation:
        journal = UpgradeJournal.open(str(vault), "0.76.2", "0.76.3")
        journal.header["recovery_paths"] = [str(backup)]
        journal.retain_guard_context(operation.evidence())
    def commit(*args, **kwargs):
        def progress(*args):
            observed["branch"] = "other"
        return upgrade._commit(str(vault), str(vault / ".brain-core"),
            upgrade._read_version_file(str(source)),
            {"status": "ok", "old_version": "0.76.2", "new_version": "0.76.3"},
            str(backup), journal, progress)
    monkeypatch.setattr(upgrade, "_upgrade_in_operation", commit)
    result = upgrade.upgrade(str(vault), str(source))
    assert result["status"] == "error"
    assert result["rollback_verified"] is False
    assert UpgradeJournal.load(str(vault)).header["branch_guard_context"]["lost"] is True
    assert (backup / "evidence").read_text() == "retain"
    assert (vault / ".brain-core/VERSION").read_text() == "0.76.2\n"


def test_uninstall_reports_unregister_when_branch_changes_before_delete(guarded_vault, monkeypatch):
    vault, observed = guarded_vault
    lifecycle = _launcher()
    from _launcher import registry
    monkeypatch.setattr(lifecycle, "_mcp_cleanup", lambda context: ([], None))
    def unregister(*args, **kwargs):
        observed["branch"] = "other"
        return lifecycle.Ok("brain.unregister", 1, SimpleNamespace(removed_brain_ids=()),
                            (lifecycle.CommittedEffect("brain.unregister", "registry:removed"),))
    monkeypatch.setattr(registry, "execute_unregister", unregister)
    context = SimpleNamespace(current_vault=vault, dry_run=False, invocation_id="uninstall-test")
    result = lifecycle.execute_uninstall(context, lifecycle.BrainUninstallRequest())
    assert isinstance(result, lifecycle.Partial)
    assert result.committed_effects[0].subject == "registry:removed"
    assert (vault / ".brain-core/VERSION").exists()
    assert read_local_config(vault).document["branch_guard"]


def test_headerless_remnant_survives_context_loss_before_cleanup(guarded_vault):
    from _bootstrap.upgrade_journal import journal_directory
    vault, observed = guarded_vault
    directory = Path(journal_directory(str(vault)))
    directory.mkdir(parents=True)
    evidence = directory / "entries.jsonl"
    evidence.write_text("retained remnant\n")
    with branch_guard.operation(vault):
        branch_guard.checkpoint(vault)
        observed["branch"] = "other"
        with pytest.raises(branch_guard.BranchGuardError):
            UpgradeJournal.open(str(vault), "0.76.2", "0.76.3")
    assert evidence.read_text() == "retained remnant\n"
    assert not (directory / "journal.json").exists()


def test_journal_discard_does_not_delete_evidence_after_context_loss(guarded_vault):
    vault, observed = guarded_vault
    with branch_guard.operation(vault):
        journal = UpgradeJournal.open(str(vault), "0.76.2", "0.76.3")
        observed["branch"] = "other"
        with pytest.raises(branch_guard.BranchGuardError):
            journal.discard()
    assert Path(journal.header_path).is_file()
