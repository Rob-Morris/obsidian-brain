# DD-025: 5 MCP Tools — Privilege Split for Granular Permissions

**Status:** Implemented (v0.11.0)
**Supersedes:** DD-011, DD-020

## Context

DD-020's three-tool surface (`brain_read`, `brain_search`, `brain_action`) put all mutating operations into a single `brain_action` tool requiring the same approval level. Creating a single note and renaming files across the entire vault are both mutations — but they carry very different risk profiles. Agents needed to seek approval for low-risk additive operations as often as for destructive ones.

## Decision

Split mutations into three privilege tiers, giving a five-tool surface:

- **`brain_read`** — safe reads, auto-approvable.
- **`brain_search`** — safe search, auto-approvable.
- **`brain_list`** — safe exhaustive enumeration, auto-approvable.
- **`brain_create`** — additive only (creates files, never destroys), safe to auto-approve for most workflows.
- **`brain_edit`** — single-file mutation; approve-once or auto-approve depending on trust level.
- **`brain_action`** — vault-wide or destructive operations; require explicit approval per call.

`brain_session` was added for agent bootstrap. `brain_process` was added for content classification and ingestion.

## Consequences

- Agents can auto-approve the majority of operations without sacrificing safety on destructive ones.
- The privilege tiers map naturally to approval policies in MCP clients.
- The tool surface grew (from 3 to 5 core tools plus session/process), but each tool has a clear, narrow scope.
- Supersedes DD-011 (2 tools) and DD-020 (3 tools).
