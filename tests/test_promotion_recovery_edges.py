"""Real-Git recovery edges for authored releases and retained source history."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from test_promotion import _dev_repo, _git, _passed, _receipt, _request, _write
from _promotion import git, model, recovery_plan, workflow
import release


_EDITORIAL = (
    "\n## Editorial detail\n\n"
    "Keep the **exact** paragraph breaks, `inline code`, and this table:\n\n"
    "| Component | Planned version |\n"
    "|---|---|\n"
    "| Core | v1.1.0 |\n"
    "| CLI | 2.1.0 |\n"
    "| Proxy | 0.4.0 |\n\n"
    "> Preserve this authored callout.\n"
)


def _install_source_renderer(root: Path) -> None:
    """Give the dev cut a release renderer absent from its main base."""
    path = root / "src/scripts/release.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n_original_prepare_release = prepare_release\n"
        + "def prepare_release(root, **kwargs):\n"
        + "    edits = _original_prepare_release(root, **kwargs)\n"
        + "    note = f\"docs/changelog/v{kwargs['core_version']}.md\"\n"
        + f"    edits[note] += {_EDITORIAL!r}\n"
        + "    edits[README_PATH] += '\\n<!-- source-owned-renderer -->\\n'\n"
        + "    return edits\n",
        encoding="utf-8",
    )
    _git(root, "add", "src/scripts/release.py")
    _git(root, "commit", "-m", "WIP: retain source release renderer")
    _git(root, "push", "origin", "dev")


def _direct_main_release(
    root: Path, version: str, *, cli: str | None = None, proxy: str | None = None
) -> str:
    _git(root, "switch", "main")
    changes = release.prepare_release(
        root,
        core_version=version,
        summary="Repair published installation",
        release_date="2026-09-24",
        release_type="Emergency repair",
        changes=["Repair the published installation."],
        cli_version=cli,
        proxy_version=proxy,
    )
    release.apply_release(root, changes)
    _write(root, "hotfix.txt", f"published repair for {version}\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", f"Repair published installation (v{version})")
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    _git(root, "push", "origin", "main")
    _git(root, "switch", "dev")
    return sha


def _finished_release(root: Path, version: str, *, cli: str | None = None,
                      proxy: str | None = None) -> str:
    _receipt(root)
    request = replace(_request(None, version), cli_version=cli, proxy_version=proxy)
    candidate = workflow.prepare(root, request, run_checks=None)
    workflow.finish(root, f"promotion/v{version}", ci=_passed)
    return candidate


def test_rebuild_retains_exact_authored_note_helper_intent_and_source_renderer(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    _install_source_renderer(root)
    old = _finished_release(root, "1.1.0", cli="2.1.0", proxy="0.4.0")
    old_note = _git(root, "show", f"{old}:docs/changelog/v1.1.0.md").stdout
    assert _EDITORIAL in old_note

    main = _direct_main_release(root, "1.1.0", cli="2.2.0", proxy="0.5.0")
    plan = recovery_plan.build_plan(
        root,
        version_map={
            "core": {"1.1.0": "1.2.0"},
            "cli": {"2.1.0": "2.3.0"},
            "proxy": {"0.4.0": "0.6.0"},
        },
        run_checks=None,
    )

    assert plan.snapshot["main"] == main
    assert len(plan.entries) == 1
    entry = plan.entries[0]
    assert entry.old_candidate == old
    assert (entry.new_version, entry.cli_version, entry.proxy_version) == (
        "1.2.0", "2.3.0", "0.6.0"
    )
    expected_note = (old_note.replace("1.1.0", "1.2.0")
                     .replace("2.1.0", "2.3.0")
                     .replace("0.4.0", "0.6.0"))
    actual_note = _git(root, "show", f"{entry.new_candidate}:docs/changelog/v1.2.0.md").stdout
    assert actual_note == expected_note == entry.new_note
    assert "<!-- source-owned-renderer -->" in _git(
        root, "show", f"{entry.new_candidate}:{release.README_PATH}"
    ).stdout
    assert _git(root, "show", f"{entry.new_candidate}:{release.VERSION_PATH}").stdout == "1.2.0\n"
    assert _git(root, "show", f"{entry.new_candidate}:{release.PROXY_PATH}").stdout == (
        'PROXY_VERSION = "0.6.0"\n'
    )
    assert _git(root, "merge-base", "--is-ancestor", entry.old_source, plan.sha).returncode == 0
    assert _git(root, "merge-base", "--is-ancestor", entry.new_source, plan.sha).returncode == 0

    # A narrow fresh clone receives only main and the plan ref. The sealed plan
    # must retain both source graphs without relying on this worktree's refs.
    _git(root, "push", "origin", f"{plan.sha}:refs/heads/recovery/{plan.sha}/plan")
    clone = tmp_path / "fresh"
    _git(tmp_path, "clone", "--single-branch", "--branch", "main",
         str(tmp_path / "repo.git"), str(clone))
    _git(clone, "fetch", "origin",
         f"refs/heads/recovery/{plan.sha}/plan:refs/remotes/origin/recovery/{plan.sha}/plan")
    loaded = recovery_plan.load_plan(clone, plan.sha)
    assert loaded.sha == plan.sha
    for source in (entry.old_source, entry.new_source):
        assert _git(clone, "cat-file", "-t", source).stdout.strip() == "commit"


def test_rebuild_only_unpublished_suffix_after_prior_publication(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    first = _finished_release(root, "1.1.0")
    _write(root, "later.txt", "second release content\n")
    _git(root, "add", "later.txt")
    _git(root, "commit", "-m", "WIP: second release content")
    _git(root, "push", "origin", "dev")
    second = _finished_release(root, "1.2.0")
    workflow.publish(root, first, ci=_passed)

    hotfix = _direct_main_release(root, "1.2.0")
    plan = recovery_plan.build_plan(
        root, version_map={"core": {"1.2.0": "1.3.0"}}, run_checks=None
    )

    assert plan.snapshot["anchor"] == first
    assert plan.snapshot["main"] == hotfix
    assert [entry.old_candidate for entry in plan.entries] == [second]
    assert plan.entries[0].new_version == "1.3.0"
    assert _git(root, "merge-base", "--is-ancestor", first, plan.sha).returncode == 0
    assert _git(root, "show", f"{plan.target_unreleased}:later.txt").stdout == (
        "second release content\n"
    )


def test_candidate_check_receives_exact_rebuilt_tree_and_refuses_mutation(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    _finished_release(root, "1.1.0")
    _direct_main_release(root, "1.1.0")
    seen: list[str] = []

    def mutate_candidate(worktree: Path) -> None:
        assert (worktree / release.VERSION_PATH).read_text(encoding="utf-8") == "1.2.0\n"
        seen.append(_git(worktree, "rev-parse", "HEAD").stdout.strip())
        (worktree / "notes.txt").write_text("checker changed this checkout\n", encoding="utf-8")

    with pytest.raises(model.PromotionError, match="checks changed rebuilt v1.2.0 checkout"):
        recovery_plan.build_plan(
            root,
            version_map={"core": {"1.1.0": "1.2.0"}},
            run_checks=mutate_candidate,
        )
    assert len(seen) == 1
    assert git.rev(root, "refs/remotes/origin/unreleased") != seen[0]


def test_default_candidate_checks_run_contracts_and_serial_pytest_on_rebuilt_tree(tmp_path):
    root, _shas = _dev_repo(tmp_path)
    observed = tmp_path / "checked-candidate"
    _write(
        root,
        "src/scripts/check_repository_contracts.py",
        "from pathlib import Path\n"
        "import subprocess\n"
        "assert Path('src/brain-core/VERSION').read_text() == '1.2.0\\n'\n"
        f"Path({str(observed)!r}).write_text(subprocess.check_output("
        "['git', 'rev-parse', 'HEAD'], text=True))\n",
    )
    _write(
        root,
        "tests/test_candidate.py",
        "from pathlib import Path\n"
        "def test_rebuilt_candidate_is_the_test_subject():\n"
        "    assert Path('src/brain-core/VERSION').read_text() == '1.2.0\\n'\n",
    )
    _write(root, "template-vault/README.md", "test checkout\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "WIP: add candidate verification fixture")
    _git(root, "push", "origin", "dev")
    _finished_release(root, "1.1.0")
    _direct_main_release(root, "1.1.0")

    plan = recovery_plan.build_plan(
        root,
        version_map={"core": {"1.1.0": "1.2.0"}},
        run_checks=workflow.default_checks,
    )

    assert observed.read_text(encoding="utf-8").strip() == plan.entries[0].new_candidate
