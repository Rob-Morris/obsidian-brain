from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from brain_lab.manifests import (
    manifest_tree,
    normalised_core_manifest,
    portable_manifest,
    read_gzip_json,
)
from brain_lab.run_state import filesystem_diff


def test_manifest_is_content_derived_and_ignores_mtime(tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    file = root / "note.md"
    file.write_text("hello", encoding="utf-8")
    before = manifest_tree(root)
    os.utime(file, (1, 1))
    after = manifest_tree(root)

    assert before.tree_sha256 == after.tree_sha256
    file.write_text("changed", encoding="utf-8")
    assert manifest_tree(root).tree_sha256 != before.tree_sha256


def test_compressed_json_reader_bounds_expanded_bytes_and_entries(tmp_path: Path):
    evidence = tmp_path / "manifest.json.gz"
    evidence.write_bytes(gzip.compress(json.dumps({"padding": "x" * 100}).encode()))

    with pytest.raises(ValueError, match="expanded bound"):
        read_gzip_json(evidence, max_expanded_bytes=32)

    evidence.write_bytes(gzip.compress(json.dumps({"entries": [{}, {}, {}]}).encode()))
    with pytest.raises(ValueError, match="entry bound"):
        read_gzip_json(evidence, max_entries=2)


def test_manifest_preserves_contained_symlink_without_dereference(tmp_path: Path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "target.txt").write_text("target", encoding="utf-8")
    (root / "link.txt").symlink_to("target.txt")

    manifest = manifest_tree(root)

    link = next(entry for entry in manifest.entries if entry.path == "link.txt")
    assert link.kind == "symlink"
    assert link.link_target == "target.txt"


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside"])
def test_manifest_rejects_absolute_or_escaping_symlink(tmp_path: Path, target: str):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "link").symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        manifest_tree(root)


def test_manifest_rejects_special_files(tmp_path: Path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="special files"):
        manifest_tree(tmp_path)


def test_portable_and_core_normalisation_exclude_only_generated_state(tmp_path: Path):
    vault = tmp_path / "vault"
    core = vault / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.62.0", encoding="utf-8")
    cache = core / "__pycache__"
    cache.mkdir()
    (cache / "generated.pyc").write_bytes(b"generated")
    local = vault / ".brain" / "local"
    local.mkdir(parents=True)
    (local / "session.md").write_text("generated", encoding="utf-8")
    (vault / "note.md").write_text("portable", encoding="utf-8")

    assert all("__pycache__" not in entry.path for entry in normalised_core_manifest(vault).entries)
    assert all(not entry.path.startswith(".brain/local/") for entry in portable_manifest(vault).entries)
    assert any(entry.path == "note.md" for entry in portable_manifest(vault).entries)


def test_core_normalisation_excludes_distribution_only_upgrade_bootstrap(tmp_path: Path):
    first_vault = tmp_path / "first"
    second_vault = tmp_path / "second"
    first = first_vault / ".brain-core"
    second = second_vault / ".brain-core"
    for core in (first, second):
        scripts = core / "scripts"
        scripts.mkdir(parents=True)
        (core / "VERSION").write_text("0.54.0", encoding="utf-8")
        (core / "runtime.py").write_text("same", encoding="utf-8")

    (first / "scripts" / "upgrade.py").write_text("old bootstrap", encoding="utf-8")
    (second / "scripts" / "upgrade.py").write_text("distribution entry point", encoding="utf-8")

    assert normalised_core_manifest(first_vault).tree_sha256 == normalised_core_manifest(
        second_vault
    ).tree_sha256

    (second / "runtime.py").write_text("changed", encoding="utf-8")
    assert normalised_core_manifest(first_vault).tree_sha256 != normalised_core_manifest(
        second_vault
    ).tree_sha256


def test_run_filesystem_diff_retains_complete_metadata_for_every_change():
    before = {
        "tree_sha256": "before",
        "entries": [
            {"path": "changed.txt", "kind": "file", "size": 3, "sha256": "old"},
            {"path": "removed.txt", "kind": "file", "size": 1, "sha256": "gone"},
        ],
    }
    after = {
        "tree_sha256": "after",
        "entries": [
            {"path": "added.txt", "kind": "file", "size": 1, "sha256": "new"},
            {"path": "changed.txt", "kind": "file", "size": 4, "sha256": "newer"},
        ],
    }

    diff = filesystem_diff(before, after)

    assert diff["summary"] == {"added": 1, "removed": 1, "changed": 1}
    assert diff["added"][0]["path"] == "added.txt"
    assert diff["removed"][0]["path"] == "removed.txt"
    assert diff["changed"][0] == {
        "path": "changed.txt",
        "before": before["entries"][0],
        "after": after["entries"][1],
    }
