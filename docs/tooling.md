# Obsidian Brain — Tooling

Technical design for tooling that operates on Brain vaults. Source design doc: `Designs/brain-tooling-cli-agent-fallback.md` in the Rob vault.

## Compiled Router (DD-008, DD-013, DD-014, DD-016)

The compiled router is the interface contract between human-readable config and all tooling. Source files (`router.md`, taxonomy, skills, styles, VERSION) are the single source of truth. The compiler combines them into `_Config/.compiled-router.json` — a local, gitignored, hash-invalidated cache.

**Key properties:**
- **Filesystem-first discovery** (DD-016) — artefact types are discovered by scanning vault folders, not by reading a registry. Root-level non-system folders → living types. `_Temporal/` subfolders → temporal types. System folders excluded: `_Attachments/`, `_Config/`, `_Plugins/`, `.obsidian/`.
- **Hash invalidation** — SHA-256 of every source file stored in `meta.sources`. Stale the moment any source changes.
- **Environment-specific** — includes absolute paths, platform, runtime availability. Local-only, never committed.
- **Auto-compile on MCP startup** (DD-014) — the MCP server compiles if missing or stale before serving requests. All tools require the compiled router and auto-compile if needed (DD-013).

**Schema:** TBD. See design doc for current draft.

## Brain MCP Server (DD-010, DD-011)

Long-running MCP server at `.brain-core/mcp/server.py`. Exposes 2 tools with enum parameters:

- **`brain_read`** — safe, no side effects. Resources: `artefact`, `trigger`, `style`, `template`, `skill`, `plugin`, `environment`, `router`. Optional `name` filter.
- **`brain_action`** — mutations. Actions: `check`, `compile`, `upgrade`, `create_artefact`. Optional `params` object.

The 2-tool design minimises tool-description token footprint (~270 tokens vs ~600 for granular tools) while preserving a read/write safety split for auto-approval.

**Response payloads:** TBD.

## Lean Router Format (DD-012, DD-017)

The router file read by naive agents (no MCP, no compiled router). Format:

```
Prefer `brain_read`/`brain_action` MCP tools if available.
Otherwise read [[.brain-core/index]].

Always:
- {universal structural constraints — inline}

Conditional:
- {condition} → [[{taxonomy or skill file}]]
```

**Always-rules** are universal constraints that apply every session (e.g. "Every artefact belongs in a typed folder").

**Conditional triggers** use a goto pattern: the router states WHEN (one-line condition + wikilink pointer); the target taxonomy or skill file states WHAT and HOW in a `## Trigger` section. Zero duplication — the taxonomy file is the single source of truth.

**User preferences** (ask clarifying questions, show plan, ask before delete) live in `Agents.md`, not the router.

## check.py (DD-009)

*Fully shaped.* Router-driven vault compliance checker. Reads the compiled router, validates vault files against structural rules.

**Flags:** `--json` (structured output), `--actionable` (enriched fix context), `--severity <level>` (filter: `error`/`warning`/`info`).

**Exit codes:** 0 = clean, 1 = warnings only, 2 = errors present.

**Check catalogue:**

| Check | Severity | Validates |
|---|---|---|
| `root_files` | error | No content files in vault root |
| `naming` | warning | Files match naming pattern from taxonomy |
| `frontmatter_type` | warning | `type` field matches folder-derived type |
| `frontmatter_required` | warning | Required frontmatter fields present |
| `month_folders` | warning | Temporal files in correct `yyyy-mm/` subfolder |
| `unconfigured_type` | info | Folder has no taxonomy file |

**Constraints:** Python 3.8+ stdlib only, self-locating, stateless, idempotent, stdout-only.

## Taxonomy Discovery (DD-018, DD-019)

**Succinct readme pattern** (DD-019): `.brain-core/taxonomy/readme.md` is a lean discovery guide (~50 tokens) that explains the classification system and points agents to `_Config/Taxonomy/`. It does not enumerate types — the filesystem is the index.

**Key derivation convention** (DD-018): type key = lowercase folder name, spaces to hyphens. e.g. `Daily Notes` → `daily-notes` → `_Config/Taxonomy/{classification}/daily-notes.md`. No manual registry needed.

## Pending Design

The following are accepted but not yet fully shaped:

- **compile_router.py** — filesystem scanning algorithm, full JSON schema, system folder exclusion list
- **MCP server** — response payload schemas, session lifecycle, error handling
- **upgrade.py** — in-place upgrade flow, migration steps
- **CLI wrapper** — argument parsing, vault discovery, distribution
- **Plugin registry** — `plugins.json` schema, install flow
- **Obsidian plugin** — TypeScript implementation, shared test fixtures (DD-005, DD-006, DD-007)
- **Frontmatter timestamps absorption** (DD-004) — ignore rules, agent-aware stamping

## Design Decisions Index

| DD | Summary | Status |
|---|---|---|
| DD-001 | Drop version from wikilink paths | Implemented (v0.3.0) |
| DD-002 | Scripts ship inside `.brain-core/`, not as a plugin | Accepted |
| DD-003 | CLI delegates to scripts, never contains unique logic | Accepted |
| DD-004 | Obsidian plugin absorbs frontmatter timestamps | Proposed |
| DD-005 | Obsidian plugin has its own TypeScript implementation | Accepted |
| DD-006 | Mobile is first-class | Accepted |
| DD-007 | Dual implementation with shared test fixtures | Accepted |
| DD-008 | Compiled router as foundation | Accepted |
| DD-009 | Router-driven checks (no separate check config) | Accepted |
| DD-010 | Brain MCP server in `.brain-core/mcp/` | Accepted |
| DD-011 | MCP server exposes 2 tools with enum parameters | Accepted |
| DD-012 | Lean router — always-rules only, conditional triggers co-located | Accepted |
| DD-013 | Compiled router required for tools; markdown fallback for agents only | Accepted |
| DD-014 | MCP server auto-compiles on startup | Accepted |
| DD-015 | Single-line install — never require changes to Agents.md | Implemented (v0.4.0) |
| DD-016 | Filesystem-first artefact discovery | Accepted |
| DD-017 | Shorthand trigger index with gotos | Implemented (v0.4.0) |
| DD-018 | Taxonomy index dropped — filesystem is the index | Implemented (v0.4.0) |
| DD-019 | Succinct readme pattern for lean discovery guides | Implemented (v0.4.0) |
