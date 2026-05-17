"""Tests for build_index.py — BM25 retrieval index builder."""

import io
import copy
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import _lifecycle.retrieval_assets as retrieval_assets
import _lifecycle.retrieval_errors as retrieval_errors
import _search.index as search_index_mod
import _search.lexical as lexical
import _semantic.assets as semantic_assets
import _semantic.config as _semantic_config
import _semantic.model as _semantic_model
import _semantic.runtime as _semantic
from _search.index import (
    OUTPUT_PATH,
    build_index,
    extract_title,
    index_update,
    persist_retrieval_index,
)
from _common import (
    is_system_dir,
    iter_artefact_paths,
    parse_frontmatter,
    scan_living_types,
    scan_temporal_types,
)


def assert_corpus_stats_match_recompute(index):
    """Assert incremental corpus stats match a full recompute (modulo built_at)."""
    expected = copy.deepcopy(index)
    search_index_mod._recompute_corpus_stats(expected)
    assert index["corpus_stats"]["df"] == expected["corpus_stats"]["df"]
    assert index["corpus_stats"]["total_docs"] == expected["corpus_stats"]["total_docs"]
    assert index["corpus_stats"]["avg_dl"] == expected["corpus_stats"]["avg_dl"]
    assert index["meta"]["document_count"] == expected["meta"]["document_count"]
    assert index["meta"]["avg_doc_length"] == expected["meta"]["avg_doc_length"]


def built_index(vault):
    """Build the canonical result and return just the lexical index payload."""
    return build_index(vault).index


def _load_build_index_cli_module():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "src" / "brain-core" / "scripts" / "build_index.py"
    spec = importlib.util.spec_from_file_location("_test_build_index_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
        "BM25 is great for keyword matching.\n"
    )

    return tmp_path


def _model_manifest():
    return _semantic_model.ModelManifest(
        model_name=_semantic_model.SHIPPED_MODEL_NAME,
        revision=_semantic_model.SHIPPED_MODEL_REVISION,
        provisioned_at="2026-05-06T00:00:00+10:00",
    )


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
        types = scan_living_types(vault)
        folders = [t["folder"] for t in types]
        assert "Wiki" in folders
        assert "Designs" in folders
        assert "_Config" not in folders
        assert "_Temporal" not in folders

    def test_scan_temporal_types(self, vault):
        types = scan_temporal_types(vault)
        assert len(types) == 1
        assert types[0]["type"] == "temporal/logs"
        assert types[0]["path"] == os.path.join("_Temporal", "Logs")

    def test_scan_temporal_empty(self, empty_vault):
        types = scan_temporal_types(empty_vault)
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
        assert extract_title("file.md") == "file"

    def test_extract_title_strips_extension(self):
        assert extract_title("my-file.md") == "my-file"

    def test_extract_title_with_path(self):
        assert extract_title("Wiki/fallback.md") == "fallback"


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_basic(self):
        tokens = lexical.tokenise("Hello World")
        assert tokens == ["hello", "world"]

    def test_strips_short_tokens(self):
        tokens = lexical.tokenise("I am a big fan")
        assert "i" not in tokens
        assert "am" in tokens
        assert "big" in tokens

    def test_handles_punctuation(self):
        tokens = lexical.tokenise("BM25 is great! (really)")
        assert "bm25" in tokens
        assert "great" in tokens
        assert "really" in tokens

    def test_empty_string(self):
        assert lexical.tokenise("") == []


# ---------------------------------------------------------------------------
# Full index build
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_index_structure(self, vault):
        index = built_index(vault)
        assert "meta" in index
        assert "bm25_params" in index
        assert "corpus_stats" in index
        assert "documents" in index

    def test_in_memory_index_has_no_build_local_body_fields(self, vault):
        """Documents stay clean; embedding-only context is re-read on demand."""
        index = built_index(vault)
        for doc in index["documents"]:
            assert "_body_head" not in doc
            assert "_headings" not in doc

    def test_persisted_index_stays_free_of_build_local_body_fields(self, vault):
        """Embedding-only context must never land in retrieval-index.json."""
        index = built_index(vault)
        persist_retrieval_index(vault, index)
        with open(vault / OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for doc in data["documents"]:
            assert "_body_head" not in doc
            assert "_headings" not in doc

    @pytest.mark.parametrize("error", [OSError("disk full"), ValueError("symlink refused")])
    def test_persist_retrieval_index_wraps_write_failures(self, vault, monkeypatch, error):
        index = built_index(vault)

        def fail(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(search_index_mod, "safe_write_json", fail)

        with pytest.raises(
            retrieval_errors.RetrievalPersistenceError,
            match=OUTPUT_PATH,
        ) as exc:
            persist_retrieval_index(vault, index)

        assert "while persisting lexical retrieval state" in str(exc.value)

    def test_meta_fields(self, vault):
        index = built_index(vault)
        meta = index["meta"]
        assert meta["brain_core_version"] == "1.0.0"
        assert meta["index_version"] == "1.0.0"
        assert "built_at" in meta
        assert meta["document_count"] == 4

    def test_bm25_params(self, vault):
        index = built_index(vault)
        assert index["bm25_params"]["k1"] == 1.5
        assert index["bm25_params"]["b"] == 0.75

    def test_document_count(self, vault):
        index = built_index(vault)
        assert len(index["documents"]) == 4

    def test_document_fields(self, vault):
        index = built_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert doc["title"] == "python-basics"
        assert doc["type"] == "living/wiki"
        assert "python" in doc["tags"]
        assert doc["status"] == "active"
        assert doc["doc_length"] > 0
        assert isinstance(doc["tf"], dict)
        assert doc["tf"].get("python", 0) > 0

    def test_corpus_stats(self, vault):
        index = built_index(vault)
        stats = index["corpus_stats"]
        assert stats["total_docs"] == 4
        assert stats["avg_dl"] > 0
        assert isinstance(stats["df"], dict)
        assert stats["df"].get("python", 0) >= 1

    def test_empty_vault(self, empty_vault):
        index = built_index(empty_vault)
        assert index["meta"]["document_count"] == 0
        assert index["documents"] == []
        assert index["corpus_stats"]["total_docs"] == 0

    def test_relative_paths(self, vault):
        index = built_index(vault)
        for doc in index["documents"]:
            assert not os.path.isabs(doc["path"])

    def test_idempotent(self, vault):
        """Two builds produce same output except timestamp."""
        idx1 = built_index(vault)
        idx2 = built_index(vault)
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
        index = built_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert doc["type"] == "living/wiki"

    def test_title_tf_present(self, vault):
        index = built_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert "title_tf" in doc
        assert doc["title_tf"].get("python", 0) > 0
        assert doc["title_tf"].get("basics", 0) > 0

    def test_title_tf_includes_type_tokens(self, vault):
        index = built_index(vault)
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
        index = built_index(vault)
        doc = next(d for d in index["documents"] if "no-type" in d["path"])
        assert doc["type"] == "living/wiki"


# ---------------------------------------------------------------------------
# Incremental index updates
# ---------------------------------------------------------------------------

class TestIncrementalIndex:
    def test_index_update_new_document(self, vault):
        """index_update should add a new document and update corpus stats."""
        index = built_index(vault)
        old_count = index["meta"]["document_count"]
        # Write a new file
        (vault / "Wiki" / "new-topic.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# New Topic\n\nUnique xylophonic content here.\n"
        )
        parsed = index_update(index, vault, "Wiki/new-topic.md", type_hint="living/wiki")
        assert parsed is not None
        assert parsed.doc["title"] == "new-topic"
        assert "Unique xylophonic content here." in parsed.embedding_parts.body_head
        assert index["meta"]["document_count"] == old_count + 1
        assert "xylophonic" in index["corpus_stats"]["df"]
        paths = [d["path"] for d in index["documents"]]
        assert "Wiki/new-topic.md" in paths
        assert_corpus_stats_match_recompute(index)

    def test_index_update_unreadable_returns_none(self, vault):
        """index_update returns None for a path that doesn't exist."""
        index = built_index(vault)
        old_count = index["meta"]["document_count"]
        parsed = index_update(index, vault, "Wiki/nonexistent.md")
        assert parsed is None
        assert index["meta"]["document_count"] == old_count

    def test_build_index_raises_on_unreadable_document(self, vault):
        (vault / "Wiki" / "broken.md").write_bytes(b"\xff\xfe\x00\x00")

        with pytest.raises(
            retrieval_errors.UnreadableRetrievalSourceError,
            match="Wiki/broken.md",
        ) as exc:
            build_index(vault)
        assert "while building lexical retrieval state" in str(exc.value)

    def test_index_update_existing_document(self, vault):
        """index_update should replace an existing document's data."""
        index = built_index(vault)
        old_count = index["meta"]["document_count"]
        # Overwrite an existing file with new content
        (vault / "Wiki" / "python-basics.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Python Basics\n\nCompletely rewritten with plumbiferous content.\n"
        )
        parsed = index_update(index, vault, "Wiki/python-basics.md", type_hint="living/wiki")
        assert parsed is not None
        assert index["meta"]["document_count"] == old_count  # count unchanged
        assert "plumbiferous" in index["corpus_stats"]["df"]
        assert_corpus_stats_match_recompute(index)

    def test_index_update_missing_path_falls_back_to_add(self, vault):
        """index_update should add the document if path not found in index."""
        index = built_index(vault)
        old_count = index["meta"]["document_count"]
        (vault / "Wiki" / "brand-new.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n# Brand New\n\nContent.\n"
        )
        parsed = index_update(index, vault, "Wiki/brand-new.md", type_hint="living/wiki")
        assert parsed is not None
        assert index["meta"]["document_count"] == old_count + 1
        assert_corpus_stats_match_recompute(index)

    def test_index_update_raises_on_unreadable_existing_document(self, vault):
        index = built_index(vault)
        (vault / "Wiki" / "python-basics.md").write_bytes(b"\xff\xfe\x00\x00")

        with pytest.raises(
            retrieval_errors.UnreadableRetrievalSourceError,
            match="Wiki/python-basics.md",
        ) as exc:
            index_update(index, vault, "Wiki/python-basics.md", type_hint="living/wiki")
        assert "while building lexical retrieval state" in str(exc.value)

    def test_index_update_drops_zero_df_terms(self, vault):
        """Replacing a doc must remove its now-orphaned terms from df entirely."""
        index = built_index(vault)
        # python-basics.md introduced 'plumbiferous'-free content. Inject a
        # truly unique term, then overwrite without that term.
        (vault / "Wiki" / "python-basics.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Python Basics\n\nA truly unique unobtainium reference here.\n"
        )
        index_update(index, vault, "Wiki/python-basics.md", type_hint="living/wiki")
        assert "unobtainium" in index["corpus_stats"]["df"]

        (vault / "Wiki" / "python-basics.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Python Basics\n\nReplaced content without that term.\n"
        )
        index_update(index, vault, "Wiki/python-basics.md", type_hint="living/wiki")
        assert "unobtainium" not in index["corpus_stats"]["df"]
        assert_corpus_stats_match_recompute(index)

    def test_index_update_repeated_no_drift(self, vault):
        """Many incremental updates must not drift from a full recompute."""
        index = built_index(vault)
        for i in range(20):
            (vault / "Wiki" / f"churn-{i}.md").write_text(
                f"---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
                f"# Churn {i}\n\nIteration {i} content with token-{i}.\n"
            )
            index_update(index, vault, f"Wiki/churn-{i}.md", type_hint="living/wiki")
        for i in range(0, 20, 2):
            (vault / "Wiki" / f"churn-{i}.md").write_text(
                f"---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
                f"# Churn {i}\n\nRewritten with replacement-{i}.\n"
            )
            index_update(index, vault, f"Wiki/churn-{i}.md", type_hint="living/wiki")
        assert_corpus_stats_match_recompute(index)

# ---------------------------------------------------------------------------
# Embedding building
# ---------------------------------------------------------------------------

class TestBuildEmbeddings:
    def test_raises_typed_runtime_error_without_numpy(self, vault):
        """When NumPy is unavailable, semantic sidecar build fails explicitly."""
        with patch.object(semantic_assets, "_HAS_NUMPY", False):
            with pytest.raises(
                retrieval_errors.SemanticRuntimeUnavailableError,
                match="numpy is not installed",
            ):
                semantic_assets.build_embeddings(vault, {"artefacts": []}, [])

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

        monkeypatch.setattr(semantic_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(semantic_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            semantic_assets.semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        monkeypatch.setattr(semantic_assets, "safe_write_via", fake_safe_write_via)

        result = semantic_assets.build_embeddings(
            vault,
            {"artefacts": [], "meta": {"source_hash": "sha256:test-source-hash"}},
            [],
        )

        assert result is not None
        assert [path for path, _bounds, _payload in calls] == [
            str(vault / _semantic.TYPE_EMBEDDINGS_REL),
            str(vault / _semantic.DOC_EMBEDDINGS_REL),
        ]
        assert all(bounds == str(vault) for _path, bounds, _payload in calls)
        assert all(payload.startswith(b"\x93NUMPY") for _path, _bounds, payload in calls)

    def test_document_meta_includes_type_and_title(self, vault, monkeypatch):
        """Document embedding metadata must preserve retrieval filters and titles."""
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

        monkeypatch.setattr(semantic_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(semantic_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            semantic_assets.semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        monkeypatch.setattr(
            semantic_assets,
            "safe_write_via",
            lambda path, writer, **kwargs: writer(io.BytesIO()),
        )

        index = built_index(vault)
        router = {
            "artefacts": [],
            "meta": {"source_hash": "sha256:test-source-hash"},
        }
        result = semantic_assets.build_embeddings(vault, router, index["documents"])

        assert result is not None
        _type_emb, _doc_emb, meta = result
        assert meta["model_revision"] == _semantic_model.SHIPPED_MODEL_REVISION
        assert meta[_semantic.ROUTER_SOURCE_HASH_KEY] == "sha256:test-source-hash"
        by_path = {entry["path"]: entry for entry in meta["documents"]}
        assert by_path["Wiki/python-basics.md"]["type"] == "living/wiki"
        assert by_path["Wiki/python-basics.md"]["title"] == "python-basics"
        assert by_path["Wiki/python-basics.md"]["tags"] == ["python", "programming"]
        assert by_path["Wiki/python-basics.md"]["status"] == "active"


class TestEmbeddingsOutputs:
    def test_build_embeddings_uses_cached_embedding_parts(self, vault, monkeypatch):
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

        monkeypatch.setattr(semantic_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(semantic_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            semantic_assets.semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        monkeypatch.setattr(
            semantic_assets,
            "safe_write_via",
            lambda path, writer, **kwargs: writer(io.BytesIO()),
        )

        build_result = build_index(vault)
        index = build_result.index
        monkeypatch.setattr(
            semantic_assets,
            "read_artefact",
            lambda *_args, **_kwargs: pytest.fail("build_embeddings should use cached embedding parts"),
        )

        result = semantic_assets.build_embeddings(
            vault,
            {"artefacts": [], "meta": {"source_hash": "sha256:test-source-hash"}},
            index["documents"],
            embedding_parts_by_path=build_result.embedding_parts_by_path,
        )

        assert result is not None
        assert set(build_result.embedding_parts_by_path) == {
            "Wiki/python-basics.md",
            "Wiki/rust-ownership.md",
            "Designs/brain-tooling.md",
            "_Temporal/Logs/2026-03/20260315-retrieval-research.md",
        }

    def test_build_embeddings_propagates_missing_uncached_document_reads(self, vault, monkeypatch):
        monkeypatch.setattr(semantic_assets, "_HAS_NUMPY", True)

        with pytest.raises(
            retrieval_errors.UnreadableRetrievalSourceError,
            match="Wiki/missing.md",
        ) as exc:
            semantic_assets.build_embeddings(
                vault,
                {"artefacts": [], "meta": {"source_hash": "sha256:test-source-hash"}},
                [
                    {
                        "path": "Wiki/missing.md",
                        "type": "living/wiki",
                        "title": "missing",
                        "tags": [],
                        "status": "active",
                    }
                ],
                embedding_parts_by_path={},
            )
        assert "while building semantic embeddings" in str(exc.value)

    def test_build_embeddings_wraps_array_write_failures(self, vault, monkeypatch):
        class FakeNumpy:
            @staticmethod
            def zeros(shape):
                return {"shape": shape}

            @staticmethod
            def save(handle, array):
                handle.write(repr(array).encode("utf-8"))

        class FakeModel:
            def encode(self, texts, normalize_embeddings=True):
                return [[0.0] for _ in texts]

        monkeypatch.setattr(semantic_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(semantic_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            semantic_assets.semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        def fail_write(*_args, **_kwargs):
            raise ValueError("symlink refused")

        monkeypatch.setattr(semantic_assets, "safe_write_via", fail_write)

        with pytest.raises(
            retrieval_errors.RetrievalPersistenceError,
            match=_semantic.TYPE_EMBEDDINGS_REL,
        ) as exc:
            semantic_assets.build_embeddings(
                vault,
                {"artefacts": [], "meta": {"source_hash": "sha256:test-source-hash"}},
                [],
            )

        assert "while writing semantic embeddings sidecar" in str(exc.value)

    def test_build_embeddings_wraps_metadata_write_failures(self, vault, monkeypatch):
        class FakeNumpy:
            @staticmethod
            def zeros(shape):
                return {"shape": shape}

            @staticmethod
            def save(handle, array):
                handle.write(repr(array).encode("utf-8"))

        class FakeModel:
            def encode(self, texts, normalize_embeddings=True):
                return [[0.0] for _ in texts]

        monkeypatch.setattr(semantic_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(semantic_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            semantic_assets.semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        monkeypatch.setattr(
            semantic_assets,
            "safe_write_via",
            lambda path, writer, **kwargs: writer(io.BytesIO()),
        )

        def fail_meta(*_args, **_kwargs):
            raise ValueError("symlink refused")

        monkeypatch.setattr(semantic_assets, "safe_write_json", fail_meta)

        with pytest.raises(
            retrieval_errors.RetrievalPersistenceError,
            match=_semantic.EMBEDDINGS_META_REL,
        ) as exc:
            semantic_assets.build_embeddings(
                vault,
                {"artefacts": [], "meta": {"source_hash": "sha256:test-source-hash"}},
                [],
            )

        assert "while writing semantic embeddings metadata" in str(exc.value)

    def test_embeddings_follow_shared_feature_flags(self, vault):
        cfg = {
            "defaults": {
                "flags": {
                    "semantic_processing": False,
                    "semantic_retrieval": False,
                },
                "local_runtime": {"semantic_engine_installed": False},
            }
        }

        assert _semantic_config.semantic_processing_enabled(vault, config=cfg) is False
        assert _semantic_config.semantic_retrieval_enabled(vault, config=cfg) is False
        assert _semantic_config.embeddings_enabled(vault, config=cfg) is False

        cfg["defaults"]["flags"]["semantic_processing"] = True
        assert _semantic_config.semantic_processing_enabled(vault, config=cfg) is True
        assert _semantic_config.embeddings_enabled(vault, config=cfg) is True

        cfg["defaults"]["flags"]["semantic_processing"] = False
        cfg["defaults"]["flags"]["semantic_retrieval"] = True
        assert _semantic_config.semantic_processing_enabled(vault, config=cfg) is False
        assert _semantic_config.semantic_retrieval_enabled(vault, config=cfg) is True
        assert _semantic_config.embeddings_enabled(vault, config=cfg) is True

    def test_persist_outputs_clears_stale_sidecars_when_disabled(self, vault):
        local_dir = vault / ".brain" / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
        for rel_path in (
            _semantic.TYPE_EMBEDDINGS_REL,
            _semantic.DOC_EMBEDDINGS_REL,
            _semantic.EMBEDDINGS_META_REL,
        ):
            abs_path = vault / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(b"stale")

        index = built_index(vault)
        result = retrieval_assets.persist_retrieval_outputs(
            vault,
            index,
            router={"artefacts": []},
        )

        assert result is None
        for rel_path in (
            _semantic.TYPE_EMBEDDINGS_REL,
            _semantic.DOC_EMBEDDINGS_REL,
            _semantic.EMBEDDINGS_META_REL,
        ):
            assert not (vault / rel_path).exists()

    def test_persist_outputs_raises_when_loaded_router_reports_error(self, vault, monkeypatch):
        monkeypatch.setattr(
            retrieval_assets,
            "load_compiled_router",
            lambda _vault: {"error": "compiled router missing"},
        )

        with pytest.raises(
            retrieval_errors.CompiledRouterUnavailableError,
            match="compiled router is unavailable: compiled router missing while building semantic embeddings",
        ):
            retrieval_assets.persist_retrieval_outputs(
                vault,
                built_index(vault),
                force_embeddings=True,
            )

    def test_persist_outputs_clears_stale_sidecars_when_process_disabled(self, vault):
        for rel_path in (
            _semantic.TYPE_EMBEDDINGS_REL,
            _semantic.DOC_EMBEDDINGS_REL,
            _semantic.EMBEDDINGS_META_REL,
        ):
            abs_path = vault / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(b"stale")

        cfg = {
            "defaults": {
                "flags": {
                    "semantic_processing": False,
                    "semantic_retrieval": False,
                },
                "local_runtime": {"semantic_engine_installed": False},
            }
        }
        index = built_index(vault)
        result = retrieval_assets.persist_retrieval_outputs(
            vault,
            index,
            router={"artefacts": []},
            config=cfg,
        )

        assert result is None
        for rel_path in (
            _semantic.TYPE_EMBEDDINGS_REL,
            _semantic.DOC_EMBEDDINGS_REL,
            _semantic.EMBEDDINGS_META_REL,
        ):
            assert not (vault / rel_path).exists()

    def test_persist_outputs_raises_runtime_unavailable_in_generic_mode(
        self, vault, monkeypatch
    ):
        """If the refresh predicate passes but the runtime vanishes mid-call, propagate."""
        monkeypatch.setattr(retrieval_assets, "embeddings_should_refresh", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(
            semantic_assets,
            "refresh_embeddings_outputs",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                retrieval_errors.SemanticRuntimeUnavailableError(
                    "semantic runtime dependencies are unavailable: numpy is not installed",
                    operation="building semantic embeddings",
                )
            ),
        )

        with pytest.raises(
            retrieval_errors.SemanticRuntimeUnavailableError,
            match="numpy is not installed",
        ):
            retrieval_assets.persist_retrieval_outputs(
                vault,
                built_index(vault),
                router={"artefacts": []},
            )

    def test_persist_outputs_raises_runtime_unavailable_in_strict_mode(self, vault, monkeypatch):
        monkeypatch.setattr(
            semantic_assets,
            "refresh_embeddings_outputs",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                retrieval_errors.SemanticRuntimeUnavailableError(
                    "semantic runtime dependencies are unavailable: numpy is not installed",
                    operation="building semantic embeddings",
                )
            ),
        )

        with pytest.raises(
            retrieval_errors.SemanticRuntimeUnavailableError,
            match="numpy is not installed",
        ):
            retrieval_assets.persist_retrieval_outputs(
                vault,
                built_index(vault),
                router={"artefacts": []},
                force_embeddings=True,
            )


class TestBuildIndexCli:
    def test_main_errors_when_config_load_fails(self, vault, wrapper_cli):
        config_path = vault / ".brain" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.mkdir()

        result = wrapper_cli(vault, "build_index.py")

        assert result.returncode == 1
        assert "failed to load config" in result.stderr

    def test_main_builds_index_and_skips_embeddings_when_disabled(self, vault, wrapper_cli):
        result = wrapper_cli(vault, "build_index.py")

        assert result.returncode == 0
        assert (vault / OUTPUT_PATH).is_file()
        assert "Built retrieval index:" in result.stderr
        assert "embeddings refreshed" not in result.stderr

    def test_main_reports_unreadable_retrieval_sources(self, vault, wrapper_cli):
        (vault / "Wiki" / "broken.md").write_bytes(b"\xff\xfe\x00\x00")

        result = wrapper_cli(vault, "build_index.py")

        assert result.returncode == 1
        assert "unreadable retrieval source 'Wiki/broken.md'" in result.stderr
        assert "while building lexical retrieval state" in result.stderr

    def test_main_reports_retrieval_persistence_failures(self, vault, monkeypatch, capsys):
        build_index_cli = _load_build_index_cli_module()
        monkeypatch.setattr(build_index_cli, "find_vault_root", lambda: str(vault))
        monkeypatch.setattr(
            build_index_cli.semantic_config,
            "load_config_checked",
            lambda _vault: {},
        )

        def fail(*_args, **_kwargs):
            raise retrieval_errors.RetrievalPersistenceError(
                OUTPUT_PATH,
                "persisting lexical retrieval state",
                OSError("disk full"),
            )

        monkeypatch.setattr(build_index_cli._retrieval_assets, "persist_retrieval_outputs", fail)

        with pytest.raises(SystemExit) as exc:
            build_index_cli.main()

        assert exc.value.code == 1
        stderr = capsys.readouterr().err
        assert "failed to persist retrieval output" in stderr
        assert "while persisting lexical retrieval state" in stderr

    def test_main_json_mode_prints_index_without_persisting_outputs(self, vault, wrapper_cli):
        result = wrapper_cli(vault, "build_index.py", "--json")

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["meta"]["document_count"] == 4
        assert not (vault / OUTPUT_PATH).exists()
