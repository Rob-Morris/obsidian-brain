"""Semantic warm-up executed only in the selected managed interpreter."""

from pathlib import Path


def warm_semantic(vault_root: str | Path) -> dict:
    """Load or rebuild semantic assets without retaining them in a portable host."""
    from _common import load_compiled_router, vault_mutation_lock
    import _semantic.runtime as semantic_runtime
    from _lifecycle.retrieval_assets import refresh_embeddings_for_loaded_state
    from _search.lexical_query import load_index

    from _semantic.model import inspect_model_state, verify_local_model_load

    root = Path(vault_root)
    with vault_mutation_lock(root):
        if not verify_local_model_load(inspect_model_state(root)).healthy:
            raise RuntimeError(
                "Managed semantic model is unavailable or failed its local load check"
            )
        router = load_compiled_router(str(root))
        if "error" in router:
            raise RuntimeError(router["error"])
        index = load_index(str(root))
        type_embeddings, doc_embeddings, meta = semantic_runtime.load_embeddings_state(
            root
        )
        current = (
            type_embeddings is not None
            and doc_embeddings is not None
            and meta is not None
            and semantic_runtime.embeddings_meta_matches_router(meta, router)
        )
        if not current:
            refresh_embeddings_for_loaded_state(root, router, index["documents"])
            types, documents, metadata = semantic_runtime.load_embeddings_state(root)
            if types is None or documents is None or metadata is None:
                raise RuntimeError(
                    "Semantic warm-up did not produce its required assets"
                )
        return {"state": "ready"}
