"""Disposable Git repositories exercise ownership-aware promotion housekeeping."""
import subprocess

import pytest

from test_promotion import _dev_repo, _git, _passed, _receipt, _request, _write
from _promotion import cleanup, git, model, workflow


def _candidate(tmp_path):
    root, _ = _dev_repo(tmp_path)
    _receipt(root)
    sha = workflow.prepare(root, _request(None), run_checks=None)
    return root, sha, "promotion/v1.1.0"


def test_remote_only_discard_requires_and_uses_exact_ownership(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    git.cleanup_local(root, branch)
    with pytest.raises(model.PromotionError, match="expected-sha"):
        workflow.discard(root, branch)
    assert git.remote_branch(root, branch) == sha
    workflow.discard(root, branch, expected_sha=sha)
    assert git.remote_branch(root, branch) is None
    assert not git.rev_exists(root, f"refs/remotes/origin/{branch}")
    workflow.discard(root, branch, expected_sha=sha)


def test_remote_only_discard_from_another_clone_fetches_candidate(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    clone = tmp_path / "other"
    _git(tmp_path, "clone", "--no-local", "--single-branch", "--branch", "dev", str(tmp_path / "repo.git"), str(clone))
    assert _git(clone, "cat-file", "-e", sha, check=False).returncode != 0
    workflow.discard(clone, branch, expected_sha=sha)
    assert git.remote_branch(root, branch) is None


def test_remote_only_discard_preserves_a_different_owner(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    git.cleanup_local(root, branch)
    with pytest.raises(model.PromotionError, match="preserved"):
        workflow.discard(root, branch, expected_sha="f" * 40)
    assert git.remote_branch(root, branch) == sha


def test_cleanup_preview_and_apply_after_later_finish(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    workflow.finish(root, branch, ci=_passed)
    workflow.publish(root, sha, ci=_passed)
    _git(root, "update-ref", f"refs/heads/{branch}", sha)
    _git(root, "update-ref", f"refs/remotes/origin/{branch}", sha)
    _git(root, "worktree", "add", "--detach", str(git.worktree_dir(root, branch)), sha)
    _receipt(root)
    later = workflow.prepare(root, _request(None, "1.2.0"), run_checks=None)
    workflow.finish(root, "promotion/v1.2.0", ci=_passed)
    _git(root, "update-ref", "refs/heads/promotion/v1.2.0", later)
    persistent = {name: git.rev(root, name) for name in ("main", "dev", "origin/unreleased")}
    preview = cleanup.run(root)
    assert any(item["scope"] == "local" and item["state"] == "published" for item in preview["items"])
    assert git.rev_exists(root, f"refs/heads/{branch}")
    result = cleanup.run(root, apply=True)
    assert not result["errors"]
    assert not git.worktree_dir(root, branch).exists()
    assert not git.rev_exists(root, f"refs/heads/{branch}")
    assert not git.rev_exists(root, f"refs/remotes/origin/{branch}")
    assert not git.rev_exists(root, "refs/heads/promotion/v1.2.0")
    assert git.remote_branch(root, "promotion/v1.2.0") == later
    assert persistent == {name: git.rev(root, name) for name in persistent}
    assert not cleanup.run(root, apply=True)["errors"]


@pytest.mark.parametrize("change", ["dirty", "untracked", "foreign-head", "foreign-branch", "other-worktree"])
def test_cleanup_preserves_worktree_edits_and_other_owners(tmp_path, change):
    root, sha, branch = _candidate(tmp_path)
    workflow.finish(root, branch, ci=_passed)
    _git(root, "update-ref", f"refs/heads/{branch}", sha)
    path = git.worktree_dir(root, branch)
    if change == "other-worktree":
        path = tmp_path / "user-checkout"
        _git(root, "worktree", "add", str(path), branch)
    else:
        _git(root, "worktree", "add", "--detach", str(path), sha)
        if change == "dirty":
            _write(path, "notes.txt", "user debugging\n")
        elif change == "untracked":
            _write(path, "debug.txt", "user debugging\n")
        elif change == "foreign-head":
            _git(path, "checkout", "--detach", "HEAD^")
        else:
            _git(path, "switch", "-c", "feature/inspection")
    result = cleanup.run(root, apply=True)
    row = next(item for item in result["items"] if item["scope"] == "local")
    assert row["action"] == "keep"
    assert path.exists()
    assert git.rev(root, branch) == sha
    assert git.remote_branch(root, branch) == sha


def test_cleanup_keeps_active_unknown_and_recovery_refs(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    for ref in ("refs/heads/promotion/notes", "refs/heads/recovery/example/plan"):
        _git(root, "update-ref", ref, sha)
        _git(root, "push", "origin", f"{ref}:{ref}")
    result = cleanup.run(root, apply=True)
    assert all(item["action"] == "keep" for item in result["items"])
    assert git.remote_branch(root, branch) == sha
    assert git.rev(root, "refs/heads/recovery/example/plan") == sha
    assert git.remote_branch(root, "recovery/example/plan") == sha


def test_cleanup_removes_detached_finished_worktree_without_local_ref(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    workflow.finish(root, branch, ci=_passed)
    path = git.worktree_dir(root, branch)
    _git(root, "worktree", "add", "--detach", str(path), sha)
    assert not cleanup.run(root, apply=True)["errors"]
    assert not path.exists()
    assert git.remote_branch(root, branch) == sha


def test_cleanup_partial_local_failure_can_be_retried(tmp_path, monkeypatch):
    root, sha, branch = _candidate(tmp_path)
    workflow.finish(root, branch, ci=_passed)
    workflow.publish(root, sha, ci=_passed)
    _git(root, "update-ref", f"refs/heads/{branch}", sha)
    _git(root, "push", "origin", f"{sha}:refs/heads/{branch}")
    real = git.cleanup_local

    def fail(*args):
        raise model.PromotionError("local cleanup unavailable")

    monkeypatch.setattr(git, "cleanup_local", fail)
    result = cleanup.run(root, apply=True)
    assert result["errors"]
    assert any(item["scope"] == "remote" and item["action"] == "removed" for item in result["items"])
    assert git.remote_branch(root, branch) is None
    assert git.rev(root, branch) == sha
    monkeypatch.setattr(git, "cleanup_local", real)
    assert not cleanup.run(root, apply=True)["errors"]
    assert not git.rev_exists(root, f"refs/heads/{branch}")


def test_remote_cleanup_lease_preserves_concurrent_replacement(tmp_path, monkeypatch):
    root, sha, branch = _candidate(tmp_path)
    workflow.finish(root, branch, ci=_passed)
    _git(root, "push", "origin", f"{sha}:refs/heads/main")
    replacement = git.rev(root, "dev")
    real = git.run

    def racing(cwd, *args, **kwargs):
        if args[:2] == ("push", "--atomic"):
            _git(root, "push", "--force", "origin", f"{replacement}:refs/heads/{branch}")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(git, "run", racing)
    assert cleanup.run(root, apply=True)["errors"]
    assert git.remote_branch(root, branch) == replacement


def test_settled_remote_deletion_is_success_despite_transport_error(tmp_path, monkeypatch):
    root, sha, branch = _candidate(tmp_path)
    workflow.finish(root, branch, ci=_passed)
    _git(root, "push", "origin", f"{sha}:refs/heads/main")
    real = git.run

    def lost_response(cwd, *args, **kwargs):
        result = real(cwd, *args, **kwargs)
        if args[:2] == ("push", "--atomic"):
            return subprocess.CompletedProcess(args, 1, "", "connection lost after commit")
        return result

    monkeypatch.setattr(git, "run", lost_response)
    assert not cleanup.run(root, apply=True)["errors"]
    assert git.remote_branch(root, branch) is None


def test_finish_cleans_remote_only_candidate_detached_worktree(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    _git(root, "update-ref", "-d", f"refs/heads/{branch}", sha)
    assert git.worktree_dir(root, branch).exists()
    replay = workflow.finish(root, branch, ci=_passed)
    assert git.rev(root, "dev") == replay
    assert not git.worktree_dir(root, branch).exists()
    assert workflow.finish(root, branch, ci=lambda *_: pytest.fail("already settled")) == replay


def test_discard_preserves_dirty_candidate_and_remote_recovery_ref(tmp_path):
    root, sha, branch = _candidate(tmp_path)
    _write(git.worktree_dir(root, branch), "notes.txt", "unfinished debugging\n")
    with pytest.raises(model.PromotionError, match="dirty"):
        workflow.discard(root, branch)
    assert git.remote_branch(root, branch) == sha
    assert git.rev(root, branch) == sha


def test_cleanup_rechecks_checkout_before_deleting_local_ref(tmp_path, monkeypatch):
    root, sha, branch = _candidate(tmp_path)
    foreign = tmp_path / "foreign-checkout"
    real = git.run

    def race(cwd, *args, **kwargs):
        result = real(cwd, *args, **kwargs)
        if args[:2] == ("worktree", "remove"):
            _git(root, "worktree", "add", str(foreign), branch)
        return result

    monkeypatch.setattr(git, "run", race)
    with pytest.raises(model.PromotionError, match="checked out outside"):
        git.cleanup_local(root, branch, sha)
    assert git.rev(root, branch) == sha
    assert foreign.exists()


def test_cleanup_local_ref_cas_preserves_concurrent_replacement(tmp_path, monkeypatch):
    root, sha, branch = _candidate(tmp_path)
    replacement = git.rev(root, "dev")
    real = git.run

    def race(cwd, *args, **kwargs):
        if args[:3] == ("update-ref", "-d", f"refs/heads/{branch}"):
            _git(root, "update-ref", f"refs/heads/{branch}", replacement, sha)
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(git, "run", race)
    with pytest.raises(model.PromotionError, match="update-ref"):
        git.cleanup_local(root, branch, sha)
    assert git.rev(root, branch) == replacement
