# DD-042: Mtime-Signature Staleness Fast-Path

**Status:** Implemented (v0.31.1)
**Extends:** DD-014, DD-035

## Context

DD-014 established that the MCP server auto-compiles a stale router on startup and detects mid-session staleness via SHA-256 hash comparison against tracked source files. DD-035 added a dirty-flag and pending-queue model so that incremental writes don't trigger eager full rebuilds.

The router-staleness check has two distinct concerns:

1. **Have any tracked source files been edited?** Answered by `_check_router` — hashes the manifest's source files and compares against the stored composite hash. Cheap, well-bounded.
2. **Has any new resource been added or removed that wasn't yet in the manifest?** Answered by `_check_router_resource_counts` — walks every resource-holding directory and counts living artefacts, skills, plugins, styles, memories.

The second check is the expensive one. It runs every router TTL fire (default 5 seconds) and walks an `os.walk` tree across the vault, opening every Living artefact's frontmatter to count valid slugs. On a vault with ~864 artefacts this is ~19ms warm. On stable vaults almost every fire returns "nothing changed" — the work is wasted.

## Decision

Introduce a stat-only directory-mtime signature as a pre-check that short-circuits the resource-count walk when no resource-holding directory has moved.

`compile_router.resource_source_dirs(vault_root)` is the single source of truth for which directories govern resource-count staleness. It yields `(rel_path, descend)` pairs:

- **Shallow** dirs (vault root, `_Temporal/`, `_Config/Styles/`, `_Config/Memories/`) are stat'd once. The parent's mtime catches add/remove of immediate children.
- **Tree** dirs (`_Config/Skills/`, `.brain-core/skills/`, `_Plugins/`, plus every Living and temporal type folder) are recursively walked stat-only via `os.scandir`, with `_*`/`.*`-prefixed children skipped to align with `iter_markdown_under`'s contract. The `_*` filter is what keeps `_Archive/` operations from forcing a recompile.

The mtime tuple becomes a hashable signature. A module-level `_resource_mtime_cache` holds the last signature; if the new signature equals the cached one, the resource-count walk is skipped entirely. Otherwise the walk runs, and the cache is updated only after both the count check and the index-entry count succeed without divergence — exceptions and divergence both leave the cache untouched so the next fire re-evaluates.

## Alternatives Considered

**Hash file contents instead of stat directories.** Rejected. The check is asking "has the resource set changed?" — a stat tells us as much as a hash for that question, at a fraction of the cost. Hashing is appropriate for `_check_router` because individual file edits matter; it isn't appropriate for the resource-count gate.

**Walk the full `os.walk` tree per check, accept the ~19ms cost.** Rejected. The cost compounds across long-running sessions and is paid even when nothing changed. The mtime check is bounded by the depth of the resource tree (typically <200 directories on real vaults).

**Per-file mtime cache instead of a tuple signature.** Considered for the per-hub case (where it shipped — see `_hub_metadata_cache` in `workspace_registry.py`). For the staleness gate, a single hashable signature is simpler — there is no per-key reuse between calls beyond the binary "same as last time" question.

**Filesystem event notifications** (`fsevents`, `inotify`). Rejected for now. Adds platform-specific dependencies and a worker thread; the stat-signature check is fast enough on a 5s cadence to leave on-demand.

**Hardcoded directory list inside `server.py`.** The first iteration of this work went this way and immediately drifted out of sync with `compile_router.resource_counts`'s actual inputs (missed `_Temporal/` and per-type folders, which broke `count_living_artefact_index_entries` short-circuit). Lifting the list to `compile_router.resource_source_dirs` makes the compiler the single source of truth — both consumers (the resource walk and the mtime signature) read from the same definition.

## Consequences

- Stable vaults: per-TTL cost drops from ~19ms to ~0.2ms, an ~95% amortised reduction.
- Mutations are still caught: any add/remove of a resource folder, type folder, or living-artefact file at any depth advances a stat that lands in the signature.
- `_Archive/` creation inside an existing type folder does NOT advance the signature (filtered children), so archive operations no longer force recompiles.
- The pattern is reusable. `_hub_metadata_cache` in `workspace_registry.py` adopted the same shape (per-path mtime cache, end-of-scan eviction, shallow-copy-on-emit) for hub frontmatter reads.
- The cache is module-level and per-process. Server lifetime preserves the cache across calls; standalone CLI invocations build and discard it without harm.
- The full delta-rebuild path (replacing full compiles with per-file delta updates) remains future work, gated by performance measurement of first-read-after-write latency. This DD records what shipped (the gate); the broader delta path is shaped in the vault design `Hash-Aware Artefact Index Refresh`.

## Implementation Notes

- `_resource_mtime_signature(vault_root)` consumes `compile_router.resource_source_dirs(vault_root)` and produces a `tuple[tuple[str, float | None], ...]`. Missing directories encode as `None` so absence is distinguishable from `mtime == 0.0`.
- `_append_filtered_tree` recurses via `os.scandir` (not `os.walk`) so `entry.stat()` reuses the readdir stat, halving syscalls vs `walk + getmtime` per directory.
- The tree walk skips `entry.name.startswith(".")` and `entry.name.startswith("_")` to match `iter_markdown_under`'s convention. `+`-prefixed terminal-status folders are *included* — they hold living artefacts that contribute to counts.
- Cache update is deferred until after both `compile_router.resource_counts(...)` and `compile_router.count_living_artefact_index_entries(...)` succeed. Any exception or divergence path returns `True` without touching the cache, so the next TTL fire re-evaluates.
- Tests in `tests/test_mcp_server.py::TestResourceMtimeCache` cover: signature stability across no-op calls, cache-hit on unchanged mtime, invalidation on file-add at any depth, archive moves NOT triggering invalidation, missing-dir None-encoding, and exception paths leaving the cache untouched.
