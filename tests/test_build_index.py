"""Tests for build_index.py — BM25 retrieval index builder."""

import json
import os

import pytest

import build_index as bi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault with some markdown files."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")

    # _Config
    (tmp_path / "_Config").mkdir()

    # Living type: Wiki with 2 files
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python, programming]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language used for web development, "
        "data science, and automation. Variables in Python are dynamically typed.\n"
    )
    (wiki / "rust-ownership.md").write_text(
        "---\ntype: living/wiki\ntags: [rust, systems]\nstatus: active\n---\n\n"
        "# Rust Ownership\n\nRust uses an ownership system to manage memory without a garbage "
        "collector. Each value has exactly one owner.\n"
    )

    # Living type: Designs
    designs = tmp_path / "Designs"
    designs.mkdir()
    (designs / "brain-tooling.md").write_text(
        "---\ntype: living/design\ntags: [brain-core, tooling]\nstatus: active\n---\n\n"
        "# Brain Tooling Design\n\nThis document describes the tooling architecture for brain-core. "
        "The compiled router is the foundation. Scripts are self-contained Python files.\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs"
    logs.mkdir()
    month = logs / "2026-03"
    month.mkdir()
    (month / "20260315-retrieval-research.md").write_text(
        "---\ntype: temporal/logs\ntags: [ai, retrieval]\nstatus: done\n---\n\n"
        "# Retrieval Research Log\n\nResearched BM25 and vector search approaches. "
        "BM25 is great for keyword matching. Vector search handles semantic similarity.\n"
    )

    return tmp_path


@pytest.fixture
def empty_vault(tmp_path):
    """Create a vault with no content files."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (tmp_path / "_Config").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Vault discovery
# ---------------------------------------------------------------------------

class TestVaultDiscovery:
    def test_is_system_dir_underscore(self):
        assert bi.is_system_dir("_Config") is True

    def test_is_system_dir_dot(self):
        assert bi.is_system_dir(".obsidian") is True

    def test_is_system_dir_normal(self):
        assert bi.is_system_dir("Wiki") is False

    def test_scan_living_types(self, vault):
        types = bi.scan_living_types(vault)
        folders = [t["folder"] for t in types]
        assert "Wiki" in folders
        assert "Designs" in folders
        assert "_Config" not in folders
        assert "_Temporal" not in folders

    def test_scan_temporal_types(self, vault):
        types = bi.scan_temporal_types(vault)
        assert len(types) == 1
        assert types[0]["type"] == "temporal/logs"
        assert types[0]["path"] == os.path.join("_Temporal", "Logs")

    def test_scan_temporal_empty(self, empty_vault):
        types = bi.scan_temporal_types(empty_vault)
        assert types == []


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

class TestFileDiscovery:
    def test_find_md_files_living(self, vault):
        type_info = {"path": "Wiki"}
        files = bi.find_md_files(vault, type_info)
        assert len(files) == 2
        assert all(f.endswith(".md") for f in files)

    def test_find_md_files_temporal_recurses(self, vault):
        type_info = {"path": os.path.join("_Temporal", "Logs")}
        files = bi.find_md_files(vault, type_info)
        assert len(files) == 1
        assert "2026-03" in files[0]

    def test_find_md_files_skips_system_subdirs(self, vault):
        # Add a .obsidian subdir with an .md file
        obs = vault / "Wiki" / ".obsidian"
        obs.mkdir()
        (obs / "hidden.md").write_text("# Hidden\n")

        type_info = {"path": "Wiki"}
        files = bi.find_md_files(vault, type_info)
        assert not any(".obsidian" in f for f in files)

    def test_find_md_files_missing_dir(self, vault):
        type_info = {"path": "Nonexistent"}
        files = bi.find_md_files(vault, type_info)
        assert files == []


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

class TestFrontmatter:
    def test_parse_basic_frontmatter(self):
        text = "---\ntype: living/wiki\nstatus: active\n---\n\n# Title\n\nBody text."
        fields, body = bi.parse_frontmatter(text)
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"
        assert "# Title" in body

    def test_parse_inline_tags(self):
        text = "---\ntags: [python, rust]\n---\n\nBody."
        fields, body = bi.parse_frontmatter(text)
        assert fields["tags"] == ["python", "rust"]

    def test_parse_multiline_tags(self):
        text = "---\ntags:\n  - alpha\n  - beta\n---\n\nBody."
        fields, body = bi.parse_frontmatter(text)
        assert fields["tags"] == ["alpha", "beta"]

    def test_parse_no_frontmatter(self):
        text = "# Just a title\n\nSome body text."
        fields, body = bi.parse_frontmatter(text)
        assert fields == {}
        assert body == text

    def test_parse_quoted_values(self):
        text = "---\ncreated: '2026-01-01T00:00:00.000Z'\n---\n\nBody."
        fields, body = bi.parse_frontmatter(text)
        assert fields["created"] == "2026-01-01T00:00:00.000Z"

    def test_extract_title_uses_filename_stem(self):
        assert bi.extract_title("# My Title\n\nBody", "file.md") == "file"

    def test_extract_title_strips_extension(self):
        assert bi.extract_title("No heading here.", "my-file.md") == "my-file"

    def test_extract_title_with_path(self):
        assert bi.extract_title("## Not H1\n\nBody", "Wiki/fallback.md") == "fallback"


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_basic(self):
        tokens = bi.tokenise("Hello World")
        assert tokens == ["hello", "world"]

    def test_strips_short_tokens(self):
        tokens = bi.tokenise("I am a big fan")
        assert "i" not in tokens
        assert "am" in tokens
        assert "big" in tokens

    def test_handles_punctuation(self):
        tokens = bi.tokenise("BM25 is great! (really)")
        assert "bm25" in tokens
        assert "great" in tokens
        assert "really" in tokens

    def test_empty_string(self):
        assert bi.tokenise("") == []


# ---------------------------------------------------------------------------
# Full index build
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_index_structure(self, vault):
        index = bi.build_index(vault)
        assert "meta" in index
        assert "bm25_params" in index
        assert "corpus_stats" in index
        assert "documents" in index

    def test_meta_fields(self, vault):
        index = bi.build_index(vault)
        meta = index["meta"]
        assert meta["brain_core_version"] == "1.0.0"
        assert meta["index_version"] == "1.0.0"
        assert "built_at" in meta
        assert meta["document_count"] == 4

    def test_bm25_params(self, vault):
        index = bi.build_index(vault)
        assert index["bm25_params"]["k1"] == 1.5
        assert index["bm25_params"]["b"] == 0.75

    def test_document_count(self, vault):
        index = bi.build_index(vault)
        assert len(index["documents"]) == 4

    def test_document_fields(self, vault):
        index = bi.build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert doc["title"] == "python-basics"
        assert doc["type"] == "living/wiki"
        assert "python" in doc["tags"]
        assert doc["status"] == "active"
        assert doc["doc_length"] > 0
        assert isinstance(doc["tf"], dict)
        assert doc["tf"].get("python", 0) > 0

    def test_corpus_stats(self, vault):
        index = bi.build_index(vault)
        stats = index["corpus_stats"]
        assert stats["total_docs"] == 4
        assert stats["avg_dl"] > 0
        assert isinstance(stats["df"], dict)
        assert stats["df"].get("python", 0) >= 1

    def test_empty_vault(self, empty_vault):
        index = bi.build_index(empty_vault)
        assert index["meta"]["document_count"] == 0
        assert index["documents"] == []
        assert index["corpus_stats"]["total_docs"] == 0

    def test_relative_paths(self, vault):
        index = bi.build_index(vault)
        for doc in index["documents"]:
            assert not os.path.isabs(doc["path"])

    def test_idempotent(self, vault):
        """Two builds produce same output except timestamp."""
        idx1 = bi.build_index(vault)
        idx2 = bi.build_index(vault)
        # Remove timestamps for comparison
        idx1["meta"].pop("built_at")
        idx2["meta"].pop("built_at")
        # Remove modified times (filesystem-dependent)
        for d in idx1["documents"]:
            d.pop("modified", None)
        for d in idx2["documents"]:
            d.pop("modified", None)
        assert idx1 == idx2

    def test_type_from_frontmatter_preferred(self, vault):
        """Type in frontmatter takes precedence over folder-derived type."""
        index = bi.build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert doc["type"] == "living/wiki"

    def test_title_tf_present(self, vault):
        index = bi.build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert "title_tf" in doc
        assert doc["title_tf"].get("python", 0) > 0
        assert doc["title_tf"].get("basics", 0) > 0

    def test_type_fallback_to_folder(self, vault):
        """Missing type in frontmatter falls back to folder-derived type."""
        # Add a file without type in frontmatter
        (vault / "Wiki" / "no-type.md").write_text(
            "---\nstatus: active\n---\n\n# No Type\n\nBody.\n"
        )
        index = bi.build_index(vault)
        doc = next(d for d in index["documents"] if "no-type" in d["path"])
        assert doc["type"] == "living/wiki"
