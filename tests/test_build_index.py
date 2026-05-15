"""Tests for build_index.py — BM25 retrieval index builder."""

import io
import copy
import json
import os
from unittest.mock import patch

import pytest

import _search.assets as search_assets
import _search.index as search_index_mod
import _search.lexical as lexical
import _semantic.config as _semantic_config
import _semantic.model as _semantic_model
import _semantic.runtime as _semantic
from _search.assets import persist_retrieval_outputs
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
        index = build_index(vault)
        assert "meta" in index
        assert "bm25_params" in index
        assert "corpus_stats" in index
        assert "documents" in index

    def test_in_memory_index_retains_build_local_body_fields(self, vault):
        """Documents carry retained body slice + headings in memory."""
        index = build_index(vault)
        for doc in index["documents"]:
            assert "_body_head" in doc
            assert "_headings" in doc
            assert isinstance(doc["_body_head"], str)
            assert isinstance(doc["_headings"], list)

    def test_persisted_index_strips_build_local_body_fields(self, vault):
        """Build-local body fields must not land in retrieval-index.json."""
        index = build_index(vault)
        assert any("_body_head" in doc for doc in index["documents"])
        persist_retrieval_index(vault, index)
        with open(vault / OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for doc in data["documents"]:
            assert "_body_head" not in doc
            assert "_headings" not in doc

    def test_meta_fields(self, vault):
        index = build_index(vault)
        meta = index["meta"]
        assert meta["brain_core_version"] == "1.0.0"
        assert meta["index_version"] == "1.0.0"
        assert "built_at" in meta
        assert meta["document_count"] == 4

    def test_bm25_params(self, vault):
        index = build_index(vault)
        assert index["bm25_params"]["k1"] == 1.5
        assert index["bm25_params"]["b"] == 0.75

    def test_document_count(self, vault):
        index = build_index(vault)
        assert len(index["documents"]) == 4

    def test_document_fields(self, vault):
        index = build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert doc["title"] == "python-basics"
        assert doc["type"] == "living/wiki"
        assert "python" in doc["tags"]
        assert doc["status"] == "active"
        assert doc["doc_length"] > 0
        assert isinstance(doc["tf"], dict)
        assert doc["tf"].get("python", 0) > 0

    def test_corpus_stats(self, vault):
        index = build_index(vault)
        stats = index["corpus_stats"]
        assert stats["total_docs"] == 4
        assert stats["avg_dl"] > 0
        assert isinstance(stats["df"], dict)
        assert stats["df"].get("python", 0) >= 1

    def test_empty_vault(self, empty_vault):
        index = build_index(empty_vault)
        assert index["meta"]["document_count"] == 0
        assert index["documents"] == []
        assert index["corpus_stats"]["total_docs"] == 0

    def test_relative_paths(self, vault):
        index = build_index(vault)
        for doc in index["documents"]:
            assert not os.path.isabs(doc["path"])

    def test_idempotent(self, vault):
        """Two builds produce same output except timestamp."""
        idx1 = build_index(vault)
        idx2 = build_index(vault)
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
        index = build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert doc["type"] == "living/wiki"

    def test_title_tf_present(self, vault):
        index = build_index(vault)
        doc = next(d for d in index["documents"] if "python" in d["path"])
        assert "title_tf" in doc
        assert doc["title_tf"].get("python", 0) > 0
        assert doc["title_tf"].get("basics", 0) > 0

    def test_title_tf_includes_type_tokens(self, vault):
        index = build_index(vault)
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
        index = build_index(vault)
        doc = next(d for d in index["documents"] if "no-type" in d["path"])
        assert doc["type"] == "living/wiki"


# ---------------------------------------------------------------------------
# Incremental index updates
# ---------------------------------------------------------------------------

class TestIncrementalIndex:
    def test_index_update_new_document(self, vault):
        """index_update should add a new document and update corpus stats."""
        index = build_index(vault)
        old_count = index["meta"]["document_count"]
        # Write a new file
        (vault / "Wiki" / "new-topic.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# New Topic\n\nUnique xylophonic content here.\n"
        )
        doc = index_update(index, vault, "Wiki/new-topic.md", type_hint="living/wiki")
        assert doc is not None
        assert doc["title"] == "new-topic"
        assert index["meta"]["document_count"] == old_count + 1
        assert "xylophonic" in index["corpus_stats"]["df"]
        paths = [d["path"] for d in index["documents"]]
        assert "Wiki/new-topic.md" in paths
        assert_corpus_stats_match_recompute(index)

    def test_index_update_unreadable_returns_none(self, vault):
        """index_update returns None for a path that doesn't exist."""
        index = build_index(vault)
        old_count = index["meta"]["document_count"]
        doc = index_update(index, vault, "Wiki/nonexistent.md")
        assert doc is None
        assert index["meta"]["document_count"] == old_count

    def test_index_update_existing_document(self, vault):
        """index_update should replace an existing document's data."""
        index = build_index(vault)
        old_count = index["meta"]["document_count"]
        # Overwrite an existing file with new content
        (vault / "Wiki" / "python-basics.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Python Basics\n\nCompletely rewritten with plumbiferous content.\n"
        )
        doc = index_update(index, vault, "Wiki/python-basics.md", type_hint="living/wiki")
        assert doc is not None
        assert index["meta"]["document_count"] == old_count  # count unchanged
        assert "plumbiferous" in index["corpus_stats"]["df"]
        assert_corpus_stats_match_recompute(index)

    def test_index_update_missing_path_falls_back_to_add(self, vault):
        """index_update should add the document if path not found in index."""
        index = build_index(vault)
        old_count = index["meta"]["document_count"]
        (vault / "Wiki" / "brand-new.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n# Brand New\n\nContent.\n"
        )
        doc = index_update(index, vault, "Wiki/brand-new.md", type_hint="living/wiki")
        assert doc is not None
        assert index["meta"]["document_count"] == old_count + 1
        assert_corpus_stats_match_recompute(index)

    def test_index_update_drops_zero_df_terms(self, vault):
        """Replacing a doc must remove its now-orphaned terms from df entirely."""
        index = build_index(vault)
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
        index = build_index(vault)
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
    def test_returns_none_without_deps(self, vault):
        """When sentence-transformers is unavailable, returns None."""
        with patch.object(search_assets, "_HAS_NUMPY", False):
            result = search_assets.build_embeddings(vault, {"artefacts": []}, [])
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

        monkeypatch.setattr(search_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(search_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            search_assets._semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        monkeypatch.setattr(search_assets, "safe_write_via", fake_safe_write_via)

        result = search_assets.build_embeddings(
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

        monkeypatch.setattr(search_assets, "_HAS_NUMPY", True)
        monkeypatch.setattr(search_assets, "np", FakeNumpy(), raising=False)
        monkeypatch.setattr(
            search_assets._semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), _model_manifest()),
        )
        monkeypatch.setattr(
            search_assets,
            "safe_write_via",
            lambda path, writer, **kwargs: writer(io.BytesIO()),
        )

        index = build_index(vault)
        router = {
            "artefacts": [],
            "meta": {"source_hash": "sha256:test-source-hash"},
        }
        result = search_assets.build_embeddings(vault, router, index["documents"])

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

        index = build_index(vault)
        result = persist_retrieval_outputs(
            vault,
            index,
            router={"artefacts": []},
            enable_embeddings=False,
        )

        assert result is None
        for rel_path in (
            _semantic.TYPE_EMBEDDINGS_REL,
            _semantic.DOC_EMBEDDINGS_REL,
            _semantic.EMBEDDINGS_META_REL,
        ):
            assert not (vault / rel_path).exists()

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
        index = build_index(vault)
        result = persist_retrieval_outputs(
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

    def test_main_json_mode_prints_index_without_persisting_outputs(self, vault, wrapper_cli):
        result = wrapper_cli(vault, "build_index.py", "--json")

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["meta"]["document_count"] == 4
        assert not (vault / OUTPUT_PATH).exists()
