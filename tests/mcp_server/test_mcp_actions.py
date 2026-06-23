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


class TestBrainActionShapePresentation:
    @pytest.fixture(autouse=True)
    def setup_presentation_files(self, initialized):
        """Add presentation template and theme to the vault fixture."""
        self.vault = initialized
        # Template
        templates_dir = initialized / "_Config" / "Templates" / "Temporal"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / "Presentations.md").write_text(
            "---\ntype: temporal/presentation\ntags:\n  - presentation\n"
            "marp: true\ntheme: brain\npaginate: true\n---\n\n"
            "<!-- _class: title -->\n\n# PRESENTATION TITLE\n\n"
            "**{{date:YYYY-MM-DD}}**\n\n"
            "**Origin:** [[source-artefact|Source document]]\n\n---\n\n## Slide 1\n"
        )
        # Theme
        skills_dir = initialized / "_Config" / "Skills" / "presentations"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "theme.css").write_text("/* @theme brain */\n")

    def test_missing_params_returns_error(self, initialized):
        result = server.brain_action("shape-presentation")
        _assert_error(result, "requires params")

    def test_missing_source_returns_error(self, initialized):
        result = server.brain_action("shape-presentation", params={"slug": "test"})
        _assert_error(result, "requires params")

    def test_missing_slug_returns_error(self, initialized):
        result = server.brain_action(
            "shape-presentation",
            params={"source": "Wiki/brain-overview-abc123.md"},
        )
        _assert_error(result, "requires params")

    def test_source_not_found_returns_error(self, initialized):
        result = server.brain_action(
            "shape-presentation",
            params={"source": "Wiki/nonexistent.md", "slug": "test"},
        )
        _assert_error(result)

    @staticmethod
    def _fake_marp_run(cmd, capture_output, text):
        pdf_path = cmd[cmd.index("-o") + 1]
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    @patch("shape_presentation.subprocess.Popen")
    @patch("shape_presentation.subprocess.run")
    def test_creates_file_and_returns_status(self, mock_run, mock_popen, initialized):
        server._index_pending.clear()
        mock_run.side_effect = self._fake_marp_run
        mock_popen.return_value.pid = 12345
        result = json.loads(server.brain_action(
            "shape-presentation",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "test-deck"},
        ))
        assert result["status"] == "ok"
        assert "presentation" in result["path"]
        assert "test-deck" in result["path"]
        assert result["created"] is True
        assert result["rendered"] is True
        assert "pdf_path" in result
        assert result["preview_pid"] == 12345
        # Verify file was created on disk
        abs_path = os.path.join(str(initialized), result["path"])
        pdf_abs = os.path.join(str(initialized), result["pdf_path"])
        assert os.path.isfile(abs_path)
        assert os.path.isfile(pdf_abs)
        assert ("_Temporal/Presentations/" in result["path"])
        assert any(rel_path == result["path"] for rel_path, _ in server._index_pending)

    def test_shape_presentation_rejects_printable_only_param(self, initialized):
        result = server.brain_action(
            "shape-presentation",
            params={
                "source": "Wiki/brain-overview-abc123.md",
                "slug": "test-deck",
                "pdf_engine": "xelatex",
            },
        )
        _assert_error(result, "does not accept params field 'pdf_engine'")

    @patch("shape_presentation.subprocess.Popen")
    @patch("shape_presentation.subprocess.run")
    def test_does_not_recreate_existing_file(self, mock_run, mock_popen, initialized):
        mock_run.side_effect = self._fake_marp_run
        mock_popen.return_value.pid = 12345
        # First call creates
        result1 = json.loads(server.brain_action(
            "shape-presentation",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "existing-deck"},
        ))
        assert result1["created"] is True
        # Second call reuses
        result2 = json.loads(server.brain_action(
            "shape-presentation",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "existing-deck"},
        ))
        assert result2["status"] == "ok"
        assert result2["created"] is False

    @patch("shape_presentation.subprocess.run", side_effect=FileNotFoundError)
    def test_works_without_marp_installed(self, mock_run, initialized):
        """Should create markdown but return partial when Marp is unavailable."""
        result = json.loads(server.brain_action(
            "shape-presentation",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "no-marp"},
        ))
        assert result["status"] == "partial"
        assert result["rendered"] is False
        assert "marp" in result["warning"]
        assert "preview_pid" not in result


class TestBrainActionShapePrintable:
    @pytest.fixture(autouse=True)
    def setup_printable_files(self, initialized):
        """Add printable template and support files to the vault fixture."""
        self.vault = initialized
        templates_dir = initialized / "_Config" / "Templates" / "Temporal"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / "Printables.md").write_text(
            "---\ntype: temporal/printable\ntags:\n  - printable\n"
            "keep_heading_with_next: true\n---\n\n"
            "# PRINTABLE TITLE\n\n"
            "**{{date:YYYY-MM-DD}}**\n\n"
            "**Origin:** [[source-artefact|Source document]]\n\n"
            "## Summary\n\n"
            "Summary text.\n"
        )
        skills_dir = initialized / "_Config" / "Skills" / "printables"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "base.tex").write_text("\\usepackage{parskip}\n")
        (skills_dir / "keep-headings.tex").write_text("\\usepackage{needspace}\n")

    def test_missing_params_returns_error(self, initialized):
        result = server.brain_action("shape-printable")
        _assert_error(result, "requires params")

    def test_missing_source_returns_error(self, initialized):
        result = server.brain_action("shape-printable", params={"slug": "brief"})
        _assert_error(result, "requires params")

    def test_missing_slug_returns_error(self, initialized):
        result = server.brain_action(
            "shape-printable",
            params={"source": "Wiki/brain-overview-abc123.md"},
        )
        _assert_error(result, "requires params")

    def test_source_not_found_returns_error(self, initialized):
        result = server.brain_action(
            "shape-printable",
            params={"source": "Wiki/nonexistent.md", "slug": "brief"},
        )
        _assert_error(result)

    @staticmethod
    def _fake_which_default(cmd):
        if cmd in {"pandoc", "xelatex"}:
            return f"/usr/bin/{cmd}"
        return None

    @staticmethod
    def _fake_pandoc_run(cmd, capture_output, text):
        pdf_path = cmd[cmd.index("--output") + 1]
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_creates_file_and_renders_pdf(self, mock_which, mock_run, initialized):
        server._index_pending.clear()
        mock_which.side_effect = self._fake_which_default
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action(
            "shape-printable",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "board-brief"},
        ))
        assert result["status"] == "ok"
        assert "printable" in result["path"]
        assert "board-brief" in result["path"]
        assert result["created"] is True
        assert result["rendered"] is True
        assert result["pdf_engine"] == "xelatex"
        abs_path = os.path.join(str(initialized), result["path"])
        pdf_abs = os.path.join(str(initialized), result["pdf_path"])
        assert os.path.isfile(abs_path)
        assert os.path.isfile(pdf_abs)
        assert any(rel_path == result["path"] for rel_path, _ in server._index_pending)
        cmd = mock_run.call_args[0][0]
        assert any(arg.endswith("keep-headings.tex") for arg in cmd)

    def test_shape_printable_rejects_presentation_only_param(self, initialized):
        result = server.brain_action(
            "shape-printable",
            params={
                "source": "Wiki/brain-overview-abc123.md",
                "slug": "board-brief",
                "preview": True,
            },
        )
        _assert_error(result, "does not accept params field 'preview'")

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_can_disable_keep_heading_with_next(self, mock_which, mock_run, initialized):
        mock_which.side_effect = self._fake_which_default
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action(
            "shape-printable",
            params={
                "source": "Wiki/brain-overview-abc123.md",
                "slug": "tight-layout",
                "keep_heading_with_next": False,
            },
        ))
        assert result["status"] == "ok"
        assert result["keep_heading_with_next"] is False
        cmd = mock_run.call_args[0][0]
        assert not any(arg.endswith("keep-headings.tex") for arg in cmd)

    @patch("shape_printable.shutil.which")
    def test_works_without_pandoc_installed(self, mock_which, initialized):
        def fake_which(cmd):
            if cmd == "pandoc":
                return None
            if cmd == "xelatex":
                return "/usr/bin/xelatex"
            return None

        mock_which.side_effect = fake_which
        result = json.loads(server.brain_action(
            "shape-printable",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "no-pandoc"},
        ))
        assert result["status"] == "partial"
        assert result["rendered"] is False
        assert "pandoc" in result["warning"]

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_uses_local_config_tool_paths(self, mock_which, mock_run, initialized):
        config_dir = initialized / ".brain" / "local"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(
            "defaults:\n"
            "  tool_paths:\n"
            "    pandoc: /opt/brain-tools/pandoc\n"
            "    xelatex: /opt/brain-tools/xelatex\n"
        )

        def fake_which(cmd):
            if cmd in {"/opt/brain-tools/pandoc", "/opt/brain-tools/xelatex"}:
                return cmd
            return None

        mock_which.side_effect = fake_which
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action(
            "shape-printable",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "configured-tools"},
        ))
        assert result["status"] == "ok"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/opt/brain-tools/pandoc"
        assert "--pdf-engine=/opt/brain-tools/xelatex" in cmd

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_env_tool_paths_override_local_config(self, mock_which, mock_run, initialized, monkeypatch):
        config_dir = initialized / ".brain" / "local"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(
            "defaults:\n"
            "  tool_paths:\n"
            "    pandoc: /opt/brain-tools/pandoc-config\n"
            "    xelatex: /opt/brain-tools/xelatex-config\n"
        )
        monkeypatch.setenv("BRAIN_PANDOC_PATH", "/env/brain-tools/pandoc")
        monkeypatch.setenv("BRAIN_XELATEX_PATH", "/env/brain-tools/xelatex")

        def fake_which(cmd):
            if cmd in {
                "/env/brain-tools/pandoc",
                "/env/brain-tools/xelatex",
                "/opt/brain-tools/pandoc-config",
                "/opt/brain-tools/xelatex-config",
            }:
                return cmd
            return None

        mock_which.side_effect = fake_which
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action(
            "shape-printable",
            params={"source": "Wiki/brain-overview-abc123.md", "slug": "env-tools"},
        ))
        assert result["status"] == "ok"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/env/brain-tools/pandoc"
        assert "--pdf-engine=/env/brain-tools/xelatex" in cmd


class TestBrainActionStartShaping:
    @pytest.fixture(autouse=True)
    def setup_shaping_files(self, initialized):
        """Add shaping transcript template and a design with status."""
        self.vault = initialized
        # Designs dir + taxonomy
        designs_dir = initialized / "Designs"
        designs_dir.mkdir(exist_ok=True)
        (designs_dir / "Test Design.md").write_text(
            "---\ntype: living/designs\ntags:\n  - design\nstatus: new\n"
            "created: 2026-03-01T10:00:00+00:00\n"
            "modified: 2026-03-01T10:00:00+00:00\n---\n\n"
            "# Test Design\n\nA design.\n"
        )
        tax_living = initialized / "_Config" / "Taxonomy" / "Living"
        (tax_living / "designs.md").write_text(
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "| `new` | Newly created |\n"
            "| `shaping` | Being shaped |\n"
            "| `ready` | Ready |\n\n"
            "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design\n"
            "status: new  # new | shaping | ready\n---\n```\n\n"
            "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
        )
        templates_living = initialized / "_Config" / "Templates" / "Living"
        templates_living.mkdir(parents=True, exist_ok=True)
        (templates_living / "Designs.md").write_text(
            "---\ntype: living/designs\ntags: []\nstatus: new\n---\n\n"
        )
        # Shaping transcript taxonomy + template
        tax_temporal = initialized / "_Config" / "Taxonomy" / "Temporal"
        (tax_temporal / "shaping-transcripts.md").write_text(
            "# Shaping Transcripts\n\n"
            "## Naming\n\n`yyyymmdd-shaping-transcript~{Title}.md` in "
            "`_Temporal/Shaping Transcripts/yyyy-mm/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: temporal/shaping-transcript\ntags:\n"
            "  - transcript\n---\n```\n\n"
            "## Template\n\n[[_Config/Templates/Temporal/Shaping Transcripts]]\n"
        )
        temporal = initialized / "_Temporal"
        temporal.mkdir(exist_ok=True)
        (temporal / "Shaping Transcripts").mkdir(exist_ok=True)
        templates_temporal = initialized / "_Config" / "Templates" / "Temporal"
        templates_temporal.mkdir(parents=True, exist_ok=True)
        (templates_temporal / "Shaping Transcripts.md").write_text(
            "---\ntype: temporal/shaping-transcript\ntags:\n  - transcript\n"
            "  - SOURCE_TYPE\n---\n"
            "Shaping transcript for [[SOURCE_DOC_PATH|SOURCE_DOC_TITLE]].\n\n"
            "## {{date:YYYY-MM-DD}}\n\nQ.\n> A.\n"
        )
        # Force a router rebuild so the new taxonomy is definitely visible.
        server._router = server._compile_and_save(str(initialized))

    def test_missing_params_returns_error(self):
        result = server.brain_action("start-shaping")
        _assert_error(result, "requires params")

    def test_missing_target_returns_error(self):
        result = server.brain_action("start-shaping", params={"title": "Missing target"})
        _assert_error(result, "requires params")

    def test_target_not_found_returns_error(self):
        result = server.brain_action("start-shaping", params={"target": "Nonexistent File"})
        _assert_error(result)

    def test_happy_path_creates_transcript(self):
        server._index_pending.clear()
        result = json.loads(server.brain_action(
            "start-shaping",
            params={"target": "Designs/Test Design.md"},
        ))
        assert result["status"] == "ok"
        assert result["target_path"] == "Designs/Test Design.md"
        assert "shaping-transcript" in result["transcript_path"]
        assert result["set_status"] is True
        # Transcript exists on disk
        abs_path = os.path.join(str(self.vault), result["transcript_path"])
        assert os.path.isfile(abs_path)
        pending_paths = [rel_path for rel_path, _ in server._index_pending]
        assert result["target_path"] in pending_paths
        assert result["transcript_path"] in pending_paths

    def test_start_shaping_revives_terminal_folder_path(self):
        terminal_dir = self.vault / "Designs" / "+Implemented"
        terminal_dir.mkdir(exist_ok=True)
        source_path = terminal_dir / "Revived Design.md"
        source_path.write_text(
            "---\ntype: living/designs\ntags:\n  - design\nstatus: implemented\n"
            "created: 2026-03-01T10:00:00+00:00\n"
            "modified: 2026-03-01T10:00:00+00:00\n---\n\n"
            "# Revived Design\n\nA terminal design.\n"
        )
        tax_living = self.vault / "_Config" / "Taxonomy" / "Living"
        (tax_living / "designs.md").write_text(
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "| `new` | Newly created |\n"
            "| `shaping` | Being shaped |\n"
            "| `ready` | Ready |\n"
            "| `implemented` | Implemented |\n\n"
            "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design\n"
            "status: new  # new | shaping | ready | implemented\n---\n```\n\n"
            "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
        )
        server._router = server._compile_and_save(str(self.vault))

        result = json.loads(server.brain_action(
            "start-shaping",
            params={"target": "Designs/+Implemented/Revived Design.md"},
        ))

        assert result["status"] == "ok"
        assert result["target_path"] == "Designs/Revived Design.md"
        assert not source_path.exists()
        revived = self.vault / "Designs" / "Revived Design.md"
        assert revived.exists()
        assert "status: shaping" in revived.read_text()

    def test_start_shaping_forces_router_refresh(self):
        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_action(
                "start-shaping",
                params={"target": "Designs/Test Design.md"},
            )
        assert '"status": "ok"' in result
        mock_ensure.assert_called_once_with()


class TestBrainProcess:
    @pytest.fixture(autouse=True)
    def enable_process_feature(self, initialized, monkeypatch):
        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_processing"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None
        monkeypatch.setattr(server, "_ensure_embeddings_fresh", lambda: None)

    def test_process_classify_context_assembly(self, initialized):
        result = server.brain_process(
            operation="classify",
            content="I have a new idea for solar powered keyboards",
            mode="context_assembly",
        )
        assert isinstance(result, str)
        assert "context_assembly" in result

    def test_process_classify_bm25(self, initialized):
        # Add Purpose/When To Use to taxonomy for BM25 scoring
        tax_ideas = os.path.join(str(initialized), "_Config", "Taxonomy", "Living", "ideas.md")
        with open(tax_ideas, "w") as f:
            f.write(
                "# Ideas\n\n"
                "A concept that needs iterative refinement.\n\n"
                "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
                "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\n---\n```\n\n"
                "## Purpose\n\nCapture concepts that need development.\n\n"
                "## When To Use\n\nWhen developing a concept that needs iterative refinement.\n\n"
                "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
        )
        # Re-initialize to rebuild index
        server.startup(vault_root=str(initialized))
        assert server._wait_for_warmup()
        server._config["defaults"]["flags"]["semantic_processing"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        result = server.brain_process(
            operation="classify",
            content="a new concept idea that needs iterative development and refinement",
            mode="bm25_only",
        )
        assert isinstance(result, str)
        assert "**Classified**" in result
        assert "bm25_only" in result

    def test_process_resolve_create(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Some content about a brand new topic",
            type="wiki",
            title="Quantum Computing Primer",
        )
        assert isinstance(result, str)
        assert "**Resolve**" in result
        assert "create" in result

    def test_process_resolve_update(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Updated information",
            type="wiki",
            title="brain-overview-abc123",
        )
        assert isinstance(result, str)
        assert "**Resolve**" in result
        assert "update" in result

    def test_process_resolve_missing_params(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Some content",
        )
        _assert_error(result, "requires top-level field 'type'")

    def test_process_classify_rejects_type_hint(self, initialized):
        result = server.brain_process(
            operation="classify",
            content="Some content",
            type="wiki",
        )
        _assert_error(result, "does not accept top-level field 'type'")

    def test_process_resolve_rejects_mode(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Some content",
            type="wiki",
            title="Quantum Computing Primer",
            mode="auto",
        )
        _assert_error(result, "does not accept top-level field 'mode'")

    def test_process_ingest_creates_file(self, initialized):
        result = server.brain_process(
            operation="ingest",
            content="# Quantum Coffee\n\nWhat if coffee brewed itself?",
            type="ideas",
        )
        assert isinstance(result, str)
        assert "**Ingested**" in result
        assert "created" in result

    def test_process_ingest_needs_classification(self, initialized):
        # Without embeddings or BM25 type descriptions, falls to context_assembly
        result = server.brain_process(
            operation="ingest",
            content="Random content without hints",
        )
        # Should return context_assembly since no scoring available
        assert isinstance(result, str)
        assert "context_assembly" in result

    def test_process_classify_degrades_without_index(self, initialized):
        server._index = None

        result = server.brain_process(
            operation="classify",
            content="I have a new idea",
        )

        assert isinstance(result, str)
        assert "context_assembly" in result

    def test_process_unknown_operation(self, initialized):
        result = server.brain_process(
            operation="nonexistent",
            content="test",
        )
        _assert_error(result, "Unknown process operation")


class TestBrainProcessFeatureGate:
    def test_process_runs_in_degraded_mode_by_default(self, initialized):
        result = server.brain_process(
            operation="classify",
            content="I have a new idea",
        )
        assert isinstance(result, str)
        assert "context_assembly" in result

