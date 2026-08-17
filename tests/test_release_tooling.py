"""Release preparation, status and immutable-source export contracts."""

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

import release


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _release_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Release Tests")
    _git(root, "config", "user.email", "release@example.invalid")
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
    _write(
        root,
        "docs/changelog/v1.0.0.md",
        "# v1.0.0\n\n**Summary:** Existing release\n",
    )
    _write(
        root,
        release.FUNCTIONAL_CLI_PATH,
        "Unix `lib/brain-cli/2.0.0/`; Windows `lib\\brain-cli\\2.0.0\\`.\n"
        "`BRAIN_CLI_VERSION` is `2.0.0`; `BRAIN_INSTALL_REF` is `v1.0.0`.\n",
    )
    _write(
        root,
        release.USER_REFERENCE_PATH,
        "Reference for Brain Core 1.0.0 and CLI 2.0.0.\n",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def _set_release(root: Path, core: str, cli: str) -> None:
    _write(root, release.VERSION_PATH, core + "\n")
    _write(root, release.README_PATH, f"![Version](version-{core}-blue)\n")
    _write(
        root,
        release.UNIX_CLI_PATH,
        f'#!/bin/sh\nBRAIN_CLI_VERSION="{cli}"\nBRAIN_INSTALL_REF="v{core}"\n',
    )
    _write(
        root,
        release.WINDOWS_CLI_PATH,
        f'@echo off\nset "BRAIN_CLI_VERSION={cli}"\nset "BRAIN_INSTALL_REF=v{core}"\n',
    )
    _write(
        root,
        release.FUNCTIONAL_CLI_PATH,
        f"Unix `lib/brain-cli/{cli}/`; Windows `lib\\brain-cli\\{cli}\\`.\n"
        f"`BRAIN_CLI_VERSION` is `{cli}`; `BRAIN_INSTALL_REF` is `v{core}`.\n",
    )
    _write(
        root,
        release.USER_REFERENCE_PATH,
        f"Reference for Brain Core {core} and CLI {cli}.\n",
    )
    index = (root / release.CHANGELOG_INDEX_PATH).read_text(encoding="utf-8")
    index = release._index_with_release(index, core, "2026-08-16", f"Release {core}")
    _write(root, release.CHANGELOG_INDEX_PATH, index)


def test_status_distinguishes_head_index_and_worktree_release_facts(tmp_path):
    root = _release_repo(tmp_path)
    _set_release(root, "1.1.0", "2.1.0")
    _git(
        root,
        "add",
        release.VERSION_PATH,
        release.README_PATH,
        release.UNIX_CLI_PATH,
        release.WINDOWS_CLI_PATH,
        release.CHANGELOG_INDEX_PATH,
        release.FUNCTIONAL_CLI_PATH,
        release.USER_REFERENCE_PATH,
    )
    _set_release(root, "1.2.0", "2.2.0")

    assert release.release_facts(root, "head").core == "1.0.0"
    assert release.release_facts(root, "index").core == "1.1.0"
    assert release.release_facts(root, "worktree").core == "1.2.0"
    assert release.release_facts(root, "head").coherent is True
    assert release.release_facts(root, "index").coherent is True
    assert release.release_facts(root, "worktree").coherent is True


def test_prepare_is_dry_run_first_and_applies_explicit_release_intent(tmp_path, capsys):
    root = _release_repo(tmp_path)
    args = [
        "--repo",
        str(root),
        "prepare",
        "--core-version",
        "1.1.0",
        "--cli-version",
        "2.1.0",
        "--proxy-version",
        "0.4.0",
        "--summary",
        "Make release preparation explicit",
        "--date",
        "2026-08-16",
        "--release-type",
        "Backward-compatible release tooling",
        "--change",
        "Add an explicit release preparation command.",
    ]

    assert release.main(args) == 0
    assert (root / release.VERSION_PATH).read_text(encoding="utf-8") == "1.0.0\n"
    assert "dry run" in capsys.readouterr().out

    assert release.main([*args, "--apply"]) == 0
    assert (root / release.VERSION_PATH).read_text(encoding="utf-8") == "1.1.0\n"
    assert "version-1.1.0-blue" in (root / release.README_PATH).read_text()
    assert 'BRAIN_CLI_VERSION="2.1.0"' in (root / release.UNIX_CLI_PATH).read_text()
    assert 'PROXY_VERSION = "0.4.0"' in (root / release.PROXY_PATH).read_text()
    assert (root / "docs/changelog/v1.1.0.md").is_file()
    assert release.release_facts(root, "worktree").coherent is True


def test_export_materialises_only_the_selected_commit(tmp_path, capsys):
    root = _release_repo(tmp_path)
    tracked = root / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "add tracked")
    commit = _git(root, "rev-parse", "HEAD").strip()
    tracked.write_text("dirty\n", encoding="utf-8")
    (root / "untracked.txt").write_text("not exported\n", encoding="utf-8")
    destination = tmp_path / "export"

    assert release.main(
        ["--repo", str(root), "export", "--ref", "HEAD", "--destination", str(destination)]
    ) == 0

    assert tracked.read_text(encoding="utf-8") == "dirty\n"
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (destination / "untracked.txt").exists()
    assert json.loads(capsys.readouterr().out)["commit"] == commit


def test_release_write_attempts_every_rollback_and_reports_recovery_paths(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "release"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(f"old-{name}\n", encoding="utf-8")
    real_replace = release.os.replace

    def _replace(source, destination):
        source = Path(source)
        destination = Path(destination)
        if source.name.endswith(".release") and destination.name == "c.txt":
            raise OSError("injected release write failure")
        if source.name.endswith(".restore") and destination.name == "a.txt":
            raise OSError("injected first rollback failure")
        real_replace(source, destination)

    monkeypatch.setattr(release.os, "replace", _replace)

    with pytest.raises(release.ReleaseTransactionError) as caught:
        release._write_transaction(
            root,
            {
                "a.txt": "new-a\n",
                "b.txt": "new-b\n",
                "c.txt": "new-c\n",
            },
        )

    assert caught.value.rollback_complete is False
    assert "injected release write failure" in str(caught.value)
    assert root / "a.txt" in caught.value.recovery_paths
    assert any(path.name.endswith(".restore") for path in caught.value.recovery_paths)
    assert (root / "a.txt").read_text(encoding="utf-8") == "new-a\n"
    assert (root / "b.txt").read_text(encoding="utf-8") == "old-b.txt\n"
    assert (root / "c.txt").read_text(encoding="utf-8") == "old-c.txt\n"


def test_release_stage_cleanup_failure_does_not_mask_initiating_error(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "release"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    real_replace = release.os.replace
    real_unlink = release.Path.unlink

    def _replace(source, destination):
        if Path(source).name.endswith(".release"):
            raise OSError("initiating replacement failure")
        real_replace(source, destination)

    def _unlink(path, *args, **kwargs):
        if path.name.endswith(".release"):
            raise OSError("cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(release.os, "replace", _replace)
    monkeypatch.setattr(release.Path, "unlink", _unlink)

    with pytest.raises(release.ReleaseTransactionError) as caught:
        release._write_transaction(root, {"a.txt": "new\n"})

    assert "initiating replacement failure" in str(caught.value)
    assert caught.value.initiating_error.args == ("initiating replacement failure",)
    assert caught.value.rollback_complete is False
    assert len(caught.value.recovery_paths) == 1
    assert caught.value.recovery_paths[0].name.endswith(".release")
