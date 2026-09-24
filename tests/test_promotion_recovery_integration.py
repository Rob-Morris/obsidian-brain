"""Recovery admission through the real CLI and installed pre-push hook."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from _promotion import git, recovery_plan, recovery_workflow
from _promotion.model import PromotionError
from test_promotion import REPO_ROOT, _dev_repo, _git, _passed, _receipt, _request, _write, promotion


def _install_runtime(root: Path) -> None:
    for name in ("promotion.py", "canary_receipt.py", "check_ci.py"):
        shutil.copy2(REPO_ROOT / "src/scripts" / name, root / "src/scripts" / name)
    shutil.copytree(REPO_ROOT / "src/scripts/_promotion", root / "src/scripts/_promotion",
                    ignore=shutil.ignore_patterns("__pycache__"))
    _write(root, ".canaries/pre-recovery.md", (REPO_ROOT / ".canaries/pre-recovery.md").read_text())
    _write(root, ".githooks/pre-push", (REPO_ROOT / ".githooks/pre-push").read_text())
    (root / ".githooks/pre-push").chmod(0o755)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "WIP: install recovery contributor tooling")
    _git(root, "push", "origin", "dev")
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    with (root / ".git/info/exclude").open("a") as exclude:
        exclude.write("\n.venv/\n")


def _ready(tmp_path: Path):
    root, _ = _dev_repo(tmp_path)
    _install_runtime(root)
    _receipt(root)
    promotion.prepare(root, _request(None), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    bypass = tmp_path / "direct-main"
    _git(tmp_path, "clone", str(tmp_path / "repo.git"), str(bypass))
    _git(bypass, "config", "user.name", "Direct Main Test")
    _git(bypass, "config", "user.email", "direct@example.invalid")
    _write(bypass, "direct-main.txt", "Exceptional published fix\n")
    _git(bypass, "add", "direct-main.txt")
    _git(bypass, "commit", "-m", "chore: direct-main fixture")
    _git(bypass, "push", "origin", "main")
    planned = recovery_plan.build_plan(root, run_checks=None)
    _git(root, "config", "core.hooksPath", ".githooks")
    return root, planned


def _recovery_receipt(root: Path, sha: str) -> None:
    _write(root, ".canary--pre-recovery", f"Plan: {sha}\n" + "".join(
        f"[{index}] Review: done, fixture review\n" for index in range(1, 7)
    ))


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(root / "src/scripts/promotion.py"),
                           "--repo", str(root), "recover", *args],
                          capture_output=True, text=True, check=False)


def test_real_pre_push_accepts_stage_and_application_without_main_write(tmp_path):
    root, planned = _ready(tmp_path)
    main = git.remote_branch(root, "main")
    _recovery_receipt(root, planned.sha)
    staged = _cli(root, "stage", planned.sha)
    assert staged.returncode == 0, staged.stderr
    assert not (root / ".canary--pre-recovery").exists()
    assert git.remote_branch(root, "dev") == planned.snapshot["dev"]
    assert git.remote_branch(root, "unreleased") == planned.snapshot["unreleased"]
    observed = []

    def check(_root, commit, branch):
        observed.append((commit, branch))
        return {"state": "passed"}

    recovery_workflow.apply(root, planned.sha, ci=check)
    assert observed == [(entry.new_candidate, entry.new_ref) for entry in planned.entries]
    assert git.remote_branch(root, "main") == main
    assert git.remote_branch(root, "unreleased") == planned.target_unreleased
    assert git.remote_branch(root, "dev") == planned.target_dev
    status = _cli(root, "status", planned.sha, "--json")
    assert status.returncode == 0, status.stderr
    assert "applied" in status.stdout


def test_real_pre_push_accepts_owned_abort_and_rejects_later_apply(tmp_path):
    root, planned = _ready(tmp_path)
    _recovery_receipt(root, planned.sha)
    recovery_workflow.stage(root, planned.sha)
    aborted = _cli(root, "abort", planned.sha)
    assert aborted.returncode == 0, aborted.stderr
    assert git.remote_branch(root, "main") == planned.snapshot["main"]
    assert git.remote_branch(root, "dev") == planned.snapshot["dev"]
    assert git.remote_branch(root, "unreleased") == planned.snapshot["unreleased"]
    for entry in planned.entries:
        assert git.remote_branch(root, entry.new_ref) == entry.new_ref_old
    with pytest.raises(PromotionError, match="aborted"):
        recovery_workflow.apply(root, planned.sha, ci=lambda *_: pytest.fail("aborted plan queried CI"))


def test_stage_receipt_is_bound_to_the_reviewed_plan(tmp_path):
    root, planned = _ready(tmp_path)
    _recovery_receipt(root, "f" * 40)
    failed = _cli(root, "stage", planned.sha)
    assert failed.returncode == 2
    assert "receipt" in failed.stderr
    assert "Traceback" not in failed.stderr
    assert git.remote_branch(root, f"recovery/{planned.sha}/plan") is None
    assert (root / ".canary--pre-recovery").exists()


def test_each_rebuilt_version_needs_passing_ci_before_any_ledger_update(tmp_path):
    root, _ = _dev_repo(tmp_path)
    _install_runtime(root)
    for version in ("1.1.0", "1.2.0"):
        _write(root, "version-content.txt", f"Work for {version}\n")
        _git(root, "add", "version-content.txt")
        _git(root, "commit", "-m", f"WIP: content for {version}")
        _git(root, "push", "origin", "dev")
        _receipt(root)
        promotion.prepare(root, _request(None, version), run_checks=None)
        promotion.finish(root, f"promotion/v{version}", ci=_passed)
    baseline = git.rev(root, "main")
    hotfix = git.commit_tree(root, git.tree_of(root, baseline), (baseline,),
                            "Exceptional main change\n", git.replay_env(root, baseline))
    _git(root, "push", "origin", f"{hotfix}:refs/heads/main")
    planned = recovery_plan.build_plan(root, run_checks=None)
    assert len(planned.entries) == 2
    _git(root, "config", "core.hooksPath", ".githooks")
    _recovery_receipt(root, planned.sha)
    recovery_workflow.stage(root, planned.sha)
    expected = [(entry.new_candidate, entry.new_ref) for entry in planned.entries]
    before = git.observe_remote(root)
    for state in ("missing", "pending", "failed", "unavailable"):
        calls = []

        def check(_root, sha, branch):
            calls.append((sha, branch))
            return {"state": state if sha == planned.entries[-1].new_candidate else "passed"}

        with pytest.raises(PromotionError, match="CI"):
            recovery_workflow.apply(root, planned.sha, ci=check)
        assert calls == expected
        assert git.observe_remote(root) == before
        assert git.remote_branch(root, f"recovery/{planned.sha}/result") is None
    recovery_workflow.apply(root, planned.sha, ci=_passed)
    assert git.remote_branch(root, "main") == hotfix
    assert git.remote_branch(root, "unreleased") == planned.target_unreleased


def test_atomic_push_support_is_required_without_sequential_fallback(tmp_path):
    root, planned = _ready(tmp_path)
    _git(tmp_path / "repo.git", "config", "receive.advertiseAtomic", "false")
    before = git.observe_remote(root)
    _recovery_receipt(root, planned.sha)
    with pytest.raises(PromotionError, match="atomic|stage"):
        recovery_workflow.stage(root, planned.sha)
    assert git.observe_remote(root) == before
    assert git.remote_branch(root, f"recovery/{planned.sha}/plan") is None
    for entry in planned.entries:
        assert git.remote_branch(root, entry.new_ref) == entry.new_ref_old
    assert (root / ".canary--pre-recovery").exists()


@pytest.mark.parametrize("retain_result", [True, False])
def test_narrow_clone_without_ledger_tracking_validates_recovery_before_adopting(tmp_path, retain_result):
    root, planned = _ready(tmp_path)
    stale = tmp_path / "narrow"
    _git(tmp_path, "clone", "--single-branch", "--branch", "dev",
         str(tmp_path / "repo.git"), str(stale))
    _git(stale, "config", "user.name", "Private Work")
    _git(stale, "config", "user.email", "private@example.invalid")
    _write(stale, "private.txt", "Work outside the shared recovery\n")
    _git(stale, "add", "private.txt")
    _git(stale, "commit", "-m", "WIP: keep private suffix")
    private_tip = git.rev(stale, "HEAD")
    assert not git.rev_exists(stale, "refs/remotes/origin/unreleased")
    _recovery_receipt(root, planned.sha)
    recovery_workflow.stage(root, planned.sha)
    recovery_workflow.apply(root, planned.sha, ci=_passed)
    if not retain_result:
        # Simulate a bypassed record deletion in the disposable remote only.
        _git(root, "-c", "core.hooksPath=/dev/null", "push", "origin",
             f":refs/heads/recovery/{planned.sha}/result")
        with pytest.raises(PromotionError, match="recovery|record"):
            promotion.adopt(stale)
        assert git.rev(stale, "HEAD") == private_tip
        assert not git.rev_exists(stale, "refs/remotes/origin/unreleased")
    else:
        adopted = promotion.adopt(stale)
        assert git.is_ancestor(stale, planned.target_dev, adopted)
        assert git.rev(stale, "refs/remotes/origin/unreleased") == planned.target_unreleased
        assert git.read_commit(stale, adopted).message == git.read_commit(stale, private_tip).message
    assert (stale / "private.txt").read_text() == "Work outside the shared recovery\n"


@pytest.mark.parametrize("command", ["stage", "status", "apply", "abort"])
def test_cli_rejects_non_full_recovery_identity(tmp_path, command):
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    completed = subprocess.run([sys.executable, str(REPO_ROOT / "src/scripts/promotion.py"),
                                "--repo", str(tmp_path), "recover", command, "not-a-plan"],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert "full plan SHA" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_make_recovery_wrappers_preserve_plan_identity():
    for phase in ("stage", "status", "apply", "abort"):
        completed = subprocess.run(["make", "-n", f"promotion-recover-{phase}", f"PLAN={'a' * 40}"],
                                   cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        assert f'recover {phase} "{"a" * 40}"' in completed.stdout
