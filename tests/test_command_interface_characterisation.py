"""Closed evidence ledger for current command-interface behaviour."""

from __future__ import annotations

import ast
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHARACTERISATION_PATH = REPO_ROOT / "tests" / "fixtures" / "command_interface_characterisation_v1.json"


def _ledger() -> dict:
    return json.loads(CHARACTERISATION_PATH.read_text(encoding="utf-8"))


def _test_functions(relative_path: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def test_characterisation_ledger_is_closed_and_uniquely_identified():
    ledger = _ledger()
    cases = ledger["cases"]

    assert ledger["schema"] == "brain.command-interface-characterisation/1"
    assert ledger["brain_core_version"] == "0.54.0"
    assert ledger["status"] == "current_behaviour_closed"
    assert len({case["id"] for case in cases}) == len(cases)
    assert set(ledger["policy"]) == {"preserve", "correct", "remove"}


def test_characterisation_covers_valid_invalid_degraded_and_effect_boundaries():
    cases = _ledger()["cases"]

    assert {case["class"] for case in cases} == {
        "valid",
        "invalid",
        "degraded",
        "effect-boundary",
    }
    assert {case["behaviour"] for case in cases} == {"preserve", "correct", "remove"}
    for behaviour in ("correct", "remove"):
        assert sum(case["behaviour"] == behaviour for case in cases) >= 3


def test_every_characterisation_case_links_to_existing_executable_evidence():
    functions_by_file: dict[str, set[str]] = {}
    missing = []
    for case in _ledger()["cases"]:
        functions = functions_by_file.setdefault(
            case["test_file"],
            _test_functions(case["test_file"]),
        )
        if case["test_name"] not in functions:
            missing.append((case["id"], case["test_file"], case["test_name"]))

    assert not missing


def test_non_target_behaviour_has_replacement_or_correction_basis():
    for case in _ledger()["cases"]:
        if case["behaviour"] == "remove":
            assert case.get("replacement"), case["id"]
        elif case["behaviour"] == "correct":
            assert case.get("basis"), case["id"]
