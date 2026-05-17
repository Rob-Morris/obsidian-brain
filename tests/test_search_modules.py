"""Direct tests for canonical retrieval modules."""

from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _lifecycle.retrieval_assets as retrieval_assets
from _search.filters import SearchFilters
import _search.index as search_index_mod
import _search.lexical_query as lexical_query
import _search.mode as search_mode
import _search.semantic_query as semantic_query
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
import _taxonomy_descriptions as taxonomy_descriptions
import compile_router
import session


@pytest.fixture
def vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / "_Config").mkdir()

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython supports automation and data work.\n"
    )
    return tmp_path


def test_search_index_module_builds_index_directly(vault):
    index = search_index_mod.build_index(vault).index

    assert index["meta"]["document_count"] == 1
    assert index["documents"][0]["path"] == "Wiki/python-basics.md"


def test_mode_dispatches_lexical_search_directly(vault):
    index = search_index_mod.build_index(vault).index

    results = search_mode.dispatch_search(index, "python", vault, "lexical")

    assert results
    assert results[0]["path"] == "Wiki/python-basics.md"


def test_lexical_query_load_index_raises_named_error_when_missing(vault):
    with pytest.raises(lexical_query.IndexNotFoundError, match="retrieval index not found"):
        lexical_query.load_index(vault)


def test_semantic_query_encode_query_wraps_runtime_importerror(vault, monkeypatch):
    monkeypatch.setattr(
        semantic_runtime,
        "encode_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ImportError("missing runtime dep")),
    )

    with pytest.raises(
        search_mode.SearchModeUnavailableError,
        match="missing runtime dep",
    ):
        semantic_query.encode_query_or_unavailable(vault, "python")


def test_semantic_model_load_sentence_transformer_wraps_missing_runtime_dependency(
    vault, monkeypatch
):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers":
            raise ImportError("missing sentence_transformers")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(
        semantic_model.SemanticRuntimeUnavailableError,
        match="missing sentence_transformers",
    ):
        semantic_model._load_sentence_transformer(vault / "missing-snapshot")


def test_semantic_model_provision_does_not_redownload_on_runtime_unavailable(vault, monkeypatch):
    manifest = semantic_model.ModelManifest(
        model_name=semantic_model.SHIPPED_MODEL_NAME,
        revision=semantic_model.SHIPPED_MODEL_REVISION,
        provisioned_at="2026-05-17T00:00:00+10:00",
    )
    semantic_model.write_manifest(vault, manifest)
    snapshot_path = semantic_model.model_snapshot_path(
        vault,
        semantic_model.SHIPPED_MODEL_NAME,
        semantic_model.SHIPPED_MODEL_REVISION,
    )
    snapshot_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        semantic_model,
        "_load_sentence_transformer",
        lambda *_a, **_k: (_ for _ in ()).throw(
            semantic_model.SemanticRuntimeUnavailableError(
                "semantic runtime dependencies are unavailable: missing sentence_transformers",
                operation="loading semantic model",
            )
        ),
    )
    monkeypatch.setattr(
        semantic_model,
        "_download_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("_download_snapshot should not run for runtime dependency failures")
        ),
    )

    with pytest.raises(
        semantic_model.SemanticRuntimeUnavailableError,
        match="missing sentence_transformers",
    ):
        semantic_model.provision_semantic_model(vault)


def test_semantic_query_load_doc_embeddings_wraps_embeddings_load_error(vault):
    def boom(_vault):
        raise semantic_runtime.SemanticEmbeddingsLoadError("corrupt sidecars")

    with pytest.raises(
        search_mode.SearchModeUnavailableError,
        match="corrupt sidecars",
    ):
        semantic_query.load_doc_embeddings_or_unavailable(vault, loader=boom)


def test_semantic_query_load_doc_embeddings_reports_missing_compiled_router(vault, monkeypatch):
    monkeypatch.setattr(
        semantic_runtime,
        "embeddings_meta_matches_current_router",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            semantic_runtime.CompiledRouterMissingError(
                "Compiled router not found at .brain/local/compiled-router.json. "
                "Run compile_router.py first."
            )
        ),
    )

    with pytest.raises(
        semantic_query.EmbeddingsSidecarsUnavailableError,
        match="Compiled router not found",
    ):
        semantic_query.load_doc_embeddings_or_unavailable(
            vault,
            loader=lambda _vault: ([0.1], {"documents": []}),
        )


def test_semantic_query_encode_query_wraps_semantic_model_errors(vault, monkeypatch):
    def boom(_vault, _query, *, query_encoder=None):
        raise semantic_model.SemanticModelMissingError("missing model snapshot")

    monkeypatch.setattr(semantic_runtime, "encode_query", boom)

    with pytest.raises(
        search_mode.SearchModeUnavailableError,
        match="missing model snapshot",
    ):
        semantic_query.encode_query_or_unavailable(vault, "python")


class TestSearchFiltersMatches:
    def test_empty_filters_match_sparse_metadata(self):
        assert SearchFilters().matches({})

    def test_type_tag_and_status_filters_match_expected_metadata(self):
        filters = SearchFilters(type="living/wiki", tag="python", status="active")
        assert filters.matches(
            {
                "type": "living/wiki",
                "tags": ["python", "programming"],
                "status": "active",
            }
        )

    def test_tag_filter_rejects_missing_tags_list(self):
        assert not SearchFilters(tag="python").matches({})

    def test_dataclass_equality_is_stable(self):
        assert SearchFilters(tag="python") == SearchFilters(tag="python")


def test_extract_type_description_does_not_mutate_artefact_metadata(vault):
    taxonomy_file = vault / "taxonomy.md"
    taxonomy_file.write_text(
        "# Retrieval Type\n\nOne-line summary.\n\n## Purpose\nShared description.\n",
        encoding="utf-8",
    )
    artefact = {"taxonomy_file": "taxonomy.md"}

    first = taxonomy_descriptions.extract_type_description(vault, artefact)
    second = taxonomy_descriptions.extract_type_description(vault, artefact)

    assert "One-line summary." in first
    assert first == second
    assert artefact == {"taxonomy_file": "taxonomy.md"}


def test_extract_type_description_invalidates_cache_when_taxonomy_changes(vault):
    taxonomy_file = vault / "taxonomy.md"
    taxonomy_file.write_text(
        "# Retrieval Type\n\nFirst summary.\n",
        encoding="utf-8",
    )
    artefact = {"taxonomy_file": "taxonomy.md"}

    first = taxonomy_descriptions.extract_type_description(vault, artefact)

    taxonomy_file.write_text(
        "# Retrieval Type\n\nSecond summary.\n",
        encoding="utf-8",
    )
    new_mtime = taxonomy_file.stat().st_mtime + 5
    os.utime(taxonomy_file, (new_mtime, new_mtime))

    second = taxonomy_descriptions.extract_type_description(vault, artefact)

    assert "First summary." in first
    assert "Second summary." in second


def test_extract_type_description_cache_isolated_per_vault(tmp_path):
    first_vault = tmp_path / "vault-a"
    second_vault = tmp_path / "vault-b"
    first_vault.mkdir()
    second_vault.mkdir()

    (first_vault / "taxonomy.md").write_text("# Retrieval Type\n\nFirst vault.\n", encoding="utf-8")
    (second_vault / "taxonomy.md").write_text("# Retrieval Type\n\nSecond vault.\n", encoding="utf-8")
    artefact = {"taxonomy_file": "taxonomy.md"}

    first = taxonomy_descriptions.extract_type_description(first_vault, artefact)
    second = taxonomy_descriptions.extract_type_description(second_vault, artefact)

    assert "First vault." in first
    assert "Second vault." in second


def test_retrieval_assets_refresh_retrieval_assets_returns_note_list(vault, monkeypatch):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        retrieval_assets.search_index,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        retrieval_assets,
        "persist_retrieval_outputs",
        lambda *_args, **_kwargs: None,
    )

    result = retrieval_assets.refresh_retrieval_assets(vault)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars cleared (unavailable or disabled).",
    ]


def test_retrieval_assets_refresh_retrieval_assets_reports_refreshed_sidecars(vault, monkeypatch):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        retrieval_assets.search_index,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        retrieval_assets,
        "persist_retrieval_outputs",
        lambda *_args, **_kwargs: ("doc-embeddings", "meta"),
    )

    result = retrieval_assets.refresh_retrieval_assets(vault)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars refreshed.",
    ]


def test_retrieval_assets_refresh_retrieval_assets_forces_embeddings_when_requested(vault, monkeypatch):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        retrieval_assets.search_index,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        retrieval_assets,
        "persist_retrieval_outputs",
        lambda *_args, **_kwargs: ("doc-embeddings", "meta"),
    )

    result = retrieval_assets.refresh_retrieval_assets(vault, force_embeddings=True)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars refreshed.",
    ]


def test_retrieval_assets_refresh_retrieval_assets_raises_when_runtime_is_unavailable_in_strict_mode(
    vault,
    monkeypatch,
):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        retrieval_assets.search_index,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        retrieval_assets,
        "persist_retrieval_outputs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            retrieval_assets.SemanticRuntimeUnavailableError(
                "semantic runtime dependencies are unavailable: numpy is not installed",
                operation="building semantic embeddings",
            )
        ),
    )

    with pytest.raises(
        retrieval_assets.SemanticRuntimeUnavailableError,
        match="numpy is not installed",
    ):
        retrieval_assets.refresh_retrieval_assets(vault, force_embeddings=True)


def test_retrieval_assets_refresh_retrieval_assets_wraps_router_compile_failures(vault, monkeypatch):
    def fail_compile(_vault):
        raise ValueError("bad naming rule")

    monkeypatch.setattr(compile_router, "compile", fail_compile)

    with pytest.raises(ValueError, match="bad naming rule"):
        retrieval_assets.refresh_retrieval_assets(vault)


def test_retrieval_assets_refresh_retrieval_assets_wraps_router_write_failures(
    vault,
    monkeypatch,
):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})

    def fail_write(*_args, **_kwargs):
        raise ValueError("symlink refused")

    monkeypatch.setattr(compile_router, "persist_compiled_router", fail_write)

    with pytest.raises(
        retrieval_assets.RetrievalPersistenceError,
        match=compile_router.OUTPUT_PATH,
    ) as exc:
        retrieval_assets.refresh_retrieval_assets(vault)

    assert "while persisting compiled router" in str(exc.value)


def test_retrieval_assets_refresh_retrieval_assets_warns_when_session_markdown_refresh_fails(
    vault,
    monkeypatch,
):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)

    def fail_session(*_args, **_kwargs):
        raise ValueError("symlink refused")

    monkeypatch.setattr(compile_router, "refresh_session_markdown", fail_session)
    monkeypatch.setattr(
        retrieval_assets.search_index,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        retrieval_assets,
        "persist_retrieval_outputs",
        lambda *_args, **_kwargs: None,
    )

    result = retrieval_assets.refresh_retrieval_assets(vault)

    assert result == [
        "Compiled router refreshed.",
        f"Warning: failed to refresh {session.SESSION_MARKDOWN_REL}: symlink refused",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars cleared (unavailable or disabled).",
    ]


def test_retrieval_assets_refresh_embeddings_for_loaded_state_materialises_parts(vault, monkeypatch):
    seen = {}

    def fake_refresh(
        vault_root,
        router,
        documents,
        *,
        embedding_parts_by_path=None,
    ):
        seen["vault_root"] = str(vault_root)
        seen["document_count"] = len(documents)
        seen["parts"] = embedding_parts_by_path
        return ("types", "docs", {"documents": [], "types": []})

    monkeypatch.setattr(retrieval_assets, "embeddings_should_refresh", lambda *_a, **_k: True)
    monkeypatch.setattr(
        retrieval_assets.semantic_assets,
        "refresh_embeddings_outputs",
        fake_refresh,
    )

    index = search_index_mod.build_index(vault).index
    result = retrieval_assets.refresh_embeddings_for_loaded_state(
        vault,
        {"artefacts": []},
        index["documents"],
        embedding_parts_by_path=None,
    )

    assert result == ("types", "docs", {"documents": [], "types": []})
    assert seen["vault_root"] == str(vault)
    assert seen["document_count"] == len(index["documents"])
    assert set(seen["parts"]) == {doc["path"] for doc in index["documents"]}
    for parts in seen["parts"].values():
        assert parts.body_head or parts.headings


def test_search_modules_import_without_semantic_imports():
    scripts_root = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_root) if not existing else f"{scripts_root}{os.pathsep}{existing}"
    )
    code = """
import importlib.abc
import sys

class BlockSemantic(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "_semantic" or fullname.startswith("_semantic."):
            raise ImportError(f"blocked semantic import: {fullname}")
        return None

sys.meta_path.insert(0, BlockSemantic())

import _search.filters
import _lifecycle.document_parts
import _lifecycle.retrieval_errors
import _search.index
import _search.lexical
import _search.lexical_query
import _search.mode
import _search.paths
import _search.resource
import _search.snippet
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_semantic_modules_do_not_import_search_modules_for_refresh_work():
    repo_root = Path(__file__).resolve().parents[1]
    semantic_root = repo_root / "src" / "brain-core" / "scripts" / "_semantic"

    for path in semantic_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not any(
            name == "_search" or name.startswith("_search.")
            for name in imported
        ), f"{path} still imports _search modules for refresh work"


def test_no_module_imports_retired_search_refresh_seam():
    repo_root = Path(__file__).resolve().parents[1]
    source_roots = [
        repo_root / "src" / "brain-core",
        repo_root / "tests",
    ]
    banned = {
        "_search.assets",
        "_search.document_parts",
        "_search.errors",
    }

    for root in source_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                    imported.update(
                        f"{node.module}.{alias.name}"
                        for alias in node.names
                    )

            assert imported.isdisjoint(
                banned
            ), f"{path} still imports retired search-refresh modules: {imported & banned}"


def test_no_module_imports_search_wrappers():
    """No Python module should import the CLI wrapper modules directly.

    See docs/architecture/overview.md for the post-convergence layering rule.
    """
    repo_root = Path(__file__).resolve().parents[1]
    source_roots = [
        repo_root / "src" / "brain-core",
        repo_root / "tests",
    ]
    excluded = {
        repo_root / "src" / "brain-core" / "scripts" / "build_index.py",
        repo_root / "src" / "brain-core" / "scripts" / "search_index.py",
    }
    banned = {"build_index", "search_index"}

    for root in source_roots:
        for path in root.rglob("*.py"):
            if path in excluded:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[-1] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[-1])

            assert imported.isdisjoint(
                banned
            ), f"{path} still imports wrapper modules: {imported & banned}"
