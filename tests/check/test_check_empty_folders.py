"""Tests for the ``empty_folders`` compliance check and its scan helper."""

import os

import pytest

import check
from _common import scan_empty_artefact_folders
from brain_test_support import write_md


def _paths(findings):
    return [f["file"] for f in findings]


class TestScanEmptyArtefactFolders:
    def test_empty_owner_subfolder_is_reported(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "project~brain").mkdir()
        found = scan_empty_artefact_folders(str(tmp_path), router)
        assert "Designs/project~brain" in [f["path"] for f in found]

    def test_junk_only_directory_is_reported_with_its_junk(self, vault):
        tmp_path, router = vault
        junk_dir = tmp_path / "Designs" / "stale"
        junk_dir.mkdir()
        (junk_dir / ".DS_Store").write_bytes(b"\x00")
        (junk_dir / "Thumbs.db").write_bytes(b"\x00")
        found = {f["path"]: f for f in scan_empty_artefact_folders(str(tmp_path), router)}
        assert sorted(found["Designs/stale"]["junk_files"]) == [
            "Designs/stale/.DS_Store",
            "Designs/stale/Thumbs.db",
        ]

    def test_nested_empty_chain_reports_only_the_maximal_directory(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "a" / "b" / "c").mkdir(parents=True)
        found = {f["path"]: f for f in scan_empty_artefact_folders(str(tmp_path), router)}
        assert "Designs/a" in found
        assert "Designs/a/b" not in found
        assert found["Designs/a"]["directories"] == [
            "Designs/a",
            "Designs/a/b",
            "Designs/a/b/c",
        ]

    def test_partially_empty_parent_reports_the_empty_children_only(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "owner" / "empty").mkdir(parents=True)
        write_md(
            tmp_path / "Designs" / "owner" / "Live.md",
            {"type": "living/design", "tags": ["design"], "status": "shaping"},
        )
        paths = [f["path"] for f in scan_empty_artefact_folders(str(tmp_path), router)]
        assert "Designs/owner/empty" in paths
        assert "Designs/owner" not in paths

    def test_empty_status_and_owner_folders_are_reported(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "+Implemented").mkdir()
        paths = [f["path"] for f in scan_empty_artefact_folders(str(tmp_path), router)]
        assert "Designs/+Implemented" in paths
        # The check-suite fixture never populates this owner folder.
        assert "_Temporal/Plans/project~stale" in paths

    def test_archive_mirror_and_legacy_in_type_archive_are_covered(self, vault):
        tmp_path, router = vault
        (tmp_path / "_Archive" / "Designs" / "project~gone").mkdir(parents=True)
        (tmp_path / "Designs" / "_Archive").mkdir()
        paths = [f["path"] for f in scan_empty_artefact_folders(str(tmp_path), router)]
        assert "_Archive/Designs" in paths
        assert "Designs/_Archive" in paths

    def test_roots_symlinks_and_dot_dirs_are_never_reported(self, vault, tmp_path_factory):
        tmp_path, router = vault
        (tmp_path / "Designs" / ".obsidian-scratch").mkdir()
        outside = tmp_path_factory.mktemp("outside")
        os.symlink(outside, tmp_path / "Designs" / "linked")
        holder = tmp_path / "Designs" / "holder"
        holder.mkdir()
        os.symlink(outside, holder / "link")
        paths = [f["path"] for f in scan_empty_artefact_folders(str(tmp_path), router)]
        assert "Designs" not in paths
        assert "_Archive" not in paths
        assert "Designs/.obsidian-scratch" not in paths
        assert "Designs/linked" not in paths
        assert "Designs/holder" not in paths, "a symlink counts as content"

    def test_directory_with_a_real_file_is_not_reported(self, vault):
        tmp_path, router = vault
        keep = tmp_path / "Designs" / "keep"
        keep.mkdir()
        (keep / "notes.txt").write_text("real content\n")
        paths = [f["path"] for f in scan_empty_artefact_folders(str(tmp_path), router)]
        assert "Designs/keep" not in paths

    def test_nested_territory_root_is_a_boundary_not_a_finding(self, tmp_path):
        router = {"artefacts": [
            {"path": "Ideas", "key": "ideas"},
            {"path": "Ideas/Sketches", "key": "sketches"},
        ]}
        (tmp_path / "Ideas" / "Sketches").mkdir(parents=True)
        (tmp_path / "Ideas" / "holder" / "Sketches").mkdir(parents=True)
        router_nested = dict(router)
        router_nested["artefacts"] = router["artefacts"] + [
            {"path": "Ideas/holder/Sketches", "key": "held"}
        ]
        found = scan_empty_artefact_folders(str(tmp_path), router_nested)
        paths = [f["path"] for f in found]
        assert "Ideas/Sketches" not in paths, "a nested type root is never reported"
        assert "Ideas/holder" not in paths, "a root beneath it counts as content"
        assert paths == []

    def test_missing_scan_roots_are_skipped(self, tmp_path):
        router = {"artefacts": [{"path": "Nowhere", "key": "nowhere"}]}
        assert scan_empty_artefact_folders(str(tmp_path), router) == []

    def test_unreadable_directory_is_reported_when_asked_and_skipped_otherwise(self, vault):
        if os.geteuid() == 0:
            pytest.skip("permission bits do not bind root")
        tmp_path, router = vault
        locked = tmp_path / "Designs" / "locked"
        locked.mkdir()
        (locked / "hidden").mkdir()
        locked.chmod(0o000)
        try:
            unreadable = []
            findings = scan_empty_artefact_folders(str(tmp_path), router, unreadable=unreadable)
            assert unreadable == ["Designs/locked"]
            assert "Designs/locked" not in [f["path"] for f in findings]
            assert scan_empty_artefact_folders(str(tmp_path), router) == findings
        finally:
            locked.chmod(0o755)


class TestCheckEmptyFolders:
    def test_finding_shape_is_info_and_repairable(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "project~brain").mkdir()
        findings = [
            f for f in check.check_empty_folders(str(tmp_path), router)
            if f["file"] == "Designs/project~brain"
        ]
        assert len(findings) == 1
        finding = findings[0]
        assert finding["check"] == "empty_folders"
        assert finding["severity"] == "info"
        assert finding["repairable"] is True
        assert finding["repair"]["scope"] == "empty_folders"
        assert "empty_folders" in finding["repair"]["command"]
        assert "repair" in finding["message"]
        assert "directories" not in finding, "the repair dry run owns the full listing"

    def test_surfaces_through_run_checks_as_info(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "project~brain").mkdir()
        result = check.run_checks(str(tmp_path), router)
        matching = [
            f for f in result["findings"]
            if f["check"] == "empty_folders" and f["file"] == "Designs/project~brain"
        ]
        assert len(matching) == 1
        assert result["summary"]["info"] >= 1

    def test_clean_type_root_produces_no_findings(self, vault):
        tmp_path, router = vault
        findings = check.check_empty_folders(str(tmp_path), router)
        assert not any(f["file"].startswith("Wiki") for f in findings)
