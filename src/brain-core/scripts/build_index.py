#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

Thin CLI wrapper over `_search`.

Import `_search.index` / `_lifecycle.retrieval_assets` directly from Python; do not import
this module.
"""

from __future__ import annotations

import json
import os
import sys

from _bootstrap.runtime import (
    handoff_current_script_to_managed_runtime,
    required_modules_for_scope,
)
from _common import find_vault_root
from _repair_common import build_repair_command


_retrieval_assets = None
CompiledRouterUnavailableError = None
RetrievalPersistenceError = None
SemanticRuntimeUnavailableError = None
UnreadableRetrievalSourceError = None
_index = None
_paths = None
semantic_config = None
semantic_model = None


def _load_runtime_modules() -> None:
    """Load retrieval modules only after wrapper handoff is settled."""
    global _retrieval_assets, CompiledRouterUnavailableError, RetrievalPersistenceError
    global SemanticRuntimeUnavailableError, UnreadableRetrievalSourceError
    global _index, _paths, semantic_config, semantic_model
    if _index is not None:
        return
    import _lifecycle.retrieval_assets as _retrieval_assets_mod
    from _lifecycle.retrieval_errors import (
        CompiledRouterUnavailableError as _CompiledRouterUnavailableError,
        RetrievalPersistenceError as _RetrievalPersistenceError,
        SemanticRuntimeUnavailableError as _SemanticRuntimeUnavailableError,
        UnreadableRetrievalSourceError as _UnreadableRetrievalSourceError,
    )
    import _search.index as _index_mod
    import _search.paths as _paths_mod
    import _semantic.config as _semantic_config
    import _semantic.model as _semantic_model

    _retrieval_assets = _retrieval_assets_mod
    CompiledRouterUnavailableError = _CompiledRouterUnavailableError
    RetrievalPersistenceError = _RetrievalPersistenceError
    SemanticRuntimeUnavailableError = _SemanticRuntimeUnavailableError
    UnreadableRetrievalSourceError = _UnreadableRetrievalSourceError
    _index = _index_mod
    _paths = _paths_mod
    semantic_config = _semantic_config
    semantic_model = _semantic_model

def main():
    vault_root = find_vault_root()
    try:
        handoff_current_script_to_managed_runtime(
            vault_root,
            dependency_owner="build_index.py",
            required_modules=required_modules_for_scope("runtime"),
            script_path=os.path.abspath(__file__),
            forwarded_args=sys.argv[1:],
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(build_repair_command(vault_root, "runtime"), file=sys.stderr)
        sys.exit(1)

    _load_runtime_modules()
    try:
        cfg = semantic_config.load_config_checked(vault_root)
    except semantic_config.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        build_result = _index.build_index(vault_root)
        index = build_result.index
        json_output = json.dumps(index, indent=2, ensure_ascii=False)

        if "--json" in sys.argv:
            print(json_output)
            return

        embeddings_result = _retrieval_assets.persist_retrieval_outputs(
            vault_root,
            index,
            config=cfg,
            embedding_parts_by_path=build_result.embedding_parts_by_path,
        )
    except (
        UnreadableRetrievalSourceError,
        CompiledRouterUnavailableError,
        RetrievalPersistenceError,
        SemanticRuntimeUnavailableError,
        semantic_model.SemanticModelError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    doc_count = index["meta"]["document_count"]
    term_count = len(index["corpus_stats"]["df"])
    embeddings_note = ", embeddings refreshed" if embeddings_result is not None else ""
    print(
        f"Built retrieval index: {doc_count} documents, "
        f"{term_count} unique terms{embeddings_note} → {_paths.OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
