from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from brain_lab.capture import capture_worktree
from brain_lab.process import CommandRunner
from brain_lab.resources import _ensure_capture_space


def _repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "brain-lab@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Brain Lab"], check=True)
    (path / "tracked.txt").write_text("tracked", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return path


def test_capture_space_preflight_records_conservative_copy_headroom(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "brain_lab.resources.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=4 * 1024 * 1024 * 1024),
    )

    preflight = _ensure_capture_space(tmp_path, 1024 * 1024 * 1024)

    assert preflight["copy_multiplier"] == 2
    assert preflight["fixed_headroom_bytes"] == 512 * 1024 * 1024
    assert preflight["required_free_bytes"] == 2560 * 1024 * 1024


def test_worktree_capture_includes_tracked_modifications_but_not_untracked_by_default(tmp_path: Path):
    repository = _repository(tmp_path / "repo")
    (repository / "tracked.txt").write_text("modified", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked", encoding="utf-8")

    capture = capture_worktree(
        CommandRunner(),
        repository,
        evidence_directory=tmp_path / "evidence",
    )

    assert [entry.path for entry in capture.manifest.entries] == ["tracked.txt"]
    assert capture.excluded_untracked_paths == ("untracked.txt",)


def test_worktree_capture_requires_explicit_reviewed_untracked_paths(tmp_path: Path):
    repository = _repository(tmp_path / "repo")
    (repository / "untracked.txt").write_text("untracked", encoding="utf-8")

    capture = capture_worktree(
        CommandRunner(),
        repository,
        include_untracked=["untracked.txt"],
        evidence_directory=tmp_path / "evidence",
    )

    assert [entry.path for entry in capture.manifest.entries] == ["tracked.txt", "untracked.txt"]
    assert capture.included_untracked_paths == ("untracked.txt",)
    assert capture.excluded_untracked_paths == ()


def test_worktree_capture_excludes_ignored_paths_unless_explicitly_included(tmp_path: Path):
    repository = _repository(tmp_path / "repo")
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "ignore fixture"], check=True)
    (repository / "ignored.txt").write_text("ignored", encoding="utf-8")

    excluded = capture_worktree(
        CommandRunner(),
        repository,
        evidence_directory=tmp_path / "excluded-evidence",
    )
    included = capture_worktree(
        CommandRunner(),
        repository,
        include_untracked=["ignored.txt"],
        evidence_directory=tmp_path / "included-evidence",
    )

    assert "ignored.txt" not in [entry.path for entry in excluded.manifest.entries]
    assert "ignored.txt" in [entry.path for entry in included.manifest.entries]


def test_worktree_capture_represents_tracked_deletions_without_streaming_missing_files(tmp_path: Path):
    repository = _repository(tmp_path / "repo")
    (repository / "tracked.txt").unlink()

    capture = capture_worktree(
        CommandRunner(),
        repository,
        evidence_directory=tmp_path / "evidence",
    )

    assert capture.manifest.entries == ()
    assert capture.deleted_tracked_paths == ("tracked.txt",)


def test_worktree_capture_rejects_escaping_explicit_paths(tmp_path: Path):
    repository = _repository(tmp_path / "repo")

    with pytest.raises(ValueError, match="contained"):
        capture_worktree(
            CommandRunner(),
            repository,
            include_untracked=["../outside"],
            evidence_directory=tmp_path / "evidence",
        )
