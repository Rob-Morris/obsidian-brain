"""Named fault-state coverage for fresh disposable command-vault clones."""

from __future__ import annotations

import json

import pytest

from _common._file_lock import MutationLockError, vault_mutation_lock
from _lifecycle.derived_cache_state import inspect_lexical_cache, inspect_router_cache
from _machine import discovery

from command_vault import (
    apply_command_vault_overlay,
    isolated_command_environment,
    load_command_vault_manifest,
    load_command_vault_overlays,
)


OVERLAY_NAMES = tuple(sorted(load_command_vault_overlays()["overlays"]))


@pytest.mark.parametrize("overlay_name", OVERLAY_NAMES)
def test_every_named_overlay_applies_to_a_fresh_isolated_clone(
    command_vault_clone,
    overlay_name,
):
    definition = load_command_vault_overlays()["overlays"][overlay_name]

    with apply_command_vault_overlay(command_vault_clone, overlay_name) as overlay:
        state_path = command_vault_clone.config_home.parent / "applied-overlay.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))

        assert overlay.name == overlay_name
        assert overlay.category == definition["category"]
        assert overlay.expected == definition["expected"]
        assert state["schema"] == "brain.command-vault-applied-overlay/1"
        assert state["name"] == overlay_name


def test_cache_overlays_create_real_derived_state_staleness(
    command_vault_baseline,
    tmp_path,
):
    from command_vault import clone_command_vault

    router_clone = clone_command_vault(command_vault_baseline, tmp_path / "router-vault")
    index_clone = clone_command_vault(command_vault_baseline, tmp_path / "index-vault")

    apply_command_vault_overlay(router_clone, "stale-router").close()
    apply_command_vault_overlay(index_clone, "stale-index").close()

    assert inspect_router_cache(router_clone.vault_root).stale is True
    assert inspect_lexical_cache(index_clone.vault_root).stale is True


@pytest.mark.parametrize(
    ("overlay_name", "blocked", "malformed", "stale_count"),
    [
        ("registry-blocked", True, False, 0),
        ("registry-incomplete", False, True, 0),
        ("registry-stale", False, False, 1),
    ],
)
def test_registry_overlays_use_the_real_machine_registry_contract(
    command_vault_clone,
    overlay_name,
    blocked,
    malformed,
    stale_count,
):
    apply_command_vault_overlay(command_vault_clone, overlay_name).close()

    with isolated_command_environment(command_vault_clone.environment):
        state = discovery._load_machine_registry()

    assert state["blocked"] is blocked
    assert state["malformed"] is malformed
    assert len(state["stale_machine_registry_entries"]) == stale_count


def test_lock_contention_overlay_holds_the_real_vault_mutation_lock(command_vault_clone):
    with apply_command_vault_overlay(command_vault_clone, "lock-contention"):
        with pytest.raises(MutationLockError, match="timed out"):
            with vault_mutation_lock(command_vault_clone.vault_root, timeout=0.01):
                pass

    with vault_mutation_lock(command_vault_clone.vault_root, timeout=0.01):
        pass


def test_lifecycle_and_upgrade_overlays_preserve_explicit_fixture_state(
    command_vault_baseline,
    tmp_path,
):
    from command_vault import clone_command_vault

    lifecycle = clone_command_vault(command_vault_baseline, tmp_path / "lifecycle-vault")
    upgrade = clone_command_vault(command_vault_baseline, tmp_path / "upgrade-vault")
    manifest = load_command_vault_manifest()

    apply_command_vault_overlay(lifecycle, "lifecycle-state").close()
    apply_command_vault_overlay(upgrade, "upgrade-state").close()

    candidate = lifecycle.vault_root / manifest["stable_artefacts"]["lifecycle_candidate"]["path"]
    assert "status: parked" in candidate.read_text(encoding="utf-8")
    assert (upgrade.vault_root / ".brain-core" / "VERSION").read_text(encoding="utf-8") == "0.53.0\n"
    assert (upgrade.vault_root / ".brain" / "upgrade-state.json").is_file()


@pytest.mark.parametrize(
    ("overlay_name", "effects"),
    [
        ("failure-after-commit", "committed"),
        ("known-partial-effects", "partial"),
        ("unknown-outcome", "unknown"),
        ("rollback-failure", "partial"),
    ],
)
def test_effect_overlays_write_queryable_outcome_fixtures(
    command_vault_clone,
    overlay_name,
    effects,
):
    apply_command_vault_overlay(command_vault_clone, overlay_name).close()
    receipts = list(command_vault_clone.outcome_receipt_root.glob("*.json"))

    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["schema"] == "brain.command-outcome-fixture/1"
    assert receipt["effects"] == effects
    if effects == "unknown":
        assert receipt["retryable"] is False


def test_an_overlay_cannot_be_layered_onto_a_used_clone(command_vault_clone):
    apply_command_vault_overlay(command_vault_clone, "missing-provider").close()

    with pytest.raises(RuntimeError, match="fresh clone"):
        apply_command_vault_overlay(command_vault_clone, "missing-file")
