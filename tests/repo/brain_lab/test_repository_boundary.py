from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_contributor_lab_is_not_shipped_in_core_or_template_vault():
    assert (REPO_ROOT / "tools" / "brain-lab" / "brain-lab").is_file()
    assert not (REPO_ROOT / "src" / "brain-core" / "tools" / "brain-lab").exists()
    assert not (REPO_ROOT / "template-vault" / "tools" / "brain-lab").exists()


def test_checked_in_recreation_scenario_mutates_manifested_state():
    scenario = json.loads(
        (REPO_ROOT / "tools" / "brain-lab" / "scenarios" / "current-template.json").read_text()
    )
    mutation = next(
        step
        for step in scenario["steps"]
        if step["operation"] == "run.exec"
        and "brain-lab-mutation" in " ".join(step["request"]["argv"])
    )
    command = " ".join(mutation["request"]["argv"])

    assert "/home/brain/vault/brain-lab-mutation" in command
    assert "/home/brain/vault/.brain/local/" not in command
