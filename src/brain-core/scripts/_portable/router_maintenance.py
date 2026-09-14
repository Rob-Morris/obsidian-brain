"""Portable compiled-router repair and explicit rebuild semantics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import compile_router
from _lifecycle.derived_cache_state import CacheState, inspect_router_cache
from _semantic.runtime import clear_embeddings_outputs


@dataclass(frozen=True, slots=True)
class RouterMaintenanceResult:
    status: str
    reason: str
    dry_run: bool
    forced: bool
    sidecars_removed: tuple[str, ...] = ()
    session_refreshed: bool = False
    session_error: str | None = None
    cache_error: CacheState | None = None


def maintain_router(
    vault_root: str | Path,
    *,
    dry_run: bool,
    force: bool,
) -> RouterMaintenanceResult:
    """Repair a stale router or explicitly rebuild it when ``force`` is true."""
    root = Path(vault_root)
    state = inspect_router_cache(root, verify_content=True)
    if not force and not state.stale:
        return RouterMaintenanceResult("noop", state.reason, dry_run, force)

    reason = state.reason if state.stale else "explicit-rebuild"
    if dry_run:
        return RouterMaintenanceResult("planned", reason, True, force)

    compiled = compile_router.compile(str(root))
    compile_router.persist_compiled_router(str(root), compiled)
    removed = tuple(clear_embeddings_outputs(root))
    try:
        compile_router.refresh_session_markdown(str(root), compiled)
    except (OSError, ValueError) as exc:
        return RouterMaintenanceResult(
            "partial",
            reason,
            False,
            force,
            removed,
            session_error=str(exc),
        )
    verified = inspect_router_cache(root, verify_content=True)
    if verified.stale:
        return RouterMaintenanceResult(
            "partial", reason, False, force, removed,
            session_refreshed=True, cache_error=verified,
        )
    return RouterMaintenanceResult(
        "changed",
        reason,
        False,
        force,
        removed,
        session_refreshed=True,
    )
