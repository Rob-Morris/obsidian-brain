"""Exact-SHA CI evidence and offline post-commit advisory behaviour."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src/scripts"))
import check_ci

SHA = "a" * 40
REPO = "example/brain"


def runs():
    return [
        {"id": index, "path": path, "head_sha": SHA, "head_branch": "main",
         "event": "push", "status": "completed", "conclusion": "success",
         "run_attempt": 1, "html_url": f"https://github.com/{REPO}/actions/runs/{index}"}
        for index, path in enumerate(check_ci.REQUIRED_WORKFLOWS, 1)
    ]


def evaluate(items):
    return check_ci.evaluate_runs(items, SHA, "main", "push")


def test_all_required_workflows_must_pass_and_exist_in_repo():
    assert evaluate(runs())["state"] == "passed"
    for path in check_ci.REQUIRED_WORKFLOWS:
        assert (REPO_ROOT / path).is_file()


@pytest.mark.parametrize("suffix", ["@main", "@refs/heads/main"])
def test_branch_qualified_workflow_paths_keep_exact_run_identity(suffix):
    items = runs()
    for run in items:
        run["path"] += suffix
    assert evaluate(items)["state"] == "passed"
    items[0]["head_sha"] = "b" * 40
    with pytest.raises(ValueError, match="outside the requested"):
        evaluate(items)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "neutral", "timed_out", "action_required", "stale"])
def test_terminal_non_success_never_passes(conclusion):
    items = runs()
    items[0]["conclusion"] = conclusion
    assert evaluate(items)["state"] == "failed"


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_pending_does_not_pass_even_with_old_success_conclusion(status):
    items = runs()
    items[0]["status"] = status
    assert evaluate(items)["state"] == "pending"


def test_missing_workflow_is_not_empty_success():
    assert evaluate([])["state"] == "missing"
    result = evaluate(runs()[:-1])
    assert result["state"] == "missing"
    assert result["workflows"][-1] == {"workflow": check_ci.REQUIRED_WORKFLOWS[-1], "state": "missing"}


def test_newer_failed_run_and_current_rerun_attempt_replace_old_success():
    items = runs()
    newer = {**items[0], "id": 10, "conclusion": "failure"}
    assert evaluate([newer, *items])["state"] == "failed"
    rerun = {**newer, "run_attempt": 2, "status": "in_progress", "conclusion": None}
    result = evaluate([*items, rerun, newer])
    assert result["state"] == "pending"
    assert result["workflows"][0]["attempt"] == 2


@pytest.mark.parametrize("field,value", [("head_sha", "b" * 40), ("head_branch", "dev"), ("event", "pull_request")])
def test_wrong_commit_branch_or_event_is_rejected(field, value):
    items = runs()
    items[0][field] = value
    with pytest.raises(ValueError, match="outside the requested"):
        evaluate(items)


@pytest.mark.parametrize("field,value", [("run_attempt", None), ("html_url", ""), ("status", "unknown"), ("conclusion", None), ("path", None)])
def test_incomplete_run_evidence_is_rejected(field, value):
    items = runs()
    items[0][field] = value
    with pytest.raises(ValueError):
        evaluate(items)


def test_paginated_read_is_get_only_complete_and_bounded(monkeypatch):
    items = runs()
    pages = [{"total_count": 3, "workflow_runs": items[:1]}, {"total_count": 3, "workflow_runs": items[1:]}]
    calls = []

    def invoke(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(pages), "")

    monkeypatch.setattr(check_ci.subprocess, "run", invoke)
    assert check_ci.read_runs(REPO, SHA, "feature/ci", "push", 7) == items
    args, kwargs = calls[0]
    assert args[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
    assert args[-2:] == ["--paginate", "--slurp"]
    assert "branch=feature%2Fci" in args[6]
    assert kwargs["timeout"] == 7


@pytest.mark.parametrize("pages", [[], {}, [{"total_count": 4, "workflow_runs": runs()}],
    [{"total_count": 4, "workflow_runs": [*runs(), runs()[0]]}],
    [{"total_count": 1, "workflow_runs": [None]}],
    [{"total_count": 3, "workflow_runs": runs()[:1]}, {"total_count": 4, "workflow_runs": runs()[1:]}]])
def test_incomplete_or_malformed_pagination_is_unavailable(monkeypatch, pages):
    monkeypatch.setattr(check_ci.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(pages), ""))
    assert check_ci.check(REPO, SHA, "main", "push", wait=False, timeout=30)["state"] == "unavailable"


@pytest.mark.parametrize("error", [FileNotFoundError("gh missing"), subprocess.TimeoutExpired("gh", 1), ValueError("not authorised")])
def test_external_failure_is_reported_not_retried(monkeypatch, error):
    calls = []

    def fail(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(check_ci, "read_runs", fail)
    result = check_ci.check(REPO, SHA, "main", "push", wait=True, timeout=20)
    assert result["state"] == "unavailable"
    assert len(calls) == 1


def test_wait_is_explicit_and_bounded(monkeypatch):
    now = [0.0]
    calls = []
    monkeypatch.setattr(check_ci.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(check_ci.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(check_ci, "read_runs", lambda *args: calls.append(args) or [])
    result = check_ci.check(REPO, SHA, "main", "push", wait=False, timeout=25)
    assert result["state"] == "missing" and len(calls) == 1 and now[0] == 0
    calls.clear()
    result = check_ci.check(REPO, SHA, "main", "push", wait=True, timeout=25)
    assert result["wait_expired"] and result["state"] == "missing"
    assert len(calls) == 3 and now[0] == 25
    assert [call[-1] for call in calls] == [25, 15, 5]


def test_wait_returns_when_all_runs_finish(monkeypatch):
    responses = iter([[], runs()])
    monkeypatch.setattr(check_ci, "read_runs", lambda *args: next(responses))
    monkeypatch.setattr(check_ci.time, "sleep", lambda _: None)
    assert check_ci.check(REPO, SHA, "main", "push", wait=True, timeout=30)["state"] == "passed"


@pytest.mark.parametrize("state,code", check_ci.EXIT_CODES.items())
def test_cli_structural_result_and_exit_status(monkeypatch, capsys, state, code):
    monkeypatch.setattr(check_ci, "check", lambda *a, **k: {"state": state, "workflows": []})
    assert check_ci.main(["--repo", REPO, "--commit", SHA, "--branch", "main", "--json"]) == code
    assert json.loads(capsys.readouterr().out)["state"] == state


@pytest.mark.parametrize("option,value", [("--timeout", "0"), ("--timeout", "nan"), ("--timeout", "inf"), ("--commit", "HEAD"), ("--repo", "../other/repo"), ("--branch", "")])
def test_invalid_cli_identity_or_budget_rejected(option, value):
    with pytest.raises(SystemExit) as error:
        check_ci.main(["--repo", REPO, "--commit", SHA, "--branch", "main", option, value])
    assert error.value.code == 2


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True, timeout=20).stdout.strip()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX contributor hook")
def test_real_commit_is_offline_advisory_and_preserves_local_extension(tmp_path, monkeypatch):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "CI Tests")
    git(tmp_path, "config", "user.email", "ci@example.invalid")
    git(tmp_path, "config", "core.hooksPath", ".githooks")
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    hook = hooks / "post-commit"
    shutil.copyfile(REPO_ROOT / ".githooks/post-commit", hook)
    hook.chmod(0o755)
    tools = tmp_path / "unavailable-tools"
    tools.mkdir()
    for name in ("gh", "python", "python3", "curl"):
        stub = tools / name
        stub.write_text('#!/bin/sh\nprintf "unexpected external tool\\n" > unexpected-tool\nexit 99\n')
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    git(tmp_path, "add", ".githooks")
    result = subprocess.run(["git", "commit", "--allow-empty", "-m", "offline"], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert git(tmp_path, "rev-parse", "HEAD") in result.stderr
    assert "CI has not been verified" in result.stderr
    assert not (tmp_path / "unexpected-tool").exists()
    local = tmp_path / ".git/hooks/post-commit"
    local.write_text("#!/bin/sh\nprintf 'local extension invoked\\n' >&2\nexit 7\n")
    local.chmod(0o755)
    result = subprocess.run(["git", "commit", "--allow-empty", "-m", "extension"], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert "local extension invoked" in result.stderr
    assert "local extension failed; the commit already exists" in result.stderr
    assert git(tmp_path, "log", "-1", "--format=%s") == "extension"
    linked = tmp_path / "linked"
    git(tmp_path, "worktree", "add", "-b", "linked", str(linked))
    result = subprocess.run(["git", "commit", "--allow-empty", "-m", "linked"], cwd=linked, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert "local extension invoked" in result.stderr
    assert "CI has not been verified" in result.stderr
    assert not (linked / "unexpected-tool").exists()
    local.unlink()
    local.symlink_to(hook)
    result = subprocess.run(["git", "commit", "--allow-empty", "-m", "self-link"], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert result.stderr.count("CI has not been verified") == 1
