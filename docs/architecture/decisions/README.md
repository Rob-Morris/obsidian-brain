# Design Decisions

Brain-core is built on a foundation of explicit architectural decisions. Each decision is documented in its own file with context, rationale, and implementation notes. This directory serves as an index to those decisions.

Superseded decisions are kept for historical reference and to understand the evolution of the architecture.

| DD | Summary | Status | File |
|---|---|---|---|
| DD-001 | Drop version from wikilink paths | Implemented (v0.3.0) | [dd-001-drop-version-wikilinks.md](dd-001-drop-version-wikilinks.md) |
| DD-002 | Scripts ship inside `.brain-core/`, not as a plugin | Accepted | [dd-002-scripts-in-brain-core.md](dd-002-scripts-in-brain-core.md) |
| DD-003 | CLI delegates to scripts, never contains unique logic | Accepted | [dd-003-cli-delegates-to-scripts.md](dd-003-cli-delegates-to-scripts.md) |
| DD-004 | Obsidian plugin absorbs frontmatter timestamps | Proposed | [dd-004-plugin-frontmatter-timestamps.md](dd-004-plugin-frontmatter-timestamps.md) |
| DD-005 | Obsidian plugin has its own TypeScript implementation | Accepted | [dd-005-plugin-typescript.md](dd-005-plugin-typescript.md) |
| DD-006 | Mobile is first-class | Accepted | [dd-006-mobile-first-class.md](dd-006-mobile-first-class.md) |
| DD-007 | Dual implementation with shared test fixtures | Accepted | [dd-007-dual-implementation.md](dd-007-dual-implementation.md) |
| DD-008 | Compiled router as foundation | Implemented (v0.5.0) | [dd-008-compiled-router.md](dd-008-compiled-router.md) |
| DD-009 | Router-driven checks (no separate check config) | Implemented (v0.9.11) | [dd-009-router-driven-checks.md](dd-009-router-driven-checks.md) |
| DD-010 | Brain MCP server in `.brain-core/mcp/` | Implemented (v0.7.0) | [dd-010-mcp-server.md](dd-010-mcp-server.md) |
| DD-011 | MCP server exposes 2 tools with enum parameters | Superseded by DD-025 (v0.11.0) | [dd-011-two-mcp-tools.md](dd-011-two-mcp-tools.md) |
| DD-012 | Lean router — always-rules only, conditional triggers co-located | Accepted | [dd-012-lean-router.md](dd-012-lean-router.md) |
| DD-013 | Compiled router required for tools; markdown fallback for agents only | Accepted | [dd-013-compiled-router-required.md](dd-013-compiled-router-required.md) |
| DD-014 | MCP server auto-compiles on startup | Implemented (v0.7.0) | [dd-014-auto-compile.md](dd-014-auto-compile.md) |
| DD-015 | Single-line install — never require changes to Agents.md | Implemented (v0.4.0) | [dd-015-single-line-install.md](dd-015-single-line-install.md) |
| DD-016 | Filesystem-first artefact discovery | Implemented (v0.5.0) | [dd-016-filesystem-first.md](dd-016-filesystem-first.md) |
| DD-017 | Shorthand trigger index with gotos | Implemented (v0.4.0) | [dd-017-shorthand-triggers.md](dd-017-shorthand-triggers.md) |
| DD-018 | Taxonomy index dropped — filesystem is the index | Implemented (v0.4.0) | [dd-018-no-taxonomy-index.md](dd-018-no-taxonomy-index.md) |
| DD-019 | Succinct readme pattern for lean discovery guides | Implemented (v0.4.0) | [dd-019-succinct-readme.md](dd-019-succinct-readme.md) |
| DD-020 | 3 MCP tools: brain_read + brain_search + brain_action | Superseded by DD-025 (v0.11.0) | [dd-020-three-mcp-tools.md](dd-020-three-mcp-tools.md) |
| DD-021 | Optional Obsidian CLI integration — CLI-preferred, agent-fallback | Implemented (v0.8.0) | [dd-021-obsidian-cli.md](dd-021-obsidian-cli.md) |
| DD-022 | Obsidian CLI is internal to MCP; agents use CLI directly only when MCP unavailable | Accepted | [dd-022-cli-internal.md](dd-022-cli-internal.md) |
| DD-023 | init.py setup script | Implemented (v0.10.0) | [dd-023-init-script.md](dd-023-init-script.md) |
| DD-024 | Core skills in .brain-core/skills/ | Implemented (v0.10.0) | [dd-024-core-skills.md](dd-024-core-skills.md) |
| DD-025 | 5 MCP tools: privilege split for granular permissions | Implemented (v0.11.0) | [dd-025-privilege-split.md](dd-025-privilege-split.md) |
| DD-026 | MCP response readability: plain text over JSON blobs | Implemented (v0.14.4, polished v0.14.5) | [dd-026-response-readability.md](dd-026-response-readability.md) |
| DD-027 | MCP tool resilience conventions | Accepted | [dd-027-tool-resilience.md](dd-027-tool-resilience.md) |
| DD-028 | Version drift detection — exit code 10 + proxy restart | Implemented | [dd-028-version-drift-detection.md](dd-028-version-drift-detection.md) |
| DD-029 | Archive architecture — `_Archive/{Type}/{Project}/` with date prefix | Implemented | [dd-029-archive-architecture.md](dd-029-archive-architecture.md) |
| DD-030 | Terminal status auto-move to `+Status/` subfolder | Implemented | [dd-030-terminal-status-auto-move.md](dd-030-terminal-status-auto-move.md) |
| DD-031 | Path security model — bounds, allowlist, brain-core protection | Implemented | [dd-031-path-security-model.md](dd-031-path-security-model.md) |
| DD-032 | Three-layer config merge — template → vault → local | Implemented | [dd-032-three-layer-config-merge.md](dd-032-three-layer-config-merge.md) |
| DD-033 | Operator profiles — reader/contributor/operator with SHA-256 keys | Implemented | [dd-033-operator-profiles.md](dd-033-operator-profiles.md) |
| DD-034 | Wikilink resolution strategy — multi-strategy cascade | Implemented | [dd-034-wikilink-resolution.md](dd-034-wikilink-resolution.md) |
| DD-035 | Incremental index updates — dirty flag + pending queue + TTL | Implemented | [dd-035-incremental-index-updates.md](dd-035-incremental-index-updates.md) |
| DD-036 | Safe write pattern — tmp + fsync + rename | Implemented | [dd-036-safe-write-pattern.md](dd-036-safe-write-pattern.md) |
| DD-037 | Generous filename matching — preserve Unicode, strip only unsafe chars | Implemented | [dd-037-generous-filename-matching.md](dd-037-generous-filename-matching.md) |
| DD-038 | Unified session bootstrap — one model, JSON + markdown renderers, thin index | Implemented (v0.25.0) | [dd-038-unified-session-bootstrap.md](dd-038-unified-session-bootstrap.md) |
