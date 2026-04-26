# Architecture Overview

## System Overview

brain-core is a self-extending system for organising Obsidian vaults, for agents and humans working together. It ships a versioned engine (`.brain-core/`) into each vault that provides MCP tools, CLI scripts, and a taxonomy-driven configuration layer. Together these give agents and human operators a shared, structured interface to vault content: creating and editing artefacts, searching by keyword, enforcing naming and status conventions, and bootstrapping agents with the minimum context needed to operate correctly.

---

## Component Map

### `.brain-core/` — the engine

Copied into the vault during setup and upgrade (not symlinked — vaults are self-contained and portable). `init.py` then binds the vault into native client config surfaces: Claude project/local/user config and Codex project/user config. `repair.py` is the explicit current-vault recovery entry point and bootstraps packageful repair back into the vault-local managed runtime when needed. Contains:

- `scripts/` — all vault operation logic as importable Python modules with CLI entry points
- `brain_mcp/server.py` + `brain_mcp/_server_*.py` — MCP composition root and sibling tool handlers; holds router and index in memory
- `skills/` — core skill documents (system-provided, tagged `"source": "core"`, overwritten on upgrade)
- `index.md` — thin bootstrap entry point; routes agents to `brain_session`, `.brain/local/session.md`, or `md-bootstrap.md`
- `session-core.md` — checked-in authored source for the static core bootstrap content and core-doc references
- `md-bootstrap.md` — explicit degraded fallback for environments without MCP or a generated session mirror

### `.brain/` — vault-local runtime state

Generated, gitignored. The compiled outputs that tooling reads at runtime:

| Path | Contents |
|---|---|
| `.brain/local/compiled-router.json` | Compiled router — the interface contract between config and tooling |
| `.brain/local/session.md` | Generated markdown mirror of the canonical session model |
| `.brain/local/retrieval-index.json` | BM25 retrieval index for keyword search |
| `.brain/local/init-state.json` | Recorded MCP registrations owned by this vault for safe scoped removal |
| `.brain/config.yaml` | Vault-level configuration (layer 2 of 3) |
| `.brain/local/config.yaml` | Machine-local overrides (layer 3 of 3; gitignored) |
| `.brain/local/workspaces.json` | Workspace key-to-path registry |
| `.brain/local/mcp-server.log` | Rotating server log (2 MB max, 1 backup) with explicit startup phase markers |

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

Artefacts whose status reaches a terminal value (e.g. `implemented`, `adopted`) are moved here by type and owner-derived subfolder. Read-accessible but write-protected by the path security model.

---

## Data Flow

A typical MCP tool call follows this path:

```
MCP client request
  → brain_mcp/server.py — traces, gates, and delegates to the matching MCP handler
  → sibling MCP handler module — maps the tool to the relevant script call
  → script reads compiled router / retrieval index from in-memory state
  → operates on the vault filesystem (read, write, rename, etc.)
  → returns a structured response to the MCP client
```

The server loads the compiled router and retrieval index at startup and holds both in memory for the session lifetime. Startup now emits stable begin/success/failure markers for each major phase into `.brain/local/mcp-server.log`, which makes a stalled config load, router compile, index rebuild, embeddings load, registry load, or session-mirror refresh diagnosable without extra instrumentation. The `session-mirror refresh` phase is an enqueue onto a single long-lived daemon worker (see dd-036), so startup never blocks on that write; router and index phases are still synchronous and fail-loud on timeout because stale router/index data would silently break every subsequent call. Scripts called via MCP pay no disk I/O for router or index reads. Scripts called directly (without MCP) read the same JSON files from disk on each invocation — same logic, higher cold-start cost.

Mid-session, if `.brain-core/` is upgraded the server detects version drift on the next tool call and exits cleanly with code `10`. The MCP proxy interprets that as a planned restart and relaunches the server with the new code.

---

## Key Architectural Properties

### Filesystem-first discovery

Artefact types are discovered by scanning vault folders, not by reading a registry. Root-level non-system folders become living types. `_Temporal/` subfolders become temporal types. The convention is: any top-level folder starting with `_` or `.` is infrastructure. `_Temporal/` follows this convention (excluded from the living-type scan) but receives its own dedicated scan for its children. This means adding a new artefact type requires only creating a folder and a taxonomy file — no registry update.

### Compiled router as contract

The compiled router (`.brain/local/compiled-router.json`) is the interface between human-readable config and all tooling. Source files — `session-core.md`, `router.md`, taxonomy files, skills, styles, memories, plugins, and `VERSION` — are the single source of truth. The compiler combines them into a hash-invalidated cache: SHA-256 of every source file is stored in `meta.sources`, and the cache is considered stale the moment any source changes. The router is environment-specific (includes platform, runtime availability, absolute vault root) and is never committed to version control. The MCP server auto-compiles it at startup and auto-recompiles mid-session when sources change or new resources appear; staleness is checked on a 5-second TTL via SHA-256 hashes for edits and a directory-mtime signature for additions/deletions, so the check itself stays cheap on stable vaults (DD-042).

### Scripts as single source of truth

The MCP server is a thin wrapper. All vault operation logic lives in `.brain-core/scripts/` as importable Python modules, each with a CLI entry point. The server imports functions from scripts and adds MCP transport, in-memory caching, process-local mutation serialization for mutating tool calls, and Obsidian CLI delegation. This means agents without MCP use the scripts directly and get identical results. New operations are always implemented as scripts first, then exposed via MCP — never the reverse.

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
- **`safe_write(path, content, bounds=...)` / `safe_write_via(path, writer, bounds=...)`** — shared atomic write primitives (temp file + `os.replace`) that call `resolve_and_check_bounds` before writing. Text/JSON writes go through `safe_write(...)`; callback-driven serializers such as embeddings persistence go through `safe_write_via(...)`.

---

## Agent Reading Flow

Agents bootstrap through one canonical session model with three operating modes:

1. **MCP bootstrap** — `brain_session` returns the canonical session model as compact JSON: static core bootstrap content, structured core-doc references with explicit MCP load instructions, always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, and config/profile metadata when known. It also refreshes `.brain/local/session.md` from the same model.
2. **Generated markdown bootstrap** — if MCP is unavailable, agents read `.brain-core/index.md`, which routes them to `.brain/local/session.md`. That file is regenerated by normal runtime entry points (`brain_session`, `session.py`, router compile/startup paths), so it stays in parity with the JSON model for shared content.
3. **Degraded raw-file fallback** — if there is no MCP and no generated session mirror, `.brain-core/index.md` routes agents to `.brain-core/md-bootstrap.md`, which points them at `_Config/router.md`, user preferences, gotchas, and raw vault navigation.

All modes begin with the `AGENTS.md` bootstrap directive, which points agents to `brain_session` first and `.brain-core/index.md` as the stable no-MCP entry point.

---

## Cross-references

- `bounded-contexts.md` — bounded context map, responsibilities, and import policy
- `decisions/` — individual design decisions (rationale, trade-offs, status)
- `security.md` — detailed security model
- `../functional/` — tool and script reference documentation
