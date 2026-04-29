"""Tests for build_index.py — BM25 retrieval index builder."""

import io
import json
import os
from unittest.mock import patch

import pytest

import build_index as bi
from _common import is_system_dir, iter_artefact_paths, parse_frontmatter


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
    (bc / "session-core.md").write_text("# Session Core\n")

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
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / "_Config").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Vault discovery
# ---------------------------------------------------------------------------

class TestVaultDiscovery:
    def test_is_system_dir_underscore(self):
        assert is_system_dir("_Config") is True

    def test_is_system_dir_dot(self):
        assert is_system_dir(".obsidian") is True

    def test_is_system_dir_normal(self):
        assert is_system_dir("Wiki") is False

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
        files = list(iter_artefact_paths(vault, type_info, include_status_folders=True))
        assert len(files) == 2
        assert all(f.endswith(".md") for f in files)

    def test_find_md_files_temporal_recurses(self, vault):
        type_info = {"path": os.path.join("_Temporal", "Logs")}
        files = list(iter_artefact_paths(vault, type_info, include_status_folders=True))
        assert len(files) == 1
        assert "2026-03" in files[0]

    def test_find_md_files_skips_system_subdirs(self, vault):
        obs = vault / "Wiki" / ".obsidian"
        obs.mkdir()
        (obs / "hidden.md").write_text("# Hidden\n")

        type_info = {"path": "Wiki"}
        files = list(iter_artefact_paths(vault, type_info, include_status_folders=True))
        assert not any(".obsidian" in f for f in files)

    def test_find_md_files_missing_dir(self, vault):
        type_info = {"path": "Nonexistent"}
        files = list(iter_artefact_paths(vault, type_info, include_status_folders=True))
        assert files == []


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

class TestFrontmatter:
    def test_parse_basic_frontmatter(self):
        text = "---\ntype: living/wiki\nstatus: active\n---\n\n# Title\n\nBody text."
        fields, body = parse_frontmatter(text)
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"
        assert "# Title" in body

    def test_parse_inline_tags(self):
        text = "---\ntags: [python, rust]\n---\n\nBody."
        fields, body = parse_frontmatter(text)
        assert fields["tags"] == ["python", "rust"]

    def test_parse_multiline_tags(self):
        text = "---\ntags:\n  - alpha\n  - beta\n---\n\nBody."
        fields, body = parse_frontmatter(text)
        assert fields["tags"] == ["alpha", "beta"]

    def test_parse_no_frontmatter(self):
        text = "# Just a title\n\nSome body text."
        fields, body = parse_frontmatter(text)
        assert fields == {}
        assert body == text

    def test_parse_quoted_values(self):
        text = "---\ncreated: '2026-01-01T00:00:00.000Z'\n---\n\nBody."
        fields, body = parse_frontmatter(text)
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

    def test_title_tf_includes_type_tokens(self, vault):
        index = bi.build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        # Type is "living/wiki" — both tokens should be in title_tf
        assert doc["title_tf"].get("living", 0) > 0
        assert doc["title_tf"].get("wiki", 0) > 0

    def test_type_fallback_to_folder(self, vault):
        """Missing type in frontmatter falls back to folder-derived type."""
        # Add a file without type in frontmatter
        (vault / "Wiki" / "no-type.md").write_text(
            "---\nstatus: active\n---\n\n# No Type\n\nBody.\n"
        )
        index = bi.build_index(vault)
        doc = next(d for d in index["documents"] if "no-type" in d["path"])
        assert doc["type"] == "living/wiki"


# ---------------------------------------------------------------------------
# Type description extraction
# ---------------------------------------------------------------------------

class TestExtractTypeDescription:
    @pytest.fixture
    def vault_with_taxonomy(self, tmp_path):
        """Vault with a taxonomy file containing Purpose + When To Use."""
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "VERSION").write_text("1.0.0\n")
        (bc / "session-core.md").write_text("# Session Core\n")
        cfg = tmp_path / "_Config" / "Taxonomy" / "Living"
        cfg.mkdir(parents=True)
        (cfg / "wiki.md").write_text(
            "# Wiki\n\n"
            "Living artefact. Interconnected knowledge base.\n\n"
            "## Purpose\n\n"
            "One page per concept. Reference knowledge.\n\n"
            "## When To Use\n\n"
            "When building reference knowledge about a concept.\n\n"
            "## Naming\n\n"
            "`{Title}.md` in `Wiki/`.\n"
        )
        return tmp_path

    def test_extracts_one_liner_purpose_and_when_to_use(self, vault_with_taxonomy):
        artefact = {"taxonomy_file": "_Config/Taxonomy/Living/wiki.md"}
        desc = bi.extract_type_description(vault_with_taxonomy, artefact)
        assert "Interconnected knowledge base" in desc
        assert "One page per concept" in desc
        assert "When building reference knowledge" in desc

    def test_no_purpose_returns_one_liner(self, tmp_path):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "VERSION").write_text("1.0.0\n")
        (bc / "session-core.md").write_text("# Session Core\n")
        cfg = tmp_path / "_Config" / "Taxonomy" / "Living"
        cfg.mkdir(parents=True)
        (cfg / "notes.md").write_text(
            "# Notes\n\n"
            "Living artefact. Flat knowledge base.\n\n"
            "## Naming\n\n"
            "`{Title}.md` in `Notes/`.\n"
        )
        artefact = {"taxonomy_file": "_Config/Taxonomy/Living/notes.md"}
        desc = bi.extract_type_description(tmp_path, artefact)
        assert "Flat knowledge base" in desc
        assert "Naming" not in desc

    def test_missing_file_returns_empty(self, tmp_path):
        artefact = {"taxonomy_file": "_Config/Taxonomy/Living/nonexistent.md"}
        desc = bi.extract_type_description(tmp_path, artefact)
        assert desc == ""

    def test_no_taxonomy_file_key_returns_empty(self, tmp_path):
        artefact = {}
        desc = bi.extract_type_description(tmp_path, artefact)
        assert desc == ""

    def test_extracts_trigger_section(self, tmp_path):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "VERSION").write_text("1.0.0\n")
        (bc / "session-core.md").write_text("# Session Core\n")
        cfg = tmp_path / "_Config" / "Taxonomy" / "Temporal"
        cfg.mkdir(parents=True)
        (cfg / "logs.md").write_text(
            "# Logs\n\n"
            "Temporal artefact. Daily logs.\n\n"
            "## Purpose\n\n"
            "One file per day.\n\n"
            "## Trigger\n\n"
            "After completing meaningful work.\n\n"
            "## Template\n\n"
            "[[_Config/Templates/Temporal/Logs]]\n"
        )
        artefact = {"taxonomy_file": "_Config/Taxonomy/Temporal/logs.md"}
        desc = bi.extract_type_description(tmp_path, artefact)
        assert "After completing meaningful work" in desc


# ---------------------------------------------------------------------------
# Incremental index updates
# ---------------------------------------------------------------------------

class TestIncrementalIndex:
    def test_index_add_new_document(self, vault):
        """index_add should add a new document and update corpus stats."""
        index = bi.build_index(vault)
        old_count = index["meta"]["document_count"]
        # Write a new file
        (vault / "Wiki" / "new-topic.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# New Topic\n\nUnique xylophonic content here.\n"
        )
        doc = bi.index_add(index, vault, "Wiki/new-topic.md", type_hint="living/wiki")
        assert doc is not None
        assert doc["title"] == "new-topic"
        assert index["meta"]["document_count"] == old_count + 1
        assert "xylophonic" in index["corpus_stats"]["df"]
        paths = [d["path"] for d in index["documents"]]
        assert "Wiki/new-topic.md" in paths

    def test_index_add_unreadable_returns_none(self, vault):
        """index_add returns None for a path that doesn't exist."""
        index = bi.build_index(vault)
        old_count = index["meta"]["document_count"]
        doc = bi.index_add(index, vault, "Wiki/nonexistent.md")
        assert doc is None
        assert index["meta"]["document_count"] == old_count

    def test_index_update_existing_document(self, vault):
        """index_update should replace an existing document's data."""
        index = bi.build_index(vault)
        old_count = index["meta"]["document_count"]
        # Overwrite an existing file with new content
        (vault / "Wiki" / "python-basics.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Python Basics\n\nCompletely rewritten with plumbiferous content.\n"
        )
        doc = bi.index_update(index, vault, "Wiki/python-basics.md", type_hint="living/wiki")
        assert doc is not None
        assert index["meta"]["document_count"] == old_count  # count unchanged
        assert "plumbiferous" in index["corpus_stats"]["df"]

    def test_index_update_missing_path_falls_back_to_add(self, vault):
        """index_update should add the document if path not found in index."""
        index = bi.build_index(vault)
        old_count = index["meta"]["document_count"]
        (vault / "Wiki" / "brand-new.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n# Brand New\n\nContent.\n"
        )
        doc = bi.index_update(index, vault, "Wiki/brand-new.md", type_hint="living/wiki")
        assert doc is not None
        assert index["meta"]["document_count"] == old_count + 1


# ---------------------------------------------------------------------------
# Embedding building
# ---------------------------------------------------------------------------

class TestBuildEmbeddings:
    def test_returns_none_without_deps(self, vault):
        """When sentence-transformers is unavailable, returns None."""
        with patch.object(bi, "_HAS_EMBEDDINGS", False):
            result = bi.build_embeddings(vault, {"artefacts": []}, [])
            assert result is None

    def test_routes_npy_writes_through_safe_save_wrapper(self, vault, monkeypatch):
        """build_embeddings writes both arrays through the local atomic wrapper path."""
        calls = []

        class FakeNumpy:
            @staticmethod
            def zeros(shape):
                return {"shape": shape}

            @staticmethod
            def save(handle, array):
                handle.write(b"\x93NUMPY")
                handle.write(repr(array).encode("utf-8"))

        class FakeModel:
            def encode(self, texts, normalize_embeddings=True):  # pragma: no cover - empty inputs below
                raise AssertionError("encode should not run for empty inputs")

        def fake_safe_write_via(path, writer, **kwargs):
            handle = io.BytesIO()
            writer(handle)
            calls.append((path, kwargs.get("bounds"), handle.getvalue()))
            return str(path)

        monkeypatch.setattr(bi, "_HAS_EMBEDDINGS", True)
        monkeypatch.setattr(bi, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(bi, "SentenceTransformer", lambda model: FakeModel(), raising=False)
        monkeypatch.setattr(bi, "safe_write_via", fake_safe_write_via)

        result = bi.build_embeddings(vault, {"artefacts": []}, [])

        assert result is not None
        assert [path for path, _bounds, _payload in calls] == [
            str(vault / bi.TYPE_EMBEDDINGS_REL),
            str(vault / bi.DOC_EMBEDDINGS_REL),
        ]
        assert all(bounds == str(vault) for _path, bounds, _payload in calls)
        assert all(payload.startswith(b"\x93NUMPY") for _path, _bounds, payload in calls)

    def test_document_meta_includes_type_and_title(self, vault, monkeypatch):
        """Document embedding metadata must preserve type for same-type resolve search."""
        class FakeNumpy:
            @staticmethod
            def zeros(shape):
                return {"shape": shape}

            @staticmethod
            def save(handle, array):
                handle.write(b"\x93NUMPY")
                handle.write(repr(array).encode("utf-8"))

        class FakeModel:
            def encode(self, texts, normalize_embeddings=True):
                return [[0.0] for _ in texts]

        monkeypatch.setattr(bi, "_HAS_EMBEDDINGS", True)
        monkeypatch.setattr(bi, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(bi, "SentenceTransformer", lambda model: FakeModel(), raising=False)
        monkeypatch.setattr(
            bi,
            "safe_write_via",
            lambda path, writer, **kwargs: writer(io.BytesIO()),
        )

        index = bi.build_index(vault)
        result = bi.build_embeddings(vault, {"artefacts": []}, index["documents"])

        assert result is not None
        by_path = {entry["path"]: entry for entry in result["documents"]}
        assert by_path["Wiki/python-basics.md"]["type"] == "living/wiki"
        assert by_path["Wiki/python-basics.md"]["title"] == "python-basics"


class TestEmbeddingsOutputs:
    def test_embeddings_follow_brain_process_flag(self, vault):
        cfg = {
            "defaults": {
                "flags": {
                    "brain_process": False,
                }
            }
        }

        assert bi.process_enabled(vault, config=cfg) is False
        assert bi.embeddings_enabled(vault, config=cfg) is False

        cfg["defaults"]["flags"]["brain_process"] = True
        assert bi.process_enabled(vault, config=cfg) is True
        assert bi.embeddings_enabled(vault, config=cfg) is True

    def test_persist_outputs_clears_stale_sidecars_when_disabled(self, vault):
        local_dir = vault / ".brain" / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
        for rel_path in (
            bi.TYPE_EMBEDDINGS_REL,
            bi.DOC_EMBEDDINGS_REL,
            bi.EMBEDDINGS_META_REL,
        ):
            abs_path = vault / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(b"stale")

        index = bi.build_index(vault)
        result = bi.persist_retrieval_outputs(
            vault,
            index,
            router={"artefacts": []},
            enable_embeddings=False,
        )

        assert result is None
        for rel_path in (
            bi.TYPE_EMBEDDINGS_REL,
            bi.DOC_EMBEDDINGS_REL,
            bi.EMBEDDINGS_META_REL,
        ):
            assert not (vault / rel_path).exists()

    def test_persist_outputs_clears_stale_sidecars_when_process_disabled(self, vault):
        for rel_path in (
            bi.TYPE_EMBEDDINGS_REL,
            bi.DOC_EMBEDDINGS_REL,
            bi.EMBEDDINGS_META_REL,
        ):
            abs_path = vault / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(b"stale")

        cfg = {
            "defaults": {
                "flags": {
                    "brain_process": False,
                }
            }
        }
        index = bi.build_index(vault)
        result = bi.persist_retrieval_outputs(
            vault,
            index,
            router={"artefacts": []},
            config=cfg,
        )

        assert result is None
        for rel_path in (
            bi.TYPE_EMBEDDINGS_REL,
            bi.DOC_EMBEDDINGS_REL,
            bi.EMBEDDINGS_META_REL,
        ):
            assert not (vault / rel_path).exists()


class TestBuildIndexMain:
    def test_main_refreshes_embeddings_when_router_available(self, vault, monkeypatch, capsys):
        calls = []

        def fake_build_embeddings(vault_root, router, documents):
            calls.append((str(vault_root), router, documents))
            return {"documents": [], "types": []}

        monkeypatch.setattr(bi, "find_vault_root", lambda: vault)
        monkeypatch.setattr(bi, "embeddings_enabled", lambda *args, **kwargs: True)
        monkeypatch.setattr(bi, "load_compiled_router", lambda _vault: {"artefacts": []})
        monkeypatch.setattr(bi, "build_embeddings", fake_build_embeddings)
        monkeypatch.setattr(bi.sys, "argv", ["build_index.py"])

        bi.main()

        captured = capsys.readouterr()
        assert (vault / ".brain" / "local" / "retrieval-index.json").is_file()
        assert len(calls) == 1
        assert calls[0][0] == str(vault)
        assert calls[0][1] == {"artefacts": []}
        assert calls[0][2]
        assert "embeddings refreshed" in captured.err

    def test_main_skips_embeddings_when_flag_disabled(self, vault, monkeypatch, capsys):
        calls = []

        monkeypatch.setattr(bi, "find_vault_root", lambda: vault)
        monkeypatch.setattr(bi, "embeddings_enabled", lambda *args, **kwargs: False)
        monkeypatch.setattr(bi, "load_compiled_router", lambda _vault: {"artefacts": []})
        monkeypatch.setattr(
            bi,
            "build_embeddings",
            lambda *args, **kwargs: calls.append(args) or {"documents": [], "types": []},
        )
        monkeypatch.setattr(bi.sys, "argv", ["build_index.py"])

        bi.main()

        captured = capsys.readouterr()
        assert (vault / ".brain" / "local" / "retrieval-index.json").is_file()
        assert calls == []
        assert "embeddings refreshed" not in captured.err
