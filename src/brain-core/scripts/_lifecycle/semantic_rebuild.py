"""Managed semantic asset rebuild lifecycle owner."""

from __future__ import annotations

from pathlib import Path
import sys

from _bootstrap.runtime import step as _step
from _lifecycle.retrieval_assets import refresh_retrieval_assets
from _lifecycle_common import make_result_envelope
from _semantic.provision import SEMANTIC_ASSET_REFRESH_ERRORS, format_asset_refresh_error


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
        try:
            notes = refresh_retrieval_assets(root, force_embeddings=True)
        except SEMANTIC_ASSET_REFRESH_ERRORS as exc:
            steps = [_step("semantic_assets", "error", format_asset_refresh_error(exc))]
        else:
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
