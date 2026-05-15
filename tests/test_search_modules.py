"""Direct tests for canonical `_search` domain modules."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _search.assets as search_assets
from _search.filters import SearchFilters
import _search.index as search_index_mod
import _search.lexical_query as lexical_query
import _search.mode as search_mode
import _search.semantic_query as semantic_query
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
import _taxonomy_descriptions as taxonomy_descriptions
import compile_router


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


def test_search_assets_refresh_search_assets_returns_note_list(vault, monkeypatch):
    calls = []

    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(
        compile_router,
        "persist_compiled_router",
        lambda _vault, _compiled: calls.append("persist_router"),
    )
    monkeypatch.setattr(
        compile_router,
        "refresh_session_markdown",
        lambda _vault, _compiled: calls.append("refresh_session"),
    )
    monkeypatch.setattr(
        search_assets,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda _vault, _index, *, router=None, embedding_parts_by_path=None, enable_embeddings=None, config=None: calls.append(
            ("persist_outputs", router, embedding_parts_by_path, enable_embeddings)
        ),
    )

    result = search_assets.refresh_search_assets(vault)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars cleared (unavailable or disabled).",
    ]
    assert calls == [
        "persist_router",
        "refresh_session",
        ("persist_outputs", {"artefacts": []}, {}, None),
    ]


def test_search_assets_refresh_search_assets_reports_refreshed_sidecars(vault, monkeypatch):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        search_assets,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda *_args, **_kwargs: ("doc-embeddings", "meta"),
    )

    result = search_assets.refresh_search_assets(vault)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars refreshed.",
    ]


def test_search_assets_refresh_search_assets_forces_embeddings_when_requested(vault, monkeypatch):
    calls = []

    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        search_assets,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda _vault, _index, *, router=None, embedding_parts_by_path=None, enable_embeddings=None, config=None: calls.append(
            ("persist_outputs", router, embedding_parts_by_path, enable_embeddings)
        )
        or ("doc-embeddings", "meta"),
    )

    result = search_assets.refresh_search_assets(vault, force_embeddings=True)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars refreshed.",
    ]
    assert calls == [("persist_outputs", {"artefacts": []}, {}, True)]


def test_search_assets_refresh_search_assets_raises_when_forced_embeddings_are_not_rebuilt(
    vault,
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        search_assets,
        "build_index",
        lambda _vault: search_index_mod.IndexBuildResult(
            index={"documents": []},
            embedding_parts_by_path={},
        ),
    )
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda _vault, _index, *, router=None, embedding_parts_by_path=None, enable_embeddings=None, config=None: calls.append(
            ("persist_outputs", router, embedding_parts_by_path, enable_embeddings)
        ),
    )

    with pytest.raises(
        ValueError,
        match="semantic embeddings sidecars could not be rebuilt",
    ):
        search_assets.refresh_search_assets(vault, force_embeddings=True)

    assert calls == [("persist_outputs", {"artefacts": []}, {}, True)]


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
import _search.document_parts
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
