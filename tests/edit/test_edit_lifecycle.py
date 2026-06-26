"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import file_index_from_documents, parse_frontmatter, validate_artefact_folder


class TestEditTimestamps:
    FIXED_DT = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=11)))
    FIXED_ISO = "2026-04-02T10:00:00+11:00"

    def test_edit_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "New body\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_edit_does_not_change_created(self, vault, router):
        # Seed a stable ``created`` so this test exercises preservation rather
        # than reconciliation-on-absence (which is covered by reconcile tests).
        seed = (
            "---\ntype: living/wiki\ntags:\n  - brain-core\nstatus: active\n"
            "created: 2026-03-01T09:00:00+11:00\n---\n\n# Test Page\n\nOriginal body.\n"
        )
        (vault / "Wiki" / "test-page.md").write_text(seed)
        original_fields, _ = parse_frontmatter(seed)
        original_created = original_fields["created"]

        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "Changed body\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["created"] == original_created

    def test_append_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md", "\nAppended\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_prepend_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Prepended\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO


# ---------------------------------------------------------------------------
# Frontmatter merge tests
# ---------------------------------------------------------------------------

class TestFrontmatterMerge:
    def test_edit_overwrites_list_field(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing-1\n  - existing-2\n---\n\nBody.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Body.\n",
            frontmatter_changes={"tags": ["new"]},
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["new"]

    def test_append_extends_list_field(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing-1\n  - existing-2\n---\n\nBody.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"tags": ["new-tag"]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["existing-1", "existing-2", "new-tag"]

    def test_prepend_extends_list_field(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing-1\n  - existing-2\n---\n\nBody.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"tags": ["new-tag"]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["existing-1", "existing-2", "new-tag"]

    def test_append_deduplicates(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing\n---\n\nBody.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"tags": ["existing", "new"]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["existing", "new"]

    def test_append_overwrites_scalar(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "archived"

    def test_append_frontmatter_only(self, vault, router):
        """Empty body + frontmatter changes = frontmatter-only mutation."""
        original = (vault / "Wiki" / "test-page.md").read_text()
        _, original_body = parse_frontmatter(original)

        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == original_body  # body unchanged

    def test_prepend_frontmatter_only(self, vault, router):
        """Empty body + frontmatter changes = frontmatter-only mutation."""
        original = (vault / "Wiki" / "test-page.md").read_text()
        _, original_body = parse_frontmatter(original)

        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == original_body  # body unchanged

    def test_targeted_append_frontmatter_only_omits_structural_target(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
            target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == "## Alpha\n\nBody.\n"
        assert "structural_target" not in result

    def test_targeted_prepend_frontmatter_only_omits_structural_target(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
            target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == "## Alpha\n\nBody.\n"
        assert "structural_target" not in result


# ---------------------------------------------------------------------------
# Ownership path regression tests
# ---------------------------------------------------------------------------

class TestOwnershipEditPaths:
    def test_temporal_parent_edit_persists_without_rehoming(self, vault, router):
        month = vault / "_Temporal" / "Research" / "2026-04"
        month.mkdir(parents=True, exist_ok=True)
        path = month / "20260413-research~Sample Title.md"
        path.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Body.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "_Temporal/Research/2026-04/20260413-research~Sample Title.md",
            "",
            frontmatter_changes={"parent": "project/brain"},
        )

        assert result["path"] == "_Temporal/Research/2026-04/20260413-research~Sample Title.md"
        fields, _ = parse_frontmatter(path.read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]

    def test_parent_key_change_rehomes_children_using_canonical_owner_folder(self, vault, router):
        child_dir = vault / "Ideas" / "project~brain"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "child-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: child-idea\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Child Idea\n\nBody.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        assert result["path"] == "Projects/Brain.md"
        relocated = vault / "Ideas" / "project~brain2" / "child-idea.md"
        assert relocated.is_file()
        assert not (vault / "Ideas" / "project" / "brain2" / "child-idea.md").exists()
        fields, _ = parse_frontmatter(relocated.read_text())
        assert fields["parent"] == "project/brain2"
        assert "project/brain2" in fields["tags"]

    def test_parent_key_change_updates_temporal_children_without_rehoming(self, vault, router):
        month = vault / "_Temporal" / "Research" / "2026-04"
        month.mkdir(parents=True, exist_ok=True)
        path = month / "20260413-research~Sample Title.md"
        path.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "  - project/brain\n"
            "parent: project/brain\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Body.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        assert result["path"] == "Projects/Brain.md"
        assert path.is_file()
        fields, _ = parse_frontmatter(path.read_text())
        assert fields["parent"] == "project/brain2"
        assert "project/brain2" in fields["tags"]
        assert "project/brain" not in fields["tags"]

    def test_parent_edit_keeps_existing_terminal_status_folder(self, vault, router):
        (vault / "Projects" / "Custom.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/custom\n"
            "key: custom\n"
            "---\n\n"
            "# Custom\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        adopted_dir = vault / "Ideas" / "project~brain" / "+Adopted"
        adopted_dir.mkdir(parents=True, exist_ok=True)
        (adopted_dir / "adopted-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: adopted-idea\n"
            "parent: project/brain\n"
            "status: adopted\n"
            "---\n\n"
            "# Adopted Idea\n\nBody.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "Ideas/project~brain/+Adopted/adopted-idea.md",
            "",
            frontmatter_changes={"parent": "project/custom"},
        )

        assert result["path"] == "Ideas/project~custom/+Adopted/adopted-idea.md"
        relocated = vault / "Ideas" / "project~custom" / "+Adopted" / "adopted-idea.md"
        assert relocated.is_file()
        assert not (vault / "Ideas" / "project~custom" / "adopted-idea.md").exists()
        fields, _ = parse_frontmatter(relocated.read_text())
        assert fields["parent"] == "project/custom"


# ---------------------------------------------------------------------------
# Terminal status auto-move tests
# ---------------------------------------------------------------------------

class TestTerminalStatusMove:
    """Tests for automatic file movement on terminal status changes."""

    def _make_idea(self, vault, path, status="seed", body="# Idea\n\nBody.\n"):
        """Helper to create an idea file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            f"---\ntype: living/ideas\ntags: []\nstatus: {status}\n---\n\n{body}"
        )

    def _make_release(
        self,
        vault,
        path,
        status="active",
        version="v0.28.6",
        body=(
            "## Goal\n\nShip it.\n\n"
            "## Acceptance Criteria\n\n| Criterion | Status |\n|---|---|\n| Ship it | pending |\n\n"
            "## Designs In Scope\n\n- [[Brain Master Design]]\n\n"
            "## Release Notes\n\n"
            "## Sources\n\n- [[Brain Master Design]]\n"
        ),
    ):
        """Helper to create a release file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\n"
            "type: living/release\n"
            "tags:\n"
            "  - release\n"
            "  - project/brain\n"
            "parent: project/brain\n"
            f"status: {status}\n"
            f"version: {version}\n"
            "tag:\n"
            "commit:\n"
            "shipped:\n"
            "---\n\n"
            f"{body}"
        )

    def test_edit_terminal_status_moves_to_plus_folder(self, vault, router):
        self._make_idea(vault, "Ideas/my-idea.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/my-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/+Adopted/my-idea.md"
        assert (vault / "Ideas" / "+Adopted" / "my-idea.md").is_file()
        assert not (vault / "Ideas" / "my-idea.md").exists()

    def test_edit_terminal_status_creates_folder(self, vault, router):
        self._make_idea(vault, "Ideas/new-idea.md")
        assert not (vault / "Ideas" / "+Adopted").exists()
        edit.edit_artefact(
            str(vault), router, "Ideas/new-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert (vault / "Ideas" / "+Adopted").is_dir()
        assert (vault / "Ideas" / "+Adopted" / "new-idea.md").is_file()

    def test_edit_terminal_status_updates_wikilinks(self, vault, router):
        self._make_idea(vault, "Ideas/linked-idea.md")
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Ideas/linked-idea]].\n"
        )
        edit.edit_artefact(
            str(vault), router, "Ideas/linked-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[Ideas/+Adopted/linked-idea]]" in content
        assert "[[Ideas/linked-idea]]" not in content

    def test_edit_non_terminal_status_no_move(self, vault, router):
        self._make_idea(vault, "Ideas/staying.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/staying.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/staying.md"
        assert (vault / "Ideas" / "staying.md").is_file()

    def test_edit_already_in_plus_folder_no_move(self, vault, router):
        self._make_idea(vault, "Ideas/+Adopted/already.md", status="adopted")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/already.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/+Adopted/already.md"
        assert (vault / "Ideas" / "+Adopted" / "already.md").is_file()

    def test_edit_no_status_change_no_move(self, vault, router):
        self._make_idea(vault, "Ideas/body-only.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/body-only.md", "# Updated\n\nNew body.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "Ideas/body-only.md"
        assert (vault / "Ideas" / "body-only.md").is_file()

    def test_append_terminal_status_moves(self, vault, router):
        self._make_idea(vault, "Ideas/append-idea.md")
        result = edit.append_to_artefact(
            str(vault), router, "Ideas/append-idea.md", "\nExtra.\n",
            target=":body", scope="section",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/+Adopted/append-idea.md"
        assert (vault / "Ideas" / "+Adopted" / "append-idea.md").is_file()

    def test_edit_terminal_status_with_subfolder(self, vault, router):
        self._make_idea(vault, "Ideas/Brain/project-idea.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/Brain/project-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/Brain/+Adopted/project-idea.md"
        assert (vault / "Ideas" / "Brain" / "+Adopted" / "project-idea.md").is_file()
        assert not (vault / "Ideas" / "Brain" / "project-idea.md").exists()

    def test_release_shipped_moves_to_project_status_folder(self, vault, router):
        # Pre-ship releases use title-led filenames; shipping renames to version-led.
        self._make_release(vault, "Releases/project~brain/Search Hardening.md", version="v0.28.6")
        result = edit.edit_artefact(
            str(vault),
            router,
            "Releases/project~brain/Search Hardening.md",
            "",
            frontmatter_changes={"status": "shipped", "shipped": "2026-04-16"},
        )
        assert result["path"] == "Releases/project~brain/+Shipped/v0.28.6 - Search Hardening.md"
        assert (vault / "Releases" / "project~brain" / "+Shipped" / "v0.28.6 - Search Hardening.md").is_file()

    def test_release_cancelled_moves_to_project_status_folder(self, vault, router):
        # Cancelled releases stay title-led — no version in the filename.
        self._make_release(vault, "Releases/project~brain/Experimental Cut.md")
        result = edit.edit_artefact(
            str(vault),
            router,
            "Releases/project~brain/Experimental Cut.md",
            "",
            frontmatter_changes={"status": "cancelled"},
        )
        assert result["path"] == "Releases/project~brain/+Cancelled/Experimental Cut.md"
        assert (vault / "Releases" / "project~brain" / "+Cancelled" / "Experimental Cut.md").is_file()

    def test_edit_no_terminal_defined(self, vault, router):
        """Type with no terminal_statuses doesn't move on status change."""
        result = edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": "archived"},
        )
        assert result["path"] == "Wiki/test-page.md"
        assert (vault / "Wiki" / "test-page.md").is_file()

    def test_edit_revive_from_terminal(self, vault, router):
        """Non-terminal status on file in +Status/ folder moves it back out."""
        self._make_idea(vault, "Ideas/+Adopted/revived.md", status="adopted")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/revived.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/revived.md"
        assert (vault / "Ideas" / "revived.md").is_file()
        assert not (vault / "Ideas" / "+Adopted" / "revived.md").exists()

    def test_edit_revive_from_subfolder(self, vault, router):
        """Revive from project subfolder +Status/ moves up one level."""
        self._make_idea(vault, "Ideas/Brain/+Adopted/sub-revive.md", status="adopted")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/Brain/+Adopted/sub-revive.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/Brain/sub-revive.md"
        assert (vault / "Ideas" / "Brain" / "sub-revive.md").is_file()
        assert not (vault / "Ideas" / "Brain" / "+Adopted" / "sub-revive.md").exists()

    def test_edit_revive_cleans_empty_folder(self, vault, router):
        """Reviving last file from +Adopted/ removes the empty folder."""
        self._make_idea(vault, "Ideas/+Adopted/last-one.md", status="adopted")
        edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/last-one.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert not (vault / "Ideas" / "+Adopted").exists()

    def test_edit_revive_keeps_nonempty_folder(self, vault, router):
        """Reviving one file from +Adopted/ when others remain keeps the folder."""
        self._make_idea(vault, "Ideas/+Adopted/leaving.md", status="adopted")
        self._make_idea(vault, "Ideas/+Adopted/staying.md", status="adopted")
        edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/leaving.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert (vault / "Ideas" / "+Adopted").is_dir()
        assert (vault / "Ideas" / "+Adopted" / "staying.md").is_file()
        assert (vault / "Ideas" / "leaving.md").is_file()

    def test_edit_terminal_to_different_terminal_no_nesting(self, vault, router):
        """Changing terminal status on a file already in +Status/ moves to sibling folder, not nested."""
        # Designs have multiple terminal statuses: implemented, superseded, rejected
        abs_path = vault / "Designs" / "+Implemented" / "my-design.md"
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\ntype: living/design\ntags: [design]\nstatus: implemented\n---\n\n# Design\n"
        )
        result = edit.edit_artefact(
            str(vault), router, "Designs/+Implemented/my-design.md", "",
            frontmatter_changes={"status": "superseded"},
        )
        assert result["path"] == "Designs/+Superseded/my-design.md"
        assert (vault / "Designs" / "+Superseded" / "my-design.md").is_file()
        assert not (vault / "Designs" / "+Implemented" / "+Superseded" / "my-design.md").exists()
        assert not (vault / "Designs" / "+Implemented" / "my-design.md").exists()


# ---------------------------------------------------------------------------
# Statusdate auto-set tests
# ---------------------------------------------------------------------------

class TestStatusDate:
    """Tests that statusdate is auto-set on status transitions."""

    def _make_idea(self, vault, path, status="seed", body="# Idea\n\nBody.\n",
                   extra_fm=""):
        """Helper to create an idea file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            f"---\ntype: living/ideas\ntags: []\nstatus: {status}\n{extra_fm}---\n\n{body}"
        )

    def _read_fields(self, vault, path):
        """Read back frontmatter fields from a file."""
        content = (vault / path).read_text()
        fields, _ = parse_frontmatter(content)
        return fields

    def test_status_change_sets_statusdate(self, vault, router):
        """Changing status from seed to shaping sets statusdate."""
        self._make_idea(vault, "Ideas/sd-idea.md", status="seed")
        edit.edit_artefact(
            str(vault), router, "Ideas/sd-idea.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/sd-idea.md")
        assert "statusdate" in fields
        assert len(fields["statusdate"]) == 10  # YYYY-MM-DD

    def test_no_status_change_no_statusdate(self, vault, router):
        """Body-only edit does not add statusdate."""
        self._make_idea(vault, "Ideas/no-sd.md", status="seed")
        edit.edit_artefact(
            str(vault), router, "Ideas/no-sd.md", "Updated body.",
            target=":body", scope="section",
        )
        fields = self._read_fields(vault, "Ideas/no-sd.md")
        assert "statusdate" not in fields

    def test_same_status_no_update(self, vault, router):
        """Setting status to its current value preserves existing statusdate."""
        self._make_idea(vault, "Ideas/same-sd.md", status="shaping",
                        extra_fm="statusdate: '2020-01-01'\n")
        edit.edit_artefact(
            str(vault), router, "Ideas/same-sd.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/same-sd.md")
        assert fields["statusdate"] == "2020-01-01"

    def test_terminal_status_sets_statusdate(self, vault, router):
        """Adopting an idea sets statusdate on the moved file."""
        self._make_idea(vault, "Ideas/term-sd.md", status="seed")
        edit.edit_artefact(
            str(vault), router, "Ideas/term-sd.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        fields = self._read_fields(vault, "Ideas/+Adopted/term-sd.md")
        assert "statusdate" in fields
        assert len(fields["statusdate"]) == 10

    def test_revive_updates_statusdate(self, vault, router):
        """Reviving from +Adopted/ updates statusdate."""
        self._make_idea(vault, "Ideas/+Adopted/revive-sd.md", status="adopted",
                        extra_fm="statusdate: '2020-01-01'\n")
        edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/revive-sd.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/revive-sd.md")
        assert "statusdate" in fields
        assert fields["statusdate"] != "2020-01-01"

    def test_append_status_change_sets_statusdate(self, vault, router):
        """Append operation with status change sets statusdate."""
        self._make_idea(vault, "Ideas/app-sd.md", status="seed")
        edit.append_to_artefact(
            str(vault), router, "Ideas/app-sd.md", "Extra content.",
            target=":body", scope="section",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/app-sd.md")
        assert "statusdate" in fields
        assert len(fields["statusdate"]) == 10


class TestArchiveGuards:
    """Tests that _Archive/ files are immune to auto-move and convert."""

    def _make_archived_idea(self, vault, path="Ideas/_Archive/20260101-old-idea.md"):
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )

    def test_status_move_skipped_for_archived_file(self, vault, router):
        """Terminal status change on archived file does NOT create +Status/ inside _Archive/."""
        self._make_archived_idea(vault)
        result = edit.edit_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        assert not (vault / "Ideas" / "_Archive" / "+Adopted").exists()

    def test_nonterminal_status_on_archived_stays(self, vault, router):
        """Non-terminal status on archived file stays in _Archive/, frontmatter updated."""
        self._make_archived_idea(vault)
        result = edit.edit_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        content = (vault / "Ideas" / "_Archive" / "20260101-old-idea.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "shaping"

    def test_edit_archived_file_body_succeeds(self, vault, router):
        """Body edits on archived files work normally."""
        self._make_archived_idea(vault)
        result = edit.edit_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md",
            "# Updated\n\nFixed body.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        content = (vault / "Ideas" / "_Archive" / "20260101-old-idea.md").read_text()
        assert "Fixed body." in content

    def test_append_archived_skips_status_move(self, vault, router):
        """Append with terminal status on archived file doesn't move."""
        self._make_archived_idea(vault)
        result = edit.append_to_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "\nExtra.\n",
            target=":body", scope="section",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        assert not (vault / "Ideas" / "_Archive" / "+Adopted").exists()

    def test_convert_archived_file_raises(self, vault, router):
        """Converting an archived file raises ValueError."""
        self._make_archived_idea(vault)
        with pytest.raises(ValueError, match="Cannot convert archived file"):
            edit.convert_artefact(
                str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "designs"
            )


class TestArchiveArtefact:
    """Tests for brain_move(op='archive') — archive_artefact()."""

    def _make_idea(self, vault, name="my-idea.md", status="adopted", project=None):
        if project:
            folder = vault / "Ideas" / project
        else:
            folder = vault / "Ideas"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(
            f"---\ntype: living/ideas\ntags: []\nstatus: {status}\n---\n\nIdea body.\n"
        )
        if project:
            return f"Ideas/{project}/{name}"
        return f"Ideas/{name}"

    def test_archive_moves_to_top_level(self, vault, router):
        rel = self._make_idea(vault)
        result = edit.archive_artefact(str(vault), router, rel)
        assert result["new_path"].startswith("_Archive/Ideas/")
        assert not (vault / rel).exists()
        assert (vault / result["new_path"]).exists()

    def test_archive_adds_date_prefix(self, vault, router):
        rel = self._make_idea(vault)
        result = edit.archive_artefact(str(vault), router, rel)
        filename = os.path.basename(result["new_path"])
        assert filename[8] == "-"  # yyyymmdd-
        assert filename[9:] == "my-idea.md"

    def test_archive_adds_archiveddate(self, vault, router):
        rel = self._make_idea(vault)
        result = edit.archive_artefact(str(vault), router, rel)
        content = (vault / result["new_path"]).read_text()
        fields, _ = parse_frontmatter(content)
        assert "archiveddate" in fields

    def test_archive_preserves_project_structure(self, vault, router):
        rel = self._make_idea(vault, project="Brain")
        result = edit.archive_artefact(str(vault), router, rel)
        assert "_Archive/Ideas/Brain/" in result["new_path"]

    def test_archive_refuses_non_terminal_status(self, vault, router):
        rel = self._make_idea(vault, status="shaping")
        with pytest.raises(ValueError, match="not terminal"):
            edit.archive_artefact(str(vault), router, rel)

    def test_archive_refuses_already_archived(self, vault, router):
        archive = vault / "Ideas" / "_Archive"
        archive.mkdir(parents=True)
        (archive / "20260101-old.md").write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld.\n"
        )
        with pytest.raises(ValueError, match="already archived"):
            edit.archive_artefact(str(vault), router, "Ideas/_Archive/20260101-old.md")

    def test_archive_strips_status_folder(self, vault, router):
        """Archiving from +Adopted/ should not include +Adopted in archive path."""
        status_dir = vault / "Ideas" / "+Adopted"
        status_dir.mkdir(parents=True)
        (status_dir / "my-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n---\n\nBody.\n"
        )
        result = edit.archive_artefact(str(vault), router, "Ideas/+Adopted/my-idea.md")
        assert "+Adopted" not in result["new_path"]
        assert result["new_path"].startswith("_Archive/Ideas/")

    def test_archive_updates_wikilinks(self, vault, router):
        rel = self._make_idea(vault)
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[my-idea]].\n"
        )
        result = edit.archive_artefact(str(vault), router, rel)
        content = (vault / "Wiki" / "linker.md").read_text()
        new_stem = os.path.splitext(os.path.basename(result["new_path"]))[0]
        assert new_stem in content


class TestUnarchiveArtefact:
    """Tests for brain_move(op='unarchive') — unarchive_artefact()."""

    def _make_archived(self, vault, rel="_Archive/Ideas/20260101-my-idea.md"):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_unarchive_moves_to_type_folder(self, vault, router):
        rel = self._make_archived(vault)
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert result["new_path"] == "Ideas/my-idea.md"
        assert not (vault / rel).exists()
        assert (vault / result["new_path"]).exists()

    def test_unarchive_strips_date_prefix(self, vault, router):
        rel = self._make_archived(vault)
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert "20260101-" not in result["new_path"]

    def test_unarchive_removes_archiveddate(self, vault, router):
        rel = self._make_archived(vault)
        result = edit.unarchive_artefact(str(vault), router, rel)
        content = (vault / result["new_path"]).read_text()
        fields, _ = parse_frontmatter(content)
        assert "archiveddate" not in fields

    def test_unarchive_preserves_project_structure(self, vault, router):
        rel = self._make_archived(vault, "_Archive/Ideas/Brain/20260101-my-idea.md")
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert result["new_path"] == "Ideas/Brain/my-idea.md"

    def test_unarchive_refuses_non_archived(self, vault, router):
        (vault / "Ideas" / "live-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: shaping\n---\n\nLive.\n"
        )
        with pytest.raises(ValueError, match="not in _Archive"):
            edit.unarchive_artefact(str(vault), router, "Ideas/live-idea.md")

    def test_unarchive_updates_wikilinks(self, vault, router):
        rel = self._make_archived(vault)
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[20260101-my-idea]].\n"
        )
        result = edit.unarchive_artefact(str(vault), router, rel)
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "my-idea" in content
