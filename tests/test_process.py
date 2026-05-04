"""Tests for process.py — classify, resolve, and ingest operations."""

import os
import sys
import types

import pytest

import build_index
import compile_router
import process


@pytest.fixture
def vault(tmp_path):
    """Create a vault with typed folders, taxonomy, and templates."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text("Brain vault.\n\nAlways:\n- Typed folders.\n")

    (tmp_path / "Ideas").mkdir()
    (tmp_path / "Wiki").mkdir()

    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()

    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "A concept that needs iterative refinement.\n\n"
        "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags:\n  - idea-tag\nstatus: shaping\n---\n```\n\n"
        "## Purpose\n\nCapture concepts that need development before becoming actionable.\n\n"
        "## When To Use\n\nWhen developing a concept that needs iterative refinement.\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "Reference knowledge about a concept.\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Purpose\n\nBuild reference knowledge about concepts.\n\n"
        "## When To Use\n\nWhen building reference knowledge about a concept you want to understand.\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "Session logs recording work activity.\n\n"
        "## Naming\n\n`log~{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n\n"
        "## Purpose\n\nRecord what happened during a work session.\n\n"
        "## When To Use\n\nWhen recording what happened during a work session.\n\n"
        "## Template\n\n[[_Config/Templates/Temporal/Logs]]\n"
    )

    tpl_living = config / "Templates" / "Living"
    tpl_living.mkdir(parents=True)
    (tpl_living / "Ideas.md").write_text(
        "---\ntype: living/ideas\ntags: []\nstatus: shaping\n---\n\n# {{title}}\n\nWhat if...\n"
    )
    (tpl_living / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )

    tpl_temporal = config / "Templates" / "Temporal"
    tpl_temporal.mkdir(parents=True)
    (tpl_temporal / "Logs.md").write_text(
        "---\ntype: temporal/logs\ntags:\n  - session\n---\n\n# Log\n\n"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    """Compile the router for the vault."""
    return compile_router.compile(str(vault))


@pytest.fixture
def index(vault):
    """Build a retrieval index for the vault."""
    return build_index.build_index(str(vault))


@pytest.fixture
def populated_vault(vault):
    """Vault with existing content files."""
    (vault / "Ideas" / "Solar Powered Keyboards.md").write_text(
        "---\ntype: living/ideas\ntags: [hardware]\nstatus: shaping\n---\n\n"
        "# Solar Powered Keyboards\n\nWhat if keyboards could charge from ambient light?\n"
    )
    (vault / "Wiki" / "Python Basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python]\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language.\n"
    )
    return vault


@pytest.fixture
def populated_router(populated_vault):
    return compile_router.compile(str(populated_vault))


@pytest.fixture
def populated_index(populated_vault):
    return build_index.build_index(str(populated_vault))


class TestInferTitle:
    def test_h1_heading(self):
        assert process.infer_title("# My Great Idea\n\nSome body text.") == "My Great Idea"

    def test_first_line_fallback(self):
        assert process.infer_title("Some content without headings\n\nMore text.") == "Some content without headings"

    def test_empty_content(self):
        assert process.infer_title("") == "Untitled"

    def test_truncates_to_60(self):
        long_title = "A" * 100
        result = process.infer_title(f"# {long_title}\n\nBody.")
        assert len(result) == 60

    def test_skips_blank_lines(self):
        assert process.infer_title("\n\n\nActual content") == "Actual content"


class TestClassifyContextAssembly:
    def test_returns_type_descriptions(self, router, vault):
        result = process.classify_content(
            router,
            str(vault),
            "I have a new idea for a tool",
            mode="context_assembly",
        )
        assert result["mode"] == "context_assembly"
        assert "type_descriptions" in result
        assert "instruction" in result
        assert len(result["type_descriptions"]) > 0
        entry = result["type_descriptions"][0]
        assert "type" in entry
        assert "key" in entry
        assert "description" in entry


class TestClassifyBm25:
    def test_returns_matches(self, router, vault, index):
        result = process.classify_content(
            router,
            str(vault),
            "I want to develop a concept and refine an idea iteratively",
            index=index,
            mode="bm25_only",
        )
        assert result is not None
        assert result["mode"] == "bm25_only"
        assert "type" in result
        assert "confidence" in result
        assert "alternatives" in result

    def test_ranks_relevant_type_higher(self, router, vault, index):
        result = process.classify_content(
            router,
            str(vault),
            "a new idea that needs iterative refinement and development of a concept",
            index=index,
            mode="bm25_only",
        )
        assert result is not None
        assert result["key"] == "ideas"


class TestClassifyAuto:
    def test_without_embeddings_falls_back_to_bm25(self, router, vault, index):
        result = process.classify_content(
            router,
            str(vault),
            "develop a concept iteratively",
            index=index,
        )
        assert result["mode"] == "bm25_only"

    def test_without_anything_falls_back_to_context_assembly(self, router, vault):
        result = process.classify_content(router, str(vault), "some content")
        assert result["mode"] == "context_assembly"


class TestResolve:
    def test_create_novel(self, router, vault):
        result = process.resolve_content(router, str(vault), "ideas", "Quantum Coffee Maker")
        assert result["action"] == "create"
        assert result["key"] == "ideas"
        assert result["title"] == "Quantum Coffee Maker"

    def test_update_filename_match(self, populated_vault, populated_router):
        result = process.resolve_content(
            populated_router,
            str(populated_vault),
            "ideas",
            "Solar Powered Keyboards",
        )
        assert result["action"] == "update"
        assert "Solar Powered Keyboards.md" in result["target_path"]

    def test_update_legacy_slug_match(self, populated_vault, populated_router):
        (populated_vault / "Wiki" / "rust-ownership.md").write_text(
            "---\ntype: living/wiki\ntags: [rust]\n---\n\n# Rust Ownership\n\nOwnership model.\n"
        )
        result = process.resolve_content(
            populated_router,
            str(populated_vault),
            "wiki",
            "Rust Ownership",
        )
        assert result["action"] == "update"
        assert "rust-ownership.md" in result["target_path"]

    def test_update_match_in_status_subfolder(self, populated_vault, populated_router):
        status_dir = populated_vault / "Ideas" / "+Shaping"
        status_dir.mkdir()
        source = populated_vault / "Ideas" / "Solar Powered Keyboards.md"
        target = status_dir / "Solar Powered Keyboards.md"
        source.rename(target)

        result = process.resolve_content(
            populated_router,
            str(populated_vault),
            "ideas",
            "Solar Powered Keyboards",
        )
        assert result["action"] == "update"
        assert result["target_path"] == "Ideas/+Shaping/Solar Powered Keyboards.md"

    def test_update_match_temporal_display_name(self, populated_vault, populated_router):
        month_dir = populated_vault / "_Temporal" / "Logs" / "2026-04"
        month_dir.mkdir(parents=True, exist_ok=True)
        (month_dir / "20260430-log~Session Notes.md").write_text(
            "---\ntype: temporal/logs\ntags: [session]\n---\n\n# Session Notes\n\nRecorded work.\n"
        )

        result = process.resolve_content(
            populated_router,
            str(populated_vault),
            "logs",
            "Session Notes",
        )
        assert result["action"] == "update"
        assert result["target_path"] == "_Temporal/Logs/2026-04/20260430-log~Session Notes.md"

    def test_unknown_type_error(self, router, vault):
        result = process.resolve_content(router, str(vault), "nonexistent", "Test")
        assert result["action"] == "error"

    def test_no_index_works(self, router, vault):
        result = process.resolve_content(
            router,
            str(vault),
            "wiki",
            "Brand New Topic",
            index=None,
        )
        assert result["action"] == "create"

    def test_update_via_embeddings_same_type_only(self, populated_vault, populated_router, monkeypatch):
        (populated_vault / "Wiki" / "rust-ownership.md").write_text(
            "---\ntype: living/wiki\ntags: [rust]\n---\n\n# Rust Ownership\n\nOwnership model.\n"
        )

        class FakeEmbeddings:
            def __matmul__(self, query_vec):
                assert query_vec == "query-vector"
                return [0.96, 0.99]

        class FakeModel:
            def encode(self, texts, normalize_embeddings=True):
                assert texts == ["Ownership Primer Rust ownership memory safety"]
                return ["query-vector"]

        fake_numpy = types.ModuleType("numpy")
        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = lambda model: FakeModel()

        monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_sentence_transformers)

        result = process.resolve_content(
            populated_router,
            str(populated_vault),
            "wiki",
            "Ownership Primer",
            content="Rust ownership memory safety",
            index=None,
            doc_embeddings=FakeEmbeddings(),
            doc_embeddings_meta={
                "documents": [
                    {
                        "path": "Wiki/rust-ownership.md",
                        "type": "living/wiki",
                        "title": "Rust Ownership",
                    },
                    {
                        "path": "Ideas/Solar Powered Keyboards.md",
                        "type": "living/ideas",
                        "title": "Solar Powered Keyboards",
                    },
                ],
            },
        )

        assert result["action"] == "update"
        assert result["target_path"] == "Wiki/rust-ownership.md"
        assert result["candidates"] == ["Wiki/rust-ownership.md"]


class TestIngest:
    def test_creates_new_artefact(self, vault, router):
        result = process.ingest_content(
            router,
            str(vault),
            "# Quantum Coffee\n\nWhat if coffee brewed itself?",
            type_hint="ideas",
        )
        assert result["action_taken"] == "created"
        assert result["path"] is not None
        assert result["needs_decision"] is False
        abs_path = os.path.join(str(vault), result["path"])
        assert os.path.isfile(abs_path)

    def test_appends_to_existing(self, populated_vault, populated_router):
        result = process.ingest_content(
            populated_router,
            str(populated_vault),
            "\n\nNew thought about solar keyboards.",
            title="Solar Powered Keyboards",
            type_hint="ideas",
        )
        assert result["action_taken"] == "updated"
        assert "Solar Powered Keyboards.md" in result["path"]

    def test_ambiguous_skips(self, populated_vault, populated_router, populated_index):
        (populated_vault / "Ideas" / "Solar Keyboard Idea.md").write_text(
            "---\ntype: living/ideas\ntags: [hardware]\nstatus: shaping\n---\n\n"
            "# Solar Keyboard Idea\n\nSolar powered keyboard concept.\n"
        )
        index = build_index.build_index(str(populated_vault))
        from unittest.mock import patch

        with patch("process.resolve_content") as mock_resolve:
            mock_resolve.return_value = {
                "action": "ambiguous",
                "type": "living/ideas",
                "key": "ideas",
                "title": "Solar Keyboards",
                "target_path": None,
                "candidates": [
                    "Ideas/Solar Powered Keyboards.md",
                    "Ideas/Solar Keyboard Idea.md",
                ],
                "reasoning": "Possible matches found",
            }
            result = process.ingest_content(
                populated_router,
                str(populated_vault),
                "Solar keyboard thoughts",
                title="Solar Keyboards",
                type_hint="ideas",
                index=index,
            )
        assert result["action_taken"] == "ambiguous"
        assert result["needs_decision"] is True

    def test_infers_title_from_content(self, vault, router):
        result = process.ingest_content(
            router,
            str(vault),
            "# Auto Title Test\n\nBody content here.",
            type_hint="wiki",
        )
        assert result["title"] == "Auto Title Test"
        assert result["action_taken"] == "created"

    def test_invalid_type_hint(self, vault, router):
        result = process.ingest_content(
            router,
            str(vault),
            "Some content",
            type_hint="nonexistent_type",
        )
        assert result["action_taken"] == "error"

    def test_no_type_hint_no_index_needs_classification(self, vault, router):
        result = process.ingest_content(router, str(vault), "Random content without any hints")
        assert result["action_taken"] == "needs_classification"
        assert result["needs_decision"] is True
