"""Guarded lifecycle continuation across the real child/reply boundary."""

import json
import os
import subprocess

import pytest

from _bootstrap import branch_guard
from _bootstrap.file_lock import vault_mutation_lock
from _bootstrap.local_config import patch_local_config, read_local_config
from _lifecycle import fresh_interpreter
from _lifecycle.semantic_enable import enable_semantic


def git(root, *args):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(root), *args], env=env, check=True,
        capture_output=True, text=True, timeout=5,
    )


@pytest.fixture
def guarded_vault(tmp_path, monkeypatch):
    monkeypatch.delenv(branch_guard.CONTEXT_ENV, raising=False)
    git(tmp_path, "init", "-q", "-b", "dev")
    patch_local_config(tmp_path, lambda document: document.update(
        branch_guard={"allowed_branches": ["dev", "feature"]},
        defaults={"flags": {"semantic_retrieval": False}},
        unrelated={"keep": "value"},
    ))
    return tmp_path


def test_typed_child_reply_keeps_parent_lost_after_branch_restored(
    guarded_vault, monkeypatch,
):
    root = guarded_vault
    config = root / ".brain/local/config.yaml"
    before = config.read_bytes()
    facts = {"vault": str(root), "observed_branch": "feature"}

    def reply_after_restoring_branch(argv, **options):
        assert branch_guard.observe_git(root).branch == "feature"
        assert json.loads(options["env"][branch_guard.CONTEXT_ENV])["branch"] == "dev"
        git(root, "symbolic-ref", "HEAD", "refs/heads/dev")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"error": {
            "type": "BranchGuardError", "message": "Child refused continuation",
            "reason": "branch-changed", "facts": facts,
        }}), "")

    monkeypatch.setattr(fresh_interpreter, "run_managed", reply_after_restoring_branch)
    with vault_mutation_lock(root), branch_guard.operation(root) as operation:
        git(root, "symbolic-ref", "HEAD", "refs/heads/feature")
        with pytest.raises(branch_guard.BranchGuardError) as caught:
            fresh_interpreter.run_lifecycle_in_fresh_interpreter(
                enable_semantic, root, provision=False, lock_held=True,
            )
        assert caught.value.reason == "branch-changed"
        assert caught.value.facts == {**facts, "reason": "branch-changed"}
        assert branch_guard.observe_git(root).branch == "dev"
        assert operation.lost
        with pytest.raises(branch_guard.BranchGuardError,
                           match="unverified-recovery-context"):
            branch_guard.checkpoint(root)
    assert config.read_bytes() == before


@pytest.mark.parametrize("switch_branch", [False, True], ids=["same-branch", "changed-branch"])
def test_real_child_inherits_guard_and_parent_held_lock(guarded_vault, switch_branch):
    root = guarded_vault
    config = root / ".brain/local/config.yaml"
    before = config.read_bytes()
    original = read_local_config(root).document

    with vault_mutation_lock(root), branch_guard.operation(root) as operation:
        if switch_branch:
            # Both branches are allowed: recapturing fresh permission would
            # incorrectly enable the flag instead of refusing continuation.
            git(root, "symbolic-ref", "HEAD", "refs/heads/feature")
            with pytest.raises(branch_guard.BranchGuardError) as caught:
                fresh_interpreter.run_lifecycle_in_fresh_interpreter(
                    enable_semantic, root, provision=False, lock_held=True, timeout=20,
                )
            assert caught.value.reason == "unverified-recovery-context"
            assert config.read_bytes() == before
            git(root, "symbolic-ref", "HEAD", "refs/heads/dev")
            assert operation.lost
            with pytest.raises(branch_guard.BranchGuardError,
                               match="unverified-recovery-context"):
                branch_guard.checkpoint(root)
        else:
            result = fresh_interpreter.run_lifecycle_in_fresh_interpreter(
                enable_semantic, root, provision=False, lock_held=True, timeout=20,
            )
            assert result["status"] == "ok"
            assert any(step["name"] == "semantic_config" and step["status"] == "changed"
                       for step in result["steps"])
            assert read_local_config(root).document == {
                **original, "defaults": {"flags": {"semantic_retrieval": True}},
            }
            branch_guard.checkpoint(root)
            assert not operation.lost
