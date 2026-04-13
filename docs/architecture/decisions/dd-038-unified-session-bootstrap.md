# DD-038: Unified session bootstrap

**Status:** Implemented (v0.25.0)

## Context

The previous bootstrap model was fragmented across four surfaces with overlapping responsibilities:

- `brain_session` returns structured runtime and vault context
- `.brain-core/index.md` carries principles and bootstrap routing
- a separate polyfill doc carries additional core docs and standards links for MCP agents with local filesystem access
- `.brain-core/md-bootstrap.md` carries the raw-file fallback for agents without MCP

This makes the bootstrap hard to reason about. `brain_session` and the markdown path are not in parity, `index.md` acts as both entry point and payload, and the intentional degraded fallback is mixed into the normal path.

At the same time, Brain intentionally supports three distinct operating modes:

1. MCP bootstrap for the normal case
2. Script or generated-markdown bootstrap when MCP is unavailable
3. Raw markdown/source-file fallback for naive agents without MCP, scripts, or generated session state

The architecture needs one canonical bootstrap model for the normal paths without removing the intentional lowest-tier fallback.

## Decision

Bootstrap is unified around one canonical session model owned by `session.py`.

The canonical session model is assembled from:

- checked-in core bootstrap content
- compiled router state
- `_Config/User/preferences-always.md`
- `_Config/User/gotchas.md`
- merged config
- runtime environment state
- active workspace context when known, including optional workspace-owned defaults from `.brain/workspace.yaml`
- active profile when known

That model is rendered in two normal bootstrap forms:

- `brain_session` JSON for MCP agents
- `.brain/local/session.md` for non-MCP/script bootstrap

To support this split cleanly:

- `.brain-core/index.md` becomes a thin entry point only
- `.brain-core/session-core.md` becomes the checked-in source for static core bootstrap content
- `brain_session` exposes session-core reference docs as structured `core_docs` entries with explicit MCP load instructions, while the markdown mirror renders them as local file links
- the temporary polyfill surface is removed entirely
- `.brain-core/md-bootstrap.md` remains as the explicit degraded fallback for agents that have neither MCP nor a generated markdown session

Source bootstrap docs are authored in repo-native form. Doc-to-doc navigation uses relative markdown links; imperative bootstrap instructions use plain file paths. Installed `.brain-core/...` paths are a deployment detail normalised by the bootstrap transport, not the authored link format.

Bootstrap content is runtime-operational only. Contributor workflow policy for
changing `obsidian-brain` does not belong in `session-core.md`, `index.md`,
`brain_session`, or `.brain/local/session.md`; those surfaces are shipped to
normal Brain agents and must stay focused on using the vault rather than
contributing to the repo.

The intended agent reading flow becomes:

1. If MCP is available, call `brain_session`
2. Otherwise, read `.brain/local/session.md` if it exists
3. Otherwise, follow `.brain-core/index.md` into `.brain-core/md-bootstrap.md` and read raw source files directly

Installed vault bootstrap text is treated as versioned contract text and migrated to one canonical wording that points non-MCP agents at `.brain-core/index.md`.

`.brain-core/` is a version-bound unit and is upgraded atomically. Mixed-version or partial-upgrade states inside `.brain-core/` are considered broken installs, not compatibility targets, so bootstrap consumers fail fast rather than falling back across generations of core files.

## Consequences

- The normal bootstrap path has one source of truth instead of parallel hand-maintained surfaces.
- JSON and markdown bootstrap outputs can be tested for parity because they come from the same model.
- `index.md` becomes easier to reason about: it routes, rather than duplicating payload content.
- The intentional raw-file fallback remains supported for naive agents, but it is clearly separated from the normal bootstrap path.
- Workspace-aware bootstrap stays additive: agents can use canonical workspace context when present, but bootstrap still degrades cleanly to generic vault context when no workspace is known.
- The audience boundary is explicit: shipped bootstrap surfaces carry vault-operational guidance, while repo contributor workflow stays in contributor docs under `docs/`.
- The refactor introduces a generated artefact (`.brain/local/session.md`) that must participate in freshness and migration logic.
- The bootstrap line in installed vaults must be migrated again, including all previously known variants and the newer manually introduced wording.
