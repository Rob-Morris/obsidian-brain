# Obsidian Brain — Tooling

Technical design for tooling that operates on Brain vaults.

## Compiled Router (DD-008, DD-013, DD-014, DD-016)

The compiled router is the interface contract between human-readable config and all tooling. Source files (`router.md`, taxonomy, skills, styles, VERSION) are the single source of truth. The compiler combines them into `_Config/.compiled-router.json` — a local, gitignored, hash-invalidated cache.

**Key properties:**
- **Filesystem-first discovery** (DD-016) — artefact types are discovered by scanning vault folders, not by reading a registry. Root-level non-system folders → living types. `_Temporal/` subfolders → temporal types. System folder convention: any folder starting with `_` or `.` is infrastructure. `_Temporal/` follows this convention (excluded from living type scan) but gets its own dedicated temporal scan for its children.
- **Hash invalidation** — SHA-256 of every source file stored in `meta.sources`. Stale the moment any source changes.
- **Environment-specific** — includes platform, runtime availability, absolute vault root path. Local-only, never committed. Artefact paths are relative to vault root.
- **Auto-compile on MCP startup** (DD-014) — the MCP server compiles if missing or stale before serving requests. All tools require the compiled router and auto-compile if needed (DD-013).

**Schema:** Implemented in v0.5.0. The output structure is implementation-defined — see `src/brain-core/scripts/compile_router.py` for the canonical reference. Top-level keys: `meta`, `environment`, `always_rules`, `artefacts`, `triggers`, `skills`, `plugins`, `styles`.

**Status and archiving extensions (v0.9.9):** The compiler extracts two additional fields per artefact type from taxonomy files, consumed by `check.py` (DD-009):
- `artefacts[].frontmatter.status_enum` — valid status values, parsed from three patterns: inline YAML comment (`status: default  # val1 | val2 | val3`), markdown lifecycle table (`| \`value\` | meaning |`), or prose line (`Status values: \`val1\`, \`val2\`.`). `null` when the type has no status field.
- `artefacts[].frontmatter.terminal_statuses` — subset of `status_enum` that trigger archiving (e.g. `implemented`, `graduated`), parsed from the taxonomy's `## Archiving` section. Uses direct references (`status: value`, `` `value` status ``) and cross-references capitalised enum values against the archiving text. `null` when the type doesn't support archiving.

**CLI:** `python3 compile_router.py --json` outputs to stdout; default mode writes `_Config/.compiled-router.json` with a summary to stderr. Requires Python 3.8+ (stdlib only). On environments without Python (mobile, restricted shells), agents fall back to the lean router and wikilink traversal — see *Agent Reading Flow* in `specification.md`.

## Script Architecture (DD-023)

Scripts in `.brain-core/scripts/` are the **source of truth** for all vault operations. Each script exposes importable functions and a CLI entry point:

| Script | Purpose | CLI usage |
|---|---|---|
| `compile_router.py` | Compile router from source files | `python3 compile_router.py [--json]` |
| `compile_colours.py` | Generate folder colour CSS | (called by compile_router) |
| `build_index.py` | Build BM25 retrieval index | `python3 build_index.py [--json]` |
| `search_index.py` | BM25 keyword search | `python3 search_index.py "query" [--type T] [--json]` |
| `read.py` | Query compiled router resources | `python3 read.py RESOURCE [--name N]` |
| `create.py` | Create new artefact | `python3 create.py --type T --title "Title" [--body B] [--json]` |
| `edit.py` | Edit/append to artefact | `python3 edit.py edit\|append --path P --body B [--json]` |
| `rename.py` | Rename file + update wikilinks | `python3 rename.py "source" "dest" [--json]` |
| `check.py` | Structural compliance checks | `python3 check.py [--json] [--severity S]` |
| `shape_presentation.py` | Create presentation + launch preview | `python3 shape_presentation.py --source P --slug S` |
| `init.py` | MCP server registration | `python3 init.py [--user] [--project PATH]` |

**Why scripts hold all logic:** The MCP server is a thin wrapper that imports functions from scripts and holds the compiled router and search index in memory. Scripts are the single implementation — the server adds only MCP transport, in-memory caching, and Obsidian CLI delegation. This means:

- Agents without MCP use scripts directly — same logic, same results
- No logic duplication between MCP and CLI paths
- The server gets in-memory caching for free (router/index loaded at startup)
- Standalone scripts pay a cold-start cost reading JSON from disk
- New operations are implemented as scripts first, then exposed via MCP

**When adding new operations:** implement the logic as importable functions in a script, add a CLI entry point, then import into `server.py`. Never put operation logic directly in the server.

## Brain MCP Server (DD-010, DD-011, DD-020, DD-021, DD-025)

Long-running MCP server at `.brain-core/mcp/server.py`. Thin wrapper over scripts — exposes 5 MCP tools:

- **`brain_read`** — safe, no side effects, auto-approvable. Delegates to `read.py` resource handlers. Resources: `artefact`, `trigger`, `style`, `template`, `skill`, `plugin`, `environment`, `router`, `compliance`, `file`. Optional `name` filter. Environment resource enriched with `obsidian_cli_available` (server-only state). Compliance resource runs check.py checks; `name` parameter filters by severity (`error`/`warning`/`info`). File resource reads any artefact file by relative path; `name` = path from vault root, validated against compiled router (must belong to a configured type folder).
- **`brain_search`** — safe, no side effects, auto-approvable. Parameters: `query` (required), `type`, `tag`, `top_k`. CLI-first with BM25 fallback (DD-021). Response includes `source` field (`"obsidian_cli"` or `"bm25"`) and `results` array with path, title, type, score, snippet.
- **`brain_create`** — additive, safe to auto-approve. Creates a new vault artefact. Parameters: `type` (key or full type), `title`, optional `body` and `frontmatter` overrides. Resolves type from compiled router, reads template, generates filename from naming pattern, writes file with merged frontmatter. Returns `{path, type, title}`.
- **`brain_edit`** — single-file mutation. Parameters: `operation` (`"edit"` or `"append"`), `path`, `body`, optional `frontmatter` changes (edit only). Path validated against compiled router — wrong folder or naming rejected with helpful error. Returns `{path, operation}`.
- **`brain_action`** — vault-wide/destructive ops, gated by approval. Actions: `compile`, `build_index`, `rename`, `delete`, `convert`, `shape-presentation`. Optional `params` object. `rename` delegates to `rename.py`'s `rename_and_update_links()`, with Obsidian CLI override when available. `delete` removes a file and replaces wikilinks with strikethrough. `convert` changes artefact type, moves file, reconciles frontmatter, and updates wikilinks vault-wide. `shape-presentation` creates a Marp presentation artefact and launches live preview (`params: {source, slug}`).

DD-011 established the read/write safety split (2 tools). DD-020 adds `brain_search` as a third tool. DD-021 adds optional Obsidian CLI integration for search and rename. DD-025 splits mutations into three privilege tiers: `brain_create` (additive, safe to auto-approve), `brain_edit` (single-file), and `brain_action` (vault-wide/destructive).

**Recommended permission config:** `brain_read` and `brain_search` are safe to auto-approve always. `brain_create` is additive-only (creates files, never destroys) — safe to auto-approve for most workflows. `brain_edit` mutates a single validated file — approve-once or auto-approve depending on trust level. `brain_action` affects multiple files or system state — require explicit approval per call.

**Obsidian CLI** (DD-022): Internal dependency of the MCP server, not a separate agent-facing tier. The server delegates to the CLI for search and rename when available; agents interact only with MCP tools or scripts. When MCP is unavailable, scripts provide full functionality (read, search, rename, compile, check). The CLI is an optimisation layer, not a requirement.

**Startup:** Auto-compiles router and auto-builds index if stale (compares timestamps against source file mtimes). Both artefacts loaded into memory for the session lifetime. Probes Obsidian CLI availability and derives vault name from directory basename (overridable via `BRAIN_VAULT_NAME` env var).

**Response payloads:** All tools return JSON strings. `brain_read` returns the requested resource data (array or object). `brain_search` returns `{source, results}` where results is a ranked array of `{path, title, type, score, snippet}`. `brain_create` returns `{path, type, title}`. `brain_edit` returns `{path, operation}`. `brain_action` returns `{status, summary, compiled_at|built_at}` for compile/build_index, `{status, method, links_updated}` for rename, `{status, path, links_replaced}` for delete, or `{status, old_path, new_path, type, links_updated}` for convert.

**Dependencies:** Python >=3.10, `mcp` SDK. The server imports functions directly from scripts — never calls their `main()` (which may `sys.exit`). Optional: dsebastien/obsidian-cli-rest running on localhost:27124 (overridable via `OBSIDIAN_CLI_URL` env var).

**Version drift:** If `.brain-core/` is upgraded while the server is running (e.g. after a brain-core propagation), the server detects the version change on the next tool call and reloads all script modules in-process via `importlib.reload()`. No MCP client cooperation needed — the server self-heals. Read, search, create, and edit tools also auto-recompile the router when new taxonomy files appear mid-session.

**Status:** Implemented in v0.8.0. Script parity completed in v0.10.3 (read.py, rename.py). Privilege split in v0.11.0 — 5 tools with granular permissions (DD-025). Artefact CRUD now implemented: create, edit/append, delete, convert. File resource added in v0.11.2 (read artefact files by path). Module reload on version drift added in v0.11.8.

## Lean Router Format (DD-012, DD-017)

The router file read by naive agents (no MCP, no compiled router). Format:

```
Always read [[.brain-core/index]].

Always:
- {vault-specific constraints — optional}

Conditional:
- {condition} → [[{taxonomy or skill file}]]
```

**Always-rules** live in two places: system-level rules in `index.md`'s `Always:` section (version-bound, apply to all vaults), and vault-specific additions in the router's `Always:` section (optional). The compiler merges both into the compiled router's `always_rules` — system first, vault additions after.

**Conditional triggers** use a goto pattern: the router states WHEN (one-line condition + wikilink pointer); the target taxonomy or skill file states WHAT and HOW in a `## Trigger` section. Zero duplication — the taxonomy file is the single source of truth.

**User preferences** (ask clarifying questions, show plan, ask before delete) live in `Agents.md`, not the router.

## check.py (DD-009)

*Implemented in v0.9.11.* Router-driven vault compliance checker. Reads the compiled router, validates vault files against structural rules. check.py never parses taxonomy markdown — all per-type rules (naming patterns, required fields, status enums, terminal statuses) come from the compiled router. This means the compiler is the single point that must track vault evolution; check.py adapts automatically when the router is recompiled.

**Flags:** `--json` (structured output), `--actionable` (enriched fix context), `--severity <level>` (filter: `error`/`warning`/`info`), `--vault <path>` (check a specific vault instead of auto-detecting).

**Exit codes:** 0 = clean, 1 = warnings only, 2 = errors present.

**Check catalogue:**

| Check | Severity | Validates |
|---|---|---|
| `root_files` | error | No content files in vault root |
| `naming` | warning | Files match naming pattern from taxonomy |
| `frontmatter_type` | warning | `type` field matches folder-derived type |
| `frontmatter_required` | warning | Required frontmatter fields present |
| `month_folders` | warning | Temporal files in correct `yyyy-mm/` subfolder |
| `archive_metadata` | warning | Files in `_Archive/` have `archiveddate` field, `yyyymmdd-` filename prefix, and a terminal status from `frontmatter.terminal_statuses` |
| `status_values` | warning | Status field values match `frontmatter.status_enum` from compiled router |
| `unconfigured_type` | info | Folder has no taxonomy file |

**Constraints:** Python 3.8+ stdlib only, self-locating, stateless, idempotent, stdout-only.

**Relationship with `compliance_check.py`:** The vault-maintenance skill ships a separate `compliance_check.py` for **session hygiene** — quick checks like "did you log today? any transcripts? backups fresh?" It runs after each work block as a sanity check. `check.py` (this design) is **structural compliance** — "do all files have correct frontmatter? naming? month folders?" Deep scan, runs on demand or during maintenance. These are complementary tools, not competing ones.

## Taxonomy Discovery (DD-018, DD-019)

**Succinct readme pattern** (DD-019): `.brain-core/taxonomy/readme.md` is a lean discovery guide (~50 tokens) that explains the classification system and points agents to `_Config/Taxonomy/`. It does not enumerate types — the filesystem is the index.

**Key derivation convention** (DD-018): type key = lowercase folder name, spaces to hyphens. e.g. `Daily Notes` → `daily-notes` → `_Config/Taxonomy/{classification}/daily-notes.md`. No manual registry needed.

## init.py (DD-023)

Setup script at `.brain-core/scripts/init.py`. Configures Claude Code to use the brain MCP server. Self-contained (no `_common` imports), idempotent, supports three scopes:

- **Local** (default): `.mcp.json` in the vault root. For using Claude Code inside the vault.
- **User** (`--user`): `~/.claude.json` top-level `mcpServers`. Default brain for all projects.
- **Project** (`--project <dir>`): `.mcp.json` in the project folder. Per-project vault override.

Registration strategy: `claude mcp add-json` when CLI available, direct JSON file editing otherwise. Both produce equivalent config. Project scope also writes a CLAUDE.md bootstrap line.

**Dependencies:** Python 3.8+ stdlib only. Detects a Python with `mcp` package for the server config (vault `.venv` → current Python → PATH search).

## Core Skills (DD-024)

Skills in `.brain-core/skills/*/SKILL.md` — system-provided, versioned with brain-core, not user-editable. Discovered by the compiler alongside user skills from `_Config/Skills/`. Tagged `"source": "core"` in the compiled router (user skills tagged `"source": "user"`).

Core skills teach agents how to use brain-core's own tools. They ship in `.brain-core/` and are overwritten on upgrade, which is correct — they describe system methodology, not user configuration.

**Current core skills:**
- `brain-remote` — workflow for using brain MCP tools from an external project folder

## Retrieval Index (Phase 1 — BM25)

BM25 keyword search over all vault markdown files. Built offline like the compiled router, queried at search time from a pre-built index.

**Key properties:**
- **Zero dependencies** — hand-rolled BM25, Python 3.8+ stdlib only, matching `compile_router.py` constraints
- **Same folder discovery** — reuses `scan_living_types()` / `scan_temporal_types()` patterns, then recurses each type folder for `.md` files
- **Whole-document indexing** — each note is one entry (sufficient at vault scale)
- **Build/search split** — `build_index.py` builds the index, `search_index.py` queries it. Separate scripts, no cross-imports.

**BM25 parameters:** `k1=1.5`, `b=0.75`. IDF: `log((N - df + 0.5) / (df + 0.5) + 1)`. Score: `Σ IDF(t) * (tf(t,d) * (k1+1)) / (tf(t,d) + k1 * (1 - b + b * dl/avgdl))`. **Title boosting** (v0.11.3): terms appearing in the document title receive an additional `IDF(t) * 3.0` score contribution, stored as `title_tf` per document. Backward compatible — older indexes without `title_tf` still work.

**Output:** `_Config/.retrieval-index.json` — local, gitignored. Contains corpus stats (document frequencies, average document length), per-document term frequencies, and metadata (path, title, type, tags, status, modified).

**CLI:**
```bash
python3 build_index.py           # write _Config/.retrieval-index.json
python3 build_index.py --json    # output JSON to stdout
python3 search_index.py "query"  # search and print ranked results
python3 search_index.py "query" --type living/design --tag brain-core --top-k 5
python3 search_index.py "query" --json  # structured output
```

**Tokeniser:** lowercase, split on non-alphanumeric, strip tokens < 2 chars.

**Snippets:** ~200 chars centred on first query term match in body, expanded to nearest word boundary. Falls back to first 200 chars if no match.

**Build trigger:**

- **MCP server** (implemented v0.7.0) — rebuilds on startup if stale (compares index timestamp against file mtimes), exposes `build_index` as a `brain_action` for mid-session refresh. Same pattern as compiled router auto-compile (DD-014). Phase 4 incremental updates make this cheap.
- **Obsidian plugin** (planned) — watch for `.md` file changes and trigger rebuild so the index stays fresh for non-agent use cases (in-Obsidian search UI, etc.). Vault files can be modified by agents, by Obsidian, or directly via the filesystem — all three paths need to result in a fresh index. The plugin would either shell out to Python or use a TypeScript reimplementation (DD-007 anticipates dual implementations).
- **Manual** — `python3 build_index.py` from the vault root.

## Pending Design

The following are accepted but not yet fully shaped:

- **upgrade.py** — in-place upgrade flow, migration steps
- **CLI wrapper** — argument parsing, vault discovery, distribution
- **Plugin registry** — `plugins.json` schema, install flow
- **Obsidian plugin** — TypeScript implementation, shared test fixtures (DD-005, DD-006, DD-007)
- **Frontmatter timestamps absorption** (DD-004) — ignore rules, agent-aware stamping
- **Procedures directory** — `.brain-core/procedures/`, structured step-by-step instructions for agents without code execution

## Development

### Prerequisites

- Python 3.10+ (scripts target 3.8+ stdlib for portability, but `mcp` SDK and type syntax require >=3.10)
- `make` (standard on macOS/Linux)

### Setup

```bash
make install    # creates .venv with Python 3.12, installs mcp + pytest
make test       # runs the full test suite
make clean      # removes .venv and caches
```

Or manually:

```bash
python3.12 -m venv .venv
.venv/bin/pip install "mcp>=1.0.0" "pytest>=9.0"
.venv/bin/pytest -q
```

### Test configuration

`pyproject.toml` configures pytest with `pythonpath` entries for `src/brain-core/scripts` and `src/brain-core/mcp`, so test files can `import check`, `import server`, etc. without `sys.path` manipulation.

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
| DD-008 | Compiled router as foundation | Implemented (v0.5.0) |
| DD-009 | Router-driven checks (no separate check config) | Implemented (v0.9.11) |
| DD-010 | Brain MCP server in `.brain-core/mcp/` | Implemented (v0.7.0) |
| DD-011 | MCP server exposes 2 tools with enum parameters | Superseded by DD-025 (v0.11.0) |
| DD-012 | Lean router — always-rules only, conditional triggers co-located | Accepted |
| DD-013 | Compiled router required for tools; markdown fallback for agents only | Accepted |
| DD-014 | MCP server auto-compiles on startup | Implemented (v0.7.0) |
| DD-015 | Single-line install — never require changes to Agents.md | Implemented (v0.4.0) |
| DD-016 | Filesystem-first artefact discovery | Implemented (v0.5.0) |
| DD-017 | Shorthand trigger index with gotos | Implemented (v0.4.0) |
| DD-018 | Taxonomy index dropped — filesystem is the index | Implemented (v0.4.0) |
| DD-019 | Succinct readme pattern for lean discovery guides | Implemented (v0.4.0) |
| DD-020 | 3 MCP tools: brain_read + brain_search + brain_action | Superseded by DD-025 (v0.11.0) |
| DD-021 | Optional Obsidian CLI integration — CLI-preferred, agent-fallback | Implemented (v0.8.0) |
| DD-022 | Obsidian CLI is internal to MCP; agents use CLI directly only when MCP unavailable | Accepted |
| DD-023 | init.py setup script | Implemented (v0.10.0) |
| DD-024 | Core skills in .brain-core/skills/ | Implemented (v0.10.0) |
| DD-025 | 5 MCP tools: privilege split for granular permissions | Implemented (v0.11.0) |
