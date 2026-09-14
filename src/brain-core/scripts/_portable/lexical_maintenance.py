"""Portable lexical-index repair and explicit rebuild semantics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _lifecycle.derived_cache_state import inspect_lexical_cache
from _semantic.runtime import clear_embeddings_outputs
import _search.index as search_index


@dataclass(frozen=True, slots=True)
class LexicalMaintenanceResult:
    status: str
    reason: str
    dry_run: bool
    forced: bool
    document_count: int | None = None
    term_count: int | None = None
    sidecars_removed: tuple[str, ...] = ()


def _counts(index: object) -> tuple[int | None, int | None]:
    if not isinstance(index, dict):
        return (None, None)
    meta = index.get("meta")
    stats = index.get("corpus_stats")
    document_count = meta.get("document_count") if isinstance(meta, dict) else None
    df = stats.get("df") if isinstance(stats, dict) else None
    return (
        document_count if isinstance(document_count, int) else None,
        len(df) if isinstance(df, dict) else None,
    )


def maintain_lexical_index(
    vault_root: str | Path,
    *,
    dry_run: bool,
    force: bool,
) -> LexicalMaintenanceResult:
    """Repair a stale lexical index or explicitly rebuild it when forced."""
    root = Path(vault_root)
    state = inspect_lexical_cache(root)
    if not force and not state.stale:
        document_count, term_count = _counts(state.payload)
        return LexicalMaintenanceResult(
            "noop",
            state.reason,
            dry_run,
            force,
            document_count,
            term_count,
        )

    reason = state.reason if state.stale else "explicit-rebuild"
    if dry_run:
        return LexicalMaintenanceResult("planned", reason, True, force)

    build_result = search_index.build_index(root)
    search_index.persist_retrieval_index(root, build_result.index)
    removed = tuple(clear_embeddings_outputs(root))
    document_count, term_count = _counts(build_result.index)
    return LexicalMaintenanceResult(
        "changed",
        reason,
        False,
        force,
        document_count,
        term_count,
        removed,
    )


def update_lexical_documents(vault_root, index, paths):
    """Update known in-place writes from a fresh snapshot held under the lock."""
    from datetime import datetime, timezone

    for path in paths:
        if search_index.index_update(index, vault_root, path) is None:
            raise OSError("A changed document disappeared during lexical maintenance")
    index["meta"]["built_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    search_index.persist_retrieval_index(vault_root, index)
    clear_embeddings_outputs(vault_root)
