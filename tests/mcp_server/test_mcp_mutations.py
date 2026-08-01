"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import asyncio
import base64
import contextlib
import json
import os
import subprocess
import tempfile
import threading
import time
import types
from unittest.mock import patch

import pytest

from mcp.types import CallToolResult

import _lifecycle.retrieval_assets as retrieval_assets
import _lifecycle.retrieval_errors as retrieval_errors
import _search.paths as search_paths
import _search.semantic_query as semantic_query
import _semantic.assets as semantic_assets
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
from brain_mcp import _server_artefacts, _server_content, _server_reading, server
import compile_router
import obsidian_cli
import process
import retrieval_embeddings
import workspace_registry
import config as config_mod
from _common import (
    CyclicParentChainError,
    HasDescendantsError,
    MutationLockError,
    PartialApplyError,
    parse_frontmatter,
    vault_mutation_lock,
)
from _common._yaml import dump_mapping_text



from _mcp_helpers import (
    _assert_any_error,
    _assert_error,
    _bump_mtime,
    _extract_create_path,
    _list_result_lines,
    _list_text,
    _progress_payload,
    _result_text,
    _search_result_lines,
    _search_text,
    _write_config_text,
    _write_config_yaml,
)


def _assert_partial_apply_error(result, expected):
    _assert_error(result, expected)
    assert "Unexpected error" not in _result_text(result)
    assert "Traceback" not in _result_text(result)


def _write_parent_child_tree(vault):
    parent = vault / "Ideas" / "Parent.md"
    child = vault / "Wiki" / "ideas~parent" / "Child.md"
    child.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(
        "---\n"
        "type: living/ideas\n"
        "tags: []\n"
        "key: parent\n"
        "status: shaping\n"
        "---\n\n"
        "Parent.\n"
    )
    child.write_text(
        "---\n"
        "type: living/wiki\n"
        "tags:\n"
        "  - ideas/parent\n"
        "key: child\n"
        "parent: ideas/parent\n"
        "---\n\n"
        "Child.\n"
    )
    return parent, child


class TestBrainCreate:
    def test_create_returns_path(self, initialized):
        result = server.brain_create(type="wiki", title="New Page")
        assert _result_text(result).startswith("**Created** living/wiki: ")

    def test_stage_handle_is_consumed_only_after_success(self, initialized):
        staged = server.brain_stage("# Staged\n\nBody from Brain staging.\n")
        handle = staged.structuredContent["handle"]
        staged_path = (
            initialized / ".brain" / "local" / "staging" /
            f"{handle.removeprefix('body:')}.body"
        )
        assert staged_path.is_file()

        failed = server.brain_create(
            type="missing-type", title="Retry Me", body_handle=handle
        )
        _assert_any_error(failed)
        assert staged_path.is_file()

        result = server.brain_create(
            type="wiki", title="Staged Page", body_handle=handle
        )
        path = _extract_create_path(result)
        assert "Body from Brain staging" in (initialized / path).read_text()
        assert not staged_path.exists()
        path = _extract_create_path(result)
        assert path.startswith("Wiki/")

    def test_unused_stage_handle_can_be_discarded(self, initialized):
        staged = server.brain_stage("unused")
        handle = staged.structuredContent["handle"]
        result = server.brain_discard_stage(handle)
        assert result.structuredContent == {"handle": handle, "discarded": True}
        again = server.brain_discard_stage(handle)
        assert again.structuredContent["discarded"] is False

    def test_upload_attachment_writes_binary_content_and_returns_embed(self, initialized):
        content = b"\x89PNG\r\n\x1a\nbinary"
        encoded = base64.b64encode(content).decode("ascii")

        result = server.brain_upload_attachment("mcp-assets", "diagram.png", encoded)

        assert result.structuredContent["created"] is True
        assert result.structuredContent["path"] == "_Assets/Attachments/mcp-assets/diagram.png"
        assert result.structuredContent["embed"] == "![[_Assets/Attachments/mcp-assets/diagram.png]]"
        assert (initialized / result.structuredContent["path"]).read_bytes() == content

        retried = server.brain_upload_attachment("mcp-assets", "diagram.png", encoded)
        assert retried.structuredContent["created"] is False
        assert "Attachment already present" in _result_text(retried)

    def test_upload_attachment_rejects_invalid_base64_and_collisions(self, initialized):
        with patch.object(server, "_serialize_mutation") as serialize:
            invalid = server.brain_upload_attachment(
                "mcp-assets", "diagram.svg", "not base64"
            )
        _assert_error(invalid, "not valid base64")
        serialize.assert_not_called()

        first = base64.b64encode(b"first").decode("ascii")
        second = base64.b64encode(b"second").decode("ascii")
        assert not server.brain_upload_attachment(
            "mcp-assets", "diagram.svg", first
        ).isError

        collision = server.brain_upload_attachment(
            "mcp-assets", "diagram.svg", second
        )
        _assert_error(collision, "already exists with different content")

    def test_upload_attachment_resolves_canonical_artefact_destination(self, initialized):
        artefact = initialized / "Ideas" / "Owned.md"
        artefact.write_text(
            "---\ntype: living/ideas\ntags: []\nkey: owned\nstatus: shaping\n---\n\n# Owned\n"
        )
        server._set_router(compile_router.compile(str(initialized)))
        encoded = base64.b64encode(b"asset").decode("ascii")

        result = server.brain_upload_attachment("ideas~owned", "diagram.svg", encoded)

        assert not result.isError
        assert result.structuredContent["destination"] == {
            "kind": "artefact",
            "key": "ideas/owned",
            "folder": "ideas~owned",
        }
        assert result.structuredContent["path"] == (
            "_Assets/Attachments/ideas~owned/diagram.svg"
        )

    def test_upload_attachment_rejects_unknown_artefact_destination(self, initialized):
        with patch.object(
            server.attachment_upload,
            "decode_attachment_base64",
            side_effect=AssertionError("invalid destination must fail before decode"),
        ):
            result = server.brain_upload_attachment(
                "ideas/missing",
                "diagram.svg",
                base64.b64encode(b"asset").decode("ascii"),
            )

        _assert_error(result, "no active living artefact")

    def test_create_file_on_disk(self, initialized):
        result = server.brain_create(type="wiki", title="Disk Test")
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        assert os.path.isfile(abs_path)

    def test_create_correct_frontmatter(self, initialized):
        result = server.brain_create(type="wiki", title="FM Test")
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        with open(abs_path) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"

    def test_create_unknown_type_error(self, initialized):
        result = server.brain_create(type="nonexistent", title="Test")
        _assert_any_error(result)

    def test_create_temporal_subfolder(self, initialized):
        result = server.brain_create(type="logs", title="My Session")
        path = _extract_create_path(result)
        assert "_Temporal/Logs/" in path
        import re
        # Path should contain yyyy-mm subfolder
        assert re.search(r"\d{4}-\d{2}", path)

    def test_create_parented_temporal_files_under_owner_scope(self, initialized):
        parent_result = server.brain_create(type="wiki", title="Parent Page", key="parent-page")
        assert _result_text(parent_result).startswith("**Created** living/wiki: ")

        result = server.brain_create(
            type="logs", title="Scoped Session", parent="wiki/parent-page"
        )

        path = _extract_create_path(result)
        assert path.startswith("_Temporal/Logs/wiki~parent-page/")
        with open(os.path.join(str(initialized), path)) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["parent"] == "wiki/parent-page"

    def test_create_body_override(self, initialized):
        result = server.brain_create(
            type="wiki", title="Custom Body", body="# Custom\n\nMy content.\n"
        )
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        with open(abs_path) as f:
            content = f.read()
        assert "My content." in content

    def test_create_frontmatter_override(self, initialized):
        result = server.brain_create(
            type="ideas", title="Override Test",
            frontmatter={"status": "shaping"}
        )
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        with open(abs_path) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "shaping"

    def test_create_rejects_body_with_frontmatter_block(self, initialized):
        result = server.brain_create(
            type="wiki",
            title="Bad Body",
            body="---\nstatus: shaping\n---\n\n# Body\n",
        )
        _assert_error(result, "must not start with a frontmatter block")

    def test_create_living_with_explicit_key(self, initialized):
        result = server.brain_create(type="wiki", title="Slugged Page", key="slugged-page")
        path = _extract_create_path(result)
        with open(os.path.join(str(initialized), path)) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["key"] == "slugged-page"

    def test_create_with_canonical_parent(self, initialized):
        parent_result = server.brain_create(type="wiki", title="Parent Page", key="parent-page")
        assert _result_text(parent_result).startswith("**Created** living/wiki: ")
        child_result = server.brain_create(
            type="ideas", title="Child Idea", parent="wiki/parent-page"
        )
        child_path = _extract_create_path(child_result)
        assert child_path.startswith("Ideas/wiki~parent-page/")
        with open(os.path.join(str(initialized), child_path)) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["parent"] == "wiki/parent-page"

    def test_create_skill_resource(self, initialized):
        result = server.brain_create(
            resource="skill", name="test-skill",
            body="# Test Skill\n\nDo something.\n",
        )
        assert "**Created** skill:" in _result_text(result)
        path = _extract_create_path(result)
        assert path == "_Config/Skills/test-skill/SKILL.md"
        assert os.path.isfile(os.path.join(str(initialized), path))

    def test_create_memory_resource(self, initialized):
        result = server.brain_create(
            resource="memory", name="test-memory",
            body="Remember this.\n",
            frontmatter={"triggers": ["keyword"]},
        )
        assert "**Created** memory:" in _result_text(result)
        path = _extract_create_path(result)
        assert path == "_Config/Memories/test-memory.md"

    def test_create_style_resource(self, initialized):
        result = server.brain_create(
            resource="style", name="test-style",
            body="# Test Style\n\nWrite this way.\n",
        )
        assert "**Created** style:" in _result_text(result)
        path = _extract_create_path(result)
        assert path == "_Config/Styles/test-style.md"

    def test_create_template_resource_preserves_full_document(self, initialized):
        body = "---\ntype: living/wiki\ntags: []\n---\n\n# Template Body\n"
        result = server.brain_create(
            resource="template", name="wiki", body=body,
        )
        assert "**Created** template:" in _result_text(result)
        path = _extract_create_path(result)
        assert (initialized / path).read_text() == body

    def test_create_template_resource_rejects_separate_frontmatter(self, initialized):
        result = server.brain_create(
            resource="template",
            name="wiki",
            body="---\ntype: living/wiki\ntags: []\n---\n\n# Template Body\n",
            frontmatter={"audience": "devs"},
        )
        _assert_error(result, "Pass template frontmatter inside body")

    def test_create_resource_not_creatable(self, initialized):
        result = server.brain_create(
            resource="workspace", name="ws", body="content",
        )
        _assert_error(result, "not creatable")

    def test_create_resource_rejects_artefact_field_for_skill(self, initialized):
        """brain_create rejects artefact-only fields when resource is not artefact."""
        result = server.brain_create(
            resource="skill", name="my-skill", body="content", type="wiki",
        )
        _assert_error(result, "does not accept top-level field 'type'")

    def test_create_artefact_rejects_non_artefact_field(self, initialized):
        """brain_create rejects non-artefact fields when resource is artefact."""
        result = server.brain_create(
            resource="artefact", type="wiki", title="My Page", name="spurious",
        )
        _assert_error(result, "does not accept top-level field 'name'")

    def test_create_skill_requires_body(self, initialized):
        """brain_create(resource='skill') still errors when body is absent (handler enforcement)."""
        result = server.brain_create(resource="skill", name="my-skill")
        _assert_error(result, "requires body")

    def test_create_artefact_requires_type(self, initialized):
        result = server.brain_create(title="No Type")
        _assert_error(result, "requires top-level field 'type'")

    def test_create_artefact_requires_title(self, initialized):
        result = server.brain_create(type="wiki")
        _assert_error(result, "requires top-level field 'title'")

    def test_create_error_preserves_caller_owned_temp_body_file(self, initialized):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("temporary body\n")
            temp_path = f.name

        try:
            result = server.brain_create(
                title="No Type",
                body_file=temp_path,
            )
            _assert_error(result, "requires top-level field 'type'")
            assert os.path.exists(temp_path), "caller-owned body_file must remain retryable"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestBrainCreateFixLinks:
    def test_fix_links_handler_contract_requires_prepared_index(self):
        with pytest.raises(ValueError, match="requires a prepared filesystem"):
            _server_artefacts.require_prepared_fix_links_index(
                "brain_create", True, None
            )

    def test_mcp_create_with_fix_links_prepares_index_inside_mutation(
        self, initialized, monkeypatch
    ):
        import fix_links as _fix_links

        called = {"prepared": 0, "script": 0}
        in_mutation = {"value": False}
        original_prepare = _server_artefacts.build_vault_file_index
        original_script = _fix_links.build_vault_file_index

        def prepare_spy(*args, **kwargs):
            assert in_mutation["value"] is True
            called["prepared"] += 1
            return original_prepare(*args, **kwargs)

        def script_spy(*args, **kwargs):
            called["script"] += 1
            return original_script(*args, **kwargs)

        @contextlib.contextmanager
        def fake_serialize(_label):
            in_mutation["value"] = True
            try:
                yield
            finally:
                in_mutation["value"] = False

        monkeypatch.setattr(
            _server_artefacts, "build_vault_file_index", prepare_spy
        )
        monkeypatch.setattr(_fix_links, "build_vault_file_index", script_spy)
        monkeypatch.setattr(server, "_serialize_mutation", fake_serialize)

        result = server.brain_create(
            type="wiki",
            title="Self Link Page",
            body="See [[Self Link Page]].\n",
            fix_links=True,
        )

        assert "Error" not in _result_text(result), f"brain_create unexpectedly returned error: {result}"
        assert called == {"prepared": 1, "script": 0}
        assert "Broken wikilinks" not in _result_text(result)
        assert "Resolvable wikilinks" not in _result_text(result)

    def test_mcp_create_with_fix_links_does_not_require_search_index(
        self, initialized
    ):
        server._index = None

        result = server.brain_create(
            type="wiki",
            title="Needs Index",
            body="See [[Needs Index]].\n",
            fix_links=True,
        )

        assert "Error" not in _result_text(result), f"brain_create unexpectedly returned error: {result}"
        assert "Broken wikilinks" not in _result_text(result)
        assert "Resolvable wikilinks" not in _result_text(result)


class TestBrainEdit:
    def test_brain_edit_schema_exposes_scope_and_resource_enums(self):
        tool = asyncio.run(server.mcp.list_tools())
        brain_edit_tool = next(item for item in tool if item.name == "brain_edit")
        schema = brain_edit_tool.inputSchema
        request = schema["$defs"]["_BrainEditRequest"]
        assert set(request["properties"]["subject"]["discriminator"]["mapping"]) == {
            "artefact",
            "skill",
            "memory",
            "style",
            "template",
        }
        assert set(request["properties"]["mutation"]["discriminator"]["mapping"]) == {
            "append",
            "delete_section",
            "edit",
            "prepend",
            "replace_text",
        }
        scope = schema["$defs"]["_BrainEditMutation"]["properties"]["scope"]
        assert scope["anyOf"] == [
            {
                "enum": ["section", "intro", "body", "heading", "header"],
                "type": "string",
            },
            {"type": "null"},
        ]

    def test_edit_replaces_body(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New Body\n\nReplaced.\n",
            target=":body",
            scope="section",
        )
        assert _result_text(result) == "**Edited:** Wiki/brain-overview-abc123.md (body section)"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Replaced." in content

    def test_edit_preserves_frontmatter(self, initialized):
        server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New\n",
            target=":body",
            scope="section",
        )
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"

    def test_edit_rejects_body_with_frontmatter_block(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="---\nstatus: shaping\n---\n\n# Body\n",
            target=":body",
            scope="section",
        )
        _assert_error(result, "must not start with a frontmatter block")

    def test_replace_text_replaces_unique_match(self, initialized):
        result = server.brain_edit(
            operation="replace_text",
            path="Wiki/brain-overview-abc123.md",
            old_text="personal knowledge management",
            new_text="shared knowledge management",
        )
        assert "**Replaced text in:**" in _result_text(result)
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "shared knowledge management" in content

    def test_replace_text_ambiguous_match_is_actionable(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nRepeated. Repeated.\n"
        )
        result = server.brain_edit(
            operation="replace_text",
            path="Wiki/brain-overview-abc123.md",
            old_text="Repeated.",
            new_text="Changed.",
        )
        _assert_error(result, "found 2 exact matches")

    def test_edit_merges_frontmatter(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New\n",
            frontmatter={"summary": "Updated"},
            target=":body",
            scope="section",
        )
        assert "Error" not in _result_text(result)
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["summary"] == "Updated"

    def test_edit_rejects_lifecycle_owned_status(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"status": "archived"},
        )
        _assert_error(result, "Use brain_set_status")

    def test_edit_parent_change_to_descendant_surfaces_actionably(self, initialized):
        parent, child = _write_parent_child_tree(initialized)
        router = compile_router.compile(str(initialized))
        server._set_router(router)
        parent_before = parent.read_text()
        child_before = child.read_text()

        with patch.object(server, "_ensure_router_fresh"):
            result = server.brain_reparent(
                path="Ideas/Parent.md",
                parent="wiki/child",
            )

        _assert_error(result, "Invalid living parent chain")
        _assert_error(result, "child of descendant wiki/child")
        assert "Run check/doctor" not in _result_text(result)
        assert "reconcile the broken or cyclic parent metadata" not in _result_text(result)
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)
        assert parent.read_text() == parent_before
        assert child.read_text() == child_before

    def test_explicit_lifecycle_tools_return_structured_changes(self, initialized):
        parent, child = _write_parent_child_tree(initialized)
        server._set_router(compile_router.compile(str(initialized)))

        reparented = server.brain_reparent(path="Wiki/ideas~parent/Child.md", parent=None)
        assert reparented.structuredContent["lifecycle_field"] == "parent"
        assert reparented.structuredContent["path"] == "Wiki/Child.md"

        created = server.brain_create(type="ideas", title="Lifecycle Example")
        idea_path = created.structuredContent["path"]
        server._set_router(compile_router.compile(str(initialized)))
        keyed = server.brain_set_key(path=idea_path, key="lifecycle-example-renamed")
        assert keyed.structuredContent["new_value"] == "lifecycle-example-renamed"

        server._set_router(compile_router.compile(str(initialized)))
        status = server.brain_set_status(
            path=keyed.structuredContent["path"], status="adopted"
        )
        assert status.structuredContent["path"].startswith("Ideas/+Adopted/")
        assert status.structuredContent["lifecycle_field"] == "status"

    def test_naming_field_handler_renames_and_generic_edit_rejects(self, initialized):
        source = initialized / "Wiki" / "old-Brain Overview.md"
        original = initialized / "Wiki" / "brain-overview-abc123.md"
        original.rename(source)
        source.write_text(
            "---\ntype: living/wiki\ntags: []\ncode: old\n---\n\n# Brain Overview\n"
        )
        router = compile_router.compile(str(initialized))
        wiki = next(item for item in router["artefacts"] if item["folder"] == "Wiki")
        wiki["naming"] = {
            "pattern": "{Code}-{Title}.md",
            "folder": "Wiki/",
            "rules": [{
                "match_field": None,
                "match_values": None,
                "pattern": "{Code}-{Title}.md",
                "date_source": None,
            }],
            "placeholders": [{
                "name": "Code",
                "field": "code",
                "required_when_field": None,
                "required_values": None,
                "regex": None,
            }],
        }
        server._set_router(router)

        rejected = server.brain_edit(
            operation="edit",
            path="Wiki/old-Brain Overview.md",
            frontmatter={"code": "new"},
        )
        _assert_error(rejected, "Use brain_set_naming_field")

        changed = server.brain_set_naming_field(
            path="Wiki/old-Brain Overview.md", field="code", value="new"
        )
        assert changed.structuredContent["path"] == "Wiki/new-Brain Overview.md"
        assert (initialized / "Wiki" / "new-Brain Overview.md").is_file()

    def test_lifecycle_lock_contention_is_an_expected_error(self, initialized, monkeypatch):
        monkeypatch.setattr(
            server,
            "vault_mutation_lock",
            lambda root: vault_mutation_lock(root, timeout=0.05),
        )
        with vault_mutation_lock(initialized):
            with pytest.raises(ValueError, match="Vault is busy; retry") as exc_info:
                with server._serialize_mutation("test"):
                    pass
            assert isinstance(exc_info.value.__cause__, MutationLockError)
            result = server.brain_set_status(
                path="Wiki/brain-overview-abc123.md", status="active"
            )

        _assert_error(result, "Vault is busy; retry the mutation")
        assert "Unexpected error" not in _result_text(result)

    def test_mutation_lock_error_from_body_is_not_relabelled(self, initialized):
        with pytest.raises(MutationLockError, match="body failure"):
            with server._serialize_mutation("test"):
                raise MutationLockError("body failure")

    def test_append_works(self, initialized):
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            body="\n\nAppended text.\n",
            target=":body",
            scope="section",
        )
        assert _result_text(result) == "**Appended:** Wiki/brain-overview-abc123.md (body section)"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Appended text." in content
        assert "Brain Overview" in content  # original preserved

    def test_invalid_path_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Unknown/file.md",
            body="test",
            target=":body",
            scope="section",
        )
        _assert_any_error(result)

    def test_file_not_found(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/nonexistent.md",
            body="test",
            target=":body",
            scope="section",
        )
        _assert_any_error(result)

    def test_unknown_operation(self, initialized):
        result = server.brain_edit(
            operation="bogus",
            path="Wiki/brain-overview-abc123.md",
            body="test"
        )
        _assert_any_error(result)

    def test_prepend_works(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            body="Prepended text.\n\n",
            target=":body",
            scope="section",
        )
        assert _result_text(result) == "**Prepended:** Wiki/brain-overview-abc123.md (body section)"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Prepended text." in content
        assert "Brain Overview" in content  # original preserved

    def test_noop_edit_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_noop_append_rejected(self, initialized):
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_noop_append_with_entire_body_target_rejected(self, initialized):
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":entire_body",
        )
        _assert_error(result, "target=':entire_body' is no longer valid")

    def test_noop_prepend_rejected(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_noop_prepend_with_entire_body_target_rejected(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":entire_body",
        )
        _assert_error(result, "target=':entire_body' is no longer valid")

    def test_edit_with_target_and_empty_body_allowed(self, initialized):
        """edit with target + empty body clears that section — not a no-op."""
        # Write a file with sections
        from _common import parse_frontmatter
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="Alpha",
            scope="body",
        )
        assert "**Edited:**" in _result_text(result)

    def test_frontmatter_only_append_allowed(self, initialized):
        """append with just frontmatter changes is valid, not a no-op."""
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"aliases": ["overview"]},
        )
        assert "**Appended:**" in _result_text(result)

    def test_targeted_frontmatter_only_append_omits_structural_summary(self, initialized):
        from _common import parse_frontmatter

        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: [overview]\nstatus: active\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"tags": ["server-tag"]},
            target="## Alpha",
            scope="body",
        )
        assert _result_text(result) == "**Appended:** Wiki/brain-overview-abc123.md"
        fields, body = parse_frontmatter(path.read_text())
        assert "server-tag" in fields["tags"]
        assert body == "## Alpha\n\nBody.\n"

    def test_targeted_frontmatter_only_prepend_omits_structural_summary(self, initialized):
        from _common import parse_frontmatter

        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: [overview]\nstatus: active\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"tags": ["server-tag"]},
            target="## Alpha",
            scope="body",
        )
        assert _result_text(result) == "**Prepended:** Wiki/brain-overview-abc123.md"
        fields, body = parse_frontmatter(path.read_text())
        assert "server-tag" in fields["tags"]
        assert body == "## Alpha\n\nBody.\n"

    def test_append_frontmatter_extends_list(self, initialized):
        """append should extend list fields, not overwrite."""
        from _common import parse_frontmatter
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"tags": ["new-tag"]},
        )
        assert "**Appended:**" in _result_text(result)
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert "new-tag" in fields["tags"]
        # Original tags should still be there
        assert len(fields["tags"]) > 1

    def test_delete_section_removes_heading_and_content(self, initialized):
        """delete_section removes the target heading and its content."""
        path = "Wiki/brain-overview-abc123.md"
        # Set up a file with multiple sections
        server.brain_edit(
            operation="edit",
            path=path,
            target=":body",
            scope="section",
            body="## Intro\n\nIntro content.\n\n## Notes\n\nNotes content.\n\n## Summary\n\nSummary content.\n"
        )
        result = server.brain_edit(
            operation="delete_section",
            path=path,
            target="Notes"
        )
        assert "Error" not in _result_text(result)
        content = (initialized / path).read_text()
        assert "## Notes" not in content
        assert "Notes content." not in content
        assert "## Intro" in content
        assert "## Summary" in content

    def test_delete_section_requires_target(self, initialized):
        """delete_section with no target returns an error."""
        result = server.brain_edit(
            operation="delete_section",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "target")

    def test_delete_section_missing_heading_returns_error(self, initialized):
        """delete_section with a non-existent heading returns an error."""
        result = server.brain_edit(
            operation="delete_section",
            path="Wiki/brain-overview-abc123.md",
            target="Nonexistent Heading"
        )
        _assert_error(result, "not found")

    def test_targeted_edit_mentions_resolved_scope(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="Replaced.\n",
            target="Beta",
            scope="body",
        )
        assert _result_text(result) == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading body: ## Beta)"
        )

    def test_heading_intro_without_child_preserves_following_siblings(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha body.\n\n"
            "## Beta\n\nBeta body.\n\n"
            "## Gamma\n\nGamma body.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="Replacement.\n",
            target="## Alpha",
            scope="intro",
        )
        assert _result_text(result) == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading intro: ## Alpha)"
        )
        content = path.read_text()
        assert "Alpha body." not in content
        assert "Replacement.\n## Beta" in content
        assert "## Beta" in content
        assert "Beta body." in content
        assert "## Gamma" in content
        assert "Gamma body." in content

    def test_append_heading_intro_without_child_preserves_following_siblings(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha body.\n\n"
            "## Beta\n\nBeta body.\n\n"
            "## Gamma\n\nGamma body.\n"
        )
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            body="Appended.\n",
            target="## Alpha",
            scope="intro",
        )
        assert _result_text(result) == (
            "**Appended:** Wiki/brain-overview-abc123.md "
            "(heading intro: ## Alpha)"
        )
        content = path.read_text()
        assert "Alpha body.\n\nAppended.\n## Beta" in content
        assert "## Beta" in content
        assert "Beta body." in content
        assert "## Gamma" in content
        assert "Gamma body." in content

    def test_body_section_edit_returns_resolved_summary(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "One.\n\nTwo.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="Replacement.\n",
        )
        assert _result_text(result) == "**Edited:** Wiki/brain-overview-abc123.md (body section)"

    def test_body_target_response_uses_scope_not_context(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Intro text.\n\n## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="Replacement.\n",
        )
        assert _result_text(result) == "**Edited:** Wiki/brain-overview-abc123.md (body section)"

    def test_body_section_append_and_prepend_work_explicitly(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nBody.\n"
        )
        prepend_result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="Before.\n\n",
        )
        append_result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="\nAfter.\n",
        )
        assert _result_text(prepend_result) == "**Prepended:** Wiki/brain-overview-abc123.md (body section)"
        assert _result_text(append_result) == "**Appended:** Wiki/brain-overview-abc123.md (body section)"
        content = path.read_text()
        assert "Before." in content
        assert "After." in content

    def test_body_intro_replaces_only_heading_defined_intro(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Intro text.\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
            "\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="intro",
            body="Updated intro.\n",
        )
        assert _result_text(result) == "**Edited:** Wiki/brain-overview-abc123.md (body intro)"
        content = path.read_text()
        assert "Updated intro.\n## Alpha" in content
        assert "Intro text." not in content
        assert "> [!note] Status" not in content
        assert "## Alpha" in content
        assert "Alpha content." in content

    def test_body_intro_inserts_before_heading_first_doc(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="intro",
            body="Lead text.\n",
        )
        assert _result_text(result) == "**Edited:** Wiki/brain-overview-abc123.md (body intro)"
        content = path.read_text()
        assert "Lead text.\n## Alpha" in content

    def test_body_intro_replaces_whole_body_without_headings(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="intro",
            body="Lead text.\n",
        )
        assert _result_text(result) == "**Edited:** Wiki/brain-overview-abc123.md (body intro)"
        content = path.read_text()
        from _common import parse_frontmatter
        _fields, body = parse_frontmatter(content)
        assert body == "Lead text.\n"

    def test_body_target_requires_scope_for_mutations(self, initialized):
        edit_result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            body="Replacement.\n",
        )
        _assert_error(edit_result, "requires scope")

        append_result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            body="Extra.\n",
        )
        _assert_error(append_result, "requires scope")

        prepend_result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            body="Extra.\n",
        )
        _assert_error(prepend_result, "requires scope")

        delete_result = server.brain_edit(
            operation="delete_section",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
        )
        _assert_error(delete_result, "delete_section requires a heading or callout target")

    def test_invalid_scope_error_lists_meanings(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
        )
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target="[!note] Implementation status",
            scope="header",
            body="> More status content.\n",
        )
        _assert_error(result, "scope='header' is not valid for append on callout targets")
        _assert_error(result, "scope='body' -> the callout body")
        _assert_error(result, "scope='section' -> the whole callout")

    def test_invalid_scope_error_preserves_caller_owned_temp_body_file(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("> More status content.\n")
            temp_path = f.name

        try:
            result = server.brain_edit(
                operation="append",
                path="Wiki/brain-overview-abc123.md",
                target="[!note] Implementation status",
                scope="header",
                body_file=temp_path,
            )
            _assert_error(result, "scope='header' is not valid for append on callout targets")
            assert os.path.exists(temp_path), "caller-owned body_file must remain retryable"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_invalid_scope_error_preflights_before_body_file_read(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
        )
        with patch(
            "_staging.resolve_body_file",
            side_effect=AssertionError("resolve_body_file should not be called"),
        ):
            result = server.brain_edit(
                operation="append",
                path="Wiki/brain-overview-abc123.md",
                target="[!note] Implementation status",
                scope="header",
                body_file="/tmp/should-not-be-read.md",
            )
        _assert_error(result, "scope='header' is not valid for append on callout targets")

    def test_legacy_body_before_first_heading_target_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body_before_first_heading",
            body="Replacement.\n",
        )
        _assert_error(result, "target=':body_before_first_heading' is no longer valid")

    def test_legacy_body_preamble_rejected_for_append_and_prepend(self, initialized):
        append_result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":body_preamble",
            body="Extra.\n",
        )
        _assert_error(append_result, "Use target=':body' with scope='intro'")

        prepend_result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":body_preamble",
            body="Extra.\n",
        )
        _assert_error(prepend_result, "Use target=':body' with scope='intro'")

    def test_targeted_edit_heading_body_rejects_heading_wrapper(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="## Alpha\n\nUpdated alpha.\n",
        )
        _assert_error(result, "Use scope='section'")

    def test_targeted_edit_rejects_structural_change_without_section_mode(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="# Alpha\n\nPromoted.\n",
        )
        _assert_error(result, "Use scope='section'")

    def test_targeted_edit_allows_nested_heading_content(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="### Overview\n\nPromoted content.\n",
        )
        assert "Error" not in _result_text(result)
        content = path.read_text()
        assert "## Alpha" in content
        assert "### Overview" in content

    def test_targeted_edit_allows_callout_content(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="> [!note] Fresh note\n> Promoted content.\n",
        )
        assert "Error" not in _result_text(result)
        content = path.read_text()
        assert "## Alpha" in content
        assert "[!note] Fresh note" in content

    def test_targeted_edit_section_mode_replaces_heading(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="section",
            body="# Renamed Alpha\n\nUpdated alpha.\n",
        )
        assert _result_text(result) == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading section: ## Alpha)"
        )
        content = path.read_text()
        assert "## Alpha" not in content
        assert "# Renamed Alpha" in content
        assert "## Beta" in content

    def test_callout_header_response_mentions_resolved_scope(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Status\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="[!note] Implementation status",
            scope="header",
            body="> [!warning] Updated status\n",
        )
        assert _result_text(result) == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(callout header: [!note] Implementation status)"
        )
        content = path.read_text()
        assert "[!warning] Updated status" in content

    def test_selector_disambiguates_duplicate_targets(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst notes.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond notes.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
            scope="body",
            body="Selected notes.\n",
        )
        assert _result_text(result) == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading body: # API [2] > ## Notes)"
        )
        content = path.read_text()
        assert "First notes." in content
        assert "Selected notes." in content
        assert "Second notes." not in content

    def test_ambiguous_target_reports_candidates(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst notes.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond notes.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Notes",
            scope="body",
            body="Selected notes.\n",
        )
        _assert_error(result, "Ambiguous target '## Notes'")
        _assert_error(result, "Candidates:")

    def test_edit_skill_resource(self, initialized):
        # Create a skill first
        skill_dir = initialized / "_Config" / "Skills" / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n---\n\n# Test Skill\n\nOriginal.\n"
        )
        result = server.brain_edit(
            resource="skill", operation="edit", name="test-skill",
            body="# Updated Skill\n\nNew content.\n",
            target=":body",
            scope="section",
        )
        assert "**Edited:**" in _result_text(result)
        assert "_Config/Skills/test-skill/SKILL.md" in _result_text(result)
        content = (skill_dir / "SKILL.md").read_text()
        assert "New content." in content

    def test_edit_memory_resource(self, initialized):
        mem_dir = initialized / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - kw1\n---\n\nOriginal.\n"
        )
        result = server.brain_edit(
            resource="memory", operation="append", name="test-memory",
            body="\nAppended.\n",
            target=":body",
            scope="section",
        )
        assert "**Appended:**" in _result_text(result)

    def test_edit_memory_trigger_is_immediately_readable(self, initialized):
        mem_dir = initialized / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - kw1\n---\n\nOriginal.\n"
        )
        server.startup(vault_root=str(initialized))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        result = server.brain_edit(
            resource="memory",
            operation="append",
            name="test-memory",
            frontmatter={"triggers": ["new-trigger"]},
        )

        assert "**Appended:**" in _result_text(result)
        read_result = server.brain_read("memory", name="new-trigger")
        assert "Original." in read_result

    def test_edit_memory_does_not_pollute_artefact_search(self, initialized):
        mem_dir = initialized / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - kw1\n---\n\nOriginal.\n"
        )
        server.startup(vault_root=str(initialized))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        baseline = _search_text(server.brain_search("xenocrypticmemorytoken"))
        assert "0 results" in baseline

        result = server.brain_edit(
            resource="memory",
            operation="append",
            name="test-memory",
            body="\nContains xenocrypticmemorytoken.\n",
            target=":body",
            scope="section",
        )

        assert "**Appended:**" in _result_text(result)
        search_result = _search_text(server.brain_search("xenocrypticmemorytoken"))
        assert "0 results" in search_result
        assert "_Config/Memories/test-memory.md" not in search_result

    def test_edit_resource_not_editable(self, initialized):
        result = server.brain_edit(
            resource="workspace", operation="edit", name="ws",
            body="content",
        )
        _assert_error(result, "not supported by brain_edit")

    def test_edit_artefact_requires_path(self, initialized):
        result = server.brain_edit(
            operation="edit", body="content",
        )
        _assert_error(result, "requires top-level field 'path'")

    def test_edit_artefact_rejects_name_as_extra(self, initialized):
        """brain_edit rejects artefact-only combo when 'name' (non-artefact field) is passed."""
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            name="spurious-name",
            frontmatter={"status": "active"},
        )
        _assert_error(result, "does not accept top-level field 'name'")

    def test_edit_skill_rejects_path_as_extra(self, initialized):
        """brain_edit rejects skill+op combo when 'path' (artefact-only field) is passed."""
        result = server.brain_edit(
            operation="edit",
            resource="skill",
            name="my-skill",
            path="Wiki/some-artefact.md",
            frontmatter={"status": "active"},
        )
        _assert_error(result, "does not accept top-level field 'path'")

    def test_edit_skill_rejects_fix_links_as_extra(self, initialized):
        """brain_edit rejects fix_links for non-artefact resource."""
        result = server.brain_edit(
            operation="edit",
            resource="skill",
            name="my-skill",
            fix_links=True,
            frontmatter={"status": "active"},
        )
        _assert_error(result, "does not accept top-level field 'fix_links'")

    def test_edit_error_preserves_caller_owned_temp_body_file(self, initialized):
        """Spec validation never deletes a caller-owned body file."""
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("temporary body\n")
            temp_path = f.name

        try:
            result = server.brain_edit(
                operation="edit",
                body_file=temp_path,
            )
            _assert_error(result, "requires top-level field 'path'")
            assert os.path.exists(temp_path), "caller-owned body_file must remain retryable"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestBrainEditFixLinksIndex:
    """Regression guards for MCP mutation-time wikilink index preparation."""

    def test_mcp_edit_with_fix_links_skips_index_for_link_free_body(
        self, initialized, monkeypatch
    ):
        """brain_edit avoids a full vault scan when no wikilinks need checking."""
        import fix_links as _fix_links

        called = {"prepared": 0, "script": 0}
        in_mutation = {"value": False}
        original_prepare = _server_artefacts.build_vault_file_index
        original_script = _fix_links.build_vault_file_index

        def prepare_spy(*args, **kwargs):
            assert in_mutation["value"] is True
            called["prepared"] += 1
            return original_prepare(*args, **kwargs)

        def script_spy(*args, **kwargs):
            called["script"] += 1
            return original_script(*args, **kwargs)

        @contextlib.contextmanager
        def fake_serialize(_label):
            in_mutation["value"] = True
            try:
                yield
            finally:
                in_mutation["value"] = False

        monkeypatch.setattr(
            _server_artefacts, "build_vault_file_index", prepare_spy
        )
        monkeypatch.setattr(_fix_links, "build_vault_file_index", script_spy)
        monkeypatch.setattr(server, "_serialize_mutation", fake_serialize)

        assert server._index is not None, "fixture must populate state.index before test"

        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="No links here.\n",
            target=":body",
            scope="section",
            fix_links=True,
        )

        assert "Error" not in _result_text(result), f"brain_edit unexpectedly returned error: {result}"
        assert called == {"prepared": 0, "script": 0}

    def test_mcp_edit_with_fix_links_uses_filesystem_index_for_external_targets(
        self, initialized
    ):
        """Externally-created files missing from state.index still resolve."""
        vault = initialized
        design_titles = [
            "Cargo-Barbican Policy Init Design — Explicit Policy Scaffolding",
            "Cargo-Barbican Inventory Command Design — Dependency Inventory Audit",
            "Cargo-Barbican Gatehouse Posture Design — Holistic Readiness Report",
        ]
        target_dir = vault / "Designs" / "cargo-barbican"
        target_dir.mkdir(parents=True, exist_ok=True)
        for title in design_titles:
            (target_dir / f"{title}.md").write_text(
                "---\ntype: living/design\ntags: []\nstatus: shaping\n---\n\n"
                f"# {title}\n"
            )

        assert not any(
            doc["path"].startswith("Designs/cargo-barbican/")
            for doc in server._index["documents"]
        )

        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="\n".join(f"- [[{title}]]" for title in design_titles),
            target=":body",
            scope="section",
            fix_links=True,
        )

        assert "Error" not in _result_text(result), f"brain_edit unexpectedly returned error: {result}"
        assert "Broken wikilinks" not in _result_text(result)
        assert "Resolvable wikilinks" not in _result_text(result)

    def test_mcp_edit_with_fix_links_flushes_pending_index_updates(self, initialized):
        server.brain_create(type="wiki", title="Fresh Target")

        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="See [[Fresh Target]].\n",
            target=":body",
            scope="section",
            fix_links=True,
        )

        assert "Error" not in _result_text(result), f"brain_edit unexpectedly returned error: {result}"
        assert "Broken wikilinks" not in _result_text(result)
        assert "Resolvable wikilinks" not in _result_text(result)

    def test_mcp_edit_with_fix_links_does_not_require_search_index(
        self, initialized
    ):
        server._index = None

        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="No links here.\n",
            target=":body",
            scope="section",
            fix_links=True,
        )

        assert "Error" not in _result_text(result), f"brain_edit unexpectedly returned error: {result}"
        assert "Broken wikilinks" not in _result_text(result)
        assert "Resolvable wikilinks" not in _result_text(result)

    def test_brain_edit_partial_apply_marks_dirty_and_preserves_context(
        self, initialized, monkeypatch
    ):
        (initialized / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: shaping\n"
            "---\n\n"
            "# Parent\n"
        )
        child_dir = initialized / "Wiki" / "ideas~parent"
        child_dir.mkdir(parents=True)
        (child_dir / "Child.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - ideas/parent\n"
            "key: child\n"
            "parent: ideas/parent\n"
            "---\n\n"
            "# Child\n"
        )
        server._set_router(compile_router.compile(str(initialized)))

        def fail_move(*_args, **_kwargs):
            raise PartialApplyError(
                "move set partially applied — links already rewritten; disk full"
            )

        monkeypatch.setattr(server.edit, "move_and_update_links", fail_move)
        server._router_dirty = False
        server._index_dirty = False

        result = server.brain_set_key(
            path="Ideas/Parent.md",
            key="parent2",
        )

        _assert_partial_apply_error(result, "ownership mutation partially applied")
        _assert_error(result, "disk full")
        assert server._router_dirty is True
        assert server._index_dirty is True
        fields, _ = parse_frontmatter((initialized / "Ideas" / "Parent.md").read_text())
        assert fields["key"] == "parent2"


class TestBrainMove:
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

    def _make_archived(self, vault, rel="_Archive/Ideas/20260101-my-idea.md"):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_unknown_move_op(self, initialized):
        result = server.brain_move("bogus")
        _assert_error(result, "Unknown move op")

    def test_rename_without_cli(self, initialized):
        """Rename via grep-and-replace when CLI is unavailable."""
        vault = initialized
        (vault / "Wiki" / "linker-xyz000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# Linker\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/brain-intro-abc123.md",
        )
        assert "grep_replace" in _result_text(result)
        assert "links updated" in _result_text(result)
        assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
        assert (vault / "Wiki" / "brain-intro-abc123.md").exists()
        content = (vault / "Wiki" / "linker-xyz000.md").read_text()
        assert "[[Wiki/brain-intro-abc123]]" in content

    def test_rename_with_mocked_cli(self, initialized, cli_available, monkeypatch):
        """Rename via CLI when available."""
        monkeypatch.setattr(server, "_cli_probed_at", 0)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)
        with patch.object(obsidian_cli, "move", return_value=True):
            result = server.brain_move(
                op="rename",
                source="Wiki/old.md",
                dest="Wiki/new.md",
            )
            assert "obsidian_cli" in _result_text(result)
            assert "wikilinks auto-updated" in _result_text(result)

    def test_rename_refreshes_cli_availability_before_cli_path(
        self, initialized, cli_available, monkeypatch
    ):
        monkeypatch.setattr(server, "_cli_probed_at", 0)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: False)

        with patch.object(obsidian_cli, "move", return_value=True) as mock_move:
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/brain-intro-abc123.md",
            )

        assert "grep_replace" in _result_text(result)
        mock_move.assert_not_called()
        assert (initialized / "Wiki" / "brain-intro-abc123.md").is_file()

    def test_rename_cli_path_rejects_symlink_source_before_obsidian_cli(
        self, initialized, cli_available
    ):
        target = initialized / "Wiki" / "target.md"
        alias = initialized / "Wiki" / "alias.md"
        target.write_text("---\ntype: living/wiki\ntags: []\n---\n\n# Target\n")
        alias.symlink_to(target)

        with patch.object(obsidian_cli, "move", return_value=True) as mock_move:
            result = server.brain_move(
                op="rename",
                source="Wiki/alias.md",
                dest="Wiki/moved.md",
            )

        _assert_error(result, "Move source cannot be a symlink: Wiki/alias.md")
        mock_move.assert_not_called()
        assert target.is_file()
        assert alias.is_symlink()
        assert not (initialized / "Wiki" / "moved.md").exists()
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)

    def test_rename_destination_parent_file_surfaces_actionably_without_dirtying(
        self, initialized
    ):
        (initialized / "Wiki" / "blocked").write_text("not a directory\n")
        server._router_dirty = False
        server._index_dirty = False

        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/blocked/brain-intro-abc123.md",
        )

        _assert_error(result, "Destination parent is not a directory: Wiki/blocked")
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)
        assert (initialized / "Wiki" / "brain-overview-abc123.md").is_file()
        assert (initialized / "Wiki" / "blocked").is_file()
        assert server._router_dirty is False
        assert server._index_dirty is False

    def test_rename_cli_path_destination_parent_file_surfaces_actionably_without_dirtying(
        self, initialized, cli_available, monkeypatch
    ):
        (initialized / "Wiki" / "blocked").write_text("not a directory\n")
        server._router_dirty = False
        server._index_dirty = False
        monkeypatch.setattr(server, "_cli_probed_at", 0)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)

        with patch.object(obsidian_cli, "move", return_value=True) as mock_move:
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/blocked/brain-intro-abc123.md",
            )

        _assert_error(result, "Destination parent is not a directory: Wiki/blocked")
        mock_move.assert_not_called()
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)
        assert (initialized / "Wiki" / "brain-overview-abc123.md").is_file()
        assert (initialized / "Wiki" / "blocked").is_file()
        assert server._router_dirty is False
        assert server._index_dirty is False

    def test_rename_cli_path_broken_symlink_parent_surfaces_actionably_without_dirtying(
        self, initialized, cli_available, monkeypatch
    ):
        blocked = initialized / "Wiki" / "blocked"
        blocked.symlink_to(initialized / "Wiki" / "missing-target")
        server._router_dirty = False
        server._index_dirty = False
        monkeypatch.setattr(server, "_cli_probed_at", 0)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)

        with patch.object(obsidian_cli, "move", return_value=True) as mock_move:
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/blocked/brain-intro-abc123.md",
            )

        _assert_error(result, "Destination parent is not a directory: Wiki/blocked")
        mock_move.assert_not_called()
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)
        assert (initialized / "Wiki" / "brain-overview-abc123.md").is_file()
        assert blocked.is_symlink()
        assert not (initialized / "Wiki" / "missing-target" / "brain-intro-abc123.md").exists()
        assert server._router_dirty is False
        assert server._index_dirty is False

    def test_rename_forces_router_refresh(self, initialized):
        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/brain-intro-abc123.md",
            )
        assert "Renamed" in _result_text(result)
        mock_ensure.assert_called_once_with()

    def test_rename_marks_router_dirty(self, initialized):
        server._router_dirty = False
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/brain-intro-abc123.md",
        )
        assert "Renamed" in _result_text(result)
        assert server._router_dirty is True

    def test_rename_missing_source(self, initialized):
        result = server.brain_move(op="rename", dest="Wiki/other.md")
        _assert_error(result, "requires top-level field 'source'")

    def test_rename_source_not_found(self, initialized):
        result = server.brain_move(
            op="rename",
            source="Wiki/nonexistent.md",
            dest="Wiki/other.md",
        )
        _assert_any_error(result)

    def test_rename_rejects_non_artefact_destination(self, initialized):
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="_Config/router.md",
        )
        _assert_error(result, "does not belong to any known artefact folder")

    def test_rename_rejects_cross_type_move(self, initialized):
        (initialized / "Ideas").mkdir(exist_ok=True)
        tax_living = initialized / "_Config" / "Taxonomy" / "Living"
        (tax_living / "ideas.md").write_text(
            "# Ideas\n\n"
            "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags:\n  - topic-tag\n---\n```\n\n"
            "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
        )
        server._router = server._compile_and_save(str(initialized))
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Ideas/brain-overview-abc123.md",
        )
        _assert_error(result, "Use brain_move(op='convert'")

    def test_rename_rejects_brain_core_source(self, initialized):
        result = server.brain_move(
            op="rename",
            source=".brain-core/VERSION",
            dest="Wiki/brain-core-version.md",
        )
        _assert_error(result, ".brain-core")

    def test_rename_rejects_convert_only_field(self, initialized):
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/brain-intro-abc123.md",
            target_type="ideas",
        )
        _assert_error(result, "does not accept top-level field 'target_type'")

    def test_rename_cli_error_falls_back_to_grep(self, initialized, cli_available):
        """When CLI returns an error (False), fallback to grep-replace."""
        vault = initialized
        (vault / "Wiki" / "linker-fallback.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# Linker\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        with patch.object(obsidian_cli, "move", return_value=False):
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/brain-moved-abc123.md",
            )
            assert "grep_replace" in _result_text(result)
            assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
            assert (vault / "Wiki" / "brain-moved-abc123.md").exists()
            content = (vault / "Wiki" / "linker-fallback.md").read_text()
            assert "[[Wiki/brain-moved-abc123]]" in content

    def test_rename_surfaces_partial_apply_context(self, initialized):
        def fake_rename(vault_root, source, dest, router=None, *, allow_archive_paths=False):
            raise PartialApplyError(
                "move set partially applied — links already rewritten; "
                "committed [], failed at "
                "Wiki/brain-overview-abc123.md->Wiki/brain-moved-abc123.md"
            )

        server._router_dirty = False
        server._index_dirty = False
        with patch("brain_mcp._server_actions.rename.rename_and_update_links", fake_rename):
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/brain-moved-abc123.md",
            )

        _assert_partial_apply_error(result, "move set partially applied")
        _assert_error(result, "links already rewritten")
        assert server._router_dirty is True
        assert server._index_dirty is True

    def test_rename_unrelated_runtime_error_remains_unexpected(self, initialized):
        def fake_rename(vault_root, source, dest, router=None, *, allow_archive_paths=False):
            raise RuntimeError("programmer bug")

        with patch("brain_mcp._server_actions.rename.rename_and_update_links", fake_rename):
            result = server.brain_move(
                op="rename",
                source="Wiki/brain-overview-abc123.md",
                dest="Wiki/brain-moved-abc123.md",
            )

        _assert_error(result, "Unexpected error: programmer bug")

    def test_rename_cross_directory_without_cli(self, initialized):
        """Rename across directories creates destination dir (regression test)."""
        vault = initialized
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/subdir/brain-overview-abc123.md",
        )
        assert "grep_replace" in _result_text(result)
        assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
        assert (vault / "Wiki" / "subdir" / "brain-overview-abc123.md").exists()

    def test_rename_cli_mkdir_before_move(self, initialized, cli_available, monkeypatch):
        """CLI path creates destination directory before calling obsidian_cli.move."""
        monkeypatch.setattr(server, "_cli_probed_at", 0)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)
        with patch.object(obsidian_cli, "move", return_value=True) as mock_move, \
             patch.object(os, "makedirs") as mock_makedirs:
            server.brain_move(
                op="rename",
                source="Wiki/old.md",
                dest="Wiki/subdir/new.md",
            )
            mock_move.assert_called_once()
            assert any(
                call.args[0].endswith("Wiki/subdir")
                for call in mock_makedirs.call_args_list
            )

    def test_archive_moves_terminal_artefact(self, initialized):
        rel = self._make_idea(initialized)
        result = server.brain_move(op="archive", path=rel)
        assert result.startswith("**Archived:**")
        assert not (initialized / rel).exists()
        assert (initialized / "_Archive" / "Ideas").exists()

    def test_archive_forces_router_refresh(self, initialized):
        rel = self._make_idea(initialized)
        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_move(op="archive", path=rel)
        assert result.startswith("**Archived:**")
        mock_ensure.assert_called_once_with()

    def test_archive_marks_router_dirty(self, initialized):
        rel = self._make_idea(initialized)
        server._router_dirty = False
        result = server.brain_move(op="archive", path=rel)
        assert result.startswith("**Archived:**")
        assert server._router_dirty is True

    def test_archive_requires_path(self, initialized):
        result = server.brain_move(op="archive")
        _assert_error(result, "requires top-level field 'path'")

    def test_archive_rejects_rename_fields(self, initialized):
        result = server.brain_move(
            op="archive",
            path="Ideas/my-idea.md",
            source="Ideas/my-idea.md",
        )
        _assert_error(result, "does not accept top-level field 'source'")

    def test_archive_passes_recursive_to_script_layer(self, initialized):
        calls = []

        def fake_archive(vault_root, router, path, recursive=False):
            calls.append((path, recursive))
            return {
                "old_path": path,
                "new_path": "_Archive/Ideas/my-idea.md",
                "links_updated": 0,
            }

        with patch("brain_mcp._server_actions.edit.archive_artefact", fake_archive):
            result = server.brain_move(
                op="archive",
                path="Ideas/my-idea.md",
                recursive=True,
            )

        assert result.startswith("**Archived:**")
        assert calls == [("Ideas/my-idea.md", True)]

    def test_archive_surfaces_parent_chain_error_distinct_from_descendant_gate(self, initialized):
        def fake_archive(vault_root, router, path, recursive=False):
            raise CyclicParentChainError("Cyclic descendant chain: ideas/parent -> ideas/child -> ideas/parent")

        with patch("brain_mcp._server_actions.edit.archive_artefact", fake_archive):
            result = server.brain_move(
                op="archive",
                path="Ideas/my-idea.md",
                recursive=True,
            )

        _assert_error(result, "Invalid living parent chain")
        _assert_error(result, "Run check/doctor")
        assert "HAS_DESCENDANTS" not in _result_text(result)

    def test_archive_surfaces_stale_index_error_actionably(self, initialized):
        (initialized / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: adopted\n"
            "---\n\n"
            "Parent.\n"
        )
        router = compile_router.compile(str(initialized))
        router["artefact_index"].pop("ideas/parent")
        server._set_router(router)

        with patch.object(server, "_ensure_router_fresh"):
            result = server.brain_move(
                op="archive",
                path="Ideas/Parent.md",
            )

        _assert_error(result, "Stale compiled artefact index")
        _assert_error(result, "ideas/parent")
        _assert_error(result, "Recompile or repair the router/index")
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)
        assert "Invalid living parent chain" not in _result_text(result)

    def test_archive_surfaces_partial_apply_context(self, initialized):
        def fake_archive(vault_root, router, path, recursive=False):
            raise PartialApplyError(
                "archive partially applied — metadata files written; "
                "move failure: move set partially applied — links already rewritten"
            )

        server._router_dirty = False
        server._index_dirty = False
        with patch("brain_mcp._server_actions.edit.archive_artefact", fake_archive):
            result = server.brain_move(
                op="archive",
                path="Ideas/my-idea.md",
            )

        _assert_partial_apply_error(result, "archive partially applied")
        _assert_error(result, "move failure")
        assert server._router_dirty is True
        assert server._index_dirty is True

    def test_unarchive_restores_archived_artefact(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_move(op="unarchive", path=rel)
        assert result.startswith("**Unarchived:**")
        assert not (initialized / rel).exists()
        assert (initialized / "Ideas" / "my-idea.md").exists()

    def test_unarchive_forces_router_refresh(self, initialized):
        rel = self._make_archived(initialized)
        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_move(op="unarchive", path=rel)
        assert result.startswith("**Unarchived:**")
        mock_ensure.assert_called_once_with()

    def test_unarchive_marks_router_dirty(self, initialized):
        rel = self._make_archived(initialized)
        server._router_dirty = False
        result = server.brain_move(op="unarchive", path=rel)
        assert result.startswith("**Unarchived:**")
        assert server._router_dirty is True

    def test_unarchive_surfaces_partial_apply_context(self, initialized):
        rel = self._make_archived(initialized)

        def fake_unarchive(vault_root, router, path, recursive=False):
            raise PartialApplyError(
                "unarchive partially applied — metadata file written "
                "_Archive/Ideas/20260101-my-idea.md; "
                "move failure: move set partially applied"
            )

        server._router_dirty = False
        server._index_dirty = False
        with patch("brain_mcp._server_actions.edit.unarchive_artefact", fake_unarchive):
            result = server.brain_move(op="unarchive", path=rel)

        _assert_partial_apply_error(result, "unarchive partially applied")
        _assert_error(result, "move failure")
        assert server._router_dirty is True
        assert server._index_dirty is True

    def test_recursive_unarchive_warns_about_uninspected_candidates(self, initialized):
        def fake_unarchive(_vault, _router, path, recursive=False):
            assert recursive is True
            return {
                "old_path": path,
                "new_path": "Ideas/Parent.md",
                "links_updated": 0,
                "uninspected": [{
                    "path": "_Archive/Ideas/Child.md",
                    "reason": "Unknown artefact type",
                }],
            }

        with patch("brain_mcp._server_actions.edit.unarchive_artefact", fake_unarchive):
            result = server.brain_move(
                op="unarchive",
                path="_Archive/Ideas/Parent.md",
                recursive=True,
            )

        assert "recursive restore could not inspect archived candidate" in _result_text(result)
        assert "_Archive/Ideas/Child.md: Unknown artefact type" in _result_text(result)

    def test_unarchive_destination_collision_preflights_without_dirtying(
        self, initialized
    ):
        rel = self._make_archived(initialized)
        (initialized / "Ideas" / "my-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\n---\n\nExisting.\n"
        )

        server._router_dirty = False
        server._index_dirty = False
        result = server.brain_move(op="unarchive", path=rel)

        _assert_error(result, "Destination file already exists: Ideas/my-idea.md")
        assert "unarchive partially applied" not in _result_text(result)
        assert "Unexpected error" not in _result_text(result)
        assert server._router_dirty is False
        assert server._index_dirty is False
        content = (initialized / rel).read_text()
        assert "archiveddate" in content

    def test_unarchive_requires_path(self, initialized):
        result = server.brain_move(op="unarchive")
        _assert_error(result, "requires top-level field 'path'")


class TestBrainMoveConvert:
    def test_convert_changes_type_and_path(self, initialized):
        result = json.loads(server.brain_move(
            op="convert",
            path="Wiki/brain-overview-abc123.md",
            target_type="ideas",
        ))
        assert result["status"] == "ok"
        assert result["type"] == "living/ideas"
        assert result["new_path"].startswith("Ideas/")
        assert not (initialized / "Wiki" / "brain-overview-abc123.md").exists()
        assert os.path.isfile(os.path.join(str(initialized), result["new_path"]))

    def test_convert_updates_links(self, initialized):
        (initialized / "Wiki" / "linker-bbb000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        result = json.loads(server.brain_move(
            op="convert",
            path="Wiki/brain-overview-abc123.md",
            target_type="ideas",
        ))
        assert result["links_updated"] >= 1
        content = (initialized / "Wiki" / "linker-bbb000.md").read_text()
        new_stem = result["new_path"][:-3]
        assert f"[[{new_stem}]]" in content

    def test_convert_forces_router_refresh(self, initialized):
        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_move(
                op="convert",
                path="Wiki/brain-overview-abc123.md",
                target_type="ideas",
            )
        assert '"status": "ok"' in _result_text(result)
        mock_ensure.assert_called_once_with()

    def test_convert_surfaces_stale_indexed_descendant_missing_actionably(self, initialized):
        (initialized / "Ideas" / "parent").mkdir(parents=True, exist_ok=True)
        (initialized / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: shaping\n"
            "---\n\n"
            "Parent.\n"
        )
        child = initialized / "Ideas" / "parent" / "Child.md"
        child.write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - ideas/parent\n"
            "key: child\n"
            "parent: ideas/parent\n"
            "status: shaping\n"
            "---\n\n"
            "Child.\n"
        )
        router = compile_router.compile(str(initialized))
        child.unlink()
        server._set_router(router)

        with patch.object(server, "_ensure_router_fresh"):
            result = server.brain_move(
                op="convert",
                path="Ideas/Parent.md",
                target_type="wiki",
            )

        _assert_error(result, "Stale compiled artefact index")
        _assert_error(result, "Ideas/parent/Child.md")
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)

    def test_convert_surfaces_partial_apply_context(self, initialized):
        def fake_convert(vault_root, router, path, target_type, parent=None, recursive=False):
            raise PartialApplyError(
                "convert mutation partially applied — metadata files written; "
                "move failure: move set partially applied — links already rewritten"
            )

        server._router_dirty = False
        server._index_dirty = False
        with patch("brain_mcp._server_actions.edit.convert_artefact", fake_convert):
            result = server.brain_move(
                op="convert",
                path="Wiki/brain-overview-abc123.md",
                target_type="ideas",
            )

        _assert_partial_apply_error(result, "convert mutation partially applied")
        _assert_error(result, "move failure")
        assert server._router_dirty is True
        assert server._index_dirty is True

    def test_convert_living_parent_to_temporal_requires_recursive(self, initialized):
        _write_parent_child_tree(initialized)
        server._set_router(compile_router.compile(str(initialized)))

        result = server.brain_move(
            op="convert",
            path="Ideas/Parent.md",
            target_type="logs",
        )

        _assert_error(result, "HAS_DESCENDANTS")
        assert (initialized / "Ideas" / "Parent.md").is_file()
        assert (initialized / "Wiki" / "ideas~parent" / "Child.md").is_file()

    def test_convert_living_parent_to_temporal_accepts_recursive(self, initialized):
        _write_parent_child_tree(initialized)
        server._set_router(compile_router.compile(str(initialized)))

        result = json.loads(server.brain_move(
            op="convert",
            path="Ideas/Parent.md",
            target_type="logs",
            recursive=True,
        ))

        assert result["status"] == "ok"
        assert result["new_path"].startswith("_Temporal/Logs/")
        assert not (initialized / "Ideas" / "Parent.md").exists()
        child = initialized / "Wiki" / "Child.md"
        assert child.is_file()
        fields, _ = parse_frontmatter(child.read_text())
        assert "parent" not in fields

    def test_convert_marks_router_dirty(self, initialized):
        server._router_dirty = False
        result = server.brain_move(
            op="convert",
            path="Wiki/brain-overview-abc123.md",
            target_type="ideas",
        )
        assert '"status": "ok"' in _result_text(result)
        assert server._router_dirty is True

    def test_convert_missing_params(self, initialized):
        result = server.brain_move(op="convert")
        _assert_error(result, "requires top-level field 'path'")

    def test_convert_unknown_target(self, initialized):
        result = server.brain_move(
            op="convert",
            path="Wiki/brain-overview-abc123.md",
            target_type="nonexistent",
        )
        _assert_any_error(result)


class TestBrainAction:
    def test_action_unknown(self, initialized):
        result = server.brain_action("bogus")
        _assert_error(result, "Unknown action")


class TestBrainActionDelete:
    def test_delete_removes_file(self, initialized):
        result = server.brain_action("delete", params={"path": "Wiki/python-guide-def456.md"})
        assert result.startswith("**Deleted:**")
        assert not (initialized / "Wiki" / "python-guide-def456.md").exists()

    def test_delete_forces_router_refresh(self, initialized):
        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_action("delete", params={"path": "Wiki/python-guide-def456.md"})
        assert result.startswith("**Deleted:**")
        mock_ensure.assert_called_once_with()

    def test_delete_marks_router_dirty(self, initialized):
        server._router_dirty = False
        result = server.brain_action("delete", params={"path": "Wiki/python-guide-def456.md"})
        assert result.startswith("**Deleted:**")
        assert server._router_dirty is True

    def test_delete_cleans_links(self, initialized):
        # Add a link to the target file
        (initialized / "Wiki" / "linker-aaa000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/python-guide-def456|Python]].\n"
        )
        result = server.brain_action("delete", params={"path": "Wiki/python-guide-def456.md"})
        assert "links replaced" in _result_text(result)
        content = (initialized / "Wiki" / "linker-aaa000.md").read_text()
        assert "~~Python~~" in content

    def test_delete_preserves_and_reports_orphaned_attachment_scope(self, initialized):
        artefact = initialized / "Ideas" / "Owned.md"
        artefact.write_text(
            "---\ntype: living/ideas\ntags: []\nkey: owned\nstatus: shaping\n---\n\n# Owned\n"
        )
        attachment = initialized / "_Assets" / "Attachments" / "ideas~owned" / "diagram.svg"
        attachment.parent.mkdir(parents=True)
        attachment.write_text("<svg />")
        server._set_router(compile_router.compile(str(initialized)))

        result = server.brain_action("delete", params={"path": "Ideas/Owned.md"})

        assert "Preserved orphaned attachment scope" in _result_text(result)
        assert "_Assets/Attachments/ideas~owned" in _result_text(result)
        assert attachment.read_text() == "<svg />"

    def test_recursive_delete_reports_parent_and_descendant_attachment_scopes(
        self, initialized
    ):
        _write_parent_child_tree(initialized)
        parent_attachment = (
            initialized / "_Assets" / "Attachments" / "ideas~parent" / "parent.svg"
        )
        child_attachment = (
            initialized / "_Assets" / "Attachments" / "wiki~child" / "child.svg"
        )
        parent_attachment.parent.mkdir(parents=True)
        child_attachment.parent.mkdir(parents=True)
        parent_attachment.write_text("parent")
        child_attachment.write_text("child")
        server._set_router(compile_router.compile(str(initialized)))

        result = server.brain_action(
            "delete", params={"path": "Ideas/Parent.md", "recursive": True}
        )

        text = _result_text(result)
        assert "_Assets/Attachments/ideas~parent" in text
        assert "_Assets/Attachments/wiki~child" in text
        assert parent_attachment.read_text() == "parent"
        assert child_attachment.read_text() == "child"

    def test_delete_missing_params(self, initialized):
        result = server.brain_action("delete")
        _assert_any_error(result)

    def test_delete_not_found(self, initialized):
        result = server.brain_action("delete", params={"path": "Wiki/gone.md"})
        _assert_any_error(result)

    def test_delete_rejects_mismatched_shape_params_at_runtime(self, initialized):
        result = server.brain_action(
            "delete",
            params={"source": "Wiki/python-guide-def456.md", "slug": "brief"},
        )
        _assert_error(result, "does not accept params field 'source'")

    def test_delete_rejects_fix_links_variant_overlap(self, initialized):
        result = server.brain_action(
            "delete",
            params={"path": "Wiki/python-guide-def456.md", "fix": True},
        )
        _assert_error(result, "does not accept params field 'fix'")

    def test_delete_rejects_protected_source(self, initialized):
        result = server.brain_action("delete", params={"path": ".brain-core/VERSION"})
        _assert_error(result, ".brain-core")

    def test_delete_surfaces_has_descendants_payload(self, initialized):
        def fake_delete(vault_root, path, router=None, recursive=False, **_kwargs):
            raise HasDescendantsError(
                "delete",
                path,
                [{"key": "wiki/child", "path": "Wiki/child.md"}],
            )

        with patch("brain_mcp._server_actions.rename.delete_and_clean_links", fake_delete):
            result = server.brain_action(
                "delete",
                params={"path": "Wiki/python-guide-def456.md"},
            )

        _assert_error(result, "HAS_DESCENDANTS")
        _assert_error(result, '"descendants"')

    def test_delete_surfaces_parent_chain_error_distinct_from_descendant_gate(self, initialized):
        def fake_delete(vault_root, path, router=None, recursive=False, **_kwargs):
            raise CyclicParentChainError("Cyclic descendant chain: ideas/parent -> ideas/child -> ideas/parent")

        with patch("brain_mcp._server_actions.rename.delete_and_clean_links", fake_delete):
            result = server.brain_action(
                "delete",
                params={"path": "Wiki/python-guide-def456.md", "recursive": True},
            )

        _assert_error(result, "Invalid living parent chain")
        _assert_error(result, "Run check/doctor")
        assert "HAS_DESCENDANTS" not in _result_text(result)

    def test_delete_surfaces_partial_apply_context(self, initialized):
        def fake_delete(vault_root, path, router=None, recursive=False, **_kwargs):
            raise PartialApplyError(
                "delete set partially applied — links already rewritten; "
                "removed [], failed at Wiki/python-guide-def456.md"
            )

        server._router_dirty = False
        server._index_dirty = False
        with patch("brain_mcp._server_actions.rename.delete_and_clean_links", fake_delete):
            result = server.brain_action(
                "delete",
                params={"path": "Wiki/python-guide-def456.md"},
            )

        _assert_partial_apply_error(result, "delete set partially applied")
        _assert_error(result, "failed at Wiki/python-guide-def456.md")
        assert server._router_dirty is True
        assert server._index_dirty is True

    def test_delete_unrelated_runtime_error_remains_unexpected(self, initialized):
        def fake_delete(vault_root, path, router=None, recursive=False, **_kwargs):
            raise RuntimeError("programmer bug")

        with patch("brain_mcp._server_actions.rename.delete_and_clean_links", fake_delete):
            result = server.brain_action(
                "delete",
                params={"path": "Wiki/python-guide-def456.md"},
            )

        _assert_error(result, "Unexpected error: programmer bug")


class TestBrainActionReparent:
    def _fake_reparent(self):
        calls = []

        def fake_reparent(vault_root, router, source, to_marker=None, *, to_provided=False):
            calls.append((source, to_marker, to_provided))
            return {
                "source": source,
                "to": to_marker,
                "children": [],
                "moves": [],
                "links_updated": 0,
            }

        return calls, fake_reparent

    def test_reparent_omitted_to_lifts_to_source_parent(self, initialized):
        calls, fake_reparent = self._fake_reparent()
        params = server._BrainActionReparentParams(source="Projects/Brain.md")

        with patch("brain_mcp._server_actions.edit.reparent_children", fake_reparent):
            result = server.brain_action("reparent", params=params)

        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert calls == [("Projects/Brain.md", None, False)]

    def test_reparent_preserves_null_to_as_clear_to_top_level(self, initialized):
        calls, fake_reparent = self._fake_reparent()
        params = server._BrainActionReparentParams(
            source="Projects/Brain.md",
            to=None,
        )

        with patch("brain_mcp._server_actions.edit.reparent_children", fake_reparent):
            result = server.brain_action("reparent", params=params)

        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert calls == [("Projects/Brain.md", None, True)]

    def test_reparent_empty_to_clears_to_top_level(self, initialized):
        calls, fake_reparent = self._fake_reparent()
        params = server._BrainActionReparentParams(
            source="Projects/Brain.md",
            to="",
        )

        with patch("brain_mcp._server_actions.edit.reparent_children", fake_reparent):
            result = server.brain_action("reparent", params=params)

        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert calls == [("Projects/Brain.md", "", True)]

    def test_reparent_target_to_reparents_to_new_parent(self, initialized):
        calls, fake_reparent = self._fake_reparent()
        params = server._BrainActionReparentParams(
            source="Projects/Brain.md",
            to="Projects/Custom.md",
        )

        with patch("brain_mcp._server_actions.edit.reparent_children", fake_reparent):
            result = server.brain_action("reparent", params=params)

        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert calls == [("Projects/Brain.md", "Projects/Custom.md", True)]

    def test_reparent_real_success_moves_child_subtree_and_marks_dirty(self, initialized):
        (initialized / "Ideas" / "Brain.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - ideas/brain\n"
            "key: brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Brain\n"
        )
        (initialized / "Ideas" / "Custom.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - ideas/custom\n"
            "key: custom\n"
            "status: shaping\n"
            "---\n\n"
            "# Custom\n"
        )
        (initialized / "Wiki" / "ideas~brain").mkdir(parents=True, exist_ok=True)
        (initialized / "Wiki" / "ideas~brain" / "Child.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - ideas/brain\n"
            "key: child\n"
            "parent: ideas/brain\n"
            "---\n\n"
            "Child.\n"
        )
        (initialized / "Wiki" / "ideas~brain" / "wiki~child").mkdir(
            parents=True, exist_ok=True
        )
        (initialized / "Wiki" / "ideas~brain" / "wiki~child" / "Grand.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki/child\n"
            "key: grand\n"
            "parent: wiki/child\n"
            "---\n\n"
            "Grand.\n"
        )
        server._set_router(compile_router.compile(str(initialized)))
        server._router_dirty = False
        server._index_dirty = False

        result = server.brain_action(
            "reparent",
            params={"source": "Ideas/Brain.md", "to": "ideas/custom"},
        )

        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert payload["to"] == "ideas/custom"
        assert {
            "source": "Wiki/ideas~brain/Child.md",
            "dest": "Wiki/ideas~custom/Child.md",
        } in payload["moves"]
        child = initialized / "Wiki" / "ideas~custom" / "Child.md"
        grand = initialized / "Wiki" / "ideas~custom" / "child" / "Grand.md"
        assert child.is_file()
        assert grand.is_file()
        fields, _body = parse_frontmatter(child.read_text())
        assert fields["parent"] == "ideas/custom"
        assert "ideas/custom" in fields["tags"]
        assert "ideas/brain" not in fields["tags"]
        assert not (initialized / "Wiki" / "ideas~brain").exists()
        assert server._router_dirty is True
        assert server._index_dirty is True

    def test_reparent_source_missing_from_index_surfaces_actionably(self, initialized):
        (initialized / "Ideas" / "parent").mkdir(parents=True, exist_ok=True)
        (initialized / "Ideas" / "Parent.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags: []\n"
            "key: parent\n"
            "status: shaping\n"
            "---\n\n"
            "Parent.\n"
        )
        (initialized / "Wiki" / "ideas~parent").mkdir(parents=True, exist_ok=True)
        (initialized / "Wiki" / "ideas~parent" / "Child.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - ideas/parent\n"
            "key: child\n"
            "parent: ideas/parent\n"
            "---\n\n"
            "Child.\n"
        )
        router = compile_router.compile(str(initialized))
        router["artefact_index"].pop("ideas/parent")
        server._set_router(router)

        with patch.object(server, "_ensure_router_fresh"):
            result = server.brain_action(
                "reparent",
                params={"source": "Ideas/Parent.md", "to": None},
            )

        _assert_error(result, "Stale compiled artefact index")
        _assert_error(result, "ideas/parent")
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)

    def test_reparent_to_descendant_surfaces_actionably(self, initialized):
        parent, child = _write_parent_child_tree(initialized)
        router = compile_router.compile(str(initialized))
        server._set_router(router)
        parent_before = parent.read_text()
        child_before = child.read_text()

        with patch.object(server, "_ensure_router_fresh"):
            result = server.brain_action(
                "reparent",
                params={"source": "Ideas/Parent.md", "to": "wiki/child"},
            )

        _assert_error(result, "Invalid living parent chain")
        _assert_error(result, "child of descendant wiki/child")
        assert "Run check/doctor" not in _result_text(result)
        assert "reconcile the broken or cyclic parent metadata" not in _result_text(result)
        assert "Unexpected error" not in _result_text(result)
        assert "Traceback" not in _result_text(result)
        assert parent.read_text() == parent_before
        assert child.read_text() == child_before

    def test_reparent_surfaces_partial_apply_context(self, initialized):
        def fake_reparent(vault_root, router, source, to_marker=None, *, to_provided=False):
            raise PartialApplyError(
                "reparent partially applied — metadata files written; "
                "move failure: move set partially applied — links already rewritten"
            )

        params = server._BrainActionReparentParams(
            source="Projects/Brain.md",
            to=None,
        )
        server._router_dirty = False
        server._index_dirty = False
        with patch("brain_mcp._server_actions.edit.reparent_children", fake_reparent):
            result = server.brain_action("reparent", params=params)

        _assert_partial_apply_error(result, "reparent partially applied")
        _assert_error(result, "move failure")
        assert server._router_dirty is True
        assert server._index_dirty is True


class TestBrainActionFixLinks:
    def test_dry_run_returns_json_with_summary(self, initialized):
        """Default fix-links (no fix param) returns dry_run JSON."""
        result = json.loads(server.brain_action("fix-links"))
        assert result["mode"] == "dry_run"
        assert "summary" in result
        assert "fixed" in result
        assert "ambiguous" in result
        assert "unresolvable" in result

    def test_dry_run_detects_broken_links(self, initialized):
        """Dry run detects a broken wikilink and classifies it."""
        vault = initialized
        (vault / "Wiki" / "has-broken-link.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "See [[nonexistent-target]].\n"
        )
        result = json.loads(server.brain_action("fix-links"))
        assert result["mode"] == "dry_run"
        assert result["summary"]["total_broken"] >= 1
        all_targets = (
            [f["target"] for f in result["fixed"]]
            + [a["target"] for a in result["ambiguous"]]
            + [u["target"] for u in result["unresolvable"]]
        )
        assert "nonexistent-target" in all_targets

    def test_fix_applies_resolved_links(self, initialized):
        """fix=True applies auto-resolved link fixes."""
        vault = initialized
        (vault / "Wiki" / "My Target Page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# My Target Page\n"
        )
        (vault / "Wiki" / "referrer.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "See [[my-target-page]].\n"
        )
        result = json.loads(server.brain_action("fix-links", params={"fix": True}))
        assert result["mode"] == "fix"
        assert result["summary"]["fixed"] >= 1
        assert result.get("substitutions", 0) >= 1
        content = (vault / "Wiki" / "referrer.md").read_text()
        assert "[[My Target Page]]" in content

    def test_fix_marks_index_dirty(self, initialized):
        """Applying fixes should mark the index as dirty."""
        vault = initialized
        (vault / "Wiki" / "Target Title.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Target Title\n"
        )
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[target-title]].\n"
        )
        server.brain_action("fix-links", params={"fix": True})
        assert server._index_dirty is True


class TestMutationSerialization:
    def test_brain_edit_calls_are_serialized(self, initialized):
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        results = []

        def fake_handle_brain_edit(**_kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                first_call = not first_entered.is_set()
            if first_call:
                first_entered.set()
                release_first.wait(timeout=2)
            else:
                second_entered.set()
            time.sleep(0.01)
            with state_lock:
                active -= 1
            return "ok"

        def invoke():
            results.append(server.brain_edit(operation="edit", path="test.md", body="x"))

        with patch.object(server._server_artefacts, "handle_brain_edit", side_effect=fake_handle_brain_edit):
            t1 = threading.Thread(target=invoke)
            t2 = threading.Thread(target=invoke)
            t1.start()
            assert first_entered.wait(timeout=1)
            t2.start()
            time.sleep(0.05)
            assert not second_entered.is_set()
            release_first.set()
            t1.join(timeout=1)
            t2.join(timeout=1)

        assert results == ["ok", "ok"]
        assert second_entered.is_set()
        assert max_active == 1
