"""Validate the semantic disposition inventory as it is built."""

from __future__ import annotations

import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
COMMAND_ID = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _flatten_targets(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    return [target for child in node.values() for target in _flatten_targets(child)]


def _mapped_target_ids(dispositions: dict) -> set[str]:
    targets: set[str] = set()

    def walk(node):
        if isinstance(node, str):
            if COMMAND_ID.fullmatch(node) and not node.startswith("request."):
                targets.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for key, value in dispositions.items():
        if key not in {"command_contract_groups", "pending_disposition_fields"}:
            walk(value)
    return targets


def test_dispositions_identify_the_observed_brain_version() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    assert dispositions["schema"] == "brain.command-surface-dispositions/1"
    assert dispositions["brain_core_version"] == observation["brain_core_version"]


def test_every_mandated_aggregate_variant_has_one_granular_target() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    observed_axes = observation["observed_surfaces"]["mcp_variant_axes"]
    mappings = dispositions["aggregate_mappings"]

    assert set(mappings) == {
        "brain_action",
        "brain_create",
        "brain_define",
        "brain_edit",
        "brain_move",
    }
    for tool_name in ("brain_action", "brain_create", "brain_move"):
        axis = mappings[tool_name]["axis"]
        assert sorted(mappings[tool_name]["mappings"]) == observed_axes[tool_name][axis]

    define = mappings["brain_define"]["mappings"]
    assert sorted(define) == observed_axes["brain_define"]["request.kind"]
    assert sorted({operation for variants in define.values() for operation in variants}) == (
        observed_axes["brain_define"]["request.mutation.operation"]
    )
    assert "delete" not in define["plugin"]
    assert "delete" not in define["type"]

    edit = mappings["brain_edit"]["mappings"]
    assert sorted(edit) == observed_axes["brain_edit"]["request.subject.resource"]
    for variants in edit.values():
        assert sorted(variants) == observed_axes["brain_edit"][
            "request.mutation.operation"
        ]


def test_granular_targets_are_unique_canonical_command_ids() -> None:
    dispositions = _load("command_interface_dispositions_v1.json")
    targets = [
        target
        for aggregate in dispositions["aggregate_mappings"].values()
        for target in _flatten_targets(aggregate["mappings"])
    ]
    assert all(COMMAND_ID.fullmatch(target) for target in targets)
    duplicates = {target for target in targets if targets.count(target) > 1}
    assert duplicates == {"document.edit"}
    assert targets.count("document.edit") == 25


def test_body_source_variants_have_explicit_behaviour() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    observed_axes = observation["observed_surfaces"]["mcp_variant_axes"]

    for key, variants in dispositions["body_source_dispositions"].items():
        tool_name, axis = key.split(".", 1)
        assert sorted(variants) == observed_axes[tool_name][axis]
        assert {entry["behaviour"] for entry in variants.values()} <= {
            "preserve",
            "correct",
            "remove",
        }
        assert variants["file"]["behaviour"] == "remove"
        assert variants["inline"]["behaviour"] == "preserve"
        assert variants["stage"]["behaviour"] == "preserve"


def test_every_mcp_tool_has_one_explicit_disposition() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    tools = dispositions["mcp_tool_dispositions"]
    assert sorted(tools) == observation["observed_surfaces"]["mcp_tools"]

    aggregate_tools = set(dispositions["aggregate_mappings"])
    for tool_name, entry in tools.items():
        assert entry["disposition"] in {"remove", "replace", "split"}
        if entry["disposition"] == "remove":
            assert entry["behaviour"] == "remove"
            assert entry["replacement_guidance"]
        elif tool_name in aggregate_tools:
            assert entry["mapping_ref"] == f"aggregate_mappings.{tool_name}"
        elif entry["disposition"] == "replace":
            assert COMMAND_ID.fullmatch(entry["target"])


def test_resource_aggregate_mappings_cover_observed_axes() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    observed_axes = observation["observed_surfaces"]["mcp_variant_axes"]
    tools = dispositions["mcp_tool_dispositions"]

    for tool_name in ("brain_list", "brain_read", "brain_search"):
        entry = tools[tool_name]
        assert sorted(entry["mappings"]) == observed_axes[tool_name][entry["axis"]]
        assert all(COMMAND_ID.fullmatch(target) for target in entry["mappings"].values())


def test_every_cli_leaf_and_nested_operation_has_one_owner() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    observed = observation["observed_surfaces"]
    cli = dispositions["cli_leaf_dispositions"]
    assert sorted(cli) == sorted(observed["cli"]["dispatched"] + observed["cli"]["special"])

    nested = observed["cli_operation_axes"]
    assert sorted(cli["configure"]["mappings"]) == nested["configure.py"][
        "command_paths"
    ]
    assert sorted(cli["machine"]["mappings"]) == nested["machine.py"][
        "command_paths"
    ]
    assert sorted(cli["repair"]["mappings"]) == nested["repair.py"]["scope"]
    assert sorted(cli["read"]["mappings"]) == nested["read.py"]["resource"]
    assert sorted(cli["setup"]["mappings"]) == nested["setup.py"]["command_paths"]

    def owned_targets(node):
        if "target" in node:
            yield node
        for child in node.get("mappings", {}).values():
            yield from owned_targets(child)

    entries = [entry for node in cli.values() for entry in owned_targets(node)]
    assert entries
    assert {entry["owner"] for entry in entries} == {"application", "launcher"}
    assert all(COMMAND_ID.fullmatch(entry["target"]) for entry in entries)


def test_every_public_python_wrapper_has_one_disposition() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    wrappers = dispositions["public_python_wrapper_dispositions"]
    assert sorted(wrappers) == observation["observed_surfaces"][
        "public_python_wrappers"
    ]
    for name, entry in wrappers.items():
        if isinstance(entry, str):
            assert entry == f"mcp_tool_dispositions.{name}"
        else:
            assert name == "brain_process"
            assert entry["behaviour"] == "remove"
            assert entry["replacement_guidance"]


def test_every_recursive_direct_script_has_one_disposition() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    groups = dispositions["direct_script_dispositions"]
    assigned = [name for group in groups.values() for name in group]
    assert len(assigned) == len(set(assigned))
    assert sorted(assigned) == observation["observed_surfaces"]["direct_scripts"]

    for owner in ("application", "launcher"):
        assert all(COMMAND_ID.fullmatch(target) for target in groups[owner].values())
    for entry in groups["split"].values():
        assert entry["owner"] in {"application", "launcher", "mixed"}
        assert bool(entry.get("mapping_ref")) != bool(entry.get("targets"))
        assert all(COMMAND_ID.fullmatch(target) for target in entry.get("targets", []))
    assert all(groups["internal"].values())
    for entry in groups["remove"].values():
        assert COMMAND_ID.fullmatch(entry["replacement"])
        assert entry["rationale"]


def test_every_multiplexed_direct_script_operation_has_a_target() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    observed = observation["observed_surfaces"]["direct_script_operation_axes"]
    split = dispositions["direct_script_dispositions"]["split"]

    expected_counts = {
        "_common/_venv.py": 3,
        "fix_links.py": 2,
        "sync_definitions.py": 3,
        "vault_registry.py": 9,
        "workspace_registry.py": 4,
    }
    assert {name: len(operations) for name, operations in observed.items()} == expected_counts
    for script_name, count in expected_counts.items():
        assert len(split[script_name]["targets"]) == count


def test_every_install_upgrade_mode_is_launcher_owned() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    observed = observation["observed_surfaces"]["install_upgrade_operations"]
    assigned = dispositions["install_upgrade_dispositions"]
    assert sorted(assigned) == sorted(observed)
    for entrypoint, operations in assigned.items():
        assert sorted(operations) == observed[entrypoint]
        for entry in operations.values():
            assert entry["owner"] == "launcher"
            assert COMMAND_ID.fullmatch(entry["target"])


def test_every_target_command_has_one_complete_execution_contract() -> None:
    dispositions = _load("command_interface_dispositions_v1.json")
    expected = _mapped_target_ids(dispositions)
    groups = dispositions["command_contract_groups"]
    assigned = [command for group in groups.values() for command in group["commands"]]
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == expected

    contract_fields = {
        "owner",
        "canonical_result_owner",
        "dependency_tier",
        "locality",
        "providers",
        "authority",
        "effect_class",
        "retry_class",
        "eligible_projections",
    }
    for group in groups.values():
        contract = group["contract"]
        assert group["commands"] == sorted(set(group["commands"]))
        assert set(contract) == contract_fields
        assert contract["owner"] in {"application", "launcher"}
        assert contract["dependency_tier"] in {"bootstrap", "portable", "managed"}
        assert set(contract["providers"]) == {"required", "optional"}
        assert set(contract["providers"]["required"]).isdisjoint(
            contract["providers"]["optional"]
        )
        assert contract["eligible_projections"]


def test_current_outputs_and_every_known_consumer_have_migration_guidance() -> None:
    observation = _load("command_interface_current_surface_v1.json")
    dispositions = _load("command_interface_dispositions_v1.json")
    guidance = dispositions["consumer_replacement_guidance"]
    observed = observation["observed_surfaces"]

    assert set(dispositions["current_output_contracts"]) == {
        "mcp",
        "cli",
        "direct_scripts",
        "public_python_wrappers",
        "install_upgrade",
    }
    assert set(guidance["known_current_consumers"]) == set(
        observed["known_current_consumers"]
    )
    assert set(guidance["documented_contract_sources"]) == set(
        observed["documented_contract_sources"]
    )
    assert all(
        text
        for category in guidance.values()
        for text in category.values()
    )


def test_behaviour_policy_names_every_non_preserved_surface() -> None:
    dispositions = _load("command_interface_dispositions_v1.json")
    policy = dispositions["behaviour_classification"]
    assert policy["default"] == "preserve"
    assert set(policy["removals"]) == {
        "brain_init",
        "brain_process",
        "brain_create.request.content.source=file",
        "brain_edit.request.mutation.content.source=file",
        "start_shaping.py",
    }
    assert policy["corrections"]


def test_disposition_inventory_is_complete() -> None:
    dispositions = _load("command_interface_dispositions_v1.json")
    assert dispositions["inventory_status"] == "complete"
    assert dispositions["pending_disposition_fields"] == []
    assert set(dispositions["contract_invariants"]) == {
        "request_owner",
        "result_owner",
        "catalogue_authority",
    }
