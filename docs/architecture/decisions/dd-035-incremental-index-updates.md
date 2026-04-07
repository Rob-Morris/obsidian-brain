# DD-035: Incremental index updates

**Status:** Implemented

## Context

The vault index (BM25 document corpus built from all artefact frontmatter and content) is expensive to rebuild in full — it requires walking the entire vault filesystem. Full rebuilds are necessary when the scope of change is unknown (e.g., version drift, external edits). But `brain_create` and `brain_edit` know exactly which file was modified: rebuilding the full index for every single-file mutation wastes CPU and delays the next search.

At the same time, the MCP server is long-running and must detect external changes (e.g., the user editing a file directly in Obsidian). A pure reactive model that only responds to tool calls would miss these.

## Decision

Three index update modes are implemented and selected by `_ensure_index_fresh()`:

1. **Full rebuild** (`_index_dirty = True`) — Triggered by version drift or any operation with unknown scope (e.g., `build_index` action, `fix-links`). Rebuilds from scratch, clears the dirty flag.

2. **Incremental upsert** (`_index_pending` queue) — `brain_create` and `brain_edit` call `_mark_index_pending(rel_path, type_hint)` immediately after writing the file. On the next tool call that requires search, `_ensure_index_fresh()` drains the queue, calling `build_index.index_update()` for each queued path. Corpus-level statistics (IDF weights) are recomputed once after draining. The `built_at` timestamp is not advanced so the filesystem staleness check is not fooled into thinking a full rebuild happened.

3. **TTL-gated staleness check** — After draining the incremental queue, a filesystem-level staleness check runs if more than `_INDEX_CHECK_TTL` seconds have elapsed since the last check. This catches external edits (files modified outside the MCP server).

If an incremental update fails, the error is logged and `_mark_index_dirty()` is called, falling back to a full rebuild on the next call. The `_index_pending_lock` mutex guards the queue across all three call sites.

## Consequences

- Common operations (create/edit) update the index in sub-millisecond time for the queued path alone.
- External vault edits are detected within `_INDEX_CHECK_TTL` without blocking every tool call.
- The full rebuild path is preserved as a safe fallback for any situation with unknown scope.
- The queue is per-process in memory; if the server restarts, any pending incremental updates are lost. The filesystem staleness check on startup catches these.
