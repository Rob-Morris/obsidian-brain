# DD-036: Safe write pattern

**Status:** Implemented

## Context

Vault artefacts are the primary user data. A write failure mid-stream (power loss, process kill, disk full) that truncates or corrupts a file is unacceptable — Obsidian has no crash recovery, and the user may not notice corruption immediately.

Simple `open(path, "w").write(content)` is not atomic: it truncates the target file first, then writes. A crash between truncation and completion leaves an empty or partial file. Symlink traversal during the open can also redirect writes to unintended locations.

## Decision

`safe_write(path, content, *, bounds, ...)` and `safe_write_via(path, writer, *, bounds, ...)`
share the same tmp-fsync-rename kernel:

1. **Bounds check** — `resolve_and_check_bounds()` resolves all symlinks and confirms the target is within the vault root before any I/O begins.
2. **Write to a unique sibling temp file** — Content is written to a fresh `mkstemp()` path in the same directory. Using a sibling (same filesystem, same directory) ensures `os.replace()` is atomic on POSIX — it is a single `rename(2)` syscall.
3. **`f.flush()` + `os.fsync(f.fileno())`** — Ensures data is written to stable storage, not just the OS page cache, before the rename.
4. **`os.replace(tmp, target)`** — Atomically replaces the target. The old content remains intact until the rename completes. If the rename fails, the original file is untouched.
5. **Cleanup on any exception** — A `BaseException` handler unlinks the tmp file if any step fails, preventing orphan temp files.

Each write gets its own sibling temp path, so same-process concurrent writers no
longer collide on a shared temp filename when they target the same file.

The same sibling-tempfile pattern is used directly in the self-contained
bootstrap scripts (`init.py`, `upgrade.py`) and the historical migrations so
early install and upgrade flows stay atomic without depending on `_common`.

`safe_write_json()` is a thin wrapper over the same kernel, and embeddings-local
binary writers can use `safe_write_via()` without promoting NumPy-specific
helpers into `_common`.

## Consequences

- A crash at any point leaves either the complete old content or the complete new content on disk — never a partial write.
- `fsync` adds latency (typically 1-10ms on local SSD) but ensures durability. This is acceptable for vault artefact writes which are infrequent relative to read operations.
- The tmp file is in the same directory, so it is visible in Obsidian briefly. The `.tmp` suffix prevents Obsidian from treating it as a markdown file.
- This is an atomic replacement primitive, not a transaction manager. Parallel read-modify-write flows can still lose updates unless a higher layer coordinates them.
- `exclusive=True` mode (used by `brain_create`) checks file existence before writing, providing a lightweight create-or-fail guarantee.

## Session-mirror write path (v0.29.5)

The `.brain/local/session.md` mirror does **not** use `_run_with_timeout` or an abandon-on-timeout worker pattern. `v0.29.5` routes refreshes through a single long-lived daemon worker consuming a coalescing queue (`maxsize=1`). Callers enqueue a refresh request — non-blocking — and the worker drains the queue FIFO. Rapid-fire refreshes collapse to the latest intent because a pending slot is dropped on the next enqueue. The worker writes via `safe_write` inside `session.persist_session_markdown`, so each write is still atomic.

Invariants:

- Startup never blocks on the mirror write; `_run_startup_phase("session_mirror_refresh", ...)` completes as soon as the request lands in the queue.
- No abandoned threads: there is one worker per process, not one per refresh.
- No late-writer clobber: a slow write delays subsequent writes, but the queue ordering guarantees the most recent enqueue wins once the worker unsticks.
- Shutdown registers an `atexit` drain (2s cap) that signals the worker to exit after the in-flight write. If the filesystem is stuck longer than the cap, the daemon thread is killed on interpreter exit; any orphaned `session.md.*.tmp` file is swept by `_sweep_mirror_tmpfiles` at the next startup.

This pattern is deliberately scoped to the mirror write path. `_run_with_timeout` remains in use for the critical startup compile and index-build phases, where fail-loud-on-timeout is the right semantic.
