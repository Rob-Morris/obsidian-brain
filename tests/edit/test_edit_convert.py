"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import file_index_from_documents, parse_frontmatter, validate_artefact_folder


class TestConvertArtefact:
    def test_convert_between_living_types(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        assert result["type"] == "living/designs"
        assert result["new_path"].startswith("Designs/")
        # Old file removed
        assert not (vault / "Wiki" / "test-page.md").exists()
        # New file exists
        assert os.path.isfile(os.path.join(str(vault), result["new_path"]))

    def test_convert_updates_frontmatter_type(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        abs_new = os.path.join(str(vault), result["new_path"])
        with open(abs_new) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/designs"

    def test_convert_generates_distinctive_key_without_suffix(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["key"] == "test-page"

    def test_convert_adds_random_suffix_only_after_keyword_collisions(self, vault, router):
        (vault / "Designs" / "Existing Pair.md").write_text(
            "---\ntype: living/designs\ntags: []\nkey: test-page\nstatus: shaping\n---\n\n# Existing\n"
        )
        (vault / "Designs" / "Existing Single.md").write_text(
            "---\ntype: living/designs\ntags: []\nkey: test\nstatus: shaping\n---\n\n# Existing\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert re.fullmatch(r"test-page-[a-z2-9]{3}", fields["key"])

    def test_convert_preserves_body(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        abs_new = os.path.join(str(vault), result["new_path"])
        with open(abs_new) as f:
            content = f.read()
        assert "Original body." in content

    def test_convert_updates_wikilinks(self, vault, router):
        # Create a file that links to the source
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/test-page]].\n"
        )
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        content = (vault / "Wiki" / "linker.md").read_text()
        new_stem = result["new_path"][:-3]  # strip .md
        assert f"[[{new_stem}]]" in content
        assert "[[Wiki/test-page]]" not in content

    def test_convert_unknown_target_error(self, vault, router):
        with pytest.raises(ValueError, match="Unknown artefact type"):
            edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "nonexistent")

    def test_convert_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.convert_artefact(str(vault), router, "Wiki/gone.md", "designs")

    def test_convert_preserves_parent_subfolder(self, vault, router):
        """Canonical parent ownership survives convert across living types."""
        hub_dir = vault / "Ideas" / "project~brain"
        hub_dir.mkdir(parents=True)
        (hub_dir / "my-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: my-idea\n"
            "parent: project/brain\n"
            "---\n\n"
            "# My Idea\n\nBody.\n"
        )
        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/my-idea.md", "designs"
        )
        assert result["new_path"].startswith("Designs/project~brain/")
        assert not (hub_dir / "my-idea.md").exists()
        assert os.path.isfile(os.path.join(str(vault), result["new_path"]))
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/brain"
        assert fields["key"] == "my-idea"

    def test_convert_flat_source_no_parent(self, vault, router):
        """Source directly in Ideas/ (no subfolder) should produce Designs/ with no subfolder."""
        (vault / "Ideas" / "flat-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nkey: flat-idea\n---\n\n# Flat Idea\n\nBody.\n"
        )
        result = edit.convert_artefact(str(vault), router, "Ideas/flat-idea.md", "designs")
        assert result["new_path"].startswith("Designs/")
        assert "/" not in result["new_path"][len("Designs/"):]
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["key"] == "flat-idea"
        assert "parent" not in fields

    def test_convert_explicit_parent_override(self, vault, router):
        """Explicit canonical parent takes precedence over the source parent."""
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
        hub_dir = vault / "Ideas" / "project~brain"
        hub_dir.mkdir(parents=True)
        (hub_dir / "overridden.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: overridden\n"
            "parent: project/brain\n"
            "---\n\n"
            "# Overridden\n\nBody.\n"
        )
        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/overridden.md", "designs", parent="project/custom"
        )
        assert result["new_path"].startswith("Designs/project~custom/")
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/custom"

    def test_convert_rejects_target_type_key_collision(self, vault, router):
        (vault / "Ideas" / "collision.md").write_text(
            "---\ntype: living/ideas\ntags: []\nkey: shared\n---\n\n# Shared Idea\n"
        )
        (vault / "Designs" / "shared.md").write_text(
            "---\ntype: living/designs\ntags: []\nkey: shared\nstatus: shaping\n---\n\n# Existing Design\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        with pytest.raises(ValueError, match="KEY_TAKEN"):
            edit.convert_artefact(str(vault), router, "Ideas/collision.md", "designs")

    def test_convert_living_child_to_temporal_preserves_parent_metadata(self, vault, router):
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

        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/child-idea.md", "reports"
        )

        assert result["new_path"].startswith("_Temporal/Reports/")
        assert "project~brain" not in result["new_path"]
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]

    def test_convert_owner_to_temporal_removes_child_owner_reference_cleanly(self, vault, router):
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
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.convert_artefact(
            str(vault), router, "Projects/Brain.md", "reports"
        )

        assert result["new_path"].startswith("_Temporal/Reports/")
        relocated = vault / "Ideas" / "child-idea.md"
        assert relocated.is_file()
        fields, _ = parse_frontmatter(relocated.read_text())
        assert "parent" not in fields
        assert fields["tags"] == []

    def test_convert_owner_to_temporal_keeps_tag_only_reference_in_place(self, vault, router):
        (vault / "Wiki" / "tagged.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - note\n"
            "  - project/brain\n"
            "key: tagged\n"
            "---\n\n"
            "# Tagged\n\nBody.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        edit.convert_artefact(str(vault), router, "Projects/Brain.md", "reports")

        tagged = vault / "Wiki" / "tagged.md"
        assert tagged.is_file()
        fields, _ = parse_frontmatter(tagged.read_text())
        assert "parent" not in fields
        assert fields["tags"] == ["note"]

    def test_convert_temporal_to_temporal_strips_old_prefix(self, vault, router):
        """Converting research → reports must not nest the old prefix in the new filename.

        Regression test for the bug where the filename stem (including the old
        type's naming prefix) was used as the title when no title frontmatter was
        present, producing '20260413-report~20260413-research~Foo.md'.
        """
        import re
        research_dir = vault / "_Temporal" / "Research" / "2026-04"
        research_dir.mkdir(parents=True, exist_ok=True)
        src_name = "20260413-research~Sample Title.md"
        (research_dir / src_name).write_text(
            "---\ntype: temporal/research\ntags:\n  - research\n---\n\nBody.\n"
        )

        result = edit.convert_artefact(
            str(vault), router,
            f"_Temporal/Research/2026-04/{src_name}",
            "reports",
        )

        new_basename = os.path.basename(result["new_path"])
        assert "research~" not in new_basename, (
            f"Old prefix leaked into new filename: {new_basename}"
        )
        assert re.match(r"^\d{8}-report~Sample Title\.md$", new_basename), (
            f"Unexpected filename shape: {new_basename}"
        )

    def test_convert_collision_uses_standard_suffix_in_target_folder(self, vault, router):
        # Same created date → both converts target the same report folder and filename.
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "20260301-log-foo-a.md").write_text(
            "---\n"
            "type: temporal/logs\n"
            "title: Foo\n"
            "tags:\n"
            "  - report-source\n"
            "---\n\n"
            "First body.\n"
        )
        (month / "20260301-log-foo-b.md").write_text(
            "---\n"
            "type: temporal/logs\n"
            "title: Foo\n"
            "tags:\n"
            "  - report-source\n"
            "---\n\n"
            "Second body.\n"
        )

        first = edit.convert_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/20260301-log-foo-a.md", "reports"
        )
        second = edit.convert_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/20260301-log-foo-b.md", "reports"
        )

        assert first["new_path"] != second["new_path"]
        assert re.search(r" [a-z0-9]{3}\.md$", second["new_path"])
        assert "First body." in (vault / first["new_path"]).read_text()
        assert "Second body." in (vault / second["new_path"]).read_text()

