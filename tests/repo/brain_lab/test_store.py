from __future__ import annotations

from pathlib import Path

import pytest

from brain_lab.store import StateStore


def test_state_store_round_trips_receipts_and_refuses_unsafe_ids(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    store.write("source", "source-abc", {"value": 1})

    receipt = store.read("source", "source-abc")
    assert receipt["value"] == 1
    assert receipt["resource_kind"] == "source"
    assert receipt["resource_id"] == "source-abc"
    assert store.list("source") == [receipt]

    with pytest.raises(ValueError, match="safe"):
        store.read("source", "../escape")


def test_evidence_directories_are_immutable_per_operation(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    store.evidence_directory("op-1")

    with pytest.raises(FileExistsError):
        store.evidence_directory("op-1")


def test_store_reports_live_resource_dependencies(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    store.write("source", "source-a", {})
    store.write("seed", "seed-a", {"spec": {"source_id": "source-a"}})
    store.write(
        "baseline",
        "baseline-a",
        {"recipe": {"base_id": "base-a", "source_id": "source-a", "seed_id": "seed-a"}},
    )
    store.write("run", "run-a", {"source": {"kind": "baseline", "id": "baseline-a"}})
    store.write(
        "result",
        "op-scenario",
        {"payload": {"steps": [{"operation_id": "op-primitive"}]}},
    )

    assert store.references_to("source", "source-a") == [
        {"kind": "baseline", "id": "baseline-a"},
        {"kind": "seed", "id": "seed-a"},
    ]
    assert store.references_to("baseline", "baseline-a") == [
        {"kind": "run", "id": "run-a"}
    ]
    assert store.references_to("result", "op-primitive") == [
        {"kind": "result", "id": "op-scenario"}
    ]
