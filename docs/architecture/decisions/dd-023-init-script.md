# DD-023: init.py Setup Script with Three Registration Scopes

**Status:** Implemented (v0.10.0)
**Extended by:** DD-039, DD-040

## Context

Registering the MCP server with Claude Code requires writing to `.mcp.json` (project scope), `.claude/settings.local.json` (local scope), or `~/.claude.json` (user scope). Doing this manually is error-prone, and the right scope depends on whether the vault is shared, personal, or a default for all projects.

## Decision

`init.py` is a self-contained setup script at `.brain-core/scripts/init.py`. It configures Claude Code to use the brain MCP server and supports three scopes:
- **Project** (default): `.mcp.json` + `CLAUDE.md` in the current or specified directory.
- **Local** (`--local`): `.claude/settings.local.json` + `.claude/CLAUDE.local.md` — gitignored, for personal use.
- **User** (`--user`): `~/.claude.json` — default brain for all projects.

Registration uses `claude mcp add-json` when the CLI is available, otherwise edits JSON directly. All writes are atomic (tmp + fsync + rename). The script is idempotent and warns on scope conflicts.

## Consequences

- Users choose the appropriate scope for their workflow; the script handles the implementation details.
- Idempotency means re-running after an upgrade is safe.
- The script is stdlib-only (no `_common` imports) because it runs in contexts where `_common` may not yet be available (e.g. during the install process).
- `install.sh` calls `init.py` automatically when MCP setup is enabled and dependency installation succeeds; manual installs, retries, or scope changes call it directly.
