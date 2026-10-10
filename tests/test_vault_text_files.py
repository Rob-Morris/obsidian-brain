"""DD087's filesystem scanned set, independent of router availability."""

import os
from pathlib import Path

import pytest

from _common import BOOTSTRAP_VARIANTS, LOCAL_OVERRIDE_VARIANTS
from _lifecycle.text_files import ROOT_BOOTSTRAP_VARIANTS, iter_vault_text_files
from _skill_library.tracking import empty_tracking, write_tracking


def put(root, relative, content=b"text"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scanned_roots_and_markdown_rules(tmp_path):
    included = {
        "Ideas/note.md",
        "Unregistered Type/nested/note.md",
        "Ideas/_status/note.md",
        "_Temporal/Logs/2026/note.md",
        "_Archive/Ideas/note.md",
        "_Config/Taxonomy/definition.md",
        "_Config/Memories/nested/memory.md",
        "_Config/Skills/local/SKILL.md",
        "_Plugins/plugin/docs/readme.md",
    }
    excluded = {
        "loose.md",
        "README.md",
        "_Assets/note.md",
        "_Workspaces/workspace/note.md",
        "_Other/note.md",
        ".hidden/note.md",
        ".brain-core/guide.md",
        ".brain/local/session.md",
        "Ideas/.hidden.md",
        "Ideas/.hidden/note.md",
        "_Config/.hidden/note.md",
        "_Plugins/.hidden.md",
        "Ideas/uppercase.MD",
        "Ideas/data.json",
        "_Config/config.yaml",
        "_Plugins/plugin/data.json",
    }
    for relative in included | excluded:
        put(tmp_path, relative, b"\xff\x00")
    assert set(iter_vault_text_files(str(tmp_path))) == included


@pytest.mark.parametrize("variant", [
    variant
    for variants in (*BOOTSTRAP_VARIANTS.values(), *LOCAL_OVERRIDE_VARIANTS.values())
    for variant in variants
])
def test_each_root_bootstrap_variant(tmp_path, variant):
    put(tmp_path, variant)
    put(tmp_path, "other.local.md")
    assert list(iter_vault_text_files(tmp_path)) == [variant]


def test_bootstrap_constant_contains_shared_variants():
    assert ROOT_BOOTSTRAP_VARIANTS == {
        variant
        for variants in (*BOOTSTRAP_VARIANTS.values(), *LOCAL_OVERRIDE_VARIANTS.values())
        for variant in variants
    }


def test_managed_package_ownership_excludes_whole_tree(tmp_path):
    tracking = empty_tracking()
    tracking["managed"]["managed"] = {
        "repository": "https://example.com/skills.git",
        "skill_path": "managed",
        "configured_ref": "main",
        "resolved_commit": "a" * 40,
        "source_package_sha256": "b" * 64,
        "installed_baseline_sha256": "b" * 64,
        "installed_manifest": [],
        "installed_at": "2026-10-08T00:00:00Z",
        "last_checked_at": None,
    }
    tracking["core_overrides"]["override"] = {
        "core_lineage": "override",
        "installed_baseline_sha256": "b" * 64,
        "installed_manifest": [],
        "materialised_at": "2026-10-08T00:00:00Z",
    }
    write_tracking(tmp_path, tracking)
    excluded = {"_Config/Skills/managed/SKILL.md", "_Config/Skills/managed/docs/extra.md"}
    included = {
        "_Config/Skills/detached/SKILL.md",
        "_Config/Skills/override/SKILL.md",
        "_Config/Skills/managed-other/SKILL.md",
        "_Config/Memories/managed/note.md",
        "Ideas/managed/note.md",
    }
    for relative in included | excluded:
        put(tmp_path, relative)
    assert set(iter_vault_text_files(tmp_path)) == included
    del tracking["managed"]["managed"]
    write_tracking(tmp_path, tracking)
    assert set(iter_vault_text_files(tmp_path)) == included | excluded


def test_symlink_files_directories_and_vault_root_are_excluded(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    target = put(outside, "note.md")
    kept = put(root, "Ideas/real.md")
    for relative in ("Ideas/link.md", "_Config/link.md", "_Plugins/link.md", "AGENTS.md"):
        link = root / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    for relative in ("Linked", "Ideas/linked", "_Temporal", "_Archive", "_Config/linked", "_Plugins/linked"):
        (root / relative).symlink_to(outside, target_is_directory=True)
    (root / "Ideas/internal.md").symlink_to(kept)
    (root / "Ideas/broken.md").symlink_to(outside / "missing.md")
    assert list(iter_vault_text_files(root)) == ["Ideas/real.md"]
    linked_root = tmp_path / "linked-vault"
    linked_root.symlink_to(root, target_is_directory=True)
    assert list(iter_vault_text_files(linked_root)) == []


def test_only_lstat_regular_files_are_included(tmp_path):
    put(tmp_path, "Ideas/regular.md")
    (tmp_path / "AGENTS.md").mkdir()
    if not hasattr(os, "mkfifo"):
        pytest.skip("Named pipes require POSIX")
    os.mkfifo(tmp_path / "Ideas/pipe.md")
    os.mkfifo(tmp_path / "CLAUDE.md")
    assert list(iter_vault_text_files(tmp_path)) == ["Ideas/regular.md"]


@pytest.mark.parametrize("name", ["Ideas", "_Temporal", "_Archive", "_Config", "_Plugins"])
def test_symlinked_scan_roots_are_excluded(tmp_path, name):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    put(outside, "nested/note.md")
    (root / name).symlink_to(outside, target_is_directory=True)
    assert list(iter_vault_text_files(root)) == []


@pytest.mark.parametrize("relative", ["Ideas/gone.md", "Ideas/gone", "AGENTS.md"])
def test_entry_disappearing_before_lstat_is_skipped(tmp_path, monkeypatch, relative):
    vanished = tmp_path / relative
    if relative.endswith(".md"):
        put(tmp_path, relative)
    else:
        vanished.mkdir(parents=True)
    put(tmp_path, "Ideas/kept.md")
    original = Path.lstat

    def disappear(path, *args, **kwargs):
        if path == vanished:
            if relative.endswith(".md"):
                path.unlink()
            else:
                path.rmdir()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappear)
    assert list(iter_vault_text_files(tmp_path)) == ["Ideas/kept.md"]


def test_directory_disappearing_before_listing_is_skipped(tmp_path, monkeypatch):
    vanished = tmp_path / "Gone"
    vanished.mkdir()
    put(tmp_path, "Ideas/kept.md")
    original = Path.iterdir

    def disappear(path):
        if path == vanished:
            path.rmdir()
        return original(path)

    monkeypatch.setattr(Path, "iterdir", disappear)
    assert list(iter_vault_text_files(tmp_path)) == ["Ideas/kept.md"]


def test_missing_vault_is_empty(tmp_path):
    assert list(iter_vault_text_files(tmp_path / "missing")) == []


def test_other_filesystem_errors_propagate(tmp_path, monkeypatch):
    blocked = put(tmp_path, "Ideas/blocked.md")
    original = Path.lstat

    def deny(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", deny)
    with pytest.raises(PermissionError, match="access denied"):
        list(iter_vault_text_files(tmp_path))
