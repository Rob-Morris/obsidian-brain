"""Contract tests for the installed disposable command-vault substrate."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

import check
import command_vault

from command_vault import (
    COMMAND_VAULT_FIXTURE_ROOT,
    COMMAND_VAULT_NOW,
    COMMAND_VAULT_SEED,
    COMMAND_VAULT_TIMEZONE,
    _freshen_copied_files,
    command_vault_clone_diagnostics,
    isolated_command_environment,
    load_command_vault_manifest,
    tree_hash,
    validate_command_vault_seed,
)


def test_authored_seed_is_compact_core_free_and_manifest_complete():
    validate_command_vault_seed()

    assert not (COMMAND_VAULT_SEED / ".brain-core").exists()
    assert not (COMMAND_VAULT_SEED / ".brain" / "local").exists()
    assert not list(COMMAND_VAULT_FIXTURE_ROOT.rglob("compiled-router.json"))
    assert not list(COMMAND_VAULT_FIXTURE_ROOT.rglob("retrieval-index.json"))


def test_manifest_pins_clock_and_names_required_relationship_scenarios():
    manifest = load_command_vault_manifest()

    assert manifest["clock"] == {
        "instant": COMMAND_VAULT_NOW.isoformat(),
        "timezone": COMMAND_VAULT_TIMEZONE,
    }
    assert {item["kind"] for item in manifest["relationships"]} == {"parent", "wikilink"}
    assert manifest["structural_targets"]["occurrences"] == 2
    assert set(manifest["definition_resources"]) == {
        "type",
        "template",
        "skill",
        "style",
        "memory",
    }
    assert set(manifest["destinations"]) == {
        "attachment",
        "staging",
        "outcome_receipts",
        "provider_state",
    }


@pytest.mark.skipif(os.utime not in os.supports_follow_symlinks,
                    reason="runtime lacks no-follow timestamp support")
def test_freshening_aged_copies_preserves_content_dates_and_external_symlink_targets(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    nested = owned / "nested"
    nested.mkdir()
    artefact = nested / "artefact.md"
    artefact.write_text("---\ncreated: 2020-01-01\n---\nOriginal content.\n", encoding="utf-8")
    artefact.chmod(0o640)
    external = tmp_path / "external"
    external.mkdir()
    target = external / "untouched.md"
    target.write_text("External content.\n", encoding="utf-8")
    (owned / "file-link").symlink_to(target)
    (owned / "directory-link").symlink_to(external, target_is_directory=True)
    (owned / "dangling-link").symlink_to(tmp_path / "missing")
    aged = time.time_ns() - 10 * 24 * 60 * 60 * 1_000_000_000
    for path in (artefact, target):
        os.utime(path, ns=(aged, aged))
    before_hash = tree_hash(owned)
    before_content = artefact.read_bytes()
    before_mode = artefact.stat().st_mode
    external_before = target.stat()
    started = time.time_ns()

    _freshen_copied_files(owned)

    refreshed = artefact.stat()
    assert refreshed.st_mtime_ns >= started
    assert refreshed.st_atime_ns >= started
    assert refreshed.st_mode == before_mode
    assert artefact.read_bytes() == before_content
    assert tree_hash(owned) == before_hash
    external_after = target.stat()
    assert external_after.st_mtime_ns == external_before.st_mtime_ns
    assert external_after.st_atime_ns == external_before.st_atime_ns
    assert target.read_text(encoding="utf-8") == "External content.\n"


@pytest.mark.skipif(os.utime not in os.supports_follow_symlinks,
                    reason="runtime lacks no-follow timestamp support")
def test_freshening_refuses_a_symlink_root(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    root = tmp_path / "root-link"
    root.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        _freshen_copied_files(root)


def test_freshening_unsupported_runtime_leaves_metadata_and_content_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "aged.md"
    path.write_text("Original content.\n", encoding="utf-8")
    aged = time.time_ns() - 10 * 24 * 60 * 60 * 1_000_000_000
    os.utime(path, ns=(aged, aged))
    before_hash = tree_hash(tmp_path)
    before = path.stat()

    def forbidden_utime(*args, **kwargs):
        raise AssertionError("unsupported timestamps must not fall back to following links")

    monkeypatch.setattr(command_vault, "os", SimpleNamespace(
        utime=forbidden_utime, supports_follow_symlinks=set()))
    _freshen_copied_files(tmp_path)

    after = path.stat()
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_atime_ns == before.st_atime_ns
    assert tree_hash(tmp_path) == before_hash


def test_installed_baseline_uses_current_core_generated_caches_and_passes_checks(
    command_vault_baseline,
):
    baseline = command_vault_baseline
    vault = baseline.vault_root
    evidence = json.loads(baseline.evidence_path.read_text(encoding="utf-8"))

    assert baseline.cache_key == f"command-vault-{baseline.source_hash[:20]}"
    assert (vault / ".brain-core" / "VERSION").is_file()
    # Real installation must freshen every copied file, including old Core
    # sources; generated caches and authored semantic dates still validate.
    cutoff = time.time_ns() - 24 * 60 * 60 * 1_000_000_000
    if os.utime in os.supports_follow_symlinks:
        for path in (vault / ".brain-core").rglob("*"):
            if path.is_file() and not path.is_symlink():
                assert path.stat().st_mtime_ns >= cutoff, path
                assert path.stat().st_atime_ns >= cutoff, path
    assert (vault / ".brain" / "local" / "compiled-router.json").is_file()
    assert (vault / ".brain" / "local" / "retrieval-index.json").is_file()
    assert check.run_checks(str(vault))["findings"] == []
    assert evidence["immutable_hash"] == tree_hash(vault)
    assert evidence["check_summary"] == {"errors": 0, "info": 0, "warnings": 0}
    assert evidence["assembly_seconds"] >= 0
    assert evidence["artefact_count"] == baseline.artefact_count
    assert evidence["document_count"] == baseline.document_count


def test_clone_is_writable_isolated_and_does_not_mutate_baseline(
    command_vault_baseline,
    command_vault_clone,
):
    baseline_hash = tree_hash(command_vault_baseline.vault_root)
    clone = command_vault_clone
    candidate = clone.vault_root / "Ideas" / "Command Fixture Candidate.md"

    candidate.write_text(candidate.read_text(encoding="utf-8") + "\nClone-only edit.\n", encoding="utf-8")

    assert tree_hash(clone.vault_root) != baseline_hash
    assert tree_hash(command_vault_baseline.vault_root) == baseline_hash
    assert clone.strategy in {"darwin-clonefile", "gnu-reflink", "portable-copy"}
    assert clone.clone_seconds >= 0
    cutoff = time.time_ns() - 24 * 60 * 60 * 1_000_000_000
    if os.utime in os.supports_follow_symlinks:
        for relative in (".brain-core/VERSION", ".brain-core/scripts/_common/__init__.py"):
            assert (clone.vault_root / relative).stat().st_mtime_ns >= cutoff
            assert (clone.vault_root / relative).stat().st_atime_ns >= cutoff
    assert clone.config_home.is_dir()
    assert clone.resolution_runtime.is_dir()
    assert clone.staging_root.is_dir()
    assert clone.provider_state_root.is_dir()
    assert clone.outcome_receipt_root.is_dir()
    assert clone.config_home not in clone.vault_root.parents
    assert clone.vault_root in clone.provider_state_root.parents
    diagnostics = command_vault_clone_diagnostics()
    assert diagnostics["clone_count"] >= 1
    assert diagnostics["strategy_counts"][clone.strategy] >= 1
    assert diagnostics["slowest_seconds"] >= clone.clone_seconds


def test_clone_environment_is_explicit_and_restored(command_vault_clone):
    clone = command_vault_clone
    previous = {name: os.environ.get(name) for name in clone.environment}

    with isolated_command_environment(clone.environment):
        for name, value in clone.environment.items():
            assert os.environ[name] == value

    assert {name: os.environ.get(name) for name in clone.environment} == previous


@pytest.mark.parametrize("native_clone", [False, True])
def test_clone_omits_generated_bytecode_and_preserves_writable_isolation(tmp_path, monkeypatch, native_clone):
    import shutil
    source = tmp_path / "baseline"
    cache = source / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"generated")
    (source / "scripts" / "standalone.pyc").write_bytes(b"generated")
    authored = source / "scripts" / "module.py"
    authored.write_text("original", encoding="utf-8")

    def clone(source, destination):
        if not native_clone:
            return None
        shutil.copytree(source, destination)
        return "test-native-clone"

    monkeypatch.setattr(command_vault, "_copy_on_write_clone", clone)
    result = command_vault.clone_command_vault(SimpleNamespace(vault_root=source), tmp_path / "clone")
    assert not list(result.vault_root.rglob("__pycache__"))
    assert not list(result.vault_root.rglob("*.pyc"))
    (result.vault_root / "scripts" / "module.py").write_text("changed", encoding="utf-8")
    assert authored.read_text(encoding="utf-8") == "original"
    assert (cache / "module.pyc").read_bytes() == b"generated"
