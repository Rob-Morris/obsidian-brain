"""Dev-to-main promotion topology: one cut, one candidate, and a linear tail replay."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "src" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import canary_receipt
from _promotion import workflow as promotion, candidates, git, model
import release


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _release_repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Promotion Tests")
    _git(root, "config", "user.email", "promotion@example.invalid")
    for path in ("src/scripts/release.py", "cli/_version_contract.py"):
        _write(root, path, (REPO_ROOT / path).read_text(encoding="utf-8"))
    _write(root, "src/brain-core/scripts/_application/registry.py", """
from types import SimpleNamespace
def current_application_catalogue():
    return SimpleNamespace(schema='brain.command-catalogue/1', interface_epoch=1,
                           fingerprint='sha256:test', entries=[1, 2])
""")
    _write(root, release.VERSION_PATH, "1.0.0\n")
    _write(root, release.README_PATH, "![Version](version-1.0.0-blue)\n")
    _write(
        root,
        release.UNIX_CLI_PATH,
        '#!/bin/sh\nBRAIN_CLI_VERSION="2.0.0"\nBRAIN_INSTALL_REF="v1.0.0"\n',
    )
    _write(
        root,
        release.WINDOWS_CLI_PATH,
        '@echo off\nset "BRAIN_CLI_VERSION=2.0.0"\nset "BRAIN_INSTALL_REF=v1.0.0"\n',
    )
    _write(root, release.PROXY_PATH, 'PROXY_VERSION = "0.3.0"\n')
    _write(
        root,
        release.CHANGELOG_INDEX_PATH,
        "# Changelog\n\n| Version | Date | Summary |\n|---|---|---|\n"
        "| [v1.0.0](changelog/v1.0.0.md) | 2026-08-15 | Existing release |\n",
    )
    _write(root, "docs/changelog/v1.0.0.md", "# v1.0.0\n\n**Summary:** Existing release\n")
    _write(
        root,
        release.FUNCTIONAL_CLI_PATH,
        "Unix `lib/brain-cli/2.0.0/`; Windows `lib\\brain-cli\\2.0.0\\`.\n"
        "`BRAIN_CLI_VERSION` is `2.0.0`; `BRAIN_INSTALL_REF` is `v1.0.0`.\n",
    )
    _write(root, release.USER_REFERENCE_PATH, "Reference for Brain Core 1.0.0 and CLI 2.0.0.\n")
    _write(root, release.COMMAND_CATALOGUE_PATH, '{"stale": true}\n')
    _write(root, ".gitignore", ".worktrees/\n.canary--*\n__pycache__/\n.pytest_cache/\ntemplate-vault/.brain-core\n")
    _write(
        root,
        ".canaries/pre-promotion.md",
        "# Canary: Promotion\n\n## Tasks\n\n[1] **Coherent cut.** The cut is one version.\n\n## Log\n\n[1] Example: done\n",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def _dev_repo(tmp_path: Path, name: str = "repo") -> tuple[Path, dict[str, str]]:
    root = _release_repo(tmp_path, name)
    bare = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-u", "origin", "main")
    _git(root, "switch", "-c", "dev")
    shas = {"main": _git(root, "rev-parse", "main").stdout.strip()}
    for name, text in (
        ("one", "one\n"),
        ("two", "two\n"),
        ("three", "three\n"),
        ("four", "four\n"),
    ):
        _write(root, "notes.txt", text)
        _git(root, "add", "notes.txt")
        _git(root, "commit", "-m", f"WIP: {name}")
        shas[name] = _git(root, "rev-parse", "HEAD").stdout.strip()
    _git(root, "push", "-u", "origin", "dev")
    return root, shas


def _receipt(root: Path) -> None:
    (root / ".canary--pre-promotion").write_text("[1] Coherent cut: done\n", encoding="utf-8")


def _request(cut: str | None, version: str = "1.1.0") -> model.PromotionRequest:
    return model.load_request(
        {
            "core_version": version,
            "summary": "Record the promoted notes",
            "release_type": "Backward-compatible notes",
            "changes": ["notes.txt records the promoted cut."],
            "date": "2026-09-21",
            "body": "Promote one coherent cut of the notes.\n",
            "cut": cut,
        }
    )


def _passed(_root, _commit, _branch):
    return {"state": "passed"}


def test_request_rejects_a_version_suffix_and_a_short_cut():
    with pytest.raises(model.PromotionError, match="version suffix"):
        model.load_request(
            {
                "core_version": "1.1.0",
                "summary": "Record the notes (v1.1.0)",
                "release_type": "notes",
                "changes": ["notes.txt"],
                "date": "2026-09-21",
                "body": "Body.\n",
            }
        )
    with pytest.raises(model.PromotionError, match="full SHA"):
        model.load_request(
            {
                "core_version": "1.1.0",
                "summary": "Record the notes",
                "release_type": "notes",
                "changes": ["notes.txt"],
                "date": "2026-09-21",
                "body": "Body.\n",
                "cut": "abc",
            }
        )


def test_status_lists_cuts_from_the_ledger_through_the_tip(tmp_path, capsys):
    root, shas = _dev_repo(tmp_path)

    payload = promotion.status(root, as_json=True)

    assert "main" not in payload
    assert payload["ledger"] == shas["main"]
    assert payload["dev"] == shas["four"]
    assert [item["sha"] for item in payload["commits"]] == [
        shas["one"],
        shas["two"],
        shas["three"],
        shas["four"],
    ]
    two = next(item for item in payload["cuts"] if item["cut"] == shas["two"])
    assert two["included"] == [shas["one"], shas["two"]]
    assert two["excluded"] == [shas["three"], shas["four"]]
    capsys.readouterr()
    promotion.status(root)
    text = capsys.readouterr().out
    assert f"ledger {shas['main']}" in text
    assert "commits after the ledger, oldest first:" in text
    assert "main " not in text


def test_status_refuses_a_ledger_reachable_only_through_a_second_parent(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    _git(root, "merge", "--no-ff", "-m", "WIP: merge ledger as second parent", candidate)
    _git(root, "push", "origin", f"{candidate}:refs/heads/unreleased", "dev")

    assert git.is_ancestor(root, candidate, "dev")
    assert not candidates.on_first_parent_line(root, "dev", candidate)
    with pytest.raises(model.PromotionError, match="first-parent"):
        promotion.status_payload(root)


def test_prefix_promotion_replays_the_tail_and_a_second_cut(tmp_path):
    root, shas = _dev_repo(tmp_path)

    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["two"]), run_checks=None)
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]
    prepared = git.read_commit(root, candidate)
    assert prepared.parents == (shas["main"],)
    assert _git(root, "show", f"{candidate}:notes.txt").stdout == "two\n"
    assert _git(root, "show", f"{candidate}:{release.VERSION_PATH}").stdout == "1.1.0\n"
    trailers = model.parse_trailers(prepared.message)
    assert trailers[model.SOURCE_TRAILER] == shas["two"]
    assert trailers[model.TIP_TRAILER] == shas["four"]

    dev_tip = promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == candidate
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == dev_tip
    assert _git(root, "merge-base", "--is-ancestor", shas["four"], dev_tip).returncode == 0
    replayed = git.read_commit(root, dev_tip)
    assert replayed.parents[-1] == shas["four"]
    assert replayed.subject == "WIP: preserve dev history after v1.1.0"
    assert _git(root, "show", f"{dev_tip}:notes.txt").stdout == "four\n"
    assert _git(root, "show", f"{dev_tip}:{release.VERSION_PATH}").stdout == "1.1.0\n"
    four = git.read_commit(root, replayed.parents[0])
    three = git.read_commit(root, four.parents[0])
    assert _git(root, "show", f"{three.sha}:notes.txt").stdout == "three\n"
    assert len(four.parents) == 1
    assert "WIP: reconcile dev with promoted v1.1.0" in _git(
        root, "log", "--first-parent", "--format=%s", "dev"
    ).stdout

    _receipt(root)
    second = promotion.prepare(root, _request(three.sha, "1.2.0"), run_checks=None)
    second_dev = promotion.finish(root, "promotion/v1.2.0", ci=_passed)

    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == second
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "show", f"{second}:notes.txt").stdout == "three\n"
    assert _git(root, "show", f"{second}:{release.VERSION_PATH}").stdout == "1.2.0\n"
    assert _git(root, "show", f"{second_dev}:notes.txt").stdout == "four\n"
    assert _git(root, "show", f"{second_dev}:{release.VERSION_PATH}").stdout == "1.2.0\n"
    assert _git(root, "merge-base", "--is-ancestor", shas["four"], second_dev).returncode == 0


def test_whole_tip_promotion_makes_the_trees_match(tmp_path):
    root, shas = _dev_repo(tmp_path)

    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    dev_tip = promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", f"{candidate}^{{tree}}").stdout == _git(
        root, "rev-parse", f"{dev_tip}^{{tree}}"
    ).stdout
    reconciliation = git.read_commit(root, dev_tip)
    assert reconciliation.parents == (candidate, shas["four"])
    assert reconciliation.subject == "WIP: reconcile dev with promoted v1.1.0"


def test_merge_in_the_tail_and_replay_conflict_leave_refs_unchanged(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _git(root, "switch", "-c", "side", shas["two"])
    _write(root, "side.txt", "side\n")
    _git(root, "add", "side.txt")
    _git(root, "commit", "-m", "WIP: side")
    _git(root, "switch", "dev")
    _git(root, "merge", "--no-ff", "-m", "WIP: merge side", "side")
    _git(root, "push", "origin", "dev")
    main_before = _git(root, "rev-parse", "main").stdout.strip()
    dev_before = _git(root, "rev-parse", "dev").stdout.strip()

    _receipt(root)
    with pytest.raises(model.PromotionError, match="single-parent"):
        promotion.prepare(root, _request(shas["two"]), run_checks=None)

    assert _git(root, "rev-parse", "main").stdout.strip() == main_before
    assert _git(root, "rev-parse", "dev").stdout.strip() == dev_before

    root, shas = _dev_repo(tmp_path, "conflict")
    readme = (root / release.README_PATH).read_text(encoding="utf-8")
    _write(root, release.README_PATH, readme + "\nLater note.\n")
    _git(root, "add", release.README_PATH)
    _git(root, "commit", "-m", "WIP: readme aside")
    _git(root, "push", "origin", "dev")
    main_before = _git(root, "rev-parse", "main").stdout.strip()
    dev_before = _git(root, "rev-parse", "dev").stdout.strip()

    _receipt(root)
    with pytest.raises(model.PromotionError, match="conflict"):
        promotion.prepare(root, _request(shas["two"]), run_checks=None)

    assert _git(root, "rev-parse", "main").stdout.strip() == main_before
    assert _git(root, "rev-parse", "dev").stdout.strip() == dev_before
    assert _git(root, "rev-parse", "--verify", "refs/heads/promotion/v1.1.0", check=False).returncode != 0


def test_dirty_checkout_and_failed_ci_do_not_publish(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _write(root, "dirty.txt", "dirty\n")

    with pytest.raises(model.PromotionError, match="clean"):
        promotion.prepare(root, _request(shas["four"]), run_checks=None)

    _git(root, "checkout", "--", ".")
    (root / "dirty.txt").unlink()
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    with pytest.raises(model.PromotionError, match="not passed"):
        promotion.finish(root, "promotion/v1.1.0", ci=lambda *_args: {"state": "failed"})

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]
    remote = _git(root, "ls-remote", "origin", "refs/heads/main", "refs/heads/dev").stdout
    assert shas["main"] in remote
    assert shas["four"] in remote


def test_discard_removes_the_candidate_without_moving_branches(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)

    promotion.discard(root, "promotion/v1.1.0")

    assert _git(root, "rev-parse", "--verify", "refs/heads/promotion/v1.1.0", check=False).returncode != 0
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]


def test_push_assessment_accepts_only_the_exact_promotion(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["two"]), run_checks=None)
    new_dev = candidates.build_replay(root, git.read_commit(root, candidate))
    candidates.assess_push(
        root,
        [
            model.PushUpdate("refs/heads/unreleased", model.ZERO_SHA, candidate),
            model.PushUpdate("refs/heads/dev", shas["four"], new_dev),
        ],
    )
    with pytest.raises(model.PromotionError, match="unreleased"):
        candidates.assess_push(
            root,
            [model.PushUpdate("refs/heads/main", shas["main"], candidate)],
        )
    with pytest.raises(model.PromotionError, match="not a fast-forward"):
        candidates.assess_push(
            root,
            [model.PushUpdate("refs/heads/dev", shas["four"], shas["two"])],
        )
    descendant = git.commit_tree(
        root,
        git.tree_of(root, new_dev),
        (new_dev,),
        "WIP: not the replay\n",
        git.identity_env(
            "2026-09-22T00:00:00+00:00",
            "Promotion Tests",
            "promotion@example.invalid",
        ),
    )
    with pytest.raises(model.PromotionError, match="not the replayed tip"):
        candidates.assess_push(
            root,
            [
                model.PushUpdate("refs/heads/unreleased", model.ZERO_SHA, candidate),
                model.PushUpdate("refs/heads/dev", shas["four"], descendant),
            ],
        )
    zero = model.ZERO_SHA
    candidates.assess_push(
        root,
        [model.PushUpdate("refs/heads/promotion/v1.1.0", zero, candidate)],
    )
    candidates.assess_push(
        root,
        [model.PushUpdate("refs/heads/promotion/v1.1.0", candidate, candidate)],
    )
    candidates.assess_push(
        root,
        [model.PushUpdate("refs/heads/promotion/v1.1.0", candidate, zero)],
    )
    with pytest.raises(model.PromotionError, match="immutable"):
        candidates.assess_push(
            root,
            [model.PushUpdate("refs/heads/promotion/v1.1.0", candidate, "b" * 40)],
        )


def test_cutting_before_the_preserve_commit_still_fast_forwards(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["two"]), run_checks=None)
    dev_tip = promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    content = git.read_commit(root, git.read_commit(root, dev_tip).parents[0])

    _receipt(root)
    candidate = promotion.prepare(root, _request(content.sha, "1.2.0"), run_checks=None)
    published = promotion.finish(root, "promotion/v1.2.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == candidate
    assert _git(root, "merge-base", "--is-ancestor", dev_tip, published).returncode == 0
    assert _git(root, "rev-parse", f"{candidate}^{{tree}}").stdout == _git(
        root, "rev-parse", f"{published}^{{tree}}"
    ).stdout
    remote = _git(root, "ls-remote", "origin", "refs/heads/unreleased", "refs/heads/dev").stdout
    assert candidate in remote
    assert published in remote


def test_request_rejects_an_impossible_date_and_an_as_version_summary():
    with pytest.raises(model.PromotionError, match="real YYYY-MM-DD"):
        model.load_request(
            {
                "core_version": "1.1.0",
                "summary": "Record the notes",
                "release_type": "notes",
                "changes": ["notes.txt"],
                "date": "2026-13-40",
                "body": "Body.\n",
            }
        )
    with pytest.raises(model.PromotionError, match="version suffix"):
        model.load_request(
            {
                "core_version": "1.1.0",
                "summary": "Record the notes as v1.1.0 ",
                "release_type": "notes",
                "changes": ["notes.txt"],
                "date": "2026-09-22",
                "body": "Body.\n",
            }
        )


def test_missing_git_object_is_not_a_topology_refusal(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    with pytest.raises(model.PromotionError, match="merge-base"):
        git.is_ancestor(root, "a" * 40, "b" * 40)


def test_failed_prepare_keeps_the_canary_receipt(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    with pytest.raises(model.PromotionError, match="clean"):
        _write(root, "dirty.txt", "dirty\n")
        promotion.prepare(root, _request(shas["four"]), run_checks=None)
    assert (root / ".canary--pre-promotion").is_file()


def test_canary_tasks_are_line_anchored_and_the_receipt_is_shared():
    brief = "Mention ## Tasks in prose.\n\n## Tasks\n\n[1] **Cut.** One version.\n\n## Log\n"
    assert canary_receipt.task_ids(brief) == ["[1]"]
    assert canary_receipt.receipt_problems(brief, "[1] Cut: done\n") == ()
    assert canary_receipt.receipt_problems(brief, "[1] Cut: later\n")


def test_prepare_refuses_when_dev_release_facts_differ_from_main(tmp_path):
    root, shas = _dev_repo(tmp_path)
    proxy = root / release.PROXY_PATH
    proxy.write_text(proxy.read_text(encoding="utf-8").replace("0.3.0", "0.4.0"), encoding="utf-8")
    _git(root, "add", release.PROXY_PATH)
    _git(root, "commit", "-m", "WIP: proxy")
    _git(root, "push", "origin", "dev")
    _receipt(root)

    with pytest.raises(model.PromotionError, match="must still equal the ledger"):
        promotion.prepare(root, _request(shas["four"]), run_checks=None)

    assert _git(root, "rev-parse", "--verify", "refs/heads/promotion/v1.1.0", check=False).returncode != 0


def test_prepare_consumes_the_receipt_only_after_the_candidate_push(tmp_path):
    root, shas = _dev_repo(tmp_path)
    brief = root / ".canaries/pre-promotion.md"
    receipt = root / ".canary--pre-promotion"
    _receipt(root)

    def fail(_worktree):
        raise model.PromotionError("candidate tests failed")

    with pytest.raises(model.PromotionError, match="tests failed"):
        promotion.prepare(root, _request(shas["four"]), run_checks=fail)
    assert receipt.is_file()
    assert brief.is_file()

    hook = tmp_path / "repo.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    with pytest.raises(model.PromotionError, match="push"):
        promotion.prepare(root, _request(shas["four"]), run_checks=None)
    assert receipt.is_file()
    assert brief.is_file()

    hook.unlink()
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    assert not receipt.exists()
    assert brief.is_file()


def test_finish_observes_a_rejected_push_and_a_settled_remote(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    new_dev = candidates.build_replay(root, git.read_commit(root, candidate))
    hook = tmp_path / "repo.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(model.PromotionError, match="unchanged"):
        promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]
    hook.unlink()
    _git(root, "push", "origin", f"{candidate}:refs/heads/unreleased", f"{new_dev}:refs/heads/dev")

    def unexpected_ci(*_args):
        raise AssertionError("a settled remote must not be checked again")

    promotion.finish(root, "promotion/v1.1.0", ci=unexpected_ci)
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == candidate
    assert _git(root, "rev-parse", "dev").stdout.strip() == new_dev


def test_finish_names_an_absent_remote_ref(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    _git(root, "push", "origin", ":refs/heads/dev")

    def unexpected_ci(*_args):
        raise AssertionError("an absent remote ref is diverged, not a CI check")

    with pytest.raises(model.PromotionError, match="absent"):
        promotion.finish(root, "promotion/v1.1.0", ci=unexpected_ci)

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]


def test_rev_parse_failure_is_not_a_missing_ref(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    assert git.rev_exists(root, "refs/heads/no-such") is False
    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    with pytest.raises(model.PromotionError, match="rev-parse"):
        git.rev_exists(missing, "HEAD")


def test_cleanup_keeps_candidate_ref_when_worktree_removal_fails(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    worktree = root / ".worktrees" / "v1.1.0"
    worktree.mkdir(parents=True)

    _git(root, "branch", "promotion/v1.1.0")
    with pytest.raises(model.PromotionError, match="worktree"):
        git.cleanup_local(root, "promotion/v1.1.0")
    assert git.rev_exists(root, "refs/heads/promotion/v1.1.0")


def test_remote_classification_names_every_outcome():
    old_main, old_dev, new_main, new_dev = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
    assert model.classify_remote(
        old_main, old_dev, new_main, new_dev, old_main, old_dev, observed=True
    ) == "unchanged"
    assert model.classify_remote(
        old_main, old_dev, new_main, new_dev, new_main, new_dev, observed=True
    ) == "settled"
    assert model.classify_remote(
        old_main, old_dev, new_main, new_dev, new_main, old_dev, observed=True
    ) == "diverged"
    assert model.classify_remote(
        old_main, old_dev, new_main, new_dev, None, None, observed=False
    ) == "unobserved"
    assert model.classify_remote(
        old_main, old_dev, new_main, new_dev, None, new_dev, observed=True
    ) == "diverged"
    assert model.classify_remote(
        old_main, old_dev, new_main, new_dev, old_main, None, observed=True
    ) == "diverged"


def test_publish_moves_main_only_after_finish_and_a_second_call_does_not_push(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert promotion.status(root, as_json=True)["ledger"] == candidate
    with pytest.raises(model.PromotionError, match="not passed"):
        promotion.publish(root, candidate, ci=lambda *_args: {"state": "failed"})
    assert shas["main"] in _git(root, "ls-remote", "origin", "refs/heads/main").stdout
    promotion.publish(root, candidate, ci=_passed)
    assert _git(root, "ls-remote", "origin", "refs/heads/main").stdout.split()[0] == candidate
    assert candidate not in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout
    hook = tmp_path / "repo.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert promotion.publish(root, candidate, ci=_passed) == candidate
    assert _git(root, "ls-remote", "origin", "refs/heads/main").stdout.split()[0] == candidate


def test_finish_refuses_a_sibling_cut_once_unreleased_has_moved(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    first = promotion.prepare(root, _request(shas["two"]), run_checks=None)
    _receipt(root)
    promotion.prepare(root, _request(shas["four"], "1.2.0"), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    with pytest.raises(model.PromotionError, match="neither unchanged"):
        promotion.finish(root, "promotion/v1.2.0", ci=_passed)

    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == first
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]


def test_discard_does_not_delete_a_remote_candidate_it_does_not_own(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    assert candidate in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout

    with pytest.raises(model.PromotionError, match="no local ownership ref"):
        promotion.discard(root, "promotion/v1.1.0")
    with pytest.raises(model.PromotionError, match="already on the ledger"):
        promotion.discard(root, "promotion/v1.1.0", expected_sha=candidate)

    assert candidate in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout


def test_adopt_replays_only_local_commits_and_stops_when_the_tip_was_cut(tmp_path, capsys):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    before = _git(root, "rev-parse", "dev").stdout.strip()
    origin_dev = _git(root, "rev-parse", "origin/dev").stdout.strip()
    assert before == origin_dev
    capsys.readouterr()
    assert promotion.adopt(root) == before
    assert _git(root, "rev-parse", "dev").stdout.strip() == before
    assert _git(root, "rev-parse", "origin/dev").stdout.strip() == origin_dev
    assert capsys.readouterr().out.strip() == "nothing left to cut"

    _write(root, "later.txt", "later\n")
    _git(root, "add", "later.txt")
    _git(root, "commit", "-m", "WIP: later")
    replayed = promotion.adopt(root)
    assert "nothing left to cut" not in capsys.readouterr().out
    assert _git(root, "rev-parse", f"{replayed}^").stdout.strip() == origin_dev
    assert _git(root, "rev-list", "--count", f"{origin_dev}..{replayed}").stdout.strip() == "1"
    assert replayed != origin_dev
    assert _git(root, "rev-parse", "origin/dev").stdout.strip() == origin_dev
    assert _git(root, "show", f"{replayed}:later.txt").stdout == "later\n"


def test_fetch_of_missing_unreleased_drops_a_stale_tracking_ref(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _git(root, "update-ref", "refs/remotes/origin/unreleased", shas["main"])

    git.fetch_refs(root)

    assert _git(
        root, "rev-parse", "--verify", "refs/remotes/origin/unreleased", check=False
    ).returncode != 0
    assert candidates.ledger_tip(root) == shas["main"]


def test_fetch_reports_an_unreleased_transport_failure(tmp_path, monkeypatch):
    root, shas = _dev_repo(tmp_path)
    _git(root, "update-ref", "refs/remotes/origin/unreleased", shas["main"])
    real = git.run

    def fake(cwd, *args, **kwargs):
        if args[:3] == ("fetch", "origin", "refs/heads/unreleased:refs/remotes/origin/unreleased"):
            return subprocess.CompletedProcess(args, 128, "", "fatal: Authentication failed")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(git, "run", fake)
    with pytest.raises(model.PromotionError, match="Authentication failed"):
        git.fetch_refs(root)
    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == shas["main"]


def test_finish_refuses_to_claim_a_local_dev_that_is_not_the_replay(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    new_dev = candidates.build_replay(root, git.read_commit(root, candidate))
    _git(root, "push", "origin", f"{candidate}:refs/heads/unreleased", f"{new_dev}:refs/heads/dev")
    _write(root, "later.txt", "later\n")
    _git(root, "add", "later.txt")
    _git(root, "commit", "-m", "WIP: later")
    third = _git(root, "rev-parse", "dev").stdout.strip()

    with pytest.raises(model.PromotionError, match="neither"):
        promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", "dev").stdout.strip() == third
    assert _git(root, "rev-parse", "origin/dev").stdout.strip() == new_dev
    assert _git(root, "rev-parse", "refs/remotes/origin/unreleased").stdout.strip() == candidate


def test_publish_retries_cleanup_without_pushing_main_again(tmp_path, monkeypatch):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    real = git.run
    failed = {"once": False}

    def fake(cwd, *args, **kwargs):
        if not failed["once"] and args[:3] == ("ls-remote", "origin", "refs/heads/promotion/*"):
            failed["once"] = True
            return subprocess.CompletedProcess(args, 1, "", "fatal: boom")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(git, "run", fake)
    with pytest.raises(model.PromotionError, match="boom"):
        promotion.publish(root, candidate, ci=_passed)
    assert _git(root, "ls-remote", "origin", "refs/heads/main").stdout.split()[0] == candidate
    assert candidate in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout
    hook = tmp_path / "repo.git" / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\nwhile read -r _old _new ref; do\n"
        '  if [ "$ref" = "refs/heads/main" ]; then exit 1; fi\ndone\nexit 0\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    assert promotion.publish(root, candidate, ci=_passed) == candidate
    assert candidate not in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout
    assert _git(root, "ls-remote", "origin", "refs/heads/main").stdout.split()[0] == candidate


def test_publish_and_the_hook_refuse_a_dev_replay_off_the_unreleased_line(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    dev_tip = _git(root, "rev-parse", "dev").stdout.strip()

    with pytest.raises(model.PromotionError, match="first-parent"):
        promotion.publish(root, dev_tip, ci=_passed)
    assert shas["main"] in _git(root, "ls-remote", "origin", "refs/heads/main").stdout
    with pytest.raises(model.PromotionError, match="already on unreleased"):
        candidates.assess_push(
            root,
            [model.PushUpdate("refs/heads/main", shas["main"], dev_tip)],
        )
    candidates.assess_push(
        root,
        [model.PushUpdate("refs/heads/main", shas["main"], candidate)],
    )


def _clone_dev(root: Path, destination: Path) -> Path:
    _git(root.parent, "clone", "--branch", "dev", str(root.parent / "repo.git"), str(destination))
    _git(destination, "config", "user.name", "Other Promoter")
    _git(destination, "config", "user.email", "other@example.invalid")
    return destination


@pytest.mark.parametrize("path, staged", [("notes.txt", False), ("notes.txt", True), ("untracked.txt", False)])
def test_adopt_preserves_dirty_dev(tmp_path, path, staged):
    root, _ = _dev_repo(tmp_path)
    _write(root, path, "precious work\n")
    if staged:
        _git(root, "add", path)
    before = _git(root, "status", "--porcelain").stdout
    head = _git(root, "rev-parse", "HEAD").stdout
    with pytest.raises(model.PromotionError, match="clean"):
        promotion.adopt(root)
    assert (root / path).read_text() == "precious work\n"
    assert _git(root, "status", "--porcelain").stdout == before
    assert _git(root, "rev-parse", "HEAD").stdout == head


def test_adopt_replays_a_losing_promoters_private_suffix_and_cleans_only_its_candidate(tmp_path):
    winner, shas = _dev_repo(tmp_path)
    loser = _clone_dev(winner, tmp_path / "loser")
    _receipt(loser)
    stale = promotion.prepare(loser, _request(None, "1.2.0"), run_checks=None)
    _write(loser, "private.txt", "private change\n")
    _git(loser, "add", "private.txt")
    _git(loser, "commit", "-m", "WIP: private", "--author", "Author <author@example.invalid>")
    private = _git(loser, "rev-parse", "HEAD").stdout.strip()
    _receipt(winner)
    winner_candidate = promotion.prepare(winner, _request(shas["two"]), run_checks=None)
    winner_dev = promotion.finish(winner, "promotion/v1.1.0", ci=_passed)

    replayed = promotion.adopt(loser, "promotion/v1.2.0")

    assert _git(loser, "rev-parse", f"{replayed}^").stdout.strip() == winner_dev
    assert (loser / "private.txt").read_text() == "private change\n"
    assert (loser / "notes.txt").read_text() == "four\n"
    assert _git(loser, "show", "-s", "--format=%an|%ae|%aI|%cn|%ce|%cI", replayed).stdout == _git(
        loser, "show", "-s", "--format=%an|%ae|%aI|%cn|%ce|%cI", private
    ).stdout
    refs = _git(loser, "ls-remote", "origin", "refs/heads/*").stdout
    assert stale not in refs
    assert winner_candidate in refs
    assert winner_dev in refs and replayed not in refs
    assert not git.rev_exists(loser, "refs/heads/promotion/v1.2.0")
    assert promotion.adopt(loser) == replayed


def test_adopt_replay_conflict_preserves_local_work_and_candidate(tmp_path):
    winner, shas = _dev_repo(tmp_path)
    loser = _clone_dev(winner, tmp_path / "loser")
    _receipt(loser)
    stale = promotion.prepare(loser, _request(None, "1.2.0"), run_checks=None)
    _write(loser, release.README_PATH, "private rewrite\n")
    _git(loser, "add", release.README_PATH)
    _git(loser, "commit", "-m", "WIP: private README")
    before = _git(loser, "rev-parse", "HEAD").stdout
    _receipt(winner)
    promotion.prepare(winner, _request(None), run_checks=None)
    promotion.finish(winner, "promotion/v1.1.0", ci=_passed)
    with pytest.raises(model.PromotionError, match="conflict"):
        promotion.adopt(loser, "promotion/v1.2.0")
    assert _git(loser, "rev-parse", "HEAD").stdout == before
    assert (loser / release.README_PATH).read_text() == "private rewrite\n"
    assert stale in _git(loser, "ls-remote", "origin", "refs/heads/promotion/v1.2.0").stdout


def test_adopt_refuses_to_discard_a_candidate_that_can_still_finish(tmp_path):
    root, _ = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    with pytest.raises(model.PromotionError, match="can still finish"):
        promotion.adopt(root, "promotion/v1.1.0")
    assert candidate in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout


def test_publish_ignores_unknown_foreign_candidate_objects_and_optional_local_main(tmp_path):
    root, _ = _dev_repo(tmp_path)
    other = _clone_dev(root, tmp_path / "other")
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    _receipt(other)
    sibling = promotion.prepare(other, _request(None, "1.2.0"), run_checks=None)
    assert _git(root, "cat-file", "-e", sibling, check=False).returncode != 0
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    _git(root, "branch", "-D", "main")
    promotion.publish(root, candidate, ci=_passed)
    assert sibling in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.2.0").stdout
    assert candidate in _git(root, "ls-remote", "origin", "refs/heads/main").stdout


def test_publish_refuses_before_remote_write_when_main_is_checked_out(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", str(linked), "main")
    with pytest.raises(model.PromotionError, match="main is checked out"):
        promotion.publish(root, candidate, ci=_passed)
    assert shas["main"] in _git(root, "ls-remote", "origin", "refs/heads/main").stdout
    assert _git(linked, "status", "--porcelain").stdout == ""


def test_candidate_branch_and_subject_are_bound_before_finish_or_push(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    _git(root, "branch", "promotion/v9.9.9", candidate)
    with pytest.raises(model.PromotionError, match="candidate version"):
        promotion.finish(root, "promotion/v9.9.9", ci=_passed)
    with pytest.raises(model.PromotionError, match="candidate version"):
        candidates.assess_push(root, [model.PushUpdate("refs/heads/promotion/v9.9.9", model.ZERO_SHA, candidate)])
    commit = git.read_commit(root, candidate)
    wrong = git.commit_tree(root, commit.tree, commit.parents,
                                    commit.message.replace("Record the promoted notes", "Wrong subject"),
                                    git.replay_env(root, candidate))
    with pytest.raises(model.PromotionError, match="canonical release Summary"):
        candidates.validate_candidate(root, git.read_commit(root, wrong))
    assert shas["four"] in _git(root, "ls-remote", "origin", "refs/heads/dev").stdout


def test_cut_release_imports_the_cut_parser_not_the_running_process_parser(tmp_path):
    root, shas = _dev_repo(tmp_path)
    parser = root / "cli/_version_contract.py"
    parser.write_text(parser.read_text() + '\nraise RuntimeError("selected cut parser")\n')
    _git(root, "add", "cli/_version_contract.py")
    _git(root, "commit", "-m", "WIP: distinct parser")
    _git(root, "push", "origin", "dev")
    _receipt(root)
    with pytest.raises(model.PromotionError, match="selected cut parser"):
        promotion.prepare(root, _request(None), run_checks=None)
    assert shas["main"] in _git(root, "ls-remote", "origin", "refs/heads/main").stdout


def test_default_prepare_checks_run_real_candidate_checker_and_pytest(tmp_path):
    root, _ = _dev_repo(tmp_path)
    _write(root, "template-vault/README.md", "Fixture vault\n")
    _write(root, "src/scripts/check_repository_contracts.py", """
from pathlib import Path
assert Path('src/brain-core/VERSION').read_text().strip() == '1.1.0'
assert (Path('template-vault/.brain-core') / 'VERSION').read_text().strip() == '1.1.0'
""")
    _write(root, "tests/test_candidate.py", """
from pathlib import Path
import subprocess
def test_candidate():
    assert Path('src/brain-core/VERSION').read_text().strip() == '1.1.0'
    assert subprocess.check_output(['git', 'show', 'HEAD:src/brain-core/VERSION'], text=True).strip() == '1.1.0'
""")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "test: candidate checks")
    _git(root, "push", "origin", "dev")
    _receipt(root)
    candidate = promotion.prepare(root, _request(None))
    assert candidate in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout


def test_hook_rejects_dev_deletion_but_allows_disposable_branch_deletion(tmp_path):
    root, shas = _dev_repo(tmp_path)
    with pytest.raises(model.PromotionError, match="persistent dev"):
        candidates.assess_push(root, [model.PushUpdate("refs/heads/dev", shas["four"], model.ZERO_SHA)])
    candidates.assess_push(root, [model.PushUpdate("refs/heads/feature/test", shas["four"], model.ZERO_SHA)])


def test_canary_prose_cannot_cover_receipt_tasks():
    brief = "## Tasks\n[1] First\n[2] Second\n## Log\n"
    assert canary_receipt.receipt_problems(brief, "Reviewed [1] and [2]\n") == ("missing [1], [2]",)
    assert canary_receipt.receipt_problems(brief, "[1] Both [2]: done\n") == ("missing [2]",)
    assert canary_receipt.receipt_problems(brief, "[1] First: done\n[2] Second: skip, unrelated\n") == ()


def test_finish_recovery_ref_survives_dirty_candidate_worktree(tmp_path):
    root, _ = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    worktree = root / ".worktrees/v1.1.0"
    _write(worktree, "notes.txt", "debugging edit\n")
    with pytest.raises(model.PromotionError, match="recorded"):
        promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    assert git.rev(root, "refs/heads/promotion/v1.1.0") == candidate
    assert (worktree / "notes.txt").read_text() == "debugging edit\n"
    _git(worktree, "restore", "notes.txt")
    new_dev = promotion.finish(root, "promotion/v1.1.0", ci=lambda *_: pytest.fail("already settled"))
    assert not worktree.exists()
    assert promotion.finish(root, "promotion/v1.1.0", ci=lambda *_: pytest.fail("already settled")) == new_dev


def test_prepare_retry_preserves_candidate_debugging_edits(tmp_path):
    root, _ = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    worktree = root / ".worktrees/v1.1.0"
    _write(worktree, "notes.txt", "debugging edit\n")
    _receipt(root)
    with pytest.raises(model.PromotionError, match="worktree"):
        promotion.prepare(root, _request(None), run_checks=None)
    assert (worktree / "notes.txt").read_text() == "debugging edit\n"
    assert git.rev(root, "refs/heads/promotion/v1.1.0") == candidate


@pytest.mark.parametrize("changed_ref", ["main", "dev", "promotion/v1.1.0"])
def test_finish_detects_remote_change_during_ci(tmp_path, changed_ref):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    base = shas["main"] if changed_ref == "main" else shas["four"]
    changed = git.commit_tree(root, git.tree_of(root, base), (base,), "WIP: concurrent\n", git.replay_env(root, base))

    def racing_ci(*_):
        _git(root, "push", "--force", "origin", f"{changed}:refs/heads/{changed_ref}")
        return {"state": "passed"}

    with pytest.raises(model.PromotionError):
        promotion.finish(root, "promotion/v1.1.0", ci=racing_ci)
    assert git.rev(root, "dev") == shas["four"]
    assert changed in _git(root, "ls-remote", "origin", f"refs/heads/{changed_ref}").stdout
    assert not _git(root, "ls-remote", "origin", "refs/heads/unreleased").stdout


def test_remote_cleanup_lease_preserves_a_replacement_candidate(tmp_path, monkeypatch):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    replacement = git.commit_tree(root, git.tree_of(root, shas["four"]), (shas["four"],),
                                  "WIP: replacement\n", git.replay_env(root, shas["four"]))
    real = git.run

    def racing_push(cwd, *args, **kwargs):
        if args and args[0] == "push" and ":refs/heads/promotion/v1.1.0" in args:
            _git(root, "push", "--force", "origin", f"{replacement}:refs/heads/promotion/v1.1.0")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(git, "run", racing_push)
    promotion.discard(root, "promotion/v1.1.0")
    assert replacement in _git(root, "ls-remote", "origin", "refs/heads/promotion/v1.1.0").stdout
    assert not git.rev_exists(root, "refs/heads/promotion/v1.1.0")
