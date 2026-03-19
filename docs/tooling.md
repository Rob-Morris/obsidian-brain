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

**CLI:** `python3 compile_router.py --json` outputs to stdout; default mode writes `_Config/.compiled-router.json` with a summary to stderr. Requires Python 3.8+ (stdlib only). On environments without Python (mobile, restricted shells), agents fall back to the lean router and wikilink traversal — see *Agent Reading Flow* in `specification.md`.

## Brain MCP Server (DD-010, DD-011, DD-020, DD-021)

Long-running MCP server at `.brain-core/mcp/server.py`. Exposes 3 tools:

- **`brain_read`** — safe, no side effects, auto-approvable. Resources: `artefact`, `trigger`, `style`, `template`, `skill`, `plugin`, `environment`, `router`. Optional `name` filter. Environment resource includes `obsidian_cli_available`.
- **`brain_search`** — safe, no side effects, auto-approvable. Parameters: `query` (required), `type`, `tag`, `top_k`. CLI-first with BM25 fallback (DD-021). Response includes `source` field (`"obsidian_cli"` or `"bm25"`) and `results` array with path, title, type, score, snippet.
- **`brain_action`** — mutations, gated by approval. Actions: `compile`, `build_index`, `rename` (implemented); `check`, `create_artefact` (deferred — scripts don't exist yet). Optional `params` object. `rename` uses Obsidian CLI when available (wikilink-safe), falls back to grep-and-replace.

DD-011 established the read/write safety split (2 tools). DD-020 adds `brain_search` as a third tool — search has different parameter semantics (query + filters vs. resource lookup) and response shape. DD-021 adds optional Obsidian CLI integration for search and rename.

**CLI relationship** (DD-022): The Obsidian CLI is an **internal dependency** of the MCP server — not a separate agent-facing tier. The server delegates to the CLI for search and rename when available; agents interact only with MCP tools. When MCP is unavailable, agents can use the CLI directly (for search/rename) alongside `.brain-core/scripts/` (for compile/index) — these are complementary, not competing. Raw filesystem access is the last resort. Open question: several CLI capabilities (read, create, append, property management) are not yet exposed through MCP tools.

**Startup:** Auto-compiles router and auto-builds index if stale (compares timestamps against source file mtimes). Both artefacts loaded into memory for the session lifetime. Probes Obsidian CLI availability and derives vault name from directory basename (overridable via `BRAIN_VAULT_NAME` env var).

**Response payloads:** All tools return JSON strings. `brain_read` returns the requested resource data (array or object). `brain_search` returns `{source, results}` where results is a ranked array of `{path, title, type, score, snippet}`. `brain_action` returns `{status, summary, compiled_at|built_at}` or `{status, method, links_updated}` for rename.

**Dependencies:** Python >=3.10, `mcp` SDK. The server imports functions directly from the existing scripts — never calls their `main()` (which may `sys.exit`). Optional: dsebastien/obsidian-cli-rest running on localhost:27124 (overridable via `OBSIDIAN_CLI_URL` env var).

**Status:** Implemented in v0.8.0. Phase C actions (`check`, `create_artefact`) deferred — those scripts don't exist yet. `create_artefact` may be superseded by a broader `write` action from the zettelkasten maintenance design.

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

*Designed but not yet implemented.* Router-driven vault compliance checker. Reads the compiled router, validates vault files against structural rules.

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

## Retrieval Index (Phase 1 — BM25)

BM25 keyword search over all vault markdown files. Built offline like the compiled router, queried at search time from a pre-built index.

**Key properties:**
- **Zero dependencies** — hand-rolled BM25, Python 3.8+ stdlib only, matching `compile_router.py` constraints
- **Same folder discovery** — reuses `scan_living_types()` / `scan_temporal_types()` patterns, then recurses each type folder for `.md` files
- **Whole-document indexing** — each note is one entry (sufficient at vault scale)
- **Build/search split** — `build_index.py` builds the index, `search_index.py` queries it. Separate scripts, no cross-imports.

**BM25 parameters:** `k1=1.5`, `b=0.75`. IDF: `log((N - df + 0.5) / (df + 0.5) + 1)`. Score: `Σ IDF(t) * (tf(t,d) * (k1+1)) / (tf(t,d) + k1 * (1 - b + b * dl/avgdl))`.

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
| DD-009 | Router-driven checks (no separate check config) | Accepted |
| DD-010 | Brain MCP server in `.brain-core/mcp/` | Implemented (v0.7.0) |
| DD-011 | MCP server exposes 2 tools with enum parameters | Implemented (v0.7.0) |
| DD-012 | Lean router — always-rules only, conditional triggers co-located | Accepted |
| DD-013 | Compiled router required for tools; markdown fallback for agents only | Accepted |
| DD-014 | MCP server auto-compiles on startup | Implemented (v0.7.0) |
| DD-015 | Single-line install — never require changes to Agents.md | Implemented (v0.4.0) |
| DD-016 | Filesystem-first artefact discovery | Implemented (v0.5.0) |
| DD-017 | Shorthand trigger index with gotos | Implemented (v0.4.0) |
| DD-018 | Taxonomy index dropped — filesystem is the index | Implemented (v0.4.0) |
| DD-019 | Succinct readme pattern for lean discovery guides | Implemented (v0.4.0) |
| DD-020 | 3 MCP tools: brain_read + brain_search + brain_action | Implemented (v0.7.0) |
| DD-021 | Optional Obsidian CLI integration — CLI-preferred, agent-fallback | Implemented (v0.8.0) |
| DD-022 | Obsidian CLI is internal to MCP; agents use CLI directly only when MCP unavailable | Accepted |
