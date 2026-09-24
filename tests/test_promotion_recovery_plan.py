"""Real Git graph checks for locally sealed promotion recovery plans."""
from __future__ import annotations

from pathlib import Path
import copy
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/scripts"))

from _promotion import candidates, git, workflow, recovery_plan  # noqa: E402
from _promotion.model import PromotionError, PushUpdate, ZERO_SHA  # noqa: E402
from _promotion.recovery_model import canonical_origin  # noqa: E402
from test_promotion import _dev_repo, _git, _release_repo, _request, _receipt, _write  # noqa: E402
import release  # noqa: E402


def _direct_main(root: Path, tmp_path: Path, path: str = "hotfix.txt") -> str:
    worktree = tmp_path / "hotfix"
    _git(root, "worktree", "add", "--detach", str(worktree), "refs/remotes/origin/main")
    _write(worktree, path, "urgent fix\n")
    _git(worktree, "add", path)
    _git(worktree, "commit", "-m", "WIP: direct main fix")
    sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    _git(worktree, "push", "origin", f"{sha}:refs/heads/main")
    _git(root, "worktree", "remove", str(worktree))
    return sha


def _legacy_repo(tmp_path: Path, name: str) -> tuple[Path, str]:
    root = _release_repo(tmp_path, name)
    edits = release.prepare_release(root, core_version="1.1.0",
        summary="Establish the legacy release", release_type="Patch; baseline",
        changes=["Record the established published baseline."],
        release_date="2026-09-20")
    release.apply_release(root, edits)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Establish the legacy release (v1.1.0)")
    baseline = git.rev(root, "HEAD")
    bare = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-u", "origin", "main")
    _git(root, "switch", "-c", "dev")
    _write(root, "notes.txt", "legacy branch work\n")
    _git(root, "add", "notes.txt")
    _git(root, "commit", "-m", "WIP: after legacy baseline")
    _git(root, "push", "-u", "origin", "dev")
    return root, baseline


def test_note_substitutions_use_original_text_once():
    note = "# v1.1.0\nCLI 1.3.0; old 1.1.0 remains an attributed version.\n"
    rendered, substitutions = recovery_plan._substitute_note(note, {
        "core": {"1.1.0": "1.3.0"},
        "cli": {"1.3.0": "1.4.0"},
        "proxy": {},
    })
    assert rendered == "# v1.3.0\nCLI 1.4.0; old 1.3.0 remains an attributed version.\n"
    assert substitutions == [
        {"old": "1.1.0", "new": "1.3.0", "count": 2},
        {"old": "1.3.0", "new": "1.4.0", "count": 1},
    ]


def test_old_candidate_without_body_is_diagnostic():
    with pytest.raises(PromotionError, match="authored commit body"):
        recovery_plan._body("Release (v1.1.0)\n\nBrain-Dev-Source: " + "a" * 40)


@pytest.mark.parametrize("mapping", [
    {"core": {"1.1.0": 17}},
    {"core": {17: "1.2.0"}},
    {"core": ["1.1.0"]},
    ["core"],
])
def test_malformed_version_mapping_is_diagnostic_before_git(tmp_path, mapping):
    with pytest.raises(PromotionError, match="map"):
        recovery_plan.build_plan(tmp_path, version_map=mapping, run_checks=None)


def test_canonical_origin_ignores_github_transport_and_credentials(tmp_path):
    ssh = canonical_origin(tmp_path, "git@github.com:Rob-Morris/obsidian-brain.git")
    https = canonical_origin(tmp_path, "https://user:secret@github.com/rob-morris/OBSIDIAN-BRAIN.git")
    assert ssh == https == "github:rob-morris/obsidian-brain"
    assert "secret" not in https
    assert canonical_origin(tmp_path, "https://github.com/another/obsidian-brain.git") != ssh


def test_sealed_plan_loads_across_github_transports_but_not_forks(tmp_path, monkeypatch):
    root, _shas = _dev_repo(tmp_path)
    _direct_main(root, tmp_path)
    transport = ["git@github.com:Rob-Morris/obsidian-brain.git"]
    original_out = git.out

    def observed_out(repo, *args):
        if args == ("remote", "get-url", "origin"):
            return transport[0]
        return original_out(repo, *args)

    monkeypatch.setattr(git, "out", observed_out)
    plan = recovery_plan.build_plan(root, run_checks=None)
    assert plan.manifest["repository"]["origin"] == "github:rob-morris/obsidian-brain"
    transport[0] = "https://user:secret@github.com/rob-morris/OBSIDIAN-BRAIN.git"
    assert recovery_plan.load_plan(root, plan.sha).sha == plan.sha
    transport[0] = "https://github.com/another/obsidian-brain.git"
    with pytest.raises(PromotionError, match="different repository"):
        recovery_plan.load_plan(root, plan.sha)


def test_rebuild_retains_source_and_old_dev_as_git_history(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    old = workflow.prepare(root, _request(shas["two"]), run_checks=None)
    workflow.finish(root, "promotion/v1.1.0", ci=lambda *_: {"state": "passed"})
    old_dev = git.rev(root, "refs/heads/dev")
    main = _direct_main(root, tmp_path)

    plan = recovery_plan.build_plan(root, run_checks=None)

    assert plan.snapshot == {"main": main, "anchor": shas["main"],
                             "unreleased": old, "unreleased_exists": True, "dev": old_dev}
    assert len(plan.entries) == 1
    entry = plan.entries[0]
    assert entry.old_candidate == old
    assert entry.new_candidate == plan.target_unreleased
    assert git.read_commit(root, entry.new_candidate).parents == (main,)
    assert git.read_commit(root, entry.new_reconciliation).parents == (
        entry.new_candidate, entry.new_source)
    assert git.is_ancestor(root, entry.old_source, plan.sha)
    assert git.is_ancestor(root, entry.new_source, plan.sha)
    assert git.is_ancestor(root, old_dev, plan.target_dev)
    assert git.out(root, "show", f"{plan.target_dev}:notes.txt") == "four"
    assert git.out(root, "show", f"{plan.target_unreleased}:hotfix.txt") == "urgent fix"
    assert recovery_plan.load_plan(root, plan.sha) == plan
    assert git.rev(root, recovery_plan.local_plan_ref(plan.sha)) == plan.sha


def test_two_unpublished_versions_rebuild_in_order_with_exact_notes(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    first = workflow.prepare(root, _request(shas["two"], "1.1.0"), run_checks=None)
    workflow.finish(root, "promotion/v1.1.0", ci=lambda *_: {"state": "passed"})
    _receipt(root)
    second = workflow.prepare(root, _request(None, "1.2.0"), run_checks=None)
    workflow.finish(root, "promotion/v1.2.0", ci=lambda *_: {"state": "passed"})
    old_dev = git.rev(root, "refs/heads/dev")
    main = _direct_main(root, tmp_path)
    old_notes = [
        git.run(root, "show", f"{sha}:docs/changelog/v{version}.md").stdout
        for sha, version in ((first, "1.1.0"), (second, "1.2.0"))
    ]

    plan = recovery_plan.build_plan(root, run_checks=None)

    assert [entry.old_candidate for entry in plan.entries] == [first, second]
    assert [entry.old_version for entry in plan.entries] == ["1.1.0", "1.2.0"]
    assert [entry.new_version for entry in plan.entries] == ["1.1.0", "1.2.0"]
    assert [entry.old_note for entry in plan.entries] == old_notes
    assert [entry.new_note for entry in plan.entries] == old_notes
    for index, entry in enumerate(plan.entries):
        expected_ledger_parent = main if index == 0 else plan.entries[index - 1].new_candidate
        expected_source_parent = main if index == 0 else plan.entries[index - 1].new_reconciliation
        assert git.read_commit(root, entry.new_candidate).parents == (expected_ledger_parent,)
        assert git.read_commit(root, entry.new_source).parents == (expected_source_parent,)
        assert git.read_commit(root, entry.new_reconciliation).parents == (
            entry.new_candidate, entry.new_source)
        assert git.run(root, "show", f"{entry.new_candidate}:docs/changelog/v{entry.new_version}.md").stdout == old_notes[index]
        for retained in (entry.old_candidate, entry.old_source, entry.old_tip,
                         entry.new_candidate, entry.new_source, entry.new_reconciliation):
            assert git.is_ancestor(root, retained, plan.sha)
    assert plan.target_unreleased == plan.entries[-1].new_candidate
    assert git.is_ancestor(root, old_dev, plan.target_dev)
    assert candidates.on_first_parent_line(root, plan.target_unreleased, main)
    assert candidates.on_first_parent_line(root, plan.target_dev, plan.target_unreleased)


def test_empty_queue_without_unreleased_replays_shared_dev(tmp_path):
    root, shas = _dev_repo(tmp_path)
    main = _direct_main(root, tmp_path)

    plan = recovery_plan.build_plan(root, run_checks=None)

    assert plan.entries == ()
    assert plan.snapshot["unreleased"] is None
    assert plan.snapshot["anchor"] == shas["main"]
    assert plan.target_unreleased == main
    assert git.is_ancestor(root, shas["four"], plan.target_dev)
    assert candidates.on_first_parent_line(root, plan.target_dev, main)

    forged = copy.deepcopy(plan.manifest)
    forged["transactions"]["stage"]["refs/heads/main"] = {"old": main, "new": plan.target_dev}
    raw = json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n"
    blob = git.run(root, "hash-object", "-w", "--stdin", input_text=raw).stdout.strip()
    tree = git.run(root, "mktree", input_text=f"100644 blob {blob}\tmanifest.json\n").stdout.strip()
    metadata = plan.manifest["construction"]
    altered = git.commit_tree(root, tree, git.read_commit(root, plan.sha).parents,
        "recovery: seal direct-main plan\n",
        git.identity_env(metadata["date"], metadata["name"], metadata["email"]))
    with pytest.raises(PromotionError, match="transactions"):
        recovery_plan.load_plan(root, altered)


def test_legacy_published_version_is_anchor_for_empty_queue(tmp_path):
    root, baseline = _legacy_repo(tmp_path, "legacy-empty")
    main = _direct_main(root, tmp_path)

    plan = recovery_plan.build_plan(root, run_checks=None)

    assert plan.entries == ()
    assert plan.snapshot["anchor"] == baseline
    assert plan.target_unreleased == main
    assert git.is_ancestor(root, git.rev(root, "refs/heads/dev"), plan.target_dev)


def test_legacy_published_version_is_anchor_before_first_queued_finish(tmp_path):
    root, baseline = _legacy_repo(tmp_path, "legacy-queued")
    _receipt(root)
    queued = workflow.prepare(root, _request(None, "1.2.0"), run_checks=None)
    workflow.finish(root, "promotion/v1.2.0", ci=lambda *_: {"state": "passed"})
    main = _direct_main(root, tmp_path)

    plan = recovery_plan.build_plan(root, run_checks=None)

    assert plan.snapshot["anchor"] == baseline
    assert [entry.old_candidate for entry in plan.entries] == [queued]
    assert git.read_commit(root, plan.entries[0].new_candidate).parents == (main,)


def test_source_conflict_keeps_shared_refs_unchanged(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    old = workflow.prepare(root, _request(shas["two"]), run_checks=None)
    workflow.finish(root, "promotion/v1.1.0", ci=lambda *_: {"state": "passed"})
    old_dev = git.rev(root, "refs/heads/dev")
    _direct_main(root, tmp_path, "notes.txt")
    before = {name: git.remote_branch(root, name) for name in ("main", "dev", "unreleased", "promotion/v1.1.0")}

    with pytest.raises(PromotionError, match="source .* conflicts"):
        recovery_plan.build_plan(root, run_checks=None)

    assert before == {name: git.remote_branch(root, name) for name in before}
    assert git.rev(root, "refs/heads/dev") == old_dev
    assert git.remote_branch(root, "unreleased") == old


def test_uncut_tail_conflict_preserves_remote_refs_and_primary_checkout(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    workflow.prepare(root, _request(shas["four"]), run_checks=None)
    workflow.finish(root, "promotion/v1.1.0", ci=lambda *_: {"state": "passed"})
    _write(root, "tail.txt", "shared uncut work\n")
    _git(root, "add", "tail.txt")
    _git(root, "commit", "-m", "WIP: shared uncut tail")
    _git(root, "push", "origin", "dev")
    old_dev = git.rev(root, "refs/heads/dev")
    _direct_main(root, tmp_path, "tail.txt")
    refs = ("main", "dev", "unreleased", "promotion/v1.1.0")
    before = {name: git.remote_branch(root, name) for name in refs}

    with pytest.raises(PromotionError, match="uncut tail .* conflicts"):
        recovery_plan.build_plan(root, run_checks=None)

    assert {name: git.remote_branch(root, name) for name in refs} == before
    assert git.rev(root, "refs/heads/dev") == old_dev
    assert git.run(root, "status", "--porcelain", "--untracked-files=all").stdout == ""
    assert git.out(root, "for-each-ref", "--format=%(refname)", "refs/recovery") == ""


def test_foreign_canonical_candidate_name_refuses_without_clobber(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    queued = workflow.prepare(root, _request(shas["two"]), run_checks=None)
    workflow.finish(root, "promotion/v1.1.0", ci=lambda *_: {"state": "passed"})
    main = _direct_main(root, tmp_path)
    foreign = git.commit_tree(root, git.tree_of(root, queued), (queued,),
        "foreign candidate name owner\n",
        git.identity_env("2026-09-22T00:00:00+00:00", "Other", "other@example.invalid"))
    _git(root, "push", "--force", "origin", f"{foreign}:refs/heads/promotion/v1.1.0")
    refs = ("main", "dev", "unreleased", "promotion/v1.1.0")
    before = {name: git.remote_branch(root, name) for name in refs}
    old_dev = git.rev(root, "refs/heads/dev")

    with pytest.raises(PromotionError, match="owned by another candidate"):
        recovery_plan.build_plan(root, run_checks=None)

    assert before["main"] == main
    assert before["promotion/v1.1.0"] == foreign
    assert {name: git.remote_branch(root, name) for name in refs} == before
    assert git.rev(root, "refs/heads/dev") == old_dev
    assert git.out(root, "for-each-ref", "--format=%(refname)", "refs/recovery") == ""


@pytest.mark.parametrize("phase,defect", [
    ("stage", "extra_main"),
    ("apply", "wrong_old"),
    ("abort", "partial"),
])
def test_recovery_assess_push_rejects_non_manifest_ref_sets(tmp_path, phase, defect):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    workflow.prepare(root, _request(shas["two"]), run_checks=None)
    workflow.finish(root, "promotion/v1.1.0", ci=lambda *_: {"state": "passed"})
    _direct_main(root, tmp_path)
    plan = recovery_plan.build_plan(root, run_checks=None)
    updates = recovery_plan.resolve_updates(root, plan, phase)
    if defect == "extra_main":
        updates["refs/heads/main"] = {
            "old": plan.snapshot["main"], "new": plan.target_unreleased}
    elif defect == "wrong_old":
        updates["refs/heads/unreleased"]["old"] = plan.snapshot["main"]
    else:
        updates.pop("refs/heads/" + plan.entries[0].new_ref)
    outgoing = [
        PushUpdate(ref, value["old"] or ZERO_SHA, value["new"] or ZERO_SHA)
        for ref, value in updates.items()
    ]

    with pytest.raises(PromotionError, match="do not match a sealed"):
        candidates.assess_push(root, outgoing)
