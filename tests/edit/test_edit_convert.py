"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re

import pytest

import edit
from _common import (
    ParentChainError,
    PartialApplyError,
    parse_frontmatter,
)


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

    def test_convert_nested_parent_rehomes_descendant_subtree(self, vault, router):
        (vault / "Designs" / "project~brain" / "parent").mkdir(parents=True, exist_ok=True)
        (vault / "Designs" / "project~brain" / "Parent.md").write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        (vault / "Designs" / "project~brain" / "parent" / "Child.md").write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "key: child\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Child\n"
        )
        grand_dir = vault / "Wiki" / "project~brain" / "designs~parent" / "designs~child"
        grand_dir.mkdir(parents=True, exist_ok=True)
        (grand_dir / "Grand.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - designs/child\n"
            "key: grand\n"
            "parent: designs/child\n"
            "---\n\n"
            "# Grand\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.convert_artefact(
            str(vault), router, "Designs/project~brain/Parent.md", "ideas"
        )

        assert result["new_path"] == "Ideas/project~brain/Parent.md"
        child = vault / "Designs" / "project~brain" / "ideas~parent" / "Child.md"
        assert child.is_file()
        fields, _ = parse_frontmatter(child.read_text())
        assert fields["parent"] == "ideas/parent"
        grand = vault / "Wiki" / "project~brain" / "ideas~parent" / "designs~child" / "Grand.md"
        assert grand.is_file()
        assert not (vault / "Designs" / "project~brain" / "parent").exists()
        assert (vault / "Designs").is_dir()
        assert (vault / "Wiki" / "project~brain").is_dir()

    def test_convert_living_to_temporal_deparents_descendant_subtree(self, vault, router):
        (vault / "Designs" / "project~brain" / "parent").mkdir(parents=True, exist_ok=True)
        (vault / "Designs" / "project~brain" / "Parent.md").write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        (vault / "Designs" / "project~brain" / "parent" / "Child.md").write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "key: child\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Child\n"
        )
        grand_dir = vault / "Wiki" / "project~brain" / "designs~parent" / "designs~child"
        grand_dir.mkdir(parents=True, exist_ok=True)
        (grand_dir / "Grand.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - designs/child\n"
            "key: grand\n"
            "parent: designs/child\n"
            "---\n\n"
            "# Grand\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.convert_artefact(
            str(vault), router, "Designs/project~brain/Parent.md", "research"
        )

        assert result["type"] == "temporal/research"
        assert result["new_path"].startswith("_Temporal/Research/")
        child = vault / "Designs" / "Child.md"
        assert child.is_file()
        fields, _ = parse_frontmatter(child.read_text())
        assert "parent" not in fields
        assert "designs/parent" not in fields.get("tags", [])
        grand = vault / "Wiki" / "designs~child" / "Grand.md"
        assert grand.is_file()
        fields, _ = parse_frontmatter(grand.read_text())
        assert fields["parent"] == "designs/child"

    def test_convert_stale_index_descendant_aborts_before_writes(self, vault, router):
        (vault / "Designs" / "project~brain" / "parent").mkdir(parents=True, exist_ok=True)
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        parent.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        (vault / "Designs" / "project~brain" / "parent" / "Child.md").write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "key: child\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        before = parent.read_text()
        (vault / "Designs" / "project~brain" / "parent" / "Child.md").unlink()

        with pytest.raises(ParentChainError) as exc_info:
            edit.convert_artefact(
                str(vault), router, "Designs/project~brain/Parent.md", "ideas"
            )

        assert "missing on disk" in str(exc_info.value)
        assert parent.read_text() == before
        assert not (vault / "Ideas" / "project~brain" / "Parent.md").exists()

    def test_convert_unindexed_parent_reference_aborts_before_writes(self, vault, router):
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        child = vault / "Designs" / "project~brain" / "parent" / "Keyless Child.md"
        child.parent.mkdir(parents=True, exist_ok=True)
        parent.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        child.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Keyless Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        parent_before = parent.read_text()
        child_before = child.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.convert_artefact(
                str(vault), router, "Designs/project~brain/Parent.md", "ideas"
            )

        message = str(exc_info.value)
        assert "parent reference to designs/parent" in message
        assert "absent from the compiled living index" in message
        assert parent.read_text() == parent_before
        assert child.read_text() == child_before
        assert not (vault / "Ideas" / "project~brain" / "Parent.md").exists()

    def test_convert_parent_change_to_descendant_rejected_before_writes(
        self, vault, router
    ):
        (vault / "Designs" / "project~brain" / "parent").mkdir(parents=True, exist_ok=True)
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        parent.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        child = vault / "Designs" / "project~brain" / "parent" / "Child.md"
        child.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "key: child\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        parent_before = parent.read_text()
        child_before = child.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.convert_artefact(
                str(vault),
                router,
                "Designs/project~brain/Parent.md",
                "ideas",
                parent="designs/child",
            )

        assert "child of descendant designs/child" in str(exc_info.value)
        assert parent.read_text() == parent_before
        assert child.read_text() == child_before
        assert not (vault / "Ideas" / "project~brain" / "Parent.md").exists()

    def test_convert_shared_preflight_rejects_before_writes(
        self, vault, router, monkeypatch
    ):
        (vault / "Designs" / "project~brain" / "parent").mkdir(parents=True, exist_ok=True)
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        parent.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        child = vault / "Designs" / "project~brain" / "parent" / "Child.md"
        child.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "key: child\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        parent_before = parent.read_text()
        child_before = child.read_text()

        def reject_preflight(*_args, **_kwargs):
            raise ValueError("Cyclic move set involving: Designs/project~brain/Parent.md")

        monkeypatch.setattr(edit, "preflight_move_set", reject_preflight)

        with pytest.raises(ValueError, match="Cyclic move set"):
            edit.convert_artefact(
                str(vault), router, "Designs/project~brain/Parent.md", "ideas"
            )

        assert parent.read_text() == parent_before
        assert child.read_text() == child_before
        assert not (vault / "Ideas" / "project~brain" / "Parent.md").exists()

    def test_convert_move_failure_leaves_documented_partial_state(
        self, vault, router, monkeypatch
    ):
        (vault / "Designs" / "project~brain" / "parent").mkdir(parents=True, exist_ok=True)
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        parent.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "key: parent\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        child = vault / "Designs" / "project~brain" / "parent" / "Child.md"
        child.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - designs/parent\n"
            "key: child\n"
            "parent: designs/parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        def fail_move(*_args, **_kwargs):
            raise PartialApplyError("move set partially applied")

        monkeypatch.setattr(edit, "move_and_update_links", fail_move)

        with pytest.raises(PartialApplyError, match="move set partially applied") as exc_info:
            edit.convert_artefact(
                str(vault), router, "Designs/project~brain/Parent.md", "ideas"
            )

        assert isinstance(exc_info.value.__cause__, PartialApplyError)
        assert "metadata files written ['Designs/project~brain/Parent.md', 'Designs/project~brain/parent/Child.md']" in str(exc_info.value)
        parent_fields, _ = parse_frontmatter(parent.read_text())
        assert parent_fields["type"] == "living/ideas"
        child_fields, _ = parse_frontmatter(child.read_text())
        assert child_fields["parent"] == "ideas/parent"
        assert not (vault / "Ideas" / "project~brain" / "Parent.md").exists()

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
        import compile_router
        router = compile_router.compile(str(vault))
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
        import compile_router
        router = compile_router.compile(str(vault))
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
        router = compile_router.compile(str(vault))
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
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/child-idea.md", "reports"
        )

        assert result["new_path"].startswith("_Temporal/Reports/")
        assert "project~brain" not in result["new_path"]
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]

    def test_convert_living_child_to_temporal_validates_preserved_parent(self, vault, router):
        child_dir = vault / "Ideas" / "project~brain"
        child_dir.mkdir(parents=True, exist_ok=True)
        source = child_dir / "child-idea.md"
        source.write_text(
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
        router["artefact_index"].pop("project/brain")
        before = source.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.convert_artefact(
                str(vault), router, "Ideas/project~brain/child-idea.md", "reports"
            )

        message = str(exc_info.value)
        assert "project/brain" in message
        assert "compiled living index" in message or "Broken parent reference" in message
        assert source.read_text() == before
        assert not any((vault / "_Temporal" / "Reports").glob("**/*.md"))

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
