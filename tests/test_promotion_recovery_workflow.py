"""Recovery transactions settle only through their immutable result record."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from test_promotion import REPO_ROOT, _dev_repo, _git, _passed, _receipt, _request
from _promotion import git, recovery_workflow, workflow
from _promotion.model import PromotionError


def _ready(tmp_path: Path):
    root, shas = _dev_repo(tmp_path)
    brief = root / ".canaries" / "pre-recovery.md"
    brief.write_text((REPO_ROOT / ".canaries/pre-recovery.md").read_text(encoding="utf-8"), encoding="utf-8")
    _git(root, "add", str(brief.relative_to(root)))
    _git(root, "commit", "-m", "chore: add recovery review brief")
    _git(root, "push", "origin", "dev")
    _receipt(root)
    old_candidate = workflow.prepare(root, _request(None), run_checks=None)
    old_dev = workflow.finish(root, "promotion/v1.1.0", ci=_passed)
    baseline = shas["main"]
    hotfix = git.commit_tree(
        root, git.tree_of(root, baseline), (baseline,), "Exceptional main correction\n",
        git.identity_env("2026-09-24T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"),
    )
    _git(root, "push", "origin", f"{hotfix}:refs/heads/main")
    plan = recovery_workflow.plan(root, run_checks=lambda _path: None)
    (root / ".canary--pre-recovery").write_text(
        f"Plan: {plan.sha}\n" + "".join(f"[{i}] Recovery review: done\n" for i in range(1, 7)),
        encoding="utf-8",
    )
    return root, plan, old_candidate, old_dev, hotfix


def test_stage_apply_retries_use_records_and_check_fresh_candidate_ci(tmp_path):
    root, plan, old_candidate, old_dev, hotfix = _ready(tmp_path)
    observed = []

    assert recovery_workflow.stage(root, plan.sha) == "staged"
    assert not (root / ".canary--pre-recovery").exists()
    assert recovery_workflow.stage(root, plan.sha) == "staged"
    before = git.observe_remote(root)
    assert before.main == hotfix and before.unreleased == old_candidate and before.dev == old_dev

    def ci(_root, sha, branch):
        observed.append((sha, branch))
        return {"state": "passed"}

    assert recovery_workflow.apply(root, plan.sha, ci=ci) == "applied"
    assert observed == [(plan.entries[0].new_candidate, plan.entries[0].new_ref)]
    assert recovery_workflow.apply(root, plan.sha, ci=lambda *_: pytest.fail("CI must not rerun")) == "applied"
    after = git.observe_remote(root)
    assert after.main == hotfix and after.unreleased == plan.target_unreleased and after.dev == plan.target_dev
    assert recovery_workflow.status(root, plan.sha)["state"] == "applied"
    with pytest.raises(PromotionError, match="cannot be aborted"):
        recovery_workflow.abort(root, plan.sha)


def test_abort_restores_only_owned_candidate_and_retains_shared_refs(tmp_path):
    root, plan, old_candidate, old_dev, hotfix = _ready(tmp_path)
    recovery_workflow.stage(root, plan.sha)
    assert recovery_workflow.abort(root, plan.sha) == "aborted"
    assert recovery_workflow.abort(root, plan.sha) == "aborted"
    observed = git.observe_remote(root)
    assert observed.main == hotfix and observed.unreleased == old_candidate and observed.dev == old_dev
    assert git.remote_branch(root, plan.entries[0].new_ref) == old_candidate
    with pytest.raises(PromotionError, match="aborted"):
        recovery_workflow.apply(root, plan.sha, ci=_passed)


def test_losing_clone_adopts_applied_record_and_preserves_private_suffix(tmp_path):
    root, plan, _old_candidate, old_dev, _hotfix = _ready(tmp_path)
    loser = tmp_path / "loser"
    _git(tmp_path, "clone", str(tmp_path / "repo.git"), str(loser))
    _git(loser, "config", "user.name", "Promotion Tests")
    _git(loser, "config", "user.email", "promotion@example.invalid")
    _git(loser, "switch", "dev")
    (loser / "private.txt").write_text("private suffix\n", encoding="utf-8")
    _git(loser, "add", "private.txt")
    _git(loser, "commit", "-m", "WIP: private suffix")
    private = git.rev(loser, "dev")
    assert git.rev(loser, "refs/remotes/origin/unreleased") == plan.snapshot["unreleased"]

    recovery_workflow.stage(root, plan.sha)
    recovery_workflow.apply(root, plan.sha, ci=_passed)
    aligned = workflow.adopt(loser)

    assert git.rev(loser, "refs/remotes/origin/unreleased") == plan.target_unreleased
    assert git.is_ancestor(loser, old_dev, aligned)
    assert git.is_ancestor(loser, plan.target_dev, aligned)
    assert git.read_commit(loser, aligned).message == git.read_commit(loser, private).message
    assert (loser / "private.txt").read_text(encoding="utf-8") == "private suffix\n"
    assert workflow.adopt(loser) == aligned


def test_losing_clone_adopts_two_applied_recoveries(tmp_path):
    root, first, _old_candidate, _old_dev, _hotfix = _ready(tmp_path)
    loser = tmp_path / "loser"
    _git(tmp_path, "clone", str(tmp_path / "repo.git"), str(loser))
    _git(loser, "config", "user.name", "Promotion Tests")
    _git(loser, "config", "user.email", "promotion@example.invalid")
    _git(loser, "switch", "dev")
    (loser / "private.txt").write_text("keep this\n", encoding="utf-8")
    _git(loser, "add", "private.txt")
    _git(loser, "commit", "-m", "WIP: private suffix")
    private = git.rev(loser, "dev")

    recovery_workflow.stage(root, first.sha)
    recovery_workflow.apply(root, first.sha, ci=_passed)
    workflow.adopt(root)
    workflow.publish(root, first.target_unreleased, ci=_passed)
    next_hotfix = git.commit_tree(root, git.tree_of(root, first.target_unreleased),
        (first.target_unreleased,),
        "Second exceptional main correction\n",
        git.identity_env("2026-09-25T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"))
    _git(root, "push", "origin", f"{next_hotfix}:refs/heads/main")
    second = recovery_workflow.plan(root, run_checks=lambda _path: None)
    (root / ".canary--pre-recovery").write_text(
        f"Plan: {second.sha}\n" + "".join(f"[{i}] Recovery review: done\n" for i in range(1, 7)),
        encoding="utf-8",
    )
    recovery_workflow.stage(root, second.sha)
    assert not second.entries
    recovery_workflow.apply(root, second.sha, ci=lambda *_: pytest.fail("empty queue needs no candidate CI"))
    after_second = git.observe_remote(root)
    assert after_second.main == next_hotfix
    assert after_second.unreleased == next_hotfix
    assert after_second.dev == second.target_dev
    assert recovery_workflow.apply(root, first.sha,
        ci=lambda *_: pytest.fail("applied retry must not rerun CI")) == "applied"

    aligned = workflow.adopt(loser)
    assert git.rev(loser, "refs/remotes/origin/unreleased") == second.target_unreleased
    assert git.is_ancestor(loser, second.target_dev, aligned)
    assert git.read_commit(loser, aligned).message == git.read_commit(loser, private).message
    assert (loser / "private.txt").read_text(encoding="utf-8") == "keep this\n"


def test_adoption_crosses_ordinary_finish_between_two_recoveries(tmp_path):
    root, first, _old_candidate, _old_dev, _hotfix = _ready(tmp_path)
    loser = tmp_path / "loser"
    _git(tmp_path, "clone", str(tmp_path / "repo.git"), str(loser))
    _git(loser, "config", "user.name", "Promotion Tests")
    _git(loser, "config", "user.email", "promotion@example.invalid")
    _git(loser, "switch", "dev")
    (loser / "private.txt").write_text("keep ordinary gap work\n", encoding="utf-8")
    _git(loser, "add", "private.txt")
    _git(loser, "commit", "-m", "WIP: private suffix")

    recovery_workflow.stage(root, first.sha)
    recovery_workflow.apply(root, first.sha, ci=_passed)
    workflow.adopt(root)
    (root / "notes.txt").write_text("fifth\n", encoding="utf-8")
    _git(root, "add", "notes.txt")
    _git(root, "commit", "-m", "WIP: fifth")
    _git(root, "push", "origin", "dev")
    _receipt(root)
    ordinary = workflow.prepare(root, _request(None, "1.2.0"), run_checks=None)
    workflow.finish(root, "promotion/v1.2.0", ci=_passed)
    workflow.publish(root, first.target_unreleased, ci=_passed)
    second_main = git.commit_tree(root, git.tree_of(root, first.target_unreleased),
        (first.target_unreleased,), "Second exceptional main correction\n",
        git.identity_env("2026-09-25T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"))
    _git(root, "push", "origin", f"{second_main}:refs/heads/main")
    second = recovery_workflow.plan(root, run_checks=lambda _path: None)
    assert second.snapshot["unreleased"] == ordinary
    (root / ".canary--pre-recovery").write_text(
        f"Plan: {second.sha}\n" + "".join(f"[{i}] Recovery review: done\n" for i in range(1, 7)),
        encoding="utf-8",
    )
    recovery_workflow.stage(root, second.sha)
    recovery_workflow.apply(root, second.sha, ci=_passed)

    aligned = workflow.adopt(loser)
    assert git.rev(loser, "refs/remotes/origin/unreleased") == second.target_unreleased
    assert git.is_ancestor(loser, second.target_dev, aligned)
    assert (loser / "private.txt").read_text(encoding="utf-8") == "keep ordinary gap work\n"


def test_lost_stage_and_apply_responses_settle_from_records(tmp_path, monkeypatch):
    root, plan, _old_candidate, _old_dev, _hotfix = _ready(tmp_path)
    actual_run = git.run

    def lose_push_reply(path, *args, **kwargs):
        result = actual_run(path, *args, **kwargs)
        if args and args[0] == "push" and "--atomic" in args and result.returncode == 0:
            return subprocess.CompletedProcess(result.args, 1, result.stdout, "response lost")
        return result

    monkeypatch.setattr(git, "run", lose_push_reply)
    assert recovery_workflow.stage(root, plan.sha) == "staged"
    assert recovery_workflow.apply(root, plan.sha, ci=_passed) == "applied"
    assert recovery_workflow.status(root, plan.sha)["state"] == "applied"


def test_lost_abort_response_settles_from_aborted_result(tmp_path, monkeypatch):
    root, plan, old_candidate, old_dev, hotfix = _ready(tmp_path)
    recovery_workflow.stage(root, plan.sha)
    actual_run = git.run

    def lose_abort_reply(path, *args, **kwargs):
        result = actual_run(path, *args, **kwargs)
        if args and args[0] == "push" and "--atomic" in args and result.returncode == 0:
            return subprocess.CompletedProcess(result.args, 1, result.stdout, "response lost")
        return result

    monkeypatch.setattr(git, "run", lose_abort_reply)
    assert recovery_workflow.abort(root, plan.sha) == "aborted"
    assert recovery_workflow.status(root, plan.sha)["state"] == "aborted"
    observed = git.observe_remote(root)
    assert observed.main == hotfix and observed.unreleased == old_candidate and observed.dev == old_dev


def test_competing_abort_wins_while_apply_checks_ci(tmp_path):
    root, plan, old_candidate, old_dev, hotfix = _ready(tmp_path)
    recovery_workflow.stage(root, plan.sha)
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(tmp_path / "repo.git"), str(other))

    def abort_during_ci(_root, _sha, _branch):
        assert recovery_workflow.abort(other, plan.sha) == "aborted"
        return {"state": "passed"}

    with pytest.raises(PromotionError, match="changed during CI"):
        recovery_workflow.apply(root, plan.sha, ci=abort_during_ci)
    observed = git.observe_remote(root)
    assert observed.main == hotfix and observed.unreleased == old_candidate and observed.dev == old_dev
    assert recovery_workflow.status(root, plan.sha)["state"] == "aborted"


def test_apply_rechecks_after_ci_and_abort_can_restore_despite_dev_advance(tmp_path):
    root, plan, old_candidate, old_dev, _hotfix = _ready(tmp_path)
    recovery_workflow.stage(root, plan.sha)

    def advance_dev(_root, _sha, _branch):
        later = git.commit_tree(root, git.tree_of(root, old_dev), (old_dev,),
            "WIP: other promoter advanced dev\n",
            git.identity_env("2026-09-25T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"))
        _git(root, "push", "origin", f"{later}:refs/heads/dev")
        return {"state": "passed"}

    with pytest.raises(PromotionError, match="changed during CI"):
        recovery_workflow.apply(root, plan.sha, ci=advance_dev)
    assert recovery_workflow.abort(root, plan.sha) == "aborted"
    assert git.remote_branch(root, plan.entries[0].new_ref) == old_candidate
    assert git.observe_remote(root).dev != old_dev


def test_applied_retry_accepts_later_dev_and_reports_incompatible_main(tmp_path):
    root, plan, _old_candidate, _old_dev, hotfix = _ready(tmp_path)
    recovery_workflow.stage(root, plan.sha)
    recovery_workflow.apply(root, plan.sha, ci=_passed)
    later_dev = git.commit_tree(root, git.tree_of(root, plan.target_dev),
        (plan.target_dev,), "WIP: later shared dev\n",
        git.identity_env("2026-09-25T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"))
    _git(root, "push", "origin", f"{later_dev}:refs/heads/dev")
    assert recovery_workflow.apply(root, plan.sha, ci=lambda *_: pytest.fail("CI must not rerun")) == "applied"
    later_main = git.commit_tree(root, git.tree_of(root, hotfix), (hotfix,),
        "Another direct-main bypass\n",
        git.identity_env("2026-09-25T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"))
    _git(root, "push", "origin", f"{later_main}:refs/heads/main")
    with pytest.raises(PromotionError, match="applied recovery .* diagnosis required: main is outside unreleased"):
        recovery_workflow.apply(root, plan.sha, ci=lambda *_: pytest.fail("CI must not rerun"))
    assert recovery_workflow.status(root, plan.sha)["state"] == "applied"


def test_narrow_clone_prior_ledger_ignores_quoted_trailer_in_wip_body(tmp_path):
    root, _plan, old_candidate, _old_dev, _hotfix = _ready(tmp_path)
    _git(root, "commit", "--allow-empty", "-m", "WIP: quote old logs",
         "-m", "An example contains Brain-Dev-Source: abc in its body.")
    _git(root, "push", "origin", "dev")
    git.fetch_refs(root)
    assert recovery_workflow._prior_ledger_from_dev(root) == old_candidate


def test_empty_queue_without_unreleased_creates_it_under_absent_lease(tmp_path):
    root, shas = _dev_repo(tmp_path)
    brief = root / ".canaries" / "pre-recovery.md"
    brief.write_text((REPO_ROOT / ".canaries/pre-recovery.md").read_text(encoding="utf-8"), encoding="utf-8")
    _git(root, "add", str(brief.relative_to(root)))
    _git(root, "commit", "-m", "chore: add recovery review brief")
    _git(root, "push", "origin", "dev")
    old_dev = git.rev(root, "dev")
    hotfix = git.commit_tree(root, git.tree_of(root, shas["main"]),
        (shas["main"],), "Exceptional main correction\n",
        git.identity_env("2026-09-24T00:00:00+00:00", "Promotion Tests", "promotion@example.invalid"))
    _git(root, "push", "origin", f"{hotfix}:refs/heads/main")
    assert git.observe_remote(root).unreleased is None
    plan = recovery_workflow.plan(root, run_checks=lambda _path: None)
    assert not plan.entries and plan.snapshot["unreleased"] is None
    (root / ".canary--pre-recovery").write_text(
        f"Plan: {plan.sha}\n" + "".join(f"[{i}] Recovery review: done\n" for i in range(1, 7)),
        encoding="utf-8",
    )
    recovery_workflow.stage(root, plan.sha)
    assert git.observe_remote(root).unreleased is None
    recovery_workflow.apply(root, plan.sha,
        ci=lambda *_: pytest.fail("empty queue needs no candidate CI"))
    observed = git.observe_remote(root)
    assert observed.main == hotfix and observed.unreleased == hotfix
    assert observed.dev == plan.target_dev and git.is_ancestor(root, old_dev, observed.dev)
