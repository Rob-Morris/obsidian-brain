"""Contract tests for the installed disposable command-vault substrate."""

from __future__ import annotations

import json
import os

import check

from command_vault import (
    COMMAND_VAULT_FIXTURE_ROOT,
    COMMAND_VAULT_NOW,
    COMMAND_VAULT_SEED,
    COMMAND_VAULT_TIMEZONE,
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


def test_installed_baseline_uses_current_core_generated_caches_and_passes_checks(
    command_vault_baseline,
):
    baseline = command_vault_baseline
    vault = baseline.vault_root
    evidence = json.loads(baseline.evidence_path.read_text(encoding="utf-8"))

    assert baseline.cache_key == f"command-vault-{baseline.source_hash[:20]}"
    assert (vault / ".brain-core" / "VERSION").is_file()
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
