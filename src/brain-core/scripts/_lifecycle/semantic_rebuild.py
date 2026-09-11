"""Managed semantic asset rebuild lifecycle owner."""

from __future__ import annotations

from pathlib import Path
import sys

from _bootstrap.runtime import step as _step
from _lifecycle.retrieval_assets import refresh_retrieval_assets
from _lifecycle_common import make_result_envelope


def rebuild_semantic(vault_root: str | Path, *, dry_run: bool) -> dict:
    """Force-refresh the compiled router, retrieval index and semantic sidecars."""
    root = Path(vault_root)
    notes: list[str] = []
    if dry_run:
        steps = [
            _step(
                "semantic_assets",
                "planned",
                "Would rebuild the compiled router, retrieval index, and semantic embeddings sidecars.",
            )
        ]
    else:
        notes = refresh_retrieval_assets(root, force_embeddings=True)
        steps = [
            _step(
                "semantic_assets",
                "changed",
                "Rebuilt the compiled router, retrieval index, and semantic embeddings sidecars.",
            )
        ]
    return make_result_envelope(
        action="semantic_rebuild",
        vault_root=root,
        managed_python=sys.executable,
        steps=steps,
        notes=notes,
        dry_run=dry_run,
    )
