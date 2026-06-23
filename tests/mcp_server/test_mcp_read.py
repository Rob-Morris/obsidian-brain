"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import asyncio
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
from _common._yaml import dump_mapping_text



from _mcp_helpers import (
    _assert_error,
    _bump_mtime,
    _extract_create_path,
    _list_result_lines,
    _list_text,
    _progress_payload,
    _search_result_lines,
    _search_text,
    _write_config_text,
    _write_config_yaml,
)


class TestBrainRead:
    def test_read_type_requires_name(self, initialized):
        result = server.brain_read("type")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_type_by_name(self, initialized):
        result = json.loads(server.brain_read("type", name="wiki"))
        assert len(result) == 1
        assert result[0]["key"] == "wiki"

    def test_read_type_by_type(self, initialized):
        result = json.loads(server.brain_read("type", name="living/wiki"))
        assert len(result) == 1
        assert result[0]["type"] == "living/wiki"

    def test_read_type_not_found(self, initialized):
        result = server.brain_read("type", name="nonexistent")
        _assert_error(result)

    def test_read_trigger_requires_name(self, initialized):
        result = server.brain_read("trigger")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_style_requires_name(self, initialized):
        result = server.brain_read("style")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_style_content(self, initialized):
        result = server.brain_read("style", name="concise")
        assert "Be brief and direct." in result

    def test_read_style_not_found(self, initialized):
        result = server.brain_read("style", name="nonexistent")
        _assert_error(result)

    def test_read_template(self, initialized):
        result = server.brain_read("template", name="wiki")
        assert "{{title}}" in result

    def test_read_template_requires_name(self, initialized):
        result = server.brain_read("template")
        _assert_error(result)

    def test_read_skill_requires_name(self, initialized):
        result = server.brain_read("skill")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_core_skill_content(self, initialized):
        result = server.brain_read("skill", name="test-skill")
        assert "Test Skill (Core)" in result

    def test_read_skill_content(self, initialized):
        result = server.brain_read("skill", name="Vault Maintenance")
        assert "Keep the vault tidy." in result

    def test_read_plugin_requires_name(self, initialized):
        result = server.brain_read("plugin")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_plugin_content(self, initialized):
        result = server.brain_read("plugin", name="Undertask")
        assert "Task management plugin." in result

    def test_read_environment(self, initialized):
        result = server.brain_read("environment")
        assert "vault_root=" in result
        assert "platform=" in result
        assert "obsidian_cli_available=" in result

    def test_read_environment_includes_cli_status(self, initialized):
        """Environment response should reflect CLI availability."""
        result = server.brain_read("environment")
        assert "obsidian_cli_available=False" in result

    def test_read_router(self, initialized):
        result = json.loads(server.brain_read("router"))
        assert "always_rules" in result
        assert "meta" in result
        assert len(result["always_rules"]) >= 1

    def test_read_unknown_resource(self, initialized):
        result = server.brain_read("bogus")
        _assert_error(result, "not readable via brain_read")


class TestBrainReadSpecValidation:
    """brain_read strict-extras and required-field validation."""

    def test_read_environment_rejects_name(self, initialized):
        """brain_read(resource='environment') does not accept name."""
        result = server.brain_read("environment", name="anything")
        _assert_error(result, "does not accept top-level field 'name'")

    def test_read_router_rejects_name(self, initialized):
        """brain_read(resource='router') does not accept name."""
        result = server.brain_read("router", name="anything")
        _assert_error(result, "does not accept top-level field 'name'")

    def test_read_compliance_is_not_a_read_resource(self, initialized):
        """brain_read(resource='compliance') is rejected."""
        result = server.brain_read("compliance")
        _assert_error(result, "not readable via brain_read")


class TestBrainReadMemory:
    @pytest.fixture(autouse=True)
    def setup_memories(self, initialized):
        """Add a memories directory with a test memory to the vault fixture."""
        self.vault = initialized
        memories_dir = initialized / "_Config" / "Memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        (memories_dir / "README.md").write_text("# Memories\n\nDispatch doc.\n")
        (memories_dir / "brain-core-reference.md").write_text(
            "---\ntriggers: [brain core, obsidian-brain, vault system]\n---\n\n"
            "# Brain Core Reference\n\nBrain-core is the system.\n"
        )
        (memories_dir / "python-setup.md").write_text(
            "---\ntriggers: [python, dev environment]\n---\n\n"
            "# Python Setup\n\nUse Python 3.12.\n"
        )
        # Restart to recompile and pick up memories
        server.startup(vault_root=str(initialized))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

    def test_read_memory_requires_name(self):
        result = server.brain_read("memory")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_by_trigger(self):
        result = server.brain_read("memory", name="brain core")
        assert "Brain-core is the system." in result

    def test_trigger_case_insensitive(self):
        result = server.brain_read("memory", name="BRAIN CORE")
        assert "Brain-core is the system." in result

    def test_trigger_substring(self):
        result = server.brain_read("memory", name="brain")
        # "brain" is a substring of "brain core" and "obsidian-brain"
        # Should match brain-core-reference — single match returns content
        assert "Brain-core is the system." in result

    def test_fallback_to_name(self):
        result = server.brain_read("memory", name="python-setup")
        assert "Use Python 3.12." in result

    def test_not_found(self):
        result = server.brain_read("memory", name="nonexistent-thing")
        _assert_error(result)

    def test_brain_session_includes_memories_after_restart(self):
        result = json.loads(server.brain_session())
        assert len(result["memories"]) >= 2


class TestBrainReadArchive:
    """Tests for brain_read(resource='archive')."""

    def _make_archived(self, vault, rel="_Archive/Ideas/20260101-old-idea.md"):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_list_archives_via_brain_list(self, initialized):
        self._make_archived(initialized)
        result = server.brain_list(resource="archive")
        text = _search_text(result)
        assert "1 archive(s)" in text
        assert "_Archive/Ideas/20260101-old-idea.md" in text

    def test_list_empty_archive(self, initialized):
        result = server.brain_list(resource="archive")
        text = _search_text(result)
        assert "0 archive(s)" in text

    def test_read_archive_requires_name(self, initialized):
        result = server.brain_read("archive")
        _assert_error(result, "requires top-level field 'name'")

    def test_read_specific_archive(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_read("archive", name=rel)
        assert "Old idea." in result

    def test_read_non_archive_path_rejected(self, initialized):
        result = server.brain_read("archive", name="Ideas/my-idea.md")
        _assert_error(result, "not in _Archive")

    def test_list_legacy_per_type_archives(self, initialized):
        """Per-type _Archive/ dirs are also scanned."""
        self._make_archived(initialized, "Ideas/_Archive/20260101-legacy.md")
        result = server.brain_list(resource="archive")
        text = _search_text(result)
        assert "1 archive(s)" in text
        assert "Ideas/_Archive/20260101-legacy.md" in text


class TestArchiveGuardsMcp:
    """Verify brain_read and brain_edit reject archived paths at the MCP boundary."""

    def _make_archived(self, vault, rel="Ideas/_Archive/20260101-old-idea.md"):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_read_artefact_rejects_archived_path(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_read("artefact", name=rel)
        _assert_error(result, "archived")

    def test_read_file_rejects_archived_path(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_read("file", name=rel)
        _assert_error(result, "archived")

    def test_read_artefact_rejects_top_level_archive(self, initialized):
        rel = self._make_archived(initialized, "_Archive/Ideas/20260101-old-idea.md")
        result = server.brain_read("artefact", name=rel)
        _assert_error(result, "archived")

    def test_edit_rejects_archived_path(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_edit(operation="edit", path=rel, body="new body")
        _assert_error(result, "archived")


class TestBrainListSpecValidation:
    """brain_list strict-extras and required-field validation."""

    def test_list_skill_rejects_type_filter(self, initialized):
        """brain_list(resource='skill') does not accept type (artefact-only)."""
        result = server.brain_list(resource="skill", type="living/wiki")
        _assert_error(result, "does not accept top-level field 'type'")

    def test_list_skill_rejects_since_filter(self, initialized):
        """brain_list(resource='skill') does not accept since (artefact-only)."""
        result = server.brain_list(resource="skill", since="2026-01-01")
        _assert_error(result, "does not accept top-level field 'since'")

    def test_list_skill_rejects_sort(self, initialized):
        """brain_list(resource='skill') does not accept sort (artefact-only)."""
        result = server.brain_list(resource="skill", sort="date_asc")
        _assert_error(result, "does not accept top-level field 'sort'")

    def test_list_skill_rejects_top_k(self, initialized):
        """brain_list(resource='skill') does not accept top_k (artefact-only)."""
        result = server.brain_list(resource="skill", top_k=10)
        _assert_error(result, "does not accept top-level field 'top_k'")

    def test_list_workspace_rejects_query(self, initialized):
        """brain_list(resource='workspace') does not accept query."""
        result = server.brain_list(resource="workspace", query="foo")
        _assert_error(result, "does not accept top-level field 'query'")

    def test_list_archive_rejects_query(self, initialized):
        """brain_list(resource='archive') does not accept query."""
        result = server.brain_list(resource="archive", query="old")
        _assert_error(result, "does not accept top-level field 'query'")

    def test_list_artefact_accepts_all_filters(self, initialized):
        """brain_list(resource='artefact') accepts type, since, until, tag, top_k, sort."""
        result = server.brain_list(
            resource="artefact",
            type="living/wiki",
            since="2020-01-01",
            until="2099-12-31",
            tag="brain-core",
            top_k=10,
            sort="date_asc",
        )
        # Should succeed (no error)
        assert result is not None
        text = _list_text(result)
        assert "Listed:" in text

    def test_list_artefact_rejects_query(self, initialized):
        """brain_list(resource='artefact') does not accept query (unused for artefacts)."""
        result = server.brain_list(resource="artefact", query="wiki")
        _assert_error(result, "does not accept top-level field 'query'")

    def test_list_unknown_resource(self, initialized):
        """brain_list with an unlisted resource returns a clear error."""
        result = server.brain_list(resource="bogus")
        _assert_error(result, "not listable via brain_list")


class TestBrainList:
    def test_list_all(self, initialized):
        """No filters returns all indexed documents; meta says 'Listed: N results'."""
        resp = server.brain_list()
        text = _list_text(resp)
        assert "Listed:" in text
        assert "results" in text

    def test_list_by_type(self, initialized):
        """Filter by type returns only artefacts of that type."""
        resp = server.brain_list(type="living/wiki")
        lines = _list_result_lines(resp)
        assert len(lines) >= 1
        for line in lines:
            assert "living/wiki" in line

    def test_list_by_since(self, initialized):
        """since filter with a past date returns all documents (all are newer)."""
        resp_all = server.brain_list()
        resp_since = server.brain_list(since="2020-01-01")
        assert _list_text(resp_since).count("\t") >= _list_text(resp_all).count("\t") - 1

        # since far in the future returns nothing
        resp_future = server.brain_list(since="2099-01-01")
        text = _list_text(resp_future)
        assert "0 results" in text

    def test_list_by_until(self, initialized):
        """until filter with a future date returns all; past date returns nothing."""
        resp_until = server.brain_list(until="2099-12-31")
        lines_all = _list_result_lines(server.brain_list())
        lines_until = _list_result_lines(resp_until)
        assert len(lines_until) == len(lines_all)

        resp_past = server.brain_list(until="2020-01-01")
        text = _list_text(resp_past)
        assert "0 results" in text

    def test_list_by_tag(self, initialized):
        """Tag filter returns only documents containing that tag."""
        resp = server.brain_list(tag="brain-core")
        lines = _list_result_lines(resp)
        assert len(lines) >= 1
        # Each result path should correspond to brain-overview (has brain-core tag)
        for line in lines:
            assert "brain-overview" in line or "brain-core" in line or "Wiki" in line

    def test_list_sort_date_asc(self, initialized):
        """date_asc sort returns dates in ascending order."""
        resp = server.brain_list(sort="date_asc")
        lines = _list_result_lines(resp)
        if len(lines) >= 2:
            dates = [line.split("\t")[0] for line in lines if "\t" in line]
            assert dates == sorted(dates)

    def test_list_sort_title(self, initialized):
        """title sort returns results in case-insensitive alphabetical title order."""
        resp = server.brain_list(type="living/wiki", sort="title")
        lines = _list_result_lines(resp)
        if len(lines) >= 2:
            titles = [line.split("\t")[1] for line in lines if line.count("\t") >= 1]
            assert titles == sorted(titles, key=str.lower)

    def test_list_top_k(self, initialized):
        """top_k=1 returns at most 1 result."""
        resp = server.brain_list(top_k=1)
        lines = _list_result_lines(resp)
        assert len(lines) <= 1

    def test_list_unknown_type(self, initialized):
        """Unknown type returns 0 results without raising an error."""
        resp = server.brain_list(type="living/nonexistent")
        text = _list_text(resp)
        assert "0 results" in text

    def test_list_by_parent(self, initialized):
        server.brain_create(type="wiki", title="Owner", key="owner")
        server.brain_create(type="ideas", title="Owned Idea", parent="wiki/owner")
        resp = server.brain_list(parent="wiki/owner")
        lines = _list_result_lines(resp)
        assert len(lines) == 1
        assert "Ideas/wiki~owner/" in lines[0]
        assert "parent=wiki/owner" in lines[0]

    def test_list_result_shape(self, initialized):
        """Each result line is tab-separated: date, title, path, type[, status]."""
        resp = server.brain_list(type="living/wiki")
        lines = _list_result_lines(resp)
        assert len(lines) >= 1
        for line in lines:
            parts = line.split("\t")
            assert len(parts) >= 4
            # First column is a date string YYYY-MM-DD or empty
            assert len(parts[0]) == 0 or (len(parts[0]) == 10 and parts[0][4] == "-")
        assert server._INDEX_CHECK_TTL > server._ROUTER_CHECK_TTL


class TestWorkspaceRead:
    """Tests for brain_read(resource='workspace')."""

    @pytest.fixture(autouse=True)
    def setup_workspaces(self, initialized):
        """Add workspace fixtures to the vault."""
        self.vault = initialized
        # Embedded workspace
        (initialized / "_Workspaces" / "analysis").mkdir(parents=True)
        # Hub artefact
        (initialized / "Workspaces").mkdir(parents=True)
        (initialized / "Workspaces" / "analysis.md").write_text(
            "---\ntype: living/workspace\nstatus: active\n"
            "workspace_mode: embedded\ntags:\n  - workspace/analysis\n---\n\n# Analysis\n"
        )

    def test_list_workspaces(self):
        result = server.brain_list(resource="workspace")
        text = _search_text(result)
        assert "analysis" in text

    def test_list_workspace_shape(self):
        result = server.brain_list(resource="workspace")
        text = _search_text(result)
        assert "embedded" in text

    def test_read_workspace_requires_name(self):
        result = server.brain_read("workspace")
        _assert_error(result, "requires top-level field 'name'")
        _assert_error(result, "brain_list(resource='workspace')")

    def test_resolve_workspace_by_slug(self):
        result = server.brain_read("workspace", name="analysis")
        assert "analysis" in result
        assert "embedded" in result

    def test_resolve_unknown_workspace(self):
        result = server.brain_read("workspace", name="nonexistent")
        _assert_error(result)

    def test_read_workspace_reflects_script_registration_without_restart(self, tmp_path):
        ext_path = tmp_path / "proj"
        workspace_registry.register_workspace(str(self.vault), "proj", str(ext_path))

        result = server.brain_read("workspace", name="proj")

        assert "proj" in result
        assert str(ext_path) in result
        assert "linked" in result
        assert server._workspace_registry["proj"]["path"] == str(ext_path)

    def test_list_and_read_workspace_reflect_script_unregistration_without_restart(
        self, tmp_path,
    ):
        ext_path = tmp_path / "proj"
        workspace_registry.register_workspace(str(self.vault), "proj", str(ext_path))
        assert "proj" in _search_text(server.brain_list(resource="workspace"))

        workspace_registry.unregister_workspace(str(self.vault), "proj")

        assert "proj" not in _search_text(server.brain_list(resource="workspace"))
        result = server.brain_read("workspace", name="proj")
        _assert_error(result, "Unknown workspace")
        assert "proj" not in server._workspace_registry

