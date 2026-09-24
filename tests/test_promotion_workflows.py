"""Execute the workflow decision surfaces for candidate reuse and fallback CI."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from test_ci_check import REPO_ROOT, SHA, runs


def _workflow(name):
    return yaml.load((REPO_ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def _condition(expression, values):
    # These workflow guards use only booleans, equality and the supplied context.
    for key in sorted(values, key=len, reverse=True):
        expression = expression.replace(key, repr(values[key]))
    expression = expression.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"(?<!['\w])true(?!['\w])", "True", expression)
    return eval(expression, {"__builtins__": {}}, {})


@pytest.mark.parametrize("filename,job", [
    ("linux-test.yml", "linux-test"),
    ("windows-smoke.yml", "windows-user-smoke"),
    ("dependency-certification.yml", "native"),
])
def test_expensive_jobs_run_on_candidates_and_fallback_main_only(filename, job):
    workflow = _workflow(filename)
    import check_ci
    assert "github.event_name == 'push' && github.event.deleted == true" in workflow["run-name"]
    assert check_ci.DELETION_RUN_TITLE in workflow["run-name"]
    assert workflow["on"]["push"]["branches"] == ["main", "promotion/**"]
    assert "pull_request" in workflow["on"] and "workflow_dispatch" in workflow["on"]
    evidence = workflow["jobs"]["candidate-evidence"]
    expensive = workflow["jobs"][job]
    assert evidence["uses"] == "./.github/workflows/candidate-evidence.yml"
    assert evidence["permissions"] == {"contents": "read", "actions": "read"}
    assert expensive["needs"] == "candidate-evidence"
    for event, ref, deleted, reuse, evidence_runs, expensive_runs in [
        ("push", "refs/heads/promotion/v1.1.0", False, "", False, True),
        ("push", "refs/heads/main", False, "true", True, False),
        ("push", "refs/heads/main", False, "false", True, True),
        ("push", "refs/heads/main", False, "", True, True),
        ("pull_request", "refs/pull/1/merge", False, "", False, True),
        ("workflow_dispatch", "refs/heads/dev", False, "", False, True),
        ("push", "refs/heads/promotion/v1.1.0", True, "", False, False),
    ]:
        values = {"github.event_name": event, "github.ref": ref, "github.event.deleted": deleted}
        assert _condition(evidence["if"], values) == evidence_runs
        values.update({
            "needs.candidate-evidence.result": "success" if evidence_runs else "skipped",
            "needs.candidate-evidence.outputs.reuse": reuse,
        })
        assert _condition(expensive["if"], values) == expensive_runs


@pytest.mark.parametrize("filename,job", [
    ("linux-test.yml", "linux-test"),
    ("windows-smoke.yml", "windows-user-smoke"),
    ("dependency-certification.yml", "native"),
])
def test_recovery_replacement_pushes_run_full_ci_but_records_do_not(filename, job):
    workflow = _workflow(filename)
    branches = workflow["on"]["push"]["branches"]
    for branch, triggered in [
        ("promotion/v1.1.0", True),
        (f"recovery/{SHA}/plan", False),
        (f"recovery/{SHA}/result", False),
        ("unreleased", False),
        ("dev", False),
    ]:
        assert any(fnmatchcase(branch, pattern) for pattern in branches) == triggered
    values = {
        "github.event_name": "push",
        "github.ref": "refs/heads/promotion/v1.1.0",
        "github.event.deleted": False,
        "github.event.forced": True,
        "needs.candidate-evidence.result": "skipped",
        "needs.candidate-evidence.outputs.reuse": "",
    }
    assert not _condition(workflow["jobs"]["candidate-evidence"]["if"], values)
    assert _condition(workflow["jobs"][job]["if"], values)


@pytest.mark.parametrize("state,reuse", [("success", "true"), ("skipped", "false"), ("missing", "false"), ("unavailable", "false")])
def test_reusable_workflow_executes_exact_candidate_evidence_and_fails_closed(tmp_path, state, reuse):
    workflow = _workflow("candidate-evidence.yml")
    step = next(s for s in workflow["jobs"]["evidence"]["steps"] if s.get("id") == "check")
    script = step["run"].replace("${{ github.repository }}", "example/brain").replace("${{ github.sha }}", SHA)
    root = tmp_path / "candidate"
    (root / "src/scripts").mkdir(parents=True)
    (root / "src/brain-core").mkdir()
    (root / "src/brain-core/VERSION").write_text("1.1.0\n")
    (root / "src/scripts/check_ci.py").write_text((REPO_ROOT / "src/scripts/check_ci.py").read_text())
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    import sys
    (fake_bin / "python").symlink_to(sys.executable)
    records = [{**item, "head_branch": "promotion/v1.1.0", "conclusion": state} for item in runs()]
    if state == "missing":
        records = []
    response = json.dumps([{"total_count": len(records), "workflow_runs": records}])
    gh = fake_bin / "gh"
    gh.write_text("#!/bin/sh\n" + ("exit 1\n" if state == "unavailable" else "printf '%s' '" + response + "'\n"))
    gh.chmod(0o755)
    output = tmp_path / "output"
    completed = subprocess.run(["bash", "-e", "-c", script], cwd=root, capture_output=True, text=True,
                               env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "GITHUB_OUTPUT": str(output)})
    assert completed.returncode == 0, completed.stderr
    assert output.read_text().strip() == f"reuse={reuse}"


@pytest.mark.parametrize("native,reuse,code", [("success", "false", 0), ("failure", "false", 1), ("skipped", "false", 1), ("skipped", "true", 0)])
def test_dependency_aggregate_requires_native_success_or_candidate_evidence(native, reuse, code):
    script = _workflow("dependency-certification.yml")["jobs"]["certified"]["steps"][0]["run"]
    completed = subprocess.run(["bash", "-e", "-c", script], env={**os.environ, "NATIVE_RESULT": native, "REUSE": reuse})
    assert completed.returncode == code
