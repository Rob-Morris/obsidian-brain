"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import file_index_from_documents, parse_frontmatter, validate_artefact_folder


class TestDeleteSection:
    def test_delete_middle_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Beta")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "Alpha content." in body
        assert "## Beta" not in body
        assert "Beta content." not in body
        assert "## Gamma" in body
        assert "Gamma content." in body
        assert "\n\n\n" not in body  # no double-blank

    def test_delete_first_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Alpha")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" not in body
        assert "Alpha content." not in body
        assert "## Beta" in body
        assert "Beta content." in body
        # Body must start directly with the remaining heading (no orphaned blank lines above it)
        assert body.startswith("## Beta\n")

    def test_delete_last_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Beta")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "Alpha content." in body
        assert "## Beta" not in body
        assert "Beta content." not in body

    def test_delete_only_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Only\n\nOnly content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Only")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Only" not in body
        assert "Only content." not in body

    def test_delete_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.delete_section_artefact(
                str(vault), router, "Wiki/test-page.md", target="Nonexistent"
            )

    def test_delete_section_target_body_rejected(self, vault, router):
        with pytest.raises(ValueError, match="delete_section requires a heading or callout target"):
            edit.delete_section_artefact(
                str(vault), router, "Wiki/test-page.md", target=":body"
            )

    def test_delete_section_with_level_qualifier(self, vault, router):
        """Level-qualified target (e.g. '## Beta') matches exactly."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="## Beta")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Beta" not in body
        assert "## Alpha" in body

    def test_delete_section_updates_modified(self, vault, router):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        fixed = datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        expected_iso = fixed.astimezone().isoformat()
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n## Alpha\n\nContent.\n"
        )
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Alpha")
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == expected_iso

    def test_delete_callout_section(self, vault, router):
        """delete_section works with callout targets."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Context\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.delete_section_artefact(
            str(vault), router, "Wiki/test-page.md",
            target="[!note] Implementation status",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "[!note] Implementation status" not in body
        assert "Old status content." not in body
        assert "## Context" in body
        assert "After callout." in body

    def test_delete_section_selector_disambiguates_duplicate_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        edit.delete_section_artefact(
            str(vault), router, "Wiki/test-page.md",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "First." in body
        assert "Second." not in body
        assert body.count("## Notes") == 1

    def test_delete_section_ambiguity_errors(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        with pytest.raises(ValueError, match="Ambiguous target '## Notes'"):
            edit.delete_section_artefact(
                str(vault), router, "Wiki/test-page.md", target="## Notes"
            )


# ---------------------------------------------------------------------------
# Resource editing (Phase 5)
# ---------------------------------------------------------------------------

class TestEditResource:
    """Tests for edit_resource() — the resource-aware dispatcher."""

    def _create_skill(self, vault):
        """Helper: create a skill file for editing tests."""
        skill_dir = vault / "_Config" / "Skills" / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        path.write_text(
            "---\ndescription: A test skill\n---\n\n"
            "# Test Skill\n\nOriginal skill body.\n"
        )
        return "_Config/Skills/test-skill/SKILL.md"

    def _create_memory(self, vault):
        """Helper: create a memory file for editing tests."""
        mem_dir = vault / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        path = mem_dir / "test-memory.md"
        path.write_text(
            "---\ntriggers:\n  - keyword1\n---\n\n"
            "# Test Memory\n\nOriginal memory body.\n"
        )
        return "_Config/Memories/test-memory.md"

    def _create_style(self, vault):
        """Helper: create a style file for editing tests."""
        style_dir = vault / "_Config" / "Styles"
        style_dir.mkdir(parents=True, exist_ok=True)
        path = style_dir / "test-style.md"
        path.write_text(
            "---\naudience: technical\n---\n\n"
            "# Test Style\n\nOriginal style body.\n"
        )
        return "_Config/Styles/test-style.md"

    def test_artefact_delegates_to_edit_artefact(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact",
            operation="edit", path="Wiki/test-page.md",
            body="# Replaced\n",
            target=":body", scope="section",
        )
        assert result["operation"] == "edit"
        assert "Wiki/test-page.md" in result["path"]

    def test_edit_skill_body(self, vault, router):
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            body="# Updated Skill\n\nNew skill content.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Skills/test-skill/SKILL.md"
        assert result["operation"] == "edit"
        content = (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").read_text()
        assert "New skill content." in content
        assert "Original skill body." not in content

    def test_edit_memory_target_entire_body(self, vault, router):
        self._create_memory(vault)
        result = edit.edit_resource(
            str(vault), router, resource="memory",
            operation="edit", name="test-memory",
            body="Replacement memory body.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Memories/test-memory.md"
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        assert "Replacement memory body." in content
        assert "Original memory body." not in content

    def test_append_to_memory(self, vault, router):
        self._create_memory(vault)
        result = edit.edit_resource(
            str(vault), router, resource="memory",
            operation="append", name="test-memory",
            body="\n## New Section\n\nAppended content.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Memories/test-memory.md"
        assert result["operation"] == "append"
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        assert "Original memory body." in content
        assert "Appended content." in content

    def test_memory_target_body_requires_scope(self, vault, router):
        self._create_memory(vault)
        with pytest.raises(ValueError, match="requires scope"):
            edit.edit_resource(
                str(vault), router, resource="memory",
                operation="append", name="test-memory",
                body="Extra.\n",
                target=":body",
            )

    def test_prepend_to_style(self, vault, router):
        self._create_style(vault)
        result = edit.edit_resource(
            str(vault), router, resource="style",
            operation="prepend", name="test-style",
            body="> Important note\n\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Styles/test-style.md"
        assert result["operation"] == "prepend"
        content = (vault / "_Config" / "Styles" / "test-style.md").read_text()
        assert content.index("Important note") < content.index("Original style body.")

    def test_edit_skill_frontmatter(self, vault, router):
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            frontmatter_changes={"description": "Updated description"},
        )
        content = (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["description"] == "Updated description"
        assert "Original skill body." in body

    def test_edit_memory_triggers(self, vault, router):
        self._create_memory(vault)
        edit.edit_resource(
            str(vault), router, resource="memory",
            operation="append", name="test-memory",
            frontmatter_changes={"triggers": ["keyword2"]},
        )
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert "keyword1" in fields["triggers"]
        assert "keyword2" in fields["triggers"]

    def test_delete_section_from_skill(self, vault, router):
        skill_dir = vault / "_Config" / "Skills" / "section-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n---\n\n# Skill\n\nIntro.\n\n## Remove Me\n\nGone.\n\n## Keep\n\nStays.\n"
        )
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="delete_section", name="section-skill",
            target="## Remove Me",
        )
        assert result["operation"] == "delete_section"
        content = (skill_dir / "SKILL.md").read_text()
        assert "Remove Me" not in content
        assert "Stays." in content

    def test_not_editable_resource(self, vault, router):
        with pytest.raises(ValueError, match="not editable"):
            edit.edit_resource(
                str(vault), router, resource="workspace",
                operation="edit", name="ws", body="content",
            )

    def test_name_required_for_non_artefact(self, vault, router):
        with pytest.raises(ValueError, match="name"):
            edit.edit_resource(
                str(vault), router, resource="skill",
                operation="edit", body="content",
            )

    def test_resource_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError, match="not found"):
            edit.edit_resource(
                str(vault), router, resource="skill",
                operation="edit", name="nonexistent",
                body="content",
            )

    def test_no_status_move_for_config_resources(self, vault, router):
        """Config resources should not auto-move on status changes."""
        self._create_skill(vault)
        edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            frontmatter_changes={"status": "implemented"},
        )
        # File should stay in place, not moved to +Implemented/
        assert (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").is_file()

    def test_edit_template(self, vault, router):
        """Templates are editable via artefact type key as name."""
        result = edit.edit_resource(
            str(vault), router, resource="template",
            operation="edit", name="wiki",
            body="# Updated Template\n\nNew template content.\n",
            target=":body", scope="section",
        )
        assert "_Config/Templates/" in result["path"]
        assert result["operation"] == "edit"

    def test_edit_resource_rejects_body_with_frontmatter_block(self, vault, router):
        self._create_skill(vault)
        with pytest.raises(ValueError, match="must not start with a frontmatter block"):
            edit.edit_resource(
                str(vault), router, resource="skill",
                operation="edit", name="test-skill",
                body="---\ndescription: updated\n---\n\n# Skill\n",
                target=":body", scope="section",
            )

    def test_targeted_edit_on_resource(self, vault, router):
        """Target parameter works for resource editing too."""
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            body="Replaced section content.\n",
            target="# Test Skill", scope="body",
        )
        content = (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").read_text()
        assert "Replaced section content." in content
        assert "Original skill body." not in content


class TestTempPathFlag:
    def test_temp_path_prints_path_and_exits(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["edit.py", "--temp-path"]
            edit.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        try:
            assert out.endswith(".md")
            assert os.path.exists(out)
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_temp_path_custom_suffix(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["edit.py", "--temp-path", ".txt"]
            edit.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        try:
            assert out.endswith(".txt")
            assert os.path.exists(out)
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_occurrence_invalid_int_prints_error_and_exits(self, capsys):
        with patch.object(sys, "argv", ["edit.py", "edit", "--occurrence", "abc"]):
            with pytest.raises(SystemExit) as exc_info:
                edit.main()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error: --occurrence expects an integer" in err

    def test_within_occurrence_invalid_int_prints_error_and_exits(self, capsys):
        with patch.object(
            sys,
            "argv",
            ["edit.py", "edit", "--within", "## Notes", "--within-occurrence", "abc"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                edit.main()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error: --within-occurrence expects an integer" in err


class TestEditWikilinkWarnings:
    def test_clean_edit_no_warnings_key(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="No links.\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" not in result

    def test_broken_link_returns_warnings(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[missing-target]].\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" in result
        warnings = result["wikilink_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["stem"] == "missing-target"
        assert warnings[0]["status"] == "broken"

    def test_append_with_broken_link(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="append",
            path="Wiki/test-page.md", body="\nAlso [[also-missing]].\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" in result

    def test_prepend_with_broken_link(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="prepend",
            path="Wiki/test-page.md", body="Top [[top-missing]]\n\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" in result


class TestEditFixLinks:
    def test_fix_links_applies_resolvable(self, vault, router):
        (vault / "Wiki" / "Real Target.md").write_text("# Real\n")
        import compile_router
        router2 = compile_router.compile(str(vault))
        result = edit.edit_resource(
            str(vault), router2, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[real-target]].\n",
            target=":body", scope="section", fix_links=True,
        )
        assert "wikilink_fixes" in result
        assert result["wikilink_fixes"]["applied"] == 1
        assert "wikilink_warnings" not in result
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "[[Real Target]]" in content
        assert "[[real-target]]" not in content

    def test_fix_links_leaves_unresolvable_as_warning(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[never-existed]].\n",
            target=":body", scope="section", fix_links=True,
        )
        assert "wikilink_fixes" not in result
        assert "wikilink_warnings" in result
        assert result["wikilink_warnings"][0]["status"] == "broken"

    def test_fix_links_false_leaves_file_untouched(self, vault, router):
        (vault / "Wiki" / "Real Target.md").write_text("# Real\n")
        import compile_router
        router2 = compile_router.compile(str(vault))
        result = edit.edit_resource(
            str(vault), router2, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[real-target]].\n",
            target=":body", scope="section",
        )
        assert "wikilink_fixes" not in result
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "[[real-target]]" in content


class TestEditFileIndexThreading:
    """Verify edit_resource skips the vault walk when file_index is supplied."""

    def test_supplied_file_index_skips_vault_walk(self, vault, router, monkeypatch):
        """When file_index is supplied, build_vault_file_index must not be called."""
        import fix_links as _fix_links
        called = {"count": 0}
        original = _fix_links.build_vault_file_index
        def spy(*args, **kwargs):
            called["count"] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(_fix_links, "build_vault_file_index", spy)

        # Construct a minimal file_index that resolves [[real-target]]
        (vault / "Wiki" / "Real Target.md").write_text("# Real\n")
        file_index = file_index_from_documents(
            [{"path": "Wiki/Real Target.md"}],
            vault_root=str(vault),
        )

        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[real-target]].\n",
            target=":body", scope="section",
            fix_links=True, file_index=file_index,
        )
        assert called["count"] == 0, "build_vault_file_index must not be called when file_index supplied"
        # The supplied index treats the link as ambiguous-or-resolved; we don't assert wikilink_fixes here
        # because the synthetic stem doesn't exactly match. The point is the walk was skipped.

    def test_no_file_index_falls_back_to_walk(self, vault, router, monkeypatch):
        """Backward-compat: omitting file_index still triggers the walk (legacy CLI/tests)."""
        import fix_links as _fix_links
        called = {"count": 0}
        original = _fix_links.build_vault_file_index
        def spy(*args, **kwargs):
            called["count"] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(_fix_links, "build_vault_file_index", spy)

        edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="No links.\n",
            target=":body", scope="section",
        )
        assert called["count"] == 1, "build_vault_file_index must be called when file_index is None"


class TestSectionReplaceBoundaryGuard:
    """Reject section-replace bodies that would duplicate the next boundary heading."""

    def _setup(self, vault, body_text):
        path = vault / "Wiki" / "boundary-page.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n" + body_text
        )

    def test_rejects_body_ending_with_boundary_heading(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nUpdated alpha.\n\n## Beta\n",
                target="## Alpha", scope="section",
            )

    def test_accepts_split_into_two_with_interior_same_level_heading(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Gamma\n\nGamma body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nFirst half.\n\n## Beta\n\n| col |\n| --- |\n| val |\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert body.count("## Beta") == 1
        assert body.count("## Gamma") == 1
        assert body.index("## Alpha") < body.index("## Beta") < body.index("## Gamma")
        assert "## Alpha\n\nFirst half.\n\n## Beta" in body

    def test_accepts_body_ending_with_deeper_subsection(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nUpdated.\n\n### Subsection\n\nDeeper.\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Beta") == 1
        assert "### Subsection" in body
        assert body.index("### Subsection") < body.index("## Beta")

    def test_accepts_when_target_has_no_next_sibling_at_level(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n### Child\n\nChild body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nUpdated.\n\n## Whatever\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert "## Whatever" in body

    def test_rejection_error_is_actionable(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        with pytest.raises(ValueError) as exc:
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nUpdated alpha.\n\n## Beta\n",
                target="## Alpha", scope="section",
            )
        msg = str(exc.value)
        assert "## Beta" in msg
        assert ("drop" in msg.lower()) or ("widen" in msg.lower()) or ("trailing" in msg.lower())

    def test_accepts_body_with_no_internal_headings_other_than_target(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Beta\n\nB body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nJust prose, no other headings.\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert body.count("## Beta") == 1

    def test_accepts_when_body_final_heading_is_deeper_level_than_boundary(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Beta\n\nB body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nUpdated.\n\n### Beta\n\nDeeper Beta.\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Beta" in body
        assert "### Beta" in body

    def test_rejects_when_boundary_heading_appears_twice_in_body(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Beta\n\nB body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nFirst.\n\n## Beta\n\nMid.\n\n## Beta\n",
                target="## Alpha", scope="section",
            )

    def test_rejects_split_into_two_when_interior_heading_matches_boundary_text(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Gamma\n\nG body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nFirst.\n\n## Beta\n\nMid.\n\n## Gamma\n",
                target="## Alpha", scope="section",
            )

    def test_rejects_body_ending_with_shallower_boundary_heading(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n# Omega\n\nO body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nUpdated.\n\n# Omega\n",
                target="## Alpha", scope="section",
            )
