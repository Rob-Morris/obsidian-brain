# Design Decisions

Brain-core is built on a foundation of explicit architectural decisions. Each decision is documented in its own file with context, rationale, and implementation notes. This directory serves as an index to those decisions.

## Purpose

Each DD captures the reasoning behind an architectural decision at the time it was made — the alternatives considered, the constraints, the trade-offs. That reasoning is the thing only DDs carry, and it is frozen. Living docs in `docs/functional/` and `docs/architecture/` describe how the system works today; DDs explain why it ended up that way.

## Conventions

**Body is immutable.** The Context, Decision, Alternatives, and Consequences sections of a DD are never rewritten. If behaviour changes, write a new DD that supersedes or extends the old one. The historical reasoning stays intact — it is expensive to reconstruct and impossible to recover once overwritten.

**Header metadata is navigational and may be updated.** A DD's top-of-file metadata block may carry `Status`, `Supersedes`, `Superseded by`, and `Extended by` lines. These are pointers between decisions, not rewrites of them. Update them when a new DD lands that changes the relationship.

**This index is the current-state map.** The Status column below is authoritative for whether a decision is live, superseded, or proposed. The topic map at the bottom groups related decisions for discovery. When in doubt, start with the topic map.

### Writing a new DD

1. Use the next available number (`dd-NNN-slug.md`).
2. Start with `# DD-NNN: Title`, then a `**Status:**` line. Add `**Supersedes:**` or `**Extends:**` if applicable.
3. Include sections: Context, Decision, Alternatives Considered, Consequences. Add Implementation Notes if useful.
4. Reference prior DDs in the Context section — new DDs point backward.
5. Add a row to the chronological table below.
6. Update any predecessor DD's header metadata with `**Extended by:** DD-NNN` or `**Superseded by:** DD-NNN`.

### Header format

```
# DD-NNN: Title

**Status:** Proposed | Accepted | Implemented (vX.Y.Z) | Superseded by DD-YYY (vA.B.C)
**Supersedes:** DD-AAA, DD-BBB        ← optional, when this DD replaces earlier ones
**Extends:** DD-CCC                   ← optional, when this DD was built atop another
**Extended by:** DD-DDD, DD-EEE       ← optional, added later when successors land
```

---

## Chronological Index

| DD | Summary | Status | File |
|---|---|---|---|
| DD-001 | Drop version from wikilink paths | Implemented (v0.3.0) | [dd-001](dd-001-drop-version-wikilinks.md) |
| DD-002 | Scripts ship inside `.brain-core/`, not as a plugin | Accepted | [dd-002](dd-002-scripts-in-brain-core.md) |
| DD-003 | CLI delegates to scripts, never contains unique logic | Accepted | [dd-003](dd-003-cli-delegates-to-scripts.md) |
| DD-004 | Obsidian plugin absorbs frontmatter timestamps | Proposed | [dd-004](dd-004-plugin-frontmatter-timestamps.md) |
| DD-005 | Obsidian plugin has its own TypeScript implementation | Accepted | [dd-005](dd-005-plugin-typescript.md) |
| DD-006 | Mobile is first-class | Accepted | [dd-006](dd-006-mobile-first-class.md) |
| DD-007 | Dual implementation with shared test fixtures | Accepted | [dd-007](dd-007-dual-implementation.md) |
| DD-008 | Compiled router as foundation | Implemented (v0.5.0) | [dd-008](dd-008-compiled-router.md) |
| DD-009 | Router-driven checks (no separate check config) | Implemented (v0.9.11) | [dd-009](dd-009-router-driven-checks.md) |
| DD-010 | Brain MCP server in `.brain-core/brain_mcp/` | Implemented (v0.7.0) | [dd-010](dd-010-mcp-server.md) |
| DD-011 | MCP server exposes 2 tools with enum parameters | Superseded by DD-025 | [dd-011](dd-011-two-mcp-tools.md) |
| DD-012 | Lean router — always-rules only, conditional triggers co-located | Accepted | [dd-012](dd-012-lean-router.md) |
| DD-013 | Compiled router required for tools; markdown fallback for agents only | Accepted | [dd-013](dd-013-compiled-router-required.md) |
| DD-014 | MCP server auto-compiles on startup | Implemented (v0.7.0) | [dd-014](dd-014-auto-compile.md) |
| DD-015 | Single-line install — never require changes to AGENTS.md | Implemented (v0.4.0) | [dd-015](dd-015-single-line-install.md) |
| DD-016 | Filesystem-first artefact discovery | Implemented (v0.5.0) | [dd-016](dd-016-filesystem-first.md) |
| DD-017 | Shorthand trigger index with gotos | Implemented (v0.4.0) | [dd-017](dd-017-shorthand-triggers.md) |
| DD-018 | Taxonomy index dropped — filesystem is the index | Implemented (v0.4.0) | [dd-018](dd-018-no-taxonomy-index.md) |
| DD-019 | Succinct readme pattern for lean discovery guides | Implemented (v0.4.0) | [dd-019](dd-019-succinct-readme.md) |
| DD-020 | 3 MCP tools: brain_read + brain_search + brain_action | Superseded by DD-025 | [dd-020](dd-020-three-mcp-tools.md) |
| DD-021 | Optional Obsidian CLI integration — CLI-preferred, agent-fallback | Implemented (v0.8.0) | [dd-021](dd-021-obsidian-cli.md) |
| DD-022 | Obsidian CLI is internal to MCP; agents use CLI directly only when MCP unavailable | Accepted | [dd-022](dd-022-cli-internal.md) |
| DD-023 | init.py setup script with registration scopes | Implemented (v0.10.0) | [dd-023](dd-023-init-script.md) |
| DD-024 | Core skills in .brain-core/skills/ | Implemented (v0.10.0) | [dd-024](dd-024-core-skills.md) |
| DD-025 | 5 MCP tools: privilege split for granular permissions | Implemented (v0.11.0) | [dd-025](dd-025-privilege-split.md) |
| DD-026 | MCP response readability: plain text over JSON blobs | Implemented (v0.14.4) | [dd-026](dd-026-response-readability.md) |
| DD-027 | MCP tool resilience conventions | Accepted | [dd-027](dd-027-tool-resilience.md) |
| DD-028 | Version drift detection — exit code 10 + proxy restart | Implemented | [dd-028](dd-028-version-drift-detection.md) |
| DD-029 | Archive architecture — `_Archive/{Type}/{Project}/` with date prefix | Implemented | [dd-029](dd-029-archive-architecture.md) |
| DD-030 | Terminal status auto-move to `+Status/` subfolder | Implemented | [dd-030](dd-030-terminal-status-auto-move.md) |
| DD-031 | Path security model — bounds, allowlist, brain-core protection | Implemented | [dd-031](dd-031-path-security-model.md) |
| DD-032 | Three-layer config merge — template → vault → local | Implemented | [dd-032](dd-032-three-layer-config-merge.md) |
| DD-033 | Operator profiles — reader/contributor/operator with SHA-256 keys | Implemented | [dd-033](dd-033-operator-profiles.md) |
| DD-034 | Wikilink resolution strategy — multi-strategy cascade | Implemented | [dd-034](dd-034-wikilink-resolution.md) |
| DD-035 | Incremental index updates — dirty flag + pending queue + TTL | Implemented | [dd-035](dd-035-incremental-index-updates.md) |
| DD-036 | Safe write pattern — tmp + fsync + rename | Implemented | [dd-036](dd-036-safe-write-pattern.md) |
| DD-037 | Generous filename matching — preserve Unicode, strip only unsafe chars | Implemented | [dd-037](dd-037-generous-filename-matching.md) |
| DD-038 | Unified session bootstrap — one model, JSON + markdown renderers, thin index | Implemented (v0.25.0) | [dd-038](dd-038-unified-session-bootstrap.md) |
| DD-039 | Multi-client MCP install keeps native client scopes | Accepted | [dd-039](dd-039-multi-client-mcp-install-scopes.md) |
| DD-040 | Workspace architecture — hub, registry, manifest, session wiring | Implemented (v0.27.5) | [dd-040](dd-040-workspace-architecture.md) |
| DD-041 | Canonical living-artefact key convention — `key:` field, `{type}/{key}` lookup, explicit `parent:` ownership | Implemented (v0.31.0) | [dd-041](dd-041-canonical-key-convention.md) |
| DD-042 | Mtime-signature staleness fast-path — stat directories before walking | Implemented (v0.31.1) | [dd-042](dd-042-mtime-signature-staleness-fast-path.md) |
| DD-043 | Bootstrap launchers and managed runtimes | Implemented (v0.32.6) | [dd-043](dd-043-bootstrap-launchers-and-managed-runtimes.md) |
| DD-044 | MCP tool metadata contract — living functional doc, DD rationale | Implemented (v0.33.0) | [dd-044](dd-044-mcp-tool-metadata-contract.md) |
| DD-045 | MCP mutation surface split — `brain_move`, residual `brain_action`, scripts-only admin | Implemented (v0.33.0) | [dd-045](dd-045-mcp-mutation-surface-split.md) |
| DD-046 | Park unfinished `brain_process` / embeddings work off-main until it is ready to ship | Implemented (v0.33.0) | [dd-046](dd-046-park-brain-process-off-main.md) |
| DD-047 | Keep the parked `brain_process` branch explicitly unreleased while aligning with `v0.33.0` conventions | Accepted | [dd-047](dd-047-brain-process-parked-branch-conventions.md) |

---

## Topic Map

Related decisions grouped by domain. Arrows show supersede/extend chains.

- **MCP tool surface:** DD-010 → DD-044, DD-010 → DD-045 → DD-046 → DD-047, DD-011 → DD-020 → DD-025 → DD-045, DD-026, DD-027, DD-028
- **Config & install:** DD-015, DD-023 → DD-039 → DD-043, DD-032, DD-033
- **Router & bootstrap:** DD-008, DD-009, DD-012, DD-013, DD-014, DD-017, DD-019, DD-038, DD-042
- **Security & integrity:** DD-031, DD-036, DD-043
- **Plugins & platforms:** DD-004, DD-005, DD-006, DD-007, DD-021, DD-022
- **Agent methodology:** DD-024, DD-035
- **Workspaces:** DD-040 (extends DD-038, DD-023)
