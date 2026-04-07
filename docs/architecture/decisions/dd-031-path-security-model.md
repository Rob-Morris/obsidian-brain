# DD-031: Path security model

**Status:** Implemented

## Context

The MCP server accepts file paths from agents and writes to the vault filesystem. Without boundaries, a malicious or confused agent could write to system directories, follow symlinks out of the vault, or modify brain-core's own scripts. The security model must handle three distinct concerns:

1. **Symlink traversal** — Symlinks can escape the vault directory tree.
2. **Protected folders** — Not all vault directories should be writable by agents.
3. **Brain-core self-modification** — Agents must not write to `.brain-core/` which contains the running code.

## Decision

Three layered checks are applied to all write paths:

**`resolve_and_check_bounds(path, bounds)`** — Resolves all symlinks via `os.realpath()` and verifies the resolved path starts with the bounds directory. This prevents symlink escape. The bounds for vault writes is the vault root; body files may additionally be in `/tmp`.

**`check_write_allowed(rel_path)`** — Enforces a folder-prefix allowlist for underscore- and dot-prefixed top-level directories. Dot-prefixed folders (`.obsidian`, `.brain-core`) are always blocked. Underscore-prefixed folders are blocked unless in an explicit allowlist: `{_Temporal, _Config}`. Regular folders and `_Archive` (reachable only via the `archive` action, not direct writes) are permitted.

**`check_not_in_brain_core(path, vault_root)`** — An explicit final check that the resolved path does not fall inside `.brain-core/`. This is belt-and-suspenders: `check_write_allowed` already blocks `.brain-core` via the dot-prefix rule, but the explicit check ensures correctness even if paths are constructed unusually.

These three functions are composed in `safe_write()` and at the call sites in `brain_create` and `brain_edit`.

## Consequences

- Agents cannot escape the vault boundary via symlinks.
- System directories (`.obsidian`, `.git`, `.brain-core`) are write-protected without requiring a blocklist of every system directory.
- `_Temporal` and `_Config` are writable because they hold user-facing artefacts and configuration respectively.
- The model is additive: new underscore-prefixed directories are blocked by default; they must be explicitly allowlisted.
