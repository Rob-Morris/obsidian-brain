"""Tests for check.py — router-driven vault compliance checker."""

import json
import os
import sys
import time

import pytest

import check
import compile_router as cr
import _lifecycle.semantic_repairs as semantic_repairs
import _search.index as search_index
import _search.paths as search_paths

from brain_test_support import make_router, write_md
from brain_test_support import filesystem_is_case_sensitive


class TestOwnershipChecks:
    def test_missing_key_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "no-key.md",
                 {"type": "living/wiki", "tags": ["wiki"]}, "# Missing Key")
        findings = check.check_living_key_fields(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "Wiki/no-key.md"]
        assert len(hits) == 1
        assert "key" in hits[0]["message"].lower()
        assert hits[0]["severity"] == "error"

    def test_broken_parent_reference_flagged(self, vault):
        tmp_path, router = vault
        child_dir = tmp_path / "Wiki" / "design~auth-redesign"
        child_dir.mkdir(parents=True)
        write_md(child_dir / "child.md",
                 {"type": "living/wiki", "tags": ["design/missing"], "key": "child", "parent": "design/missing"},
                 "# Child")
        findings = check.check_parent_contract(str(tmp_path), router)
        assert any("Broken parent reference" in f["message"] for f in findings)

    def test_parent_folder_drift_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "child.md",
                 {"type": "living/wiki", "tags": ["design/auth-redesign"], "key": "child", "parent": "design/auth-redesign"},
                 "# Child")
        findings = check.check_parent_contract(str(tmp_path), router)
        assert any("Parent-folder drift" in f["message"] for f in findings)

    def test_subfolder_without_parent_flagged(self, vault):
        """A file in a hub subfolder whose name resolves emits a missing-parent warning
        naming the inferred parent.
        """
        tmp_path, router = vault
        child_dir = tmp_path / "Wiki" / "design~auth-redesign"
        child_dir.mkdir(parents=True)
        write_md(child_dir / "tag-only.md",
                 {"type": "living/wiki", "tags": ["design/auth-redesign"], "key": "tag-only"},
                 "# Tag Only")
        findings = check.check_parent_contract(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "Wiki/design~auth-redesign/tag-only.md"]
        assert len(hits) == 1
        assert hits[0]["severity"] == "warning"
        assert "folder implies `design/auth-redesign`" in hits[0]["message"]
        assert "Set `parent: design/auth-redesign`" in hits[0]["fix"]

    def test_subfolder_with_invalid_key_name_flagged_as_orphan(self, vault):
        """A subfolder whose name isn't a valid key (spaces, mixed case) must not
        crash make_artefact_key — it falls through to the orphan branch.
        """
        tmp_path, router = vault
        child_dir = tmp_path / "Wiki" / "Claude Code"
        child_dir.mkdir(parents=True)
        write_md(child_dir / "note.md",
                 {"type": "living/wiki", "tags": [], "key": "note"},
                 "# Note")
        findings = check.check_parent_contract(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "Wiki/Claude Code/note.md"]
        assert len(hits) == 1
        assert "Orphan artefact" in hits[0]["message"]

    def test_orphan_subfolder_flagged(self, vault):
        """A file in a subfolder whose name matches no living artefact is flagged as orphan,
        with a fix hint that doesn't pretend setting parent would work.
        """
        tmp_path, router = vault
        child_dir = tmp_path / "Wiki" / "no-such-owner"
        child_dir.mkdir(parents=True)
        write_md(child_dir / "stray.md",
                 {"type": "living/wiki", "tags": [], "key": "stray"},
                 "# Stray")
        findings = check.check_parent_contract(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "Wiki/no-such-owner/stray.md"]
        assert len(hits) == 1
        assert hits[0]["severity"] == "warning"
        assert "Orphan artefact" in hits[0]["message"]
        assert "no-such-owner" in hits[0]["message"]
        assert "Move the file" in hits[0]["fix"]

    def test_base_folder_without_parent_no_finding(self, vault):
        """A file in the type's base folder (not a subfolder) with no parent: is clean.

        Parents are optional for non-children; only subfolder placement implies ownership.
        """
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "orphan-base.md",
                 {"type": "living/wiki", "tags": ["wiki"], "key": "orphan-base"},
                 "# Orphan Base")
        findings = check.check_parent_contract(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "Wiki/orphan-base.md"]
        assert hits == []

    def test_unparented_release_flagged_by_required_field_check(self, vault):
        tmp_path, router = vault
        write_md(
            tmp_path / "Releases" / "orphan-release.md",
            {"type": "living/release", "tags": ["release"], "status": "active", "key": "orphan-release"},
            "# Orphan Release",
        )
        # parent_contract has nothing to resolve, so no folder-drift finding
        contract_findings = check.check_parent_contract(str(tmp_path), router)
        contract_hits = [f for f in contract_findings if f.get("file") == "Releases/orphan-release.md"]
        assert contract_hits == []
        # frontmatter_required flags the missing parent
        required_findings = check.check_frontmatter_required(str(tmp_path), router)
        required_hits = [f for f in required_findings if f.get("file") == "Releases/orphan-release.md"]
        assert any("parent" in (hit.get("message") or "") for hit in required_hits)

    def test_release_with_parent_uses_generic_folder_drift(self, vault):
        tmp_path, router = vault
        write_md(
            tmp_path / "Releases" / "wrong-parent.md",
            {
                "type": "living/release",
                "tags": ["release", "design/auth-redesign"],
                "status": "active",
                "key": "wrong-parent",
                "parent": "design/auth-redesign",
            },
            "# Wrong Parent",
        )
        findings = check.check_parent_contract(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "Releases/wrong-parent.md"]
        assert len(hits) == 1
        assert "Parent-folder drift" in hits[0]["message"]

    def test_temporal_child_parent_is_valid_without_folder_drift(self, vault):
        tmp_path, router = vault
        month_dir = tmp_path / "_Temporal" / "Logs" / "2026-03"
        month_dir.mkdir(parents=True, exist_ok=True)
        write_md(
            month_dir / "20260315-log.md",
            {
                "type": "temporal/log",
                "tags": ["log", "design/auth-redesign"],
                "parent": "design/auth-redesign",
                "created": "2026-03-15T09:00:00+10:00",
            },
            "# Log",
        )
        findings = check.check_parent_contract(str(tmp_path), router)
        assert not any(f.get("file") == "_Temporal/Logs/2026-03/20260315-log.md" for f in findings)

    def test_temporal_child_broken_parent_reference_flagged(self, vault):
        tmp_path, router = vault
        month_dir = tmp_path / "_Temporal" / "Logs" / "2026-03"
        month_dir.mkdir(parents=True, exist_ok=True)
        write_md(
            month_dir / "20260315-log.md",
            {
                "type": "temporal/log",
                "tags": ["log", "design/missing"],
                "parent": "design/missing",
                "created": "2026-03-15T09:00:00+10:00",
            },
            "# Log",
        )
        findings = check.check_parent_contract(str(tmp_path), router)
        hits = [f for f in findings if f.get("file") == "_Temporal/Logs/2026-03/20260315-log.md"]
        assert len(hits) == 1
        assert "Broken parent reference" in hits[0]["message"]

    def test_terminal_status_folder_drift_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Designs" / "implemented.md",
                 {"type": "living/design", "tags": ["design"], "status": "implemented", "key": "implemented"},
                 "# Implemented")
        findings = check.check_status_folders(str(tmp_path), router)
        assert any("Terminal-status drift" in f["message"] for f in findings)

    def test_non_terminal_in_status_folder_flagged(self, vault):
        tmp_path, router = vault
        status_dir = tmp_path / "Designs" / "+Implemented"
        status_dir.mkdir()
        write_md(status_dir / "still-shaping.md",
                 {"type": "living/design", "tags": ["design"], "status": "shaping", "key": "still-shaping"},
                 "# Still shaping")
        findings = check.check_status_folders(str(tmp_path), router)
        assert any("Non-terminal artefact stored in status folder" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# TestCheckArchiveMetadata
# ---------------------------------------------------------------------------

class TestCheckArchiveMetadata:
    def test_complete_archive_passes(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-old-design.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert len(findings) == 0

    def test_missing_archiveddate(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-old.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("archiveddate" in f["message"] for f in findings)

    def test_missing_prefix(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "old-design.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("prefix" in f["message"] for f in findings)

    def test_wrong_status(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-bad-status.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "shaping", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("terminal" in f["message"].lower() for f in findings)

    def test_no_terminal_statuses_skips_status_check(self, vault):
        tmp_path, router = vault
        # Wiki has no terminal_statuses — status sub-check should be skipped
        archive = tmp_path / "Wiki" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-old-page.md",
                 {"type": "living/wiki", "tags": ["test"],
                  "status": "whatever", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert not any("terminal" in f.get("message", "").lower() for f in findings)

    def test_project_subfolder_archive_passes(self, vault):
        """Archives in project subfolders are validated too."""
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "Brain" / "_Archive"
        archive.mkdir(parents=True)
        write_md(archive / "20260317-old-sub.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-03-17"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert len(findings) == 0

    def test_project_subfolder_archive_missing_archiveddate(self, vault):
        """Findings generated for bad metadata in project subfolder archives."""
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "Brain" / "_Archive"
        archive.mkdir(parents=True)
        write_md(archive / "20260317-bad.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("archiveddate" in f["message"] for f in findings)
        assert any("Brain" in f["file"] for f in findings)

    def test_top_level_archive_valid(self, vault):
        """check_archive_metadata finds files in top-level _Archive/ structure."""
        tmp_path, router = vault
        archive = tmp_path / "_Archive" / "Designs"
        archive.mkdir(parents=True)
        write_md(archive / "20260101-good.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-01-01"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        good_findings = [f for f in findings if "20260101-good" in f.get("file", "")]
        assert len(good_findings) == 0  # valid, no warnings

    def test_top_level_archive_missing_date(self, vault):
        """check_archive_metadata flags missing archiveddate in top-level _Archive/."""
        tmp_path, router = vault
        archive = tmp_path / "_Archive" / "Designs"
        archive.mkdir(parents=True)
        write_md(archive / "20260101-bad.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("archiveddate" in f["message"] for f in findings)

    def test_top_level_archive_project_subfolder(self, vault):
        """check_archive_metadata scans project subfolders within top-level _Archive/."""
        tmp_path, router = vault
        archive = tmp_path / "_Archive" / "Designs" / "Brain"
        archive.mkdir(parents=True)
        write_md(archive / "20260101-proj.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-01-01"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        proj_findings = [f for f in findings if "20260101-proj" in f.get("file", "")]
        assert len(proj_findings) == 0  # valid


# ---------------------------------------------------------------------------
# TestCheckStatusValues
# ---------------------------------------------------------------------------

class TestCheckStatusValues:
    def test_valid_status_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_status_values(str(tmp_path), router)
        design_findings = [f for f in findings if "auth-redesign" in f.get("file", "")]
        assert len(design_findings) == 0

    def test_invalid_status_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Designs" / "baz.md",
                 {"type": "living/design", "tags": ["design"], "status": "wip"})
        findings = check.check_status_values(str(tmp_path), router)
        bad = [f for f in findings if "baz.md" in f.get("file", "")]
        assert len(bad) == 1
        assert "wip" in bad[0]["message"]

    def test_null_enum_skips(self, vault):
        tmp_path, router = vault
        # Wiki has no status_enum — any status value should be fine
        write_md(tmp_path / "Wiki" / "with-status.md",
                 {"type": "living/wiki", "tags": ["test"], "status": "anything"})
        findings = check.check_status_values(str(tmp_path), router)
        assert not any("with-status" in f.get("file", "") for f in findings)

    def test_no_status_field_skips(self, vault):
        tmp_path, router = vault
        # File without status field in a type that has enum — should skip, not error
        write_md(tmp_path / "Designs" / "no-status.md",
                 {"type": "living/design", "tags": ["design"]})
        findings = check.check_status_values(str(tmp_path), router)
        assert not any("no-status" in f.get("file", "") for f in findings)

    def test_archive_files_excluded(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-archived.md",
                 {"type": "living/design", "tags": ["design"], "status": "implemented"})
        findings = check.check_status_values(str(tmp_path), router)
        assert not any("_Archive" in f.get("file", "") for f in findings)


# ---------------------------------------------------------------------------
# TestCheckUnconfiguredType
# ---------------------------------------------------------------------------

class TestCheckUnconfiguredType:
    def test_unconfigured_emits_info(self, vault):
        tmp_path, router = vault
        findings = check.check_unconfigured_type(str(tmp_path), router)
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"
        assert "Projects" in findings[0]["message"]

    def test_configured_does_not_emit(self, vault):
        tmp_path, router = vault
        findings = check.check_unconfigured_type(str(tmp_path), router)
        assert not any("Wiki" in f["message"] for f in findings)

