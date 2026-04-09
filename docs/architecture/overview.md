# Architecture Overview

## System Overview

brain-core is a self-extending system for organising Obsidian vaults, for agents and humans working together. It ships a versioned engine (`.brain-core/`) into each vault that provides MCP tools, CLI scripts, and a taxonomy-driven configuration layer. Together these give agents and human operators a shared, structured interface to vault content: creating and editing artefacts, searching by keyword, enforcing naming and status conventions, and bootstrapping agents with the minimum context needed to operate correctly.

---

## Component Map

### `.brain-core/` — the engine

Copied into the vault during setup and upgrade (not symlinked — vaults are self-contained and portable). Contains:

- `scripts/` — all vault operation logic as importable Python modules with CLI entry points
- `mcp/server.py` — thin MCP wrapper over scripts; holds router and index in memory
- `skills/` — core skill documents (system-provided, tagged `"source": "core"`, overwritten on upgrade)
- `index.md` — system principles, always-rules, and tooling instructions; read every session
- `session-polyfill.md` — core documentation and standards links (temporary supplement until brain_session delivers natively)
- `md-bootstrap.md` — fallback bootstrap for environments without MCP tools

### `.brain/` — vault-local runtime state

Generated, gitignored. The compiled outputs that tooling reads at runtime:

| Path | Contents |
|---|---|
| `.brain/local/compiled-router.json` | Compiled router — the interface contract between config and tooling |
| `.brain/local/retrieval-index.json` | BM25 retrieval index for keyword search |
| `.brain/config.yaml` | Vault-level configuration (layer 2 of 3) |
| `.brain/local/config.yaml` | Machine-local overrides (layer 3 of 3; gitignored) |
| `.brain/local/workspaces.json` | Workspace slug-to-path registry |
| `.brain/local/mcp-server.log` | Rotating server log (2 MB max, 1 backup) |

### `_Config/` — user-customisable definitions

Instance configuration specific to this vault installation:

- `_Config/router.md` — lean bridge: capability detection, always-rules, conditional trigger gotos (~45 tokens)
- `_Config/Taxonomy/` — one file per artefact type with detailed instructions; loaded on demand
- `_Config/Skills/` — user-defined skill documents; discovered by the compiler alongside core skills
- `_Config/Memories/` — standing context injected by trigger matching
- `_Config/Styles/` — formatting style definitions
- `_Config/Templates/` — artefact creation templates
- `_Config/User/preferences-always.md` — vault owner's workflow preferences and quality standards
- `_Config/User/gotchas.md` — learned lessons from previous sessions

### `_Temporal/` — time-stamped artefacts

Working files in type subfolders under `_Temporal/`, each organised into `yyyy-mm/` month folders. Temporal types are discovered by scanning `_Temporal/` subfolders (distinct from the living-type scan).

### Living artefact folders — user content

Root-level folders without a `_` or `.` prefix are living artefact types (e.g. `Projects/`, `Research/`, `Ideas/`). Discovered by scanning the vault root — no registry required. Each type maps to a folder defined in `_Config/Taxonomy/`.

### `_Archive/` — terminal artefacts

Artefacts whose status reaches a terminal value (e.g. `implemented`, `adopted`) are moved here by type and project subfolder. Read-accessible but write-protected by the path security model.

---

## Data Flow

A typical MCP tool call follows this path:

```
MCP client request
  → server.py — dispatches to the matching tool handler
  → imports and calls the relevant script function
  → script reads compiled router / retrieval index from in-memory state
  → operates on the vault filesystem (read, write, rename, etc.)
  → returns a structured response to the MCP client
```

The server loads the compiled router and retrieval index at startup and holds both in memory for the session lifetime. This means scripts called via MCP pay no disk I/O for router or index reads. Scripts called directly (without MCP) read the same JSON files from disk on each invocation — same logic, higher cold-start cost.

Mid-session, if `.brain-core/` is upgraded the server detects version drift on the next tool call and exits cleanly (code 0). The MCP client restarts it with the new code.

---

## Key Architectural Properties

### Filesystem-first discovery

Artefact types are discovered by scanning vault folders, not by reading a registry. Root-level non-system folders become living types. `_Temporal/` subfolders become temporal types. The convention is: any top-level folder starting with `_` or `.` is infrastructure. `_Temporal/` follows this convention (excluded from the living-type scan) but receives its own dedicated scan for its children. This means adding a new artefact type requires only creating a folder and a taxonomy file — no registry update.

### Compiled router as contract

The compiled router (`.brain/local/compiled-router.json`) is the interface between human-readable config and all tooling. Source files — `router.md`, taxonomy files, skills, styles, and `VERSION` — are the single source of truth. The compiler combines them into a hash-invalidated cache: SHA-256 of every source file is stored in `meta.sources`, and the cache is considered stale the moment any source changes. The router is environment-specific (includes platform, runtime availability, absolute vault root) and is never committed to version control. The MCP server auto-compiles it at startup and auto-recompiles mid-session when new taxonomy files appear.

### Scripts as single source of truth

The MCP server is a thin wrapper. All vault operation logic lives in `.brain-core/scripts/` as importable Python modules, each with a CLI entry point. The server imports functions from scripts and adds only MCP transport, in-memory caching, and Obsidian CLI delegation. This means agents without MCP use the scripts directly and get identical results. New operations are always implemented as scripts first, then exposed via MCP — never the reverse.

### Three-layer config merge

Vault configuration is assembled from three layers at server startup:

1. **Template defaults** — built-in baseline values
2. **`.brain/config.yaml`** — vault-level configuration (committed with the vault)
3. **`.brain/local/config.yaml`** — machine-local overrides (gitignored)

Later layers override earlier ones. This lets vault owners set shared defaults while individual machines or operators override specific values without affecting others.

### Path security model

Two complementary guards protect the vault from unintended writes:

- **`resolve_and_check_bounds(path, bounds)`** — resolves symlinks and verifies the target is within the vault root. Raises `ValueError` if the resolved path escapes the boundary or is a symlink when symlink-following is disabled. Used on every read that accepts a caller-supplied path.
- **`check_write_allowed(rel_path)`** — enforces folder-level write restrictions. Dot-prefixed top-level folders (`.brain/`, `.obsidian/`, `.brain-core/`) are always blocked. Underscore-prefixed top-level folders are blocked unless in the explicit allowlist: only `_Temporal/` and `_Config/` are writable. `_Archive/`, `_Plugins/`, `_Workspaces/`, and `_Assets/` are protected.
- **`safe_write(path, content, bounds=...)`** — atomic write (temp file + `os.replace`) that calls `resolve_and_check_bounds` before writing. All script write operations go through this function.

---

## Agent Reading Flow

Agents bootstrap in four tiers, each degrading gracefully when the previous is unavailable:

1. **MCP tools** — `brain_session` returns a compiled bootstrap payload in one call: always-rules, user preferences, gotchas, triggers, condensed artefact types, and environment. Lowest token cost; uses in-memory caching. A SessionStart hook calls `session.py` automatically so agents receive session context before their first turn.
2. **Scripts** — if MCP is unavailable, `.brain-core/scripts/` provides full functionality via CLI: `read.py` queries the compiled router, `search_index.py` runs BM25 search, and so on. Same logic as MCP, disk-based.
3. **Lean router** — if neither MCP nor scripts are available, the agent reads `Agents.md` then `_Config/router.md` (~45 tokens). The router provides conditional trigger pointers and vault-specific rules. Taxonomy files are loaded on demand when a condition matches.
4. **Naive fallback** — if the agent has no knowledge of the system, it reads `Agents.md`, follows wikilinks through `router.md`, and discovers the vault structure directly from the filesystem: root-level non-system folders are living types, `_Temporal/` subfolders are temporal types.

All tiers begin with the `Agents.md` bootstrap directive, which points agents to `brain_session` and `.brain-core/index.md`. This ensures every agent receives the system principles and constraints, regardless of which access tier is available.

---

## Cross-references

- `decisions/` — individual design decisions (rationale, trade-offs, status)
- `security.md` — detailed security model (to be created)
- `../functional/` — tool and script reference documentation (to be created)
