"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
import rename
import rename
from _common import (
    HasDescendantsError,
    ParentChainError,
    PartialApplyError,
    parse_frontmatter,
    resolve_parent_reference,
)


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
    def test_generic_frontmatter_edit_opens_artefact_once(
        self, vault, router, monkeypatch
    ):
        original = edit.open_document
        calls = []

        def tracked_open(*args, **kwargs):
            calls.append(args[3])
            return original(*args, **kwargs)

        monkeypatch.setattr(edit, "open_document", tracked_open)

        edit.edit_resource(
            str(vault),
            router,
            resource="artefact",
            operation="edit",
            path="Wiki/test-page.md",
            frontmatter_changes={"tags": ["single-open"]},
        )

        assert calls == ["Wiki/test-page.md"]

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
    def _write_nested_design_tree(self, vault):
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

    def test_ancestor_key_change_relocates_deep_descendant_by_traversal(self, vault, router):
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        assert result["path"] == "Projects/Brain.md"
        assert (vault / "Designs" / "project~brain2" / "Parent.md").is_file()
        assert (vault / "Designs" / "project~brain2" / "parent" / "Child.md").is_file()
        grand = vault / "Wiki" / "project~brain2" / "designs~parent" / "designs~child" / "Grand.md"
        assert grand.is_file()
        fields, _ = parse_frontmatter(grand.read_text())
        assert fields["parent"] == "designs/child"
        assert "project/brain2" not in fields.get("tags", [])
        assert not (vault / "Designs" / "project~brain").exists()
        assert not (vault / "Wiki" / "project~brain").exists()
        assert (vault / "Designs").is_dir()
        assert (vault / "Wiki").is_dir()

    def test_key_change_moves_attachment_scope_and_rewrites_embed(self, vault, router):
        attachment = vault / "_Assets" / "Attachments" / "project~brain" / "diagram.svg"
        attachment.parent.mkdir(parents=True)
        attachment.write_text("<svg />")
        nested_attachment = attachment.parent / "snippets" / "notes.pdf"
        nested_attachment.parent.mkdir()
        nested_attachment.write_bytes(b"%PDF-nested")
        linker = vault / "Wiki" / "attachment-linker.md"
        linker.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "![[_Assets/Attachments/project~brain/diagram.svg]]\n"
            "![[_Assets/Attachments/project~brain/snippets/notes.pdf]]\n"
        )

        edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        moved = vault / "_Assets" / "Attachments" / "project~brain2" / "diagram.svg"
        moved_nested = moved.parent / "snippets" / "notes.pdf"
        assert moved.read_text() == "<svg />"
        assert moved_nested.read_bytes() == b"%PDF-nested"
        assert not attachment.exists()
        assert not (vault / "_Assets" / "Attachments" / "project~brain").exists()
        linker_content = linker.read_text()
        assert "![[_Assets/Attachments/project~brain2/diagram.svg]]" in linker_content
        assert (
            "![[_Assets/Attachments/project~brain2/snippets/notes.pdf]]"
            in linker_content
        )

    def test_key_change_attachment_scope_collision_rejects_before_writes(self, vault, router):
        source = vault / "Projects" / "Brain.md"
        before = source.read_text()
        old_scope = vault / "_Assets" / "Attachments" / "project~brain"
        new_scope = vault / "_Assets" / "Attachments" / "project~brain2"
        old_scope.mkdir(parents=True)
        new_scope.mkdir(parents=True)
        (old_scope / "old.svg").write_text("old")
        (new_scope / "new.svg").write_text("new")

        with pytest.raises(FileExistsError, match="destination already exists"):
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        assert source.read_text() == before
        assert (old_scope / "old.svg").read_text() == "old"
        assert (new_scope / "new.svg").read_text() == "new"

    def test_key_change_attachment_enumeration_failure_rejects_before_writes(
        self, vault, router, monkeypatch
    ):
        source = vault / "Projects" / "Brain.md"
        before = source.read_text()
        scope = vault / "_Assets" / "Attachments" / "project~brain"
        scope.mkdir(parents=True)
        (scope / "diagram.svg").write_text("<svg />")

        def unreadable_scope(*_args, **_kwargs):
            raise OSError("Cannot enumerate attachment scope: scope unreadable")

        monkeypatch.setattr(
            edit.attachment_upload,
            "plan_attachment_scope_moves",
            unreadable_scope,
        )

        with pytest.raises(OSError, match="Cannot enumerate attachment scope"):
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        assert source.read_text() == before
        assert (scope / "diagram.svg").read_text() == "<svg />"
        assert not (vault / "_Assets" / "Attachments" / "project~brain2").exists()

    def test_key_change_attachment_prune_failure_reports_partial_state(
        self, vault, router, monkeypatch
    ):
        attachment = vault / "_Assets" / "Attachments" / "project~brain" / "diagram.svg"
        attachment.parent.mkdir(parents=True)
        attachment.write_text("<svg />")

        def fail_prune(*_args, **_kwargs):
            raise OSError("old scope is not empty")

        monkeypatch.setattr(
            edit.attachment_upload,
            "prune_vacated_attachment_scope",
            fail_prune,
        )

        with pytest.raises(PartialApplyError, match="attachment moves committed"):
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        moved = vault / "_Assets" / "Attachments" / "project~brain2" / "diagram.svg"
        assert moved.read_text() == "<svg />"
        fields, _ = parse_frontmatter((vault / "Projects" / "Brain.md").read_text())
        assert fields["key"] == "brain2"

    def test_parent_change_relocates_nested_descendant_subtree(self, vault, router):
        (vault / "Projects" / "Custom.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/custom\n"
            "key: custom\n"
            "---\n\n"
            "# Custom\n"
        )
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.edit_artefact(
            str(vault),
            router,
            "Designs/project~brain/Parent.md",
            "",
            frontmatter_changes={"parent": "project/custom"},
        )

        assert result["path"] == "Designs/project~custom/Parent.md"
        assert (vault / "Designs" / "project~custom" / "parent" / "Child.md").is_file()
        grand = vault / "Wiki" / "project~custom" / "designs~parent" / "designs~child" / "Grand.md"
        assert grand.is_file()
        assert not (vault / "Designs" / "project~brain").exists()
        assert not (vault / "Wiki" / "project~brain").exists()

    def test_parent_change_to_descendant_rejected_before_writes(self, vault, router, monkeypatch):
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        child = vault / "Designs" / "project~brain" / "parent" / "Child.md"
        parent_before = parent.read_text()
        child_before = child.read_text()

        def fail_move(*_args, **_kwargs):
            raise AssertionError("move should not run")

        monkeypatch.setattr(rename, "apply_move_and_links", fail_move)

        with pytest.raises(ParentChainError) as exc_info:
            edit.edit_artefact(
                str(vault),
                router,
                "Designs/project~brain/Parent.md",
                "",
                frontmatter_changes={"parent": "designs/child"},
            )

        assert "child of descendant designs/child" in str(exc_info.value)
        assert parent.read_text() == parent_before
        assert child.read_text() == child_before

    def test_parent_change_self_parent_rejected_before_writes(self, vault, router):
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))
        parent = vault / "Designs" / "project~brain" / "Parent.md"
        before = parent.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.edit_artefact(
                str(vault),
                router,
                "Designs/project~brain/Parent.md",
                "",
                frontmatter_changes={"parent": "designs/parent"},
            )

        assert "parent itself" in str(exc_info.value)
        assert parent.read_text() == before

    def test_ownership_edit_shared_preflight_rejects_before_writes(
        self, vault, router, monkeypatch
    ):
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))
        source = vault / "Projects" / "Brain.md"
        before = source.read_text()

        def fail_preflight(*_args, **_kwargs):
            raise ValueError("Cyclic move set involving: Projects/Brain.md")

        monkeypatch.setattr(rename, "preflight_move_set", fail_preflight)

        with pytest.raises(ValueError, match="Cyclic move set"):
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        assert source.read_text() == before

    def test_parent_change_with_broken_grandparent_chain_fails_loud(self, vault, router):
        (vault / "Projects" / "Custom.md").write_text(
            "---\ntype: living/project\ntags:\n  - project/custom\nkey: custom\nparent: project/missing\n---\n\n# Custom\n"
        )
        (vault / "Ideas" / "Idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nkey: idea\nstatus: shaping\n---\n\nIdea.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        with pytest.raises(ParentChainError, match="project/missing"):
            edit.edit_artefact(
                str(vault),
                router,
                "Ideas/Idea.md",
                body="Idea.\n",
                target=":body",
                scope="section",
                frontmatter_changes={"parent": "project/custom"},
            )

        assert (vault / "Ideas" / "Idea.md").is_file()
        assert not (vault / "Ideas" / "project~custom" / "Idea.md").exists()

    def test_path_resolved_parent_missing_from_index_reports_stale_index(self, vault, router):
        (vault / "Projects" / "Custom.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/custom\n"
            "key: custom\n"
            "---\n\n"
            "# Custom\n"
        )

        with pytest.raises(ParentChainError) as exc_info:
            resolve_parent_reference(str(vault), router, "Projects/Custom.md")

        message = str(exc_info.value)
        assert "resolved parent Projects/Custom.md" in message
        assert "missing from the compiled living index" in message

    def test_path_resolved_parent_missing_on_disk_reports_stale_index(self, vault, router):
        with pytest.raises(ParentChainError) as exc_info:
            resolve_parent_reference(str(vault), router, "Projects/Vanished.md")

        message = str(exc_info.value)
        assert "resolved parent Projects/Vanished.md" in message
        assert "missing on disk" in message

    def test_key_change_stale_index_descendant_aborts_before_writes(self, vault, router):
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))
        source = vault / "Projects" / "Brain.md"
        before = source.read_text()
        missing = (
            vault
            / "Wiki"
            / "project~brain"
            / "designs~parent"
            / "designs~child"
            / "Grand.md"
        )
        missing.unlink()

        with pytest.raises(ParentChainError) as exc_info:
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        assert "missing on disk" in str(exc_info.value)
        assert source.read_text() == before
        assert not (vault / "Designs" / "project~brain2" / "Parent.md").exists()

    def test_key_change_unindexed_parent_reference_aborts_before_writes(self, vault, router):
        source = vault / "Projects" / "Brain.md"
        child = vault / "Designs" / "project~brain" / "Keyless Child.md"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.write_text(
            "---\n"
            "type: living/designs\n"
            "tags:\n"
            "  - project/brain\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Keyless Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        source_before = source.read_text()
        child_before = child.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        message = str(exc_info.value)
        assert "parent reference to project/brain" in message
        assert "absent from the compiled living index" in message
        assert source.read_text() == source_before
        assert child.read_text() == child_before
        assert not (vault / "Designs" / "project~brain2" / "Keyless Child.md").exists()

    def test_key_change_move_failure_leaves_documented_partial_state(
        self, vault, router, monkeypatch
    ):
        self._write_nested_design_tree(vault)
        import compile_router
        router = compile_router.compile(str(vault))

        def fail_move(*_args, **_kwargs):
            raise PartialApplyError("move set partially applied")

        monkeypatch.setattr(rename, "apply_move_and_links", fail_move)

        with pytest.raises(PartialApplyError, match="move set partially applied") as exc_info:
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        assert isinstance(exc_info.value.__cause__, PartialApplyError)
        assert "metadata files written ['Projects/Brain.md', 'Designs/project~brain/Parent.md']" in str(exc_info.value)
        source_fields, _ = parse_frontmatter((vault / "Projects" / "Brain.md").read_text())
        assert source_fields["key"] == "brain2"
        parent_path = vault / "Designs" / "project~brain" / "Parent.md"
        parent_fields, _ = parse_frontmatter(parent_path.read_text())
        assert parent_fields["parent"] == "project/brain2"
        assert not (vault / "Designs" / "project~brain2" / "Parent.md").exists()

    def test_key_change_stamps_root_modified_but_not_descendant(self, vault, router):
        self._write_nested_design_tree(vault)
        source = vault / "Projects" / "Brain.md"
        source.write_text(
            source.read_text().replace(
                "key: brain\n",
                "key: brain\nmodified: 2020-01-01T00:00:00+00:00\n",
            )
        )
        parent_path = vault / "Designs" / "project~brain" / "Parent.md"
        parent_path.write_text(
            parent_path.read_text().replace(
                "status: shaping\n",
                "status: shaping\nmodified: 2020-01-01T00:00:00+00:00\n",
            )
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        assert result["path"] == "Projects/Brain.md"
        source_fields, _ = parse_frontmatter(source.read_text())
        assert source_fields["modified"] != "2020-01-01T00:00:00+00:00"
        moved_parent = vault / "Designs" / "project~brain2" / "Parent.md"
        parent_fields, _ = parse_frontmatter(moved_parent.read_text())
        assert parent_fields["parent"] == "project/brain2"
        assert parent_fields["modified"] == "2020-01-01T00:00:00+00:00"

    def test_key_change_bare_living_type_with_child_uses_pending_classification(self, vault, router):
        taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "quests.md"
        taxonomy.write_text(
            "# Quests\n\n"
            "## Naming\n\n`{Title}.md` in `Quests/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: quest\ntags: []\n---\n```\n"
        )
        (vault / "Quests" / "parent").mkdir(parents=True)
        (vault / "Quests" / "Parent.md").write_text(
            "---\n"
            "type: quest\n"
            "tags: []\n"
            "key: parent\n"
            "---\n\n"
            "# Parent\n"
        )
        (vault / "Quests" / "parent" / "Child.md").write_text(
            "---\n"
            "type: quest\n"
            "tags:\n"
            "  - quest/parent\n"
            "key: child\n"
            "parent: quest/parent\n"
            "---\n\n"
            "# Child\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        edit.edit_artefact(
            str(vault),
            router,
            "Quests/Parent.md",
            "",
            frontmatter_changes={"key": "parent2"},
        )

        assert (vault / "Quests" / "parent2" / "Child.md").is_file()
        child_fields, _ = parse_frontmatter(
            (vault / "Quests" / "parent2" / "Child.md").read_text()
        )
        assert child_fields["parent"] == "quest/parent2"

    def test_reference_mutation_write_failure_reports_written_context(
        self, vault, router, monkeypatch
    ):
        calls = []

        def flaky_write(*_args, **_kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise OSError("disk full")

        monkeypatch.setattr(
            edit, "safe_write_active_or_archived_artefact", flaky_write
        )

        with pytest.raises(PartialApplyError, match="reference mutation partially applied") as exc_info:
            edit._write_frontmatter_mutations(
                str(vault),
                [
                    {"path": "Wiki/a.md", "fields": {"type": "living/wiki"}, "body": "A"},
                    {"path": "Wiki/b.md", "fields": {"type": "living/wiki"}, "body": "B"},
                ],
                operation="reference mutation",
            )

        assert isinstance(exc_info.value.__cause__, OSError)
        assert "files written ['Wiki/a.md']" in str(exc_info.value)

    def test_temporal_parent_edit_rehomes_under_owner_scope(self, vault, router):
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        path = research / "20260413-research~Sample Title.md"
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
            "_Temporal/Research/20260413-research~Sample Title.md",
            "",
            frontmatter_changes={"parent": "project/brain"},
        )

        new_path = "_Temporal/Research/project~brain/20260413-research~Sample Title.md"
        assert result["path"] == new_path
        assert not path.exists()
        fields, _ = parse_frontmatter((vault / new_path).read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]

    def test_temporal_created_change_moves_the_date_in_the_filename_only(
        self, vault, router
    ):
        """Only a parent change re-files a temporal artefact.

        The folder carries no date segment any more, so editing ``created``
        re-renders the dated filename and leaves the owner folder alone.
        """
        owner_dir = vault / "_Temporal" / "Research" / "project~brain"
        owner_dir.mkdir(parents=True, exist_ok=True)
        path = owner_dir / "20260413-research~Sample Title.md"
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
            "_Temporal/Research/project~brain/20260413-research~Sample Title.md",
            "",
            frontmatter_changes={"created": "2026-09-01T09:00:00+10:00"},
        )

        assert result["path"] == (
            "_Temporal/Research/project~brain/20260901-research~Sample Title.md"
        )
        assert os.path.dirname(result["path"]) == "_Temporal/Research/project~brain"
        assert not path.exists()
        assert (vault / result["path"]).is_file()

    def test_temporal_body_edit_leaves_an_existing_broken_parent_in_place(
        self, vault, router
    ):
        """Filing follows the owner chain, so a body edit never re-files.

        The stale ``parent`` is left for the doctor to report rather than
        blocking an unrelated edit.
        """
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        path = research / "20260413-research~Broken Parent.md"
        path.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "parent: project/missing\n"
            "---\n\n"
            "Original body.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "_Temporal/Research/20260413-research~Broken Parent.md",
            "Changed body.\n",
            target=":body",
            scope="section",
        )

        assert result["path"] == "_Temporal/Research/20260413-research~Broken Parent.md"
        assert "Changed body." in path.read_text()
        assert not (vault / "_Temporal" / "Research" / "project~missing").exists()

    def test_temporal_parent_change_to_a_broken_parent_fails_before_write(
        self, vault, router
    ):
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        path = research / "20260413-research~Broken Parent.md"
        original = (
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Original body.\n"
        )
        path.write_text(original)

        with pytest.raises(ValueError, match="INVALID_PARENT.*project/missing"):
            edit.edit_artefact(
                str(vault),
                router,
                "_Temporal/Research/20260413-research~Broken Parent.md",
                "",
                frontmatter_changes={"parent": "project/missing"},
            )

        assert path.read_text() == original
        assert not (
            vault
            / "_Temporal"
            / "Research"
            / "project~missing"
            / "20260413-research~Broken Parent.md"
        ).exists()

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
        import compile_router
        router = compile_router.compile(str(vault))

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

    def test_parent_key_change_rehomes_temporal_children_under_new_owner_scope(self, vault, router):
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        path = research / "20260413-research~Sample Title.md"
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
        new_path = (
            vault
            / "_Temporal"
            / "Research"
            / "project~brain2"
            / "20260413-research~Sample Title.md"
        )
        assert new_path.is_file()
        assert not path.exists()
        fields, _ = parse_frontmatter(new_path.read_text())
        assert fields["parent"] == "project/brain2"
        assert "project/brain2" in fields["tags"]
        assert "project/brain" not in fields["tags"]

    def test_same_key_parent_change_rehomes_temporal_children_under_new_owner_chain(
        self, vault, router
    ):
        (vault / "Projects" / "Old Parent.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/old-parent\n"
            "key: old-parent\n"
            "---\n\n"
            "# Old Parent\n"
        )
        (vault / "Projects" / "New Parent.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/new-parent\n"
            "key: new-parent\n"
            "---\n\n"
            "# New Parent\n"
        )
        child_dir = vault / "Projects" / "old-parent"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "Child.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/child\n"
            "key: child\n"
            "parent: project/old-parent\n"
            "---\n\n"
            "# Child\n"
        )
        old_temporal_dir = (
            vault
            / "_Temporal"
            / "Research"
            / "project~old-parent"
            / "project~child"
        )
        old_temporal_dir.mkdir(parents=True, exist_ok=True)
        old_temporal_path = old_temporal_dir / "20260413-research~Sample Title.md"
        old_temporal_path.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "  - project/child\n"
            "parent: project/child\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Body.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/old-parent/Child.md",
            "",
            frontmatter_changes={"parent": "project/new-parent"},
        )

        assert result["path"] == "Projects/new-parent/Child.md"
        new_temporal_path = (
            vault
            / "_Temporal"
            / "Research"
            / "project~new-parent"
            / "project~child"
            / "20260413-research~Sample Title.md"
        )
        assert new_temporal_path.is_file()
        assert not old_temporal_path.exists()
        fields, _ = parse_frontmatter(new_temporal_path.read_text())
        assert fields["parent"] == "project/child"

    def test_parent_key_change_temporal_rehome_failure_reports_partial_context(
        self, vault, router, monkeypatch
    ):
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        path = research / "20260413-research~Sample Title.md"
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

        def fail_move(*_args, **_kwargs):
            raise PartialApplyError("move set partially applied")

        monkeypatch.setattr(rename, "apply_move_and_links", fail_move)

        with pytest.raises(PartialApplyError, match="ownership mutation partially applied") as exc_info:
            edit.edit_artefact(
                str(vault),
                router,
                "Projects/Brain.md",
                "",
                frontmatter_changes={"key": "brain2"},
            )

        message = str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, PartialApplyError)
        assert "Projects/Brain.md" in message
        assert "_Temporal/Research/20260413-research~Sample Title.md" in message
        fields, _ = parse_frontmatter(path.read_text())
        assert fields["parent"] == "project/brain2"
        assert not (
            vault
            / "_Temporal"
            / "Research"
            / "project~brain2"
            / "20260413-research~Sample Title.md"
        ).exists()

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
        router = compile_router.compile(str(vault))

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

class TestExplicitLifecycleFields:
    def _make_idea(self, vault):
        path = vault / "Ideas" / "Lifecycle.md"
        path.write_text(
            "---\ntype: living/ideas\ntags: []\nkey: lifecycle\nstatus: shaping\n---\n\nBody.\n"
        )
        return "Ideas/Lifecycle.md"

    @pytest.mark.parametrize("value", [None, ""])
    def test_status_cannot_be_empty(self, vault, router, value):
        path = self._make_idea(vault)

        with pytest.raises(ValueError, match="status cannot be empty"):
            edit.update_lifecycle_field(str(vault), router, path, "status", value)

    def test_invalid_status_lists_valid_values(self, vault, router):
        path = self._make_idea(vault)

        with pytest.raises(ValueError, match="Invalid status 'invented'"):
            edit.update_lifecycle_field(
                str(vault), router, path, "status", "invented"
            )

    def test_key_cannot_be_empty(self, vault, router):
        path = self._make_idea(vault)

        with pytest.raises(ValueError, match="key cannot be empty"):
            edit.update_lifecycle_field(str(vault), router, path, "key", "")

    def test_non_owned_field_is_rejected(self, vault, router):
        path = self._make_idea(vault)

        with pytest.raises(ValueError, match="is not lifecycle-owned"):
            edit.update_lifecycle_field(str(vault), router, path, "summary", "No")

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

    def test_terminal_status_move_failure_reports_metadata_written_context(
        self, vault, router, monkeypatch
    ):
        self._make_idea(vault, "Ideas/my-idea.md")

        def fail_move(*_args, **_kwargs):
            raise OSError("disk refused move")

        monkeypatch.setattr(rename, "apply_move_and_links", fail_move)

        with pytest.raises(PartialApplyError) as exc_info:
            edit.edit_artefact(
                str(vault), router, "Ideas/my-idea.md", "",
                frontmatter_changes={"status": "adopted"},
            )

        assert "metadata files written" in str(exc_info.value)
        assert "Ideas/my-idea.md" in str(exc_info.value)
        assert "disk refused move" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, OSError)
        fields, _ = parse_frontmatter((vault / "Ideas" / "my-idea.md").read_text())
        assert fields["status"] == "adopted"
        assert not (vault / "Ideas" / "+Adopted" / "my-idea.md").exists()

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

    def test_lifecycle_status_preflight_rejects_missing_naming_field_atomically(
        self, vault, router
    ):
        path = "Releases/project~brain/Missing Version.md"
        self._make_release(vault, path, version="")
        source = vault / path
        before = source.read_text()

        with pytest.raises(ValueError, match="requires frontmatter field 'version'"):
            edit.update_lifecycle_field(
                str(vault), router, path, "status", "shipped"
            )

        assert source.read_text() == before
        assert not (vault / "Releases" / "project~brain" / "+Shipped").exists()

    def test_naming_field_preflight_rejects_regex_mismatch_atomically(
        self, vault, router
    ):
        path = "Releases/project~brain/+Shipped/v0.28.6 - Invalid Version.md"
        self._make_release(vault, path, status="shipped", version="v0.28.6")
        source = vault / path
        before = source.read_text()

        with pytest.raises(ValueError, match="does not match.*regex"):
            edit.update_lifecycle_field(
                str(vault), router, path, "version", "definitely-not-semver"
            )

        assert source.read_text() == before

    def test_inactive_naming_field_regex_does_not_block_tentative_value(
        self, vault, router
    ):
        path = "Releases/project~brain/Tentative Version.md"
        self._make_release(vault, path, status="planned", version="v0.28.6")

        result = edit.update_lifecycle_field(
            str(vault), router, path, "version", "TBD"
        )

        assert result["path"] == path
        fields, _body = parse_frontmatter((vault / path).read_text())
        assert fields["version"] == "TBD"

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

    def test_archive_apply_rejects_symlink_swap_into_configuration(self, vault, router):
        rel = self._make_idea(vault)
        plan = edit.plan_archive(str(vault), router, rel)
        protected = vault / "_Config/my-idea.md"
        protected.write_text((vault / rel).read_text())
        (vault / rel).unlink()
        (vault / "Ideas").rmdir()
        (vault / "Ideas").symlink_to(vault / "_Config", target_is_directory=True)
        before = protected.read_bytes()

        with pytest.raises(ValueError, match="Cannot write artefacts to '_Config'"):
            edit.apply_artefact_transition(str(vault), plan)

        assert protected.read_bytes() == before
        assert not (vault / plan.result["new_path"]).exists()

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

    def test_archive_preserves_non_terminal_status(self, vault, router):
        rel = self._make_idea(vault, status="shaping")
        result = edit.archive_artefact(str(vault), router, rel)
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["status"] == "shaping"

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

    def _make_archive_tree(self, vault):
        (vault / "Ideas" / "parent").mkdir(parents=True, exist_ok=True)
        (vault / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: adopted\n"
            "---\n\n"
            "Parent.\n"
        )
        (vault / "Ideas" / "parent" / "Child.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - ideas/parent\n"
            "key: child\n"
            "parent: ideas/parent\n"
            "status: adopted\n"
            "---\n\n"
            "Child.\n"
        )
        (vault / "Wiki" / "ideas~parent" / "ideas~child").mkdir(parents=True, exist_ok=True)
        (vault / "Wiki" / "ideas~parent" / "ideas~child" / "Grand.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - ideas/child\n"
            "key: grand\n"
            "parent: ideas/child\n"
            "---\n\n"
            "Grand.\n"
        )
        import compile_router
        return compile_router.compile(str(vault))

    def test_archive_refuses_living_descendants_by_default(self, vault, router):
        router = self._make_archive_tree(vault)

        with pytest.raises(HasDescendantsError) as exc_info:
            edit.archive_artefact(str(vault), router, "Ideas/Parent.md")

        payload = exc_info.value.to_payload()
        assert payload["code"] == "HAS_DESCENDANTS"
        assert payload["operation"] == "archive"
        assert payload["source"] == "Ideas/Parent.md"
        assert [entry["key"] for entry in payload["descendants"]] == [
            "ideas/child",
            "wiki/grand",
        ]
        assert (vault / "Ideas" / "Parent.md").is_file()
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()

    def test_archive_source_missing_from_index_reports_stale_index(self, vault, router):
        router = self._make_archive_tree(vault)
        source_before = (vault / "Ideas" / "Parent.md").read_text()
        router["artefact_index"].pop("ideas/parent")

        with pytest.raises(ParentChainError) as exc_info:
            edit.archive_artefact(str(vault), router, "Ideas/Parent.md")

        message = str(exc_info.value)
        assert "source artefact key is not in the compiled living index" in message
        assert "ideas/parent" in message
        assert "Broken parent reference: ideas/parent" not in message
        assert (vault / "Ideas" / "Parent.md").read_text() == source_before
        assert not (vault / "_Archive" / "Ideas").exists()

    def test_archive_recursive_cascades_living_subtree(self, vault, router):
        router = self._make_archive_tree(vault)

        result = edit.archive_artefact(
            str(vault), router, "Ideas/Parent.md", recursive=True
        )

        archived_paths = {entry["old_path"]: entry["new_path"] for entry in result["archived"]}
        assert set(archived_paths) == {
            "Ideas/Parent.md",
            "Ideas/parent/Child.md",
            "Wiki/ideas~parent/ideas~child/Grand.md",
        }
        for old_path, new_path in archived_paths.items():
            assert not (vault / old_path).exists()
            assert (vault / new_path).is_file()
            fields, _ = parse_frontmatter((vault / new_path).read_text())
            assert "archiveddate" in fields
        assert archived_paths["Wiki/ideas~parent/ideas~child/Grand.md"].startswith(
            "_Archive/Wiki/ideas~parent/ideas~child/"
        )
        assert not (vault / "Ideas" / "parent").exists()
        assert not (vault / "Wiki" / "ideas~parent").exists()
        assert (vault / "Ideas").is_dir()
        assert (vault / "Wiki").is_dir()

    def test_recursive_unarchive_restores_archived_subtree(self, vault, router):
        router = self._make_archive_tree(vault)
        archived = edit.archive_artefact(
            str(vault), router, "Ideas/Parent.md", recursive=True
        )
        restored = edit.unarchive_artefact(
            str(vault), router, archived["new_path"], recursive=True
        )

        assert {item["new_path"] for item in restored["restored"]} == {
            "Ideas/+Adopted/Parent.md",
            "Ideas/parent/+Adopted/Child.md",
            "Wiki/ideas~parent/ideas~child/Grand.md",
        }
        for item in restored["restored"]:
            assert (vault / item["new_path"]).is_file()
            fields, _ = parse_frontmatter((vault / item["new_path"]).read_text())
            assert "archiveddate" not in fields

    def test_archive_recursive_cascades_non_terminal_living_descendant(self, vault, router):
        router = self._make_archive_tree(vault)
        child = vault / "Ideas" / "parent" / "Child.md"
        fields, body = parse_frontmatter(child.read_text())
        fields["status"] = "shaping"
        child.write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - ideas/parent\n"
            "key: child\n"
            "parent: ideas/parent\n"
            "status: shaping\n"
            "---\n\n"
            f"{body}"
        )

        result = edit.archive_artefact(
            str(vault), router, "Ideas/Parent.md", recursive=True
        )

        archived_paths = {entry["old_path"]: entry["new_path"] for entry in result["archived"]}
        archived_child = vault / archived_paths["Ideas/parent/Child.md"]
        assert archived_child.is_file()
        fields, _ = parse_frontmatter(archived_child.read_text())
        assert fields["status"] == "shaping"
        assert "archiveddate" in fields

    def test_archive_recursive_refuses_write_protected_descendant(self, vault, router, monkeypatch):
        router = self._make_archive_tree(vault)
        original = edit.check_write_allowed

        def guard(rel_path):
            if rel_path == "Ideas/parent/Child.md":
                raise ValueError("Cannot write to protected descendant")
            return original(rel_path)

        monkeypatch.setattr(edit, "check_write_allowed", guard)

        with pytest.raises(ValueError, match="protected descendant"):
            edit.archive_artefact(
                str(vault), router, "Ideas/Parent.md", recursive=True
            )

        fields, _ = parse_frontmatter((vault / "Ideas" / "Parent.md").read_text())
        assert "archiveddate" not in fields
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()

    def test_archive_with_only_temporal_children_proceeds(self, vault, router):
        rel = self._make_idea(vault, name="Parent.md", status="adopted")
        (vault / rel).write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: adopted\n"
            "---\n\n"
            "Parent.\n"
        )
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        temporal = research / "20260413-research~Child.md"
        temporal.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - ideas/parent\n"
            "parent: ideas/parent\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Temporal.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.archive_artefact(str(vault), router, rel)

        assert (vault / result["new_path"]).is_file()
        assert temporal.is_file()

    def test_archive_cyclic_graph_aborts_before_writes(self, vault, router):
        router = self._make_archive_tree(vault)
        router["artefact_index"]["ideas/parent"]["parent"] = "wiki/grand"

        with pytest.raises(Exception, match="Cyclic descendant chain"):
            edit.archive_artefact(
                str(vault), router, "Ideas/Parent.md", recursive=True
            )

        fields, _ = parse_frontmatter((vault / "Ideas" / "Parent.md").read_text())
        assert "archiveddate" not in fields
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()

    def test_archive_write_failure_reports_written_context(self, vault, router, monkeypatch):
        router = self._make_archive_tree(vault)
        original = edit.safe_write_active_or_archived_artefact
        calls = []

        def flaky_write(path, content, **kwargs):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            return original(path, content, **kwargs)

        monkeypatch.setattr(
            edit, "safe_write_active_or_archived_artefact", flaky_write
        )

        with pytest.raises(PartialApplyError, match="archive partially applied") as exc_info:
            edit.archive_artefact(
                str(vault), router, "Ideas/Parent.md", recursive=True
            )

        assert isinstance(exc_info.value.__cause__, OSError)
        assert "files written ['Ideas/Parent.md']" in str(exc_info.value)

    def test_archive_move_failure_reports_metadata_written_context(self, vault, router, monkeypatch):
        router = self._make_archive_tree(vault)

        def fail_move(*_args, **_kwargs):
            raise PartialApplyError("move set partially applied")

        import rename
        monkeypatch.setattr(rename, "apply_move_and_links", fail_move)

        with pytest.raises(PartialApplyError, match="archive partially applied") as exc_info:
            edit.archive_artefact(
                str(vault), router, "Ideas/Parent.md", recursive=True
            )

        assert isinstance(exc_info.value.__cause__, PartialApplyError)
        message = str(exc_info.value)
        assert "metadata files written ['Ideas/Parent.md', 'Ideas/parent/Child.md', 'Wiki/ideas~parent/ideas~child/Grand.md']" in message
        assert "move failure: move set partially applied" in message

    def test_archive_shared_preflight_rejects_before_writes(self, vault, router, monkeypatch):
        router = self._make_archive_tree(vault)
        parent = vault / "Ideas" / "Parent.md"
        before = parent.read_text()

        def fail_preflight(*_args, **_kwargs):
            raise ValueError("Cyclic move set involving: Ideas/Parent.md")

        monkeypatch.setattr(rename, "preflight_move_set", fail_preflight)

        with pytest.raises(ValueError, match="Cyclic move set"):
            edit.archive_artefact(
                str(vault), router, "Ideas/Parent.md", recursive=True
            )

        assert parent.read_text() == before


class TestReparentChildren:
    def _write_reparent_tree(self, vault):
        (vault / "Projects" / "Custom.md").write_text(
            "---\ntype: living/project\ntags:\n  - project/custom\nkey: custom\n---\n\n# Custom\n"
        )
        (vault / "Ideas" / "project~brain" / "child").mkdir(parents=True, exist_ok=True)
        (vault / "Ideas" / "project~brain" / "Child.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: child\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "Child.\n"
        )
        (vault / "Wiki" / "project~brain" / "ideas~child").mkdir(parents=True, exist_ok=True)
        (vault / "Wiki" / "project~brain" / "ideas~child" / "Grand.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - ideas/child\n"
            "key: grand\n"
            "parent: ideas/child\n"
            "---\n\n"
            "Grand.\n"
        )
        import compile_router
        return compile_router.compile(str(vault))

    def test_reparent_to_new_parent_moves_each_child_subtree(self, vault, router, monkeypatch):
        router = self._write_reparent_tree(vault)
        calls = []
        original = rename.apply_move_and_links
        monkeypatch.setattr(
            rename,
            "apply_move_and_links",
            lambda vault_root, plan: calls.append(list(plan.moves))
            or original(vault_root, plan),
        )

        result = edit.reparent_children(
            str(vault), router, "Projects/Brain.md", "project/custom", to_provided=True
        )

        assert len(calls) == 1
        assert result["to"] == "project/custom"
        child = vault / "Ideas" / "project~custom" / "Child.md"
        grand = vault / "Wiki" / "project~custom" / "ideas~child" / "Grand.md"
        assert child.is_file()
        assert grand.is_file()
        fields, _ = parse_frontmatter(child.read_text())
        assert fields["parent"] == "project/custom"
        assert "project/custom" in fields["tags"]
        assert "project/brain" not in fields["tags"]
        assert not (vault / "Wiki" / "project~brain").exists()
        assert (vault / "Ideas").is_dir()
        assert (vault / "Wiki").is_dir()
        assert {
            "source": "Ideas/project~brain/Child.md",
            "dest": "Ideas/project~custom/Child.md",
        } in result["moves"]

    def test_reparent_to_self_rejected_before_writes(self, vault, router):
        router = self._write_reparent_tree(vault)
        child = vault / "Ideas" / "project~brain" / "Child.md"
        before = child.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.reparent_children(
                str(vault), router, "Projects/Brain.md", "project/brain", to_provided=True
            )

        assert "parent itself" in str(exc_info.value)
        assert child.read_text() == before

    def test_reparent_to_descendant_rejected_before_writes(self, vault, router):
        router = self._write_reparent_tree(vault)
        child = vault / "Ideas" / "project~brain" / "Child.md"
        before = child.read_text()

        with pytest.raises(ParentChainError) as exc_info:
            edit.reparent_children(
                str(vault), router, "Projects/Brain.md", "ideas/child", to_provided=True
            )

        assert "child of descendant ideas/child" in str(exc_info.value)
        assert child.read_text() == before

    def test_reparent_shared_preflight_rejects_before_writes(self, vault, router, monkeypatch):
        router = self._write_reparent_tree(vault)
        child = vault / "Ideas" / "project~brain" / "Child.md"
        before = child.read_text()

        def fail_preflight(*_args, **_kwargs):
            raise ValueError("Cyclic move set involving: Ideas/project~brain/Child.md")

        monkeypatch.setattr(rename, "preflight_move_set", fail_preflight)

        with pytest.raises(ValueError, match="Cyclic move set"):
            edit.reparent_children(
                str(vault), router, "Projects/Brain.md", "project/custom", to_provided=True
            )

        assert child.read_text() == before

    def test_reparent_source_missing_from_index_reports_stale_index(self, vault, router):
        router = self._write_reparent_tree(vault)
        source_before = (vault / "Projects" / "Brain.md").read_text()
        child_before = (vault / "Ideas" / "project~brain" / "Child.md").read_text()
        router["artefact_index"].pop("project/brain")

        with pytest.raises(ParentChainError) as exc_info:
            edit.reparent_children(
                str(vault), router, "Projects/Brain.md", "project/custom", to_provided=True
            )

        message = str(exc_info.value)
        assert "source artefact key is not in the compiled living index" in message
        assert "project/brain" in message
        assert (vault / "Projects" / "Brain.md").read_text() == source_before
        assert (vault / "Ideas" / "project~brain" / "Child.md").read_text() == child_before
        assert not (vault / "Ideas" / "project~custom" / "Child.md").exists()

    def test_reparent_omitted_to_dissolves_to_grandparent(self, vault, router, monkeypatch):
        (vault / "Projects" / "Brain.md").unlink()
        (vault / "Projects" / "Root.md").write_text(
            "---\ntype: living/project\ntags:\n  - project/root\nkey: root\n---\n\n# Root\n"
        )
        (vault / "Projects" / "project~root").mkdir(parents=True, exist_ok=True)
        (vault / "Projects" / "project~root" / "Brain.md").write_text(
            "---\ntype: living/project\ntags:\n  - project/brain\nkey: brain\nparent: project/root\n---\n\n# Brain\n"
        )
        (vault / "Ideas" / "project~root" / "project~brain").mkdir(parents=True, exist_ok=True)
        (vault / "Ideas" / "project~root" / "project~brain" / "Child.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: child\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "Child.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        calls = []
        original = rename.apply_move_and_links
        monkeypatch.setattr(
            rename,
            "apply_move_and_links",
            lambda vault_root, plan: calls.append(list(plan.moves))
            or original(vault_root, plan),
        )

        edit.reparent_children(str(vault), router, "Projects/project~root/Brain.md")

        assert len(calls) == 1
        child = vault / "Ideas" / "project~root" / "Child.md"
        assert child.is_file()
        fields, _ = parse_frontmatter(child.read_text())
        assert fields["parent"] == "project/root"
        assert "project/root" in fields["tags"]
        assert "project/brain" not in fields["tags"]

    def test_reparent_cleared_to_moves_children_top_level(self, vault, router, monkeypatch):
        router = self._write_reparent_tree(vault)
        calls = []
        original = rename.apply_move_and_links
        monkeypatch.setattr(
            rename,
            "apply_move_and_links",
            lambda vault_root, plan: calls.append(list(plan.moves))
            or original(vault_root, plan),
        )

        result = edit.reparent_children(
            str(vault), router, "Projects/Brain.md", None, to_provided=True
        )

        assert len(calls) == 1
        assert result["to"] is None
        child = vault / "Ideas" / "Child.md"
        grand = vault / "Wiki" / "ideas~child" / "Grand.md"
        assert child.is_file()
        assert grand.is_file()
        fields, _ = parse_frontmatter(child.read_text())
        assert "parent" not in fields
        assert "project/brain" not in fields["tags"]

    def test_reparent_write_failure_reports_written_context(self, vault, router, monkeypatch):
        router = self._write_reparent_tree(vault)

        def fail_write(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            edit, "safe_write_active_or_archived_artefact", fail_write
        )

        with pytest.raises(OSError, match="reparent failed before writing") as exc_info:
            edit.reparent_children(
                str(vault), router, "Projects/Brain.md", "project/custom", to_provided=True
            )

        assert isinstance(exc_info.value.__cause__, OSError)
        assert "Ideas/project~brain/Child.md" in str(exc_info.value)

    def test_reparent_move_failure_reports_metadata_written_context(self, vault, router, monkeypatch):
        router = self._write_reparent_tree(vault)

        def fail_move(*_args, **_kwargs):
            raise PartialApplyError("move set partially applied")

        monkeypatch.setattr(rename, "apply_move_and_links", fail_move)

        with pytest.raises(PartialApplyError, match="reparent partially applied") as exc_info:
            edit.reparent_children(
                str(vault), router, "Projects/Brain.md", "project/custom", to_provided=True
            )

        assert isinstance(exc_info.value.__cause__, PartialApplyError)
        message = str(exc_info.value)
        assert "metadata files written ['Ideas/project~brain/Child.md']" in message
        assert "move failure: move set partially applied" in message


class TestDeleteLivingDescendants:
    def _write_delete_tree(self, vault):
        (vault / "Ideas" / "parent").mkdir(parents=True, exist_ok=True)
        (vault / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: shaping\n"
            "---\n\n"
            "Parent.\n"
        )
        (vault / "Ideas" / "parent" / "Child.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - ideas/parent\n"
            "key: child\n"
            "parent: ideas/parent\n"
            "status: shaping\n"
            "---\n\n"
            "Child links [[Ideas/Parent|Parent]].\n"
        )
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Ideas/parent/Child|Child]].\n"
        )
        import compile_router
        return compile_router.compile(str(vault))

    def test_delete_refuses_living_descendants_by_default(self, vault, router):
        router = self._write_delete_tree(vault)

        with pytest.raises(HasDescendantsError) as exc_info:
            rename.delete_and_clean_links(
                str(vault), "Ideas/Parent.md", router=router
            )

        payload = exc_info.value.to_payload()
        assert payload["code"] == "HAS_DESCENDANTS"
        assert payload["operation"] == "delete"
        assert payload["descendants"][0]["key"] == "ideas/child"
        assert (vault / "Ideas" / "Parent.md").is_file()
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()

    def test_delete_source_missing_from_index_reports_stale_index(self, vault, router):
        router = self._write_delete_tree(vault)
        source_before = (vault / "Ideas" / "Parent.md").read_text()
        router["artefact_index"].pop("ideas/parent")

        with pytest.raises(ParentChainError) as exc_info:
            rename.delete_and_clean_links(
                str(vault), "Ideas/Parent.md", router=router
            )

        message = str(exc_info.value)
        assert "source artefact key is not in the compiled living index" in message
        assert "ideas/parent" in message
        assert "Broken parent reference: ideas/parent" not in message
        assert (vault / "Ideas" / "Parent.md").read_text() == source_before

    def test_delete_recursive_cascades_living_subtree(self, vault, router):
        router = self._write_delete_tree(vault)

        count = rename.delete_and_clean_links(
            str(vault), "Ideas/Parent.md", router=router, recursive=True
        )

        assert count == 2
        assert not (vault / "Ideas" / "Parent.md").exists()
        assert not (vault / "Ideas" / "parent" / "Child.md").exists()
        assert "~~Child~~" in (vault / "Wiki" / "linker.md").read_text()

    def test_delete_recursive_remove_failure_reports_partial_state(self, vault, router, monkeypatch):
        router = self._write_delete_tree(vault)
        real_remove = os.remove
        calls = []

        def flaky_remove(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("cloud sync busy")
            return real_remove(path)

        monkeypatch.setattr(rename.os, "remove", flaky_remove)

        with pytest.raises(rename.PartialApplyError) as exc_info:
            rename.delete_and_clean_links(
                str(vault), "Ideas/Parent.md", router=router, recursive=True
            )

        assert isinstance(exc_info.value.__cause__, OSError)
        message = str(exc_info.value)
        assert "delete set partially applied" in message
        assert "removed ['Ideas/Parent.md']" in message
        assert "failed at Ideas/parent/Child.md" in message
        assert not (vault / "Ideas" / "Parent.md").exists()
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()
        assert "~~Child~~" in (vault / "Wiki" / "linker.md").read_text()

    def test_delete_with_only_temporal_children_proceeds(self, vault, router):
        (vault / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: shaping\n"
            "---\n\n"
            "Parent.\n"
        )
        research = vault / "_Temporal" / "Research"
        research.mkdir(parents=True, exist_ok=True)
        temporal = research / "20260413-research~Child.md"
        temporal.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - ideas/parent\n"
            "parent: ideas/parent\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Temporal.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        rename.delete_and_clean_links(str(vault), "Ideas/Parent.md", router=router)

        assert not (vault / "Ideas" / "Parent.md").exists()
        assert temporal.is_file()

    def test_delete_cyclic_graph_aborts_before_removing_files(self, vault, router):
        router = self._write_delete_tree(vault)
        router["artefact_index"]["ideas/parent"]["parent"] = "ideas/child"

        with pytest.raises(Exception, match="Cyclic descendant chain"):
            rename.delete_and_clean_links(
                str(vault), "Ideas/Parent.md", router=router, recursive=True
            )

        assert (vault / "Ideas" / "Parent.md").is_file()
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()


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
        assert result["new_path"] == "Ideas/+Adopted/my-idea.md"
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

    def test_unarchive_refiles_flat_when_no_parent_confirms_the_owner_chain(
        self, vault, router
    ):
        """Archive placement is not a restoration record without a living parent.

        The recorded ``Brain/`` owner folder is not resurrected: the file
        re-files by current convention, flat under the type root.
        """
        rel = self._make_archived(vault, "_Archive/Ideas/Brain/20260101-my-idea.md")
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert result["new_path"] == "Ideas/+Adopted/my-idea.md"
        assert (vault / "Ideas" / "+Adopted" / "my-idea.md").is_file()

    def test_unarchive_refiles_parentless_temporal_flat_under_the_type_root(
        self, vault, router
    ):
        """A temporal file archived from a legacy month folder restores flat."""
        rel = "_Archive/_Temporal/Research/2026-04/20260413-research~Orphan.md"
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: temporal/research\ntags:\n  - research\n"
            "created: 2026-04-13T09:00:00+10:00\narchiveddate: 2026-05-01\n---\n\nOrphan.\n"
        )

        result = edit.unarchive_artefact(str(vault), router, rel)

        assert result["new_path"] == "_Temporal/Research/20260413-research~Orphan.md"
        assert (vault / result["new_path"]).is_file()

    def test_unarchive_with_unresolvable_parent_refiles_at_the_type_root(
        self, vault, router
    ):
        """A recorded parent the router cannot resolve does not rebuild its chain."""
        rel = "_Archive/_Temporal/Research/project~ghost/20260413-research~Stranded.md"
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: temporal/research\ntags:\n  - project/ghost\n"
            "parent: project/ghost\ncreated: 2026-04-13T09:00:00+10:00\n"
            "archiveddate: 2026-05-01\n---\n\nStranded.\n"
        )

        result = edit.unarchive_artefact(str(vault), router, rel)

        assert result["new_path"] == "_Temporal/Research/20260413-research~Stranded.md"
        assert (vault / result["new_path"]).is_file()
        assert not (vault / "_Temporal" / "Research" / "project~ghost").exists()

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

    def test_recursive_unarchive_reports_uninspected_archive_candidates(
        self, vault, router
    ):
        source = vault / "_Archive" / "Wiki" / "20260101-parent.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "---\ntype: living/wiki\ntags: []\nkey: parent\n"
            "archiveddate: 2026-01-01\n---\n\nParent.\n"
        )
        candidate = vault / "_Archive" / "Wiki" / "20260101-child.md"
        candidate.write_text(
            "---\ntype: living/unknown\ntags: []\nkey: child\n"
            "parent: wiki/parent\narchiveddate: 2026-01-01\n---\n\nChild.\n"
        )

        result = edit.unarchive_artefact(
            str(vault), router, "_Archive/Wiki/20260101-parent.md", recursive=True
        )

        assert len(result["uninspected"]) == 1
        assert result["uninspected"][0]["path"] == "_Archive/Wiki/20260101-child.md"
        assert "Unknown artefact type 'living/unknown'" in result["uninspected"][0]["reason"]
        assert candidate.is_file()

    def test_unarchive_move_failure_reports_metadata_written_context(
        self, vault, router, monkeypatch
    ):
        rel = self._make_archived(vault)

        def fail_rename(*_args, **_kwargs):
            raise PartialApplyError("move set partially applied")

        import rename
        monkeypatch.setattr(rename, "apply_move_and_links", fail_rename)

        with pytest.raises(PartialApplyError, match="unarchive partially applied") as exc_info:
            edit.unarchive_artefact(str(vault), router, rel)

        assert isinstance(exc_info.value.__cause__, PartialApplyError)
        message = str(exc_info.value)
        assert f"metadata files written ['{rel}']" in message
        assert "move failure: move set partially applied" in message

    def test_unarchive_destination_collision_preflights_before_metadata_write(
        self, vault, router
    ):
        rel = self._make_archived(vault)
        (vault / "Ideas" / "+Adopted").mkdir(exist_ok=True)
        (vault / "Ideas" / "+Adopted" / "my-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\n---\n\nExisting.\n"
        )

        with pytest.raises(FileExistsError, match="Destination file already exists"):
            edit.unarchive_artefact(str(vault), router, rel)

        archived_fields, _ = parse_frontmatter((vault / rel).read_text())
        assert "archiveddate" in archived_fields
        assert (vault / "Ideas" / "+Adopted" / "my-idea.md").is_file()
