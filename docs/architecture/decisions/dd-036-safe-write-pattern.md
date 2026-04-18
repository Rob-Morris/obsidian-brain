# DD-036: Safe write pattern

**Status:** Implemented

## Context

Vault artefacts are the primary user data. A write failure mid-stream (power loss, process kill, disk full) that truncates or corrupts a file is unacceptable — Obsidian has no crash recovery, and the user may not notice corruption immediately.

Simple `open(path, "w").write(content)` is not atomic: it truncates the target file first, then writes. A crash between truncation and completion leaves an empty or partial file. Symlink traversal during the open can also redirect writes to unintended locations.

## Decision

`safe_write(path, content, *, bounds, ...)` implements the tmp-fsync-rename pattern:

1. **Bounds check** — `resolve_and_check_bounds()` resolves all symlinks and confirms the target is within the vault root before any I/O begins.
2. **Write to a unique sibling temp file** — Content is written to a fresh `mkstemp()` path in the same directory. Using a sibling (same filesystem, same directory) ensures `os.replace()` is atomic on POSIX — it is a single `rename(2)` syscall.
3. **`f.flush()` + `os.fsync(f.fileno())`** — Ensures data is written to stable storage, not just the OS page cache, before the rename.
4. **`os.replace(tmp, target)`** — Atomically replaces the target. The old content remains intact until the rename completes. If the rename fails, the original file is untouched.
5. **Cleanup on any exception** — A `BaseException` handler unlinks the tmp file if any step fails, preventing orphan temp files.

Each write gets its own sibling temp path, so same-process concurrent writers no
longer collide on a shared temp filename when they target the same file.

`safe_write_json()` is a thin wrapper that serialises to JSON first, then delegates to `safe_write`.

## Consequences

- A crash at any point leaves either the complete old content or the complete new content on disk — never a partial write.
- `fsync` adds latency (typically 1-10ms on local SSD) but ensures durability. This is acceptable for vault artefact writes which are infrequent relative to read operations.
- The tmp file is in the same directory, so it is visible in Obsidian briefly. The `.tmp` suffix prevents Obsidian from treating it as a markdown file.
- This is an atomic replacement primitive, not a transaction manager. Parallel read-modify-write flows can still lose updates unless a higher layer coordinates them.
- `exclusive=True` mode (used by `brain_create`) checks file existence before writing, providing a lightweight create-or-fail guarantee.
