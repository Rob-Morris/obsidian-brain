"""Dev-to-main promotion topology: one cut, one candidate, and a linear tail replay."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "src" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import canary_receipt
import promotion
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
    _write(root, ".gitignore", ".worktrees/\n.canary--*\n")
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


def _request(cut: str | None, version: str = "1.1.0") -> promotion.PromotionRequest:
    return promotion.load_request(
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


@pytest.fixture
def catalogue(monkeypatch):
    monkeypatch.setattr(
        release,
        "_render_command_catalogue_route",
        lambda _root: json.dumps(
            {
                "schema": "brain.command-catalogue/1",
                "interface_epoch": 1,
                "static_fingerprint": "sha256:test",
                "installed_application_command_count": 2,
            },
            indent=2,
        )
        + "\n",
    )


def _passed(_root, _commit, _branch):
    return {"state": "passed"}


def test_request_rejects_a_version_suffix_and_a_short_cut():
    with pytest.raises(promotion.PromotionError, match="version suffix"):
        promotion.load_request(
            {
                "core_version": "1.1.0",
                "summary": "Record the notes (v1.1.0)",
                "release_type": "notes",
                "changes": ["notes.txt"],
                "date": "2026-09-21",
                "body": "Body.\n",
            }
        )
    with pytest.raises(promotion.PromotionError, match="full SHA"):
        promotion.load_request(
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


def test_status_lists_cuts_from_main_through_the_tip(tmp_path):
    root, shas = _dev_repo(tmp_path)

    payload = promotion.status(root, as_json=True)

    assert payload["main"] == shas["main"]
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


def test_prefix_promotion_replays_the_tail_and_a_second_cut(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)

    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["two"]), run_checks=None)
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]
    prepared = promotion.read_commit(root, candidate)
    assert prepared.parents == (shas["main"],)
    assert _git(root, "show", f"{candidate}:notes.txt").stdout == "two\n"
    assert _git(root, "show", f"{candidate}:{release.VERSION_PATH}").stdout == "1.1.0\n"
    trailers = promotion.parse_trailers(prepared.message)
    assert trailers[promotion.SOURCE_TRAILER] == shas["two"]
    assert trailers[promotion.TIP_TRAILER] == shas["four"]

    dev_tip = promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == candidate
    assert _git(root, "rev-parse", "dev").stdout.strip() == dev_tip
    assert _git(root, "merge-base", "--is-ancestor", shas["four"], dev_tip).returncode == 0
    replayed = promotion.read_commit(root, dev_tip)
    assert replayed.parents[-1] == shas["four"]
    assert replayed.subject == "WIP: preserve dev history after v1.1.0"
    assert _git(root, "show", f"{dev_tip}:notes.txt").stdout == "four\n"
    assert _git(root, "show", f"{dev_tip}:{release.VERSION_PATH}").stdout == "1.1.0\n"
    four = promotion.read_commit(root, replayed.parents[0])
    three = promotion.read_commit(root, four.parents[0])
    assert _git(root, "show", f"{three.sha}:notes.txt").stdout == "three\n"
    assert len(four.parents) == 1
    assert "WIP: reconcile dev with promoted v1.1.0" in _git(
        root, "log", "--first-parent", "--format=%s", "dev"
    ).stdout

    _receipt(root)
    second = promotion.prepare(root, _request(three.sha, "1.2.0"), run_checks=None)
    second_dev = promotion.finish(root, "promotion/v1.2.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == second
    assert _git(root, "show", f"{second}:notes.txt").stdout == "three\n"
    assert _git(root, "show", f"{second}:{release.VERSION_PATH}").stdout == "1.2.0\n"
    assert _git(root, "show", f"{second_dev}:notes.txt").stdout == "four\n"
    assert _git(root, "show", f"{second_dev}:{release.VERSION_PATH}").stdout == "1.2.0\n"
    assert _git(root, "merge-base", "--is-ancestor", shas["four"], second_dev).returncode == 0


def test_whole_tip_promotion_makes_the_trees_match(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)

    _receipt(root)
    candidate = promotion.prepare(root, _request(None), run_checks=None)
    dev_tip = promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", f"{candidate}^{{tree}}").stdout == _git(
        root, "rev-parse", f"{dev_tip}^{{tree}}"
    ).stdout
    reconciliation = promotion.read_commit(root, dev_tip)
    assert reconciliation.parents == (candidate, shas["four"])
    assert reconciliation.subject == "WIP: reconcile dev with promoted v1.1.0"


def test_merge_in_the_tail_and_replay_conflict_leave_refs_unchanged(tmp_path, catalogue):
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
    with pytest.raises(promotion.PromotionError, match="single-parent"):
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
    with pytest.raises(promotion.PromotionError, match="conflict"):
        promotion.prepare(root, _request(shas["two"]), run_checks=None)

    assert _git(root, "rev-parse", "main").stdout.strip() == main_before
    assert _git(root, "rev-parse", "dev").stdout.strip() == dev_before
    assert _git(root, "rev-parse", "--verify", "refs/heads/promotion/v1.1.0", check=False).returncode != 0


def test_dirty_checkout_and_failed_ci_do_not_publish(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _write(root, "dirty.txt", "dirty\n")

    with pytest.raises(promotion.PromotionError, match="clean"):
        promotion.prepare(root, _request(shas["four"]), run_checks=None)

    _git(root, "checkout", "--", ".")
    (root / "dirty.txt").unlink()
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    with pytest.raises(promotion.PromotionError, match="not passed"):
        promotion.finish(root, "promotion/v1.1.0", ci=lambda *_args: {"state": "failed"})

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]
    remote = _git(root, "ls-remote", "origin", "refs/heads/main", "refs/heads/dev").stdout
    assert shas["main"] in remote
    assert shas["four"] in remote


def test_discard_removes_the_candidate_without_moving_branches(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)

    promotion.discard(root, "promotion/v1.1.0")

    assert _git(root, "rev-parse", "--verify", "refs/heads/promotion/v1.1.0", check=False).returncode != 0
    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]


def test_push_assessment_accepts_only_the_exact_promotion(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["two"]), run_checks=None)
    new_dev = promotion.build_replay(root, promotion.read_commit(root, candidate))
    promotion.assess_push(
        root,
        [
            promotion.PushUpdate("refs/heads/main", shas["main"], candidate),
            promotion.PushUpdate("refs/heads/dev", shas["four"], new_dev),
        ],
    )
    with pytest.raises(promotion.PromotionError, match="only together"):
        promotion.assess_push(
            root,
            [promotion.PushUpdate("refs/heads/main", shas["main"], candidate)],
        )
    with pytest.raises(promotion.PromotionError, match="not a fast-forward"):
        promotion.assess_push(
            root,
            [promotion.PushUpdate("refs/heads/dev", shas["four"], shas["two"])],
        )
    descendant = promotion._commit_tree(
        root,
        promotion._tree_of(root, new_dev),
        (new_dev,),
        "WIP: not the replay\n",
        promotion._identity_env(
            "2026-09-22T00:00:00+00:00",
            "Promotion Tests",
            "promotion@example.invalid",
        ),
    )
    with pytest.raises(promotion.PromotionError, match="not the replayed tip"):
        promotion.assess_push(
            root,
            [
                promotion.PushUpdate("refs/heads/main", shas["main"], candidate),
                promotion.PushUpdate("refs/heads/dev", shas["four"], descendant),
            ],
        )
    zero = promotion.ZERO_SHA
    promotion.assess_push(
        root,
        [promotion.PushUpdate("refs/heads/promotion/v1.1.0", zero, candidate)],
    )
    promotion.assess_push(
        root,
        [promotion.PushUpdate("refs/heads/promotion/v1.1.0", candidate, candidate)],
    )
    promotion.assess_push(
        root,
        [promotion.PushUpdate("refs/heads/promotion/v1.1.0", candidate, zero)],
    )
    with pytest.raises(promotion.PromotionError, match="immutable"):
        promotion.assess_push(
            root,
            [promotion.PushUpdate("refs/heads/promotion/v1.1.0", candidate, "b" * 40)],
        )


def test_cutting_before_the_preserve_commit_still_fast_forwards(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["two"]), run_checks=None)
    dev_tip = promotion.finish(root, "promotion/v1.1.0", ci=_passed)
    content = promotion.read_commit(root, promotion.read_commit(root, dev_tip).parents[0])

    _receipt(root)
    candidate = promotion.prepare(root, _request(content.sha, "1.2.0"), run_checks=None)
    published = promotion.finish(root, "promotion/v1.2.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == candidate
    assert _git(root, "merge-base", "--is-ancestor", dev_tip, published).returncode == 0
    assert _git(root, "rev-parse", f"{candidate}^{{tree}}").stdout == _git(
        root, "rev-parse", f"{published}^{{tree}}"
    ).stdout
    remote = _git(root, "ls-remote", "origin", "refs/heads/main", "refs/heads/dev").stdout
    assert candidate in remote
    assert published in remote


def test_request_rejects_an_impossible_date_and_an_as_version_summary():
    with pytest.raises(promotion.PromotionError, match="real YYYY-MM-DD"):
        promotion.load_request(
            {
                "core_version": "1.1.0",
                "summary": "Record the notes",
                "release_type": "notes",
                "changes": ["notes.txt"],
                "date": "2026-13-40",
                "body": "Body.\n",
            }
        )
    with pytest.raises(promotion.PromotionError, match="version suffix"):
        promotion.load_request(
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
    with pytest.raises(promotion.PromotionError, match="merge-base"):
        promotion._is_ancestor(root, "a" * 40, "b" * 40)


def test_failed_prepare_keeps_the_canary_receipt(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    with pytest.raises(promotion.PromotionError, match="clean"):
        _write(root, "dirty.txt", "dirty\n")
        promotion.prepare(root, _request(shas["four"]), run_checks=None)
    assert (root / ".canary--pre-promotion").is_file()


def test_canary_tasks_are_line_anchored_and_the_receipt_is_shared():
    brief = "Mention ## Tasks in prose.\n\n## Tasks\n\n[1] **Cut.** One version.\n\n## Log\n"
    assert canary_receipt.task_ids(brief) == ["[1]"]
    assert canary_receipt.receipt_problems(brief, "[1] Cut: done\n") == ()
    assert canary_receipt.receipt_problems(brief, "[1] Cut: later\n")


def test_prepare_refuses_when_dev_release_facts_differ_from_main(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    proxy = root / release.PROXY_PATH
    proxy.write_text(proxy.read_text(encoding="utf-8").replace("0.3.0", "0.4.0"), encoding="utf-8")
    _git(root, "add", release.PROXY_PATH)
    _git(root, "commit", "-m", "WIP: proxy")
    _git(root, "push", "origin", "dev")
    _receipt(root)

    with pytest.raises(promotion.PromotionError, match="must still equal main"):
        promotion.prepare(root, _request(shas["four"]), run_checks=None)

    assert _git(root, "rev-parse", "--verify", "refs/heads/promotion/v1.1.0", check=False).returncode != 0


def test_prepare_consumes_the_receipt_only_after_the_candidate_push(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    brief = root / ".canaries/pre-promotion.md"
    receipt = root / ".canary--pre-promotion"
    _receipt(root)

    def fail(_worktree):
        raise promotion.PromotionError("candidate tests failed")

    with pytest.raises(promotion.PromotionError, match="tests failed"):
        promotion.prepare(root, _request(shas["four"]), run_checks=fail)
    assert receipt.is_file()
    assert brief.is_file()

    hook = tmp_path / "repo.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    with pytest.raises(promotion.PromotionError, match="push"):
        promotion.prepare(root, _request(shas["four"]), run_checks=None)
    assert receipt.is_file()
    assert brief.is_file()

    hook.unlink()
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    assert not receipt.exists()
    assert brief.is_file()


def test_finish_observes_a_rejected_push_and_a_settled_remote(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    candidate = promotion.prepare(root, _request(shas["four"]), run_checks=None)
    new_dev = promotion.build_replay(root, promotion.read_commit(root, candidate))
    hook = tmp_path / "repo.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(promotion.PromotionError, match="unchanged"):
        promotion.finish(root, "promotion/v1.1.0", ci=_passed)

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]
    hook.unlink()
    _git(root, "push", "origin", f"{candidate}:refs/heads/main", f"{new_dev}:refs/heads/dev")

    def unexpected_ci(*_args):
        raise AssertionError("a settled remote must not be checked again")

    promotion.finish(root, "promotion/v1.1.0", ci=unexpected_ci)
    assert _git(root, "rev-parse", "main").stdout.strip() == candidate
    assert _git(root, "rev-parse", "dev").stdout.strip() == new_dev


def test_finish_names_an_absent_remote_ref(tmp_path, catalogue):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    promotion.prepare(root, _request(shas["four"]), run_checks=None)
    _git(root, "push", "origin", ":refs/heads/dev")

    def unexpected_ci(*_args):
        raise AssertionError("an absent remote ref is diverged, not a CI check")

    with pytest.raises(promotion.PromotionError, match="absent"):
        promotion.finish(root, "promotion/v1.1.0", ci=unexpected_ci)

    assert _git(root, "rev-parse", "main").stdout.strip() == shas["main"]
    assert _git(root, "rev-parse", "dev").stdout.strip() == shas["four"]


def test_rev_parse_failure_is_not_a_missing_ref(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    assert promotion._rev_exists(root, "refs/heads/no-such") is False
    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    with pytest.raises(promotion.PromotionError, match="rev-parse"):
        promotion._rev_exists(missing, "HEAD")


def test_cleanup_reports_earlier_failures_when_origin_cannot_be_observed(tmp_path, monkeypatch):
    root, _shas = _dev_repo(tmp_path)
    worktree = root / ".worktrees" / "v1.1.0"
    worktree.mkdir(parents=True)

    def unobserved(_root, _branch):
        raise promotion.PromotionError("cannot observe origin promotion/v1.1.0: boom")

    monkeypatch.setattr(promotion, "_remote_branch", unobserved)
    with pytest.raises(promotion.PromotionError, match="cleanup failed") as raised:
        promotion._cleanup(root, "promotion/v1.1.0")
    message = str(raised.value)
    assert "cannot observe" in message
    assert "v1.1.0" in message


def test_remote_classification_names_every_outcome():
    old_main, old_dev, new_main, new_dev = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
    assert promotion.classify_remote(
        old_main, old_dev, new_main, new_dev, old_main, old_dev, observed=True
    ) == "unchanged"
    assert promotion.classify_remote(
        old_main, old_dev, new_main, new_dev, new_main, new_dev, observed=True
    ) == "settled"
    assert promotion.classify_remote(
        old_main, old_dev, new_main, new_dev, new_main, old_dev, observed=True
    ) == "diverged"
    assert promotion.classify_remote(
        old_main, old_dev, new_main, new_dev, None, None, observed=False
    ) == "unobserved"
    assert promotion.classify_remote(
        old_main, old_dev, new_main, new_dev, None, new_dev, observed=True
    ) == "diverged"
    assert promotion.classify_remote(
        old_main, old_dev, new_main, new_dev, old_main, None, observed=True
    ) == "diverged"
