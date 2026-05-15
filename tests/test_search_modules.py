"""Direct tests for internal `_search` domain modules."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _search.assets as search_assets
import _search.index as search_index_mod
import _search.query as search_query
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
    index = search_index_mod.build_index(vault)

    assert index["meta"]["document_count"] == 1
    assert index["documents"][0]["path"] == "Wiki/python-basics.md"


def test_search_query_dispatches_lexical_mode_directly(vault):
    index = search_index_mod.build_index(vault)

    results = search_query.dispatch_search(index, "python", vault, "lexical")

    assert results
    assert results[0]["path"] == "Wiki/python-basics.md"


def test_search_query_load_index_raises_named_error_when_missing(vault):
    with pytest.raises(search_query.IndexNotFoundError, match="retrieval index not found"):
        search_query.load_index(vault)


def test_search_query_encode_query_wraps_runtime_importerror(vault, monkeypatch):
    monkeypatch.setattr(
        search_query._semantic,
        "encode_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ImportError("missing runtime dep")),
    )

    with pytest.raises(
        search_query.SearchModeUnavailableError,
        match="missing runtime dep",
    ):
        search_query._encode_query(vault, "python")


def test_search_query_encode_query_reraises_original_error_when_proxy_class_unavailable(
    vault,
    monkeypatch,
):
    class MissingSemanticModelProxy:
        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(
        search_query._semantic,
        "encode_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )
    monkeypatch.setattr(search_query, "_semantic_model", MissingSemanticModelProxy())

    with pytest.raises(ValueError, match="boom"):
        search_query._encode_query(vault, "python")


def test_search_query_load_doc_embeddings_reraises_original_error_when_proxy_class_unavailable(
    vault,
    monkeypatch,
):
    class MissingSemanticProxy:
        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(search_query, "_semantic", MissingSemanticProxy())

    def boom(_vault):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        search_query.load_doc_embeddings_or_unavailable(vault, loader=boom)


def test_search_query_load_doc_embeddings_reraises_router_metadata_error_when_proxy_class_unavailable(
    vault,
    monkeypatch,
):
    class BrokenSemanticProxy:
        def embeddings_meta_matches_router(self, _meta, _router):
            raise ValueError("bad router metadata")

        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(search_query, "_semantic", BrokenSemanticProxy())
    monkeypatch.setattr(
        search_query,
        "load_compiled_router",
        lambda _vault: {"meta": {"source_hash": "sha256:router"}},
    )

    with pytest.raises(ValueError, match="bad router metadata"):
        search_query.load_doc_embeddings_or_unavailable(
            vault,
            loader=lambda _vault: (object(), {"documents": []}),
        )


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
        lambda _vault: {"documents": []},
    )
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda _vault, _index, *, router=None, enable_embeddings=None, config=None: calls.append(
            ("persist_outputs", router, enable_embeddings)
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
        ("persist_outputs", {"artefacts": []}, None),
    ]


def test_search_assets_refresh_search_assets_reports_refreshed_sidecars(vault, monkeypatch):
    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search_assets, "build_index", lambda _vault: {"documents": []})
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
    monkeypatch.setattr(search_assets, "build_index", lambda _vault: {"documents": []})
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda _vault, _index, *, router=None, enable_embeddings=None, config=None: calls.append(
            ("persist_outputs", router, enable_embeddings)
        )
        or ("doc-embeddings", "meta"),
    )

    result = search_assets.refresh_search_assets(vault, force_embeddings=True)

    assert result == [
        "Compiled router refreshed.",
        "Lexical retrieval assets refreshed.",
        "Semantic sidecars refreshed.",
    ]
    assert calls == [("persist_outputs", {"artefacts": []}, True)]


def test_search_assets_refresh_search_assets_raises_when_forced_embeddings_are_not_rebuilt(
    vault,
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(compile_router, "compile", lambda _vault: {"artefacts": []})
    monkeypatch.setattr(compile_router, "persist_compiled_router", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compile_router, "refresh_session_markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search_assets, "build_index", lambda _vault: {"documents": []})
    monkeypatch.setattr(
        search_assets,
        "persist_retrieval_outputs",
        lambda _vault, _index, *, router=None, enable_embeddings=None, config=None: calls.append(
            ("persist_outputs", router, enable_embeddings)
        ),
    )

    with pytest.raises(
        ValueError,
        match="semantic embeddings sidecars could not be rebuilt",
    ):
        search_assets.refresh_search_assets(vault, force_embeddings=True)

    assert calls == [("persist_outputs", {"artefacts": []}, True)]


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

import _search.assets
import _search.index
import _search.lexical
import _search.query
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
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
