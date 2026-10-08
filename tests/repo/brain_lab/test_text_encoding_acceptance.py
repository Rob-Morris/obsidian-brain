"""Fixture bytes and acceptance guards; installed integration is a separate gate."""
from pathlib import Path
import json
import runpy

import pytest
from _common._text_encoding import diagnose_text

ROOT = Path(__file__).resolve().parents[3]
CONTAINER = ROOT / "tools/brain-lab/container"


@pytest.fixture
def helper():
    return runpy.run_path(str(CONTAINER / "text_encoding_acceptance.py"))


def test_fixture_proves_closed_diagnosis_set_and_preserves_multibyte_prefix(tmp_path, helper):
    original = helper["seed_fixture"](tmp_path)
    assert len(original) == 7
    for code in helper["CLEAR_CODES"]:
        raw = original[f"Designs/{code}.md"]
        diagnosis = diagnose_text(raw)
        assert diagnosis.code == code
        assert diagnosis.fixed_bytes == helper["note_text"](code).encode()
        assert "café 🦘" in diagnosis.fixed_bytes.decode()
    legacy = diagnose_text(original[helper["LEGACY"]])
    assert legacy.code == "not_utf8"
    assert legacy.fixed_bytes is None
    assert diagnose_text(original[helper["GOOD"]]) is None
    assert original[helper["CONFIG"]].startswith(b"\xef\xbb\xbf")
    import yaml
    shared = yaml.safe_load(original[helper["CONFIG"]].decode("utf-8-sig"))
    assert shared["vault"]["brain_name"] == helper["BRAIN_NAME"]
    assert shared["vault"]["access"]["request_policy"] == "denied"
    with pytest.raises(helper["AcceptanceFailure"], match="already exists"):
        helper["seed_fixture"](tmp_path)


def test_findings_guard_requires_every_file_before_and_only_legacy_after(helper):
    legacy = {"check": "unreadable_file", "file": helper["LEGACY"], "code": "not_utf8", "repair": None}
    clear = [{"check": "text_encoding", "file": f"Designs/{code}.md", "code": code,
              "severity": "warning" if code == "utf8_bom" else "error"}
             for code in helper["CLEAR_CODES"]]
    helper["require_findings"]({"findings": [legacy, *clear]}, repaired=False)
    helper["require_findings"]({"findings": [legacy]}, repaired=True)
    for findings, repaired in (([legacy, *clear[:-1]], False), ([legacy, clear[0]], True), ([], True)):
        with pytest.raises(helper["AcceptanceFailure"], match="unexpected text findings"):
            helper["require_findings"]({"findings": findings}, repaired=repaired)


@pytest.mark.parametrize("name,policy", [("", "denied"), ("Text encoding acceptance", "allowed")])
def test_config_guard_detects_defaults_or_fail_open(helper, name, policy):
    with pytest.raises(helper["AcceptanceFailure"], match="failed open"):
        helper["require_config"]({"brain_name": name, "access": {"request_policy": policy}})


def test_scenario_uses_current_template_and_copied_worktree_script():
    directory = ROOT / "tools/brain-lab/scenarios"
    scenario = json.loads((directory / "text-encoding.json").read_text())
    template = json.loads((directory / "current-template.json").read_text())
    assert scenario["steps"][:6] == template["steps"][:6]
    assert scenario["host_state"] == {"worktrees": ["."], "vaults": []}
    execution = scenario["steps"][6]
    assert execution["operation"] == "run.exec"
    assert execution["request"]["argv"][1] == "/home/brain/copied-source/tools/brain-lab/container/text_encoding_acceptance.py"
    assert scenario["steps"][7]["operation"] == "run.discard"
