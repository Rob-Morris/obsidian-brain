"""Tests for workspace-local scaffold helpers."""

from __future__ import annotations


import pytest

from _bootstrap import workspace_scaffold
from _bootstrap.workspace_scaffold import GitInspectionError, ensure_brain_ignore_rules


def test_ensure_brain_ignore_rules_updates_gitignore(project, monkeypatch):
    gitignore = project / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")
    monkeypatch.setattr(workspace_scaffold, "_git_repo_root", lambda _target: project)
    monkeypatch.setattr(workspace_scaffold, "_git_dir", lambda _target: project / ".git")

    ensure_brain_ignore_rules(project, "project", ["claude", "codex"], skip_mcp=False)

    content = gitignore.read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".brain/local/" in content
    assert ".claude/settings.local.json" in content
    assert ".codex/config.toml" in content


def test_ensure_brain_ignore_rules_falls_back_to_git_info_exclude(project, monkeypatch):
    git_dir = project / ".git"
    (git_dir / "info").mkdir(parents=True)
    monkeypatch.setattr(workspace_scaffold, "_git_repo_root", lambda _target: project)
    monkeypatch.setattr(workspace_scaffold, "_git_dir", lambda _target: git_dir)

    ensure_brain_ignore_rules(project, "project", ["claude"], skip_mcp=True)

    exclude = (git_dir / "info" / "exclude").read_text(encoding="utf-8")
    assert ".brain/local/" in exclude
    assert ".claude/settings.local.json" not in exclude


def test_ensure_brain_ignore_rules_raises_when_destination_is_unreadable(
    project, monkeypatch
):
    gitignore = project / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")
    monkeypatch.setattr(workspace_scaffold, "_git_repo_root", lambda _target: project)
    monkeypatch.setattr(workspace_scaffold, "_git_dir", lambda _target: project / ".git")
    original_read_text = type(gitignore).read_text

    def fake_read_text(path, *args, **kwargs):
        if path == gitignore:
            raise OSError("permission denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(gitignore), "read_text", fake_read_text)

    with pytest.raises(GitInspectionError, match="failed to read ignore destination"):
        ensure_brain_ignore_rules(project, "project", ["claude"], skip_mcp=False)
