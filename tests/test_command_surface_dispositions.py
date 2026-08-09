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
    assert len(targets) == len(set(targets))
    assert all(COMMAND_ID.fullmatch(target) for target in targets)


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


def test_inventory_remains_honest_about_unassigned_contract_fields() -> None:
    dispositions = _load("command_interface_dispositions_v1.json")
    assert dispositions["inventory_status"] == "in_progress"
    assert dispositions["pending_disposition_fields"]
