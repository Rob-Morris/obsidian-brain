"""Integrity gates for the frozen pre-cutover observation fixture."""

from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "command_interface_current_surface_v1.json"
PRE_CUTOVER_BRAIN_CORE_VERSION = "0.54.0"


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_observation_remains_explicitly_historical_and_complete():
    fixture = _load()
    assert fixture["schema"] == "brain.command-surface-observation/1"
    assert fixture["brain_core_version"] == PRE_CUTOVER_BRAIN_CORE_VERSION
    assert fixture["inventory_status"] == "observed_complete_dispositions_pending"
    assert fixture["pending_surfaces"] == []


def test_observed_collections_are_deterministic_and_unique():
    observed = _load()["observed_surfaces"]
    for key in ("mcp_tools", "direct_scripts", "public_python_wrappers"):
        values = observed[key]
        assert values == sorted(set(values)), key


def test_frozen_observation_keeps_the_removed_aggregate_evidence():
    observed = _load()["observed_surfaces"]
    assert "brain_create" in observed["mcp_tools"]
    assert "brain_process" in observed["public_python_wrappers"]
    assert "start_shaping.py" in observed["direct_scripts"]
