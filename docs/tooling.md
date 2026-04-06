# Obsidian Brain — Tooling

Technical design for tooling that operates on Brain vaults.

## Compiled Router (DD-008, DD-013, DD-014, DD-016)

The compiled router is the interface contract between human-readable config and all tooling. Source files (`router.md`, taxonomy, skills, styles, VERSION) are the single source of truth. The compiler combines them into `.brain/local/compiled-router.json` — a local, gitignored, hash-invalidated cache.

**Key properties:**
- **Filesystem-first discovery** (DD-016) — artefact types are discovered by scanning vault folders, not by reading a registry. Root-level non-system folders → living types. `_Temporal/` subfolders → temporal types. System folder convention: any folder starting with `_` or `.` is infrastructure. `_Temporal/` follows this convention (excluded from living type scan) but gets its own dedicated temporal scan for its children.
- **Hash invalidation** — SHA-256 of every source file stored in `meta.sources`. Stale the moment any source changes.
- **Environment-specific** — includes platform, runtime availability, absolute vault root path. Local-only, never committed. Artefact paths are relative to vault root.
- **Auto-compile on MCP startup** (DD-014) — the MCP server compiles if missing or stale before serving requests. All tools require the compiled router and auto-compile if needed (DD-013).

**Schema:** Implemented in v0.5.0. The output structure is implementation-defined — see `src/brain-core/scripts/compile_router.py` for the canonical reference. Top-level keys: `meta`, `environment`, `always_rules`, `artefacts`, `triggers`, `skills`, `plugins`, `styles`.

**Status and archiving extensions (v0.9.9):** The compiler extracts two additional fields per artefact type from taxonomy files, consumed by `check.py` (DD-009):
- `artefacts[].frontmatter.status_enum` — valid status values, parsed from three patterns: inline YAML comment (`status: default  # val1 | val2 | val3`), markdown lifecycle table (`| \`value\` | meaning |`), or prose line (`Status values: \`val1\`, \`val2\`.`). `null` when the type has no status field.
- `artefacts[].frontmatter.terminal_statuses` — subset of `status_enum` that trigger a `+Status/` folder move or archiving (e.g. `implemented`, `adopted`), parsed from the taxonomy's `## Terminal Status` or `## Archiving` section. Uses direct references (`status: value`, `` `value` status ``) and cross-references capitalised enum values against the section text. `null` when the type has no terminal statuses. Used by `brain_edit` to auto-move artefacts into `+Status/` folders on terminal status change (v0.20.0).

**CLI:** `python3 compile_router.py --json` outputs to stdout; default mode writes `.brain/local/compiled-router.json` with a summary to stderr. Requires Python 3.8+ (stdlib only). On environments without Python (mobile, restricted shells), agents fall back to the lean router and wikilink traversal — see *Agent Reading Flow* in `specification.md`.

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
| `edit.py` | Edit/append to artefact | `python3 edit.py edit\|append --path P --body B [--target H] [--json]` |
| `rename.py` | Rename/delete file + update wikilinks (full-path and filename-only) | `python3 rename.py "source" "dest" [--json]` |
| `check.py` | Structural compliance checks | `python3 check.py [--json] [--severity S]` |
| `shape_presentation.py` | Create presentation + launch preview | `python3 shape_presentation.py --source P --slug S` |
| `upgrade.py` | In-place brain-core upgrade | `python3 upgrade.py --source P [--vault V] [--dry-run] [--force] [--json]` |
| `workspace_registry.py` | Workspace slug→path resolution | `python3 workspace_registry.py [--register SLUG PATH] [--unregister SLUG] [--resolve SLUG] [--json]` |
| `migrate_naming.py` | Migrate filenames to generous conventions | `python3 migrate_naming.py [--vault V] [--dry-run] [--json]` |
| `fix_links.py` | Auto-repair broken wikilinks | `python3 fix_links.py [--fix] [--json] [--vault V]` |
| `sync_definitions.py` | Sync artefact library definitions to vault _Config/ | `python3 sync_definitions.py [--vault V] [--dry-run] [--types t1,t2] [--json]` |
| `config.py` | Vault configuration loader (three-layer merge) | `python3 config.py` |
| `process.py` | Content classification, duplicate resolution, ingestion | (library module, used by MCP server) |
| `init.py` | MCP server registration | `python3 init.py [--user] [--local] [--project PATH]` |

**Why scripts hold all logic:** The MCP server is a thin wrapper that imports functions from scripts and holds the compiled router and search index in memory. Scripts are the single implementation — the server adds only MCP transport, in-memory caching, and Obsidian CLI delegation. This means:

- Agents without MCP use scripts directly — same logic, same results
- No logic duplication between MCP and CLI paths
- The server gets in-memory caching for free (router/index loaded at startup)
- Standalone scripts pay a cold-start cost reading JSON from disk
- New operations are implemented as scripts first, then exposed via MCP

**When adding new operations:** implement the logic as importable functions in a script, add a CLI entry point, then import into `server.py`. Never put operation logic directly in the server.

## Brain MCP Server (DD-010, DD-011, DD-020, DD-021, DD-025, DD-027)

Long-running MCP server at `.brain-core/mcp/server.py`. Thin wrapper over scripts — exposes 8 MCP tools:

- **`brain_session`** — agent bootstrap, safe, auto-approvable. Compiles a token-efficient session payload in one call: always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, memory/skill/plugin/style indexes, config metadata (profiles, brain_name). Server actively compiles this — strips frontmatter from user files, condenses artefact metadata, merges environment state. Optional `context` parameter for scoped sessions (forward-compatible, not yet implemented). Optional `operator_key` parameter for operator authentication — matches key hash against registered operators in config, sets session profile for per-call enforcement. If omitted, uses the default profile from config. Delegates to `session.py`.
- **`brain_read`** — safe, no side effects, auto-approvable. Reads a *specific* resource by name. Delegates to `read.py` resource handlers. Resources: `type`, `trigger`, `style`, `template`, `skill`, `plugin`, `memory`, `workspace`, `environment`, `router`, `compliance`, `artefact`, `file`, `archive`. The `name` parameter is required for all collection resources (type, trigger, style, skill, plugin, memory, workspace, archive) — calling without name returns an error directing to `brain_list(resource=...)` (v0.22.1). Singletons (environment, router, compliance) and aliases (template, file) work as before. Normal artefact/file resources reject archive paths with a helpful error. Environment resource enriched with `obsidian_cli_available` (server-only state). Compliance resource runs check.py checks; `name` parameter filters by severity (`error`/`warning`/`info`). Artefact resource reads an artefact file by relative path or basename. Full relative paths (containing `/`) read directly without folder validation; bare basenames resolve via wikilink-style lookup (case-insensitive, `.md`-optional) and are validated against the compiled router (must belong to a configured type folder). For temporal artefacts, the display name alone also works — e.g. `name="Colour Theory"` resolves `20260404-research~Colour Theory.md` by stripping dated prefixes and matching the display-name portion after the tilde. If basename resolves to a `_Config/` path, the error suggests the correct dedicated resource (e.g. `memory`, `skill`). File resource is a smart resolver: resolves any vault file by name and delegates to the correct handler (artefact content, memory with trigger matching, skill with metadata, etc.) — use when you don't know the resource type. Workspace resource handled by server (not router state): resolves a specific slug to its data folder path.
- **`brain_search`** — safe, no side effects, auto-approvable. Parameters: `query` (required), `resource` (default `"artefact"`, also accepts `skill`, `trigger`, `style`, `memory`, `plugin`), `type`, `tag`, `status`, `top_k` (default 10). For artefacts: CLI-first with BM25 fallback (DD-021). For non-artefact resources: text matching on name + file content (v0.22.2). Artefact-specific filters (`type`, `tag`, `status`) only apply when `resource="artefact"`. Response includes `source` field (`"obsidian_cli"`, `"bm25"`, or `"text"`) and `results` array with path, title, type, score, snippet.
- **`brain_list`** — safe, no side effects, auto-approvable. Exhaustive enumeration — not relevance-ranked. Parameters: `resource` (default `"artefact"`, also accepts `skill`, `trigger`, `style`, `plugin`, `memory`, `template`, `type`, `workspace`, `archive`), `query` (optional text filter for non-artefact resources), `type`, `since`, `until` (ISO date strings), `tag`, `top_k` (default 500), `sort` (`"date_desc"` (default), `"date_asc"`, `"title"`). Artefact-specific filters (`type`, `since`, `until`, `tag`, `sort`) only apply when `resource="artefact"`. For artefacts: filters the in-memory BM25 index directly — no filesystem walk. For other resources: reads from the compiled router's small collections with optional `query` substring filtering. Use instead of `brain_search` when completeness matters (e.g. "all research from the last 2 weeks"). Use `resource` to list non-artefact collections — this replaces the previous brain_read listing behavior (v0.22.1).
- **`brain_create`** — additive, safe to auto-approve. Creates a new vault resource. Write-guarded: rejects paths targeting dot-prefixed folders (`.brain/`, `.obsidian/`, etc.) and protected underscore folders (`_Archive/`, `_Plugins/`, `_Workspaces/`, `_Assets/`); only `_Temporal/` and `_Config/` are writable (v0.22.0). Parameters: `resource` (default `"artefact"`, also accepts `skill`, `memory`, `style`, `template` — v0.22.4), `type` (key, full type, or singular form — e.g. `"ideas"`, `"living/ideas"`, or `"idea"`; required for artefacts), `title` (required for artefacts), `name` (required for non-artefact resources — slugified for filesystem paths; for templates, name is the artefact type key), optional `body` (required for non-artefact resources), optional `body_file` (absolute path to a file containing body content — must be inside the vault or system temp directory; temp files are deleted after reading, vault files are left in place; mutually exclusive with `body`; use for large content to keep MCP call displays compact), `frontmatter` overrides (for memories, use `{"triggers": ["keyword1", "keyword2"]}`), and `parent` (project subfolder name for living types — e.g. `"Brain"`; ignored for temporal types and non-artefact resources). For artefacts: resolves type from compiled router, reads template, generates filename from naming pattern, writes file with merged frontmatter; auto-injects `created` and `modified` ISO 8601 timestamps (respects overrides); auto-disambiguates basename collisions by appending `(type)`. For non-artefact resources: creates in the appropriate `_Config/` subfolder — skills at `_Config/Skills/{name}/SKILL.md`, memories at `_Config/Memories/{name}.md`, styles at `_Config/Styles/{name}.md`, templates at `_Config/Templates/{classification}/{Type}.md`. Returns confirmation message with path.
- **`brain_edit`** — single-file mutation. Write-guarded: same folder restrictions as `brain_create` (v0.22.0). Parameters: `resource` (default `"artefact"`, also accepts `skill`, `memory`, `style`, `template` — v0.22.7), `operation` (`"edit"`, `"append"`, `"prepend"`, or `"delete_section"`), `path` (relative path or basename — resolves like wikilinks; required when `resource="artefact"`), `name` (resource name for non-artefact resources; required when resource is skill, memory, style, or template; for templates, name is the artefact type key), `body` (omit for frontmatter-only changes; ignored for `delete_section`), optional `body_file` (absolute path to a file containing body content — must be inside the vault or system temp directory; temp files are deleted after reading, vault files are left in place; mutually exclusive with `body`), optional `frontmatter` changes (merge strategy depends on operation: edit overwrites fields; append/prepend extend list fields with dedup and overwrite scalars; set a field to null to delete it), optional `target` (heading, callout title, or `:body` for whole-body targeting — edit replaces that section's content; append inserts at the end of the section; prepend inserts before the section's heading line; delete_section removes the heading and all its content; include `#` markers to disambiguate duplicate headings, `[!type]` prefix for callouts e.g. `"[!note] Status"`; use `target=":body"` to explicitly target the entire body; the `:` prefix is a reserved namespace that avoids collision with markdown headings). `delete_section` requires `target`. For artefacts: path validated against compiled router — wrong folder or naming rejected with helpful error; auto-updates `modified` frontmatter field on every write; auto-sets `statusdate` (YYYY-MM-DD) whenever `status` actually changes (v0.21.6); terminal status auto-moves to `+Status/` subfolder with vault-wide wikilink updates, reverts on non-terminal (v0.20.0). For non-artefact resources: resolves via `_Config/` conventions using shared `config_resource_rel_path()`; no terminal status auto-move or `modified` injection. Returns `{path, resolved_path, operation}`. Targeted operations include surrounding heading context in the response for placement verification.
- **`brain_action`** — vault-wide/destructive ops, gated by approval. Actions: `compile`, `build_index`, `rename`, `delete`, `convert`, `shape-presentation`, `migrate_naming`, `register_workspace`, `unregister_workspace`, `fix-links`, `sync_definitions`, `archive`, `unarchive`. `archive` moves a terminal-status artefact to `_Archive/{Type}/{Project}/` with date-prefix rename, archiveddate, and vault-wide wikilink updates (`params: {path}`). `unarchive` restores an archived artefact to its original type folder, strips date prefix, removes archiveddate (`params: {path}`). Optional `params` object. `rename` delegates to `rename.py`'s `rename_and_update_links()`, with Obsidian CLI override when available. Wikilink updates match both full-path (`[[Wiki/topic-a]]`) and filename-only (`[[topic-a]]`) forms, including heading anchors (`[[file#heading]]`), block references (`[[file#^id]]`), embeds (`![[file]]`), and aliases — preserving the original format in replacements; filename-only matching is skipped when the basename is ambiguous (multiple files with the same name). `delete` removes a file and replaces wikilinks with strikethrough (same matching). `convert` changes artefact type, moves file, reconciles frontmatter, and updates wikilinks vault-wide (same matching). `shape-presentation` creates a Marp presentation artefact and launches live preview (`params: {source, slug}`). `register_workspace` registers a linked workspace in `.brain/local/workspaces.json` (`params: {slug, path}`). `unregister_workspace` removes a linked workspace registration (`params: {slug}`). `fix-links` scans for broken wikilinks and attempts auto-resolution using naming convention heuristics (slug→title, double-dash→tilde, temporal prefix matching); optional `params: {fix: true}` applies unambiguous fixes; returns JSON with fixed/ambiguous/unresolvable breakdown. `sync_definitions` syncs artefact library definitions to vault `_Config/` using three-way hash comparison (upstream vs installed vs local); optional `params: {dry_run, force, types, preference}`; returns updated/skipped/warnings lists. Warnings indicate conflicts or collisions; `force` overwrites despite them. The `preference` parameter overrides the file-based `artefact_sync` setting. Per-file exclusions via `defaults.exclude.artefact_sync` in `.brain/config.yaml` (migrated from `.brain/preferences.json` in v0.17.0). Chains after CLI upgrade, gated by the `artefact_sync` preference (`auto`/`ask`/`skip`).
- **`brain_process`** — content processing operations. Parameters: `operation` (required), `content` (required), optional `type`, `title`, `mode`. Operations: `classify` determines the best artefact type for content using three-tier fallback (embedding → BM25 → context_assembly); returns ranked type matches with confidence scores. `resolve` checks if content should create a new artefact or update an existing one (requires `type` and `title`); matches against generous filenames, legacy slugs, BM25 search, and optional embeddings; returns create/update/ambiguous decision. `ingest` runs the full pipeline: classify → infer title → resolve → create/update; optional `type`/`title` hints skip their respective steps. Mode parameter (for classify/ingest): `"auto"` (default), `"embedding"`, `"bm25_only"`, `"context_assembly"`. Index auto-refreshes after successful mutations.

DD-011 established the read/write safety split (2 tools). DD-020 adds `brain_search` as a third tool. DD-021 adds optional Obsidian CLI integration for search and rename. DD-025 splits mutations into three privilege tiers: `brain_create` (additive, safe to auto-approve), `brain_edit` (single-file), and `brain_action` (vault-wide/destructive). `brain_process` adds content classification and ingestion (can create/update files via `ingest`). `brain_list` adds exhaustive enumeration complementing `brain_search` for completeness-sensitive queries.

**Recommended permission config:** `brain_session`, `brain_read`, `brain_search`, and `brain_list` are safe to auto-approve always. `brain_create` is additive-only (creates files, never destroys) — safe to auto-approve for most workflows. `brain_edit` mutates a single validated file — approve-once or auto-approve depending on trust level. `brain_process` with `classify`/`resolve` is read-only; `ingest` can create/update files — treat like `brain_create`/`brain_edit` combined. `brain_action` affects multiple files or system state — require explicit approval per call.

**Obsidian CLI** (DD-022): Internal dependency of the MCP server, not a separate agent-facing tier. The server delegates to the CLI for search and rename when available; agents interact only with MCP tools or scripts. When MCP is unavailable, scripts provide full functionality (read, search, rename, compile, check). The CLI is an optimisation layer, not a requirement.

**Startup:** Loads vault config via three-layer merge (template → `.brain/config.yaml` → `.brain/local/config.yaml`). Auto-compiles router and auto-builds index if stale (compares timestamps against source file mtimes). Both artefacts loaded into memory for the session lifetime. Mid-session, the server detects version drift and auto-reloads modules, recompiles the router, and marks the index for rebuild. Loads workspace registry from `.brain/local/workspaces.json` (empty dict if absent). Probes Obsidian CLI availability and derives vault name from config `brain_name`, then `BRAIN_VAULT_NAME` env var, then directory basename.

**Logging (v0.21.7):** The server writes persistent logs to `.brain/local/mcp-server.log` using Python's `RotatingFileHandler` (2 MB max, 1 backup). Log levels: startup diagnostics and tool call tracing at INFO, tool arguments at DEBUG, errors at ERROR, version drift warnings at WARN. Stderr receives WARN+ messages only (for MCP client visibility). The file handler level defaults to INFO; set `BRAIN_LOG_LEVEL=DEBUG` to include tool arguments in the log. The log file is gitignored (inside `.brain/local/`).

**Operator profiles:** The config system defines three built-in profiles (`reader`, `contributor`, `operator`) with per-tool allow-lists. `brain_session` authenticates operators via SHA-256 key hashing. All tools except `brain_session` enforce the active profile — denied calls return an error `CallToolResult`. No config loaded = no enforcement (backward compatible with existing vaults).

**Response payloads (DD-026):** Tools return either plain text or `list[TextContent]` multi-block responses for readability in MCP clients (see DD-026 for rationale). The FastMCP SDK natively supports both — `str` returns become a single `TextContent` block; `list[TextContent]` returns become multiple content blocks rendered separately.

Response format by tool:

- **`brain_session`** — single JSON string, no indentation (token efficiency). Agent-consumed bootstrap payload; readability not a priority.
- **`brain_read`** — resource-dependent. Artefact/file content returned as plain text (already optimal). Single-item resources (type, trigger, memory) returned as JSON. Complex resources (router, compliance) remain JSON where structure aids comprehension. Environment returned as formatted key=value pairs.
- **`brain_search`** — multi-block: bold past-tense metadata block (`**Searched:** N results (source)`) + results as a readable text list (one result per line: title, path, type, score).
- **`brain_list`** — multi-block: bold past-tense metadata block (`**Listed:** N results`) + results as a readable text list. For artefacts: date, title, path, type, status. For non-artefact resources: name per line.
- **`brain_create`** — plain text confirmation with bold past-tense action: `"**Created** {type}: {path}"` for artefacts, `"**Created** {resource}: {path}"` for non-artefact resources.
- **`brain_edit`** — plain text confirmation with bold past-tense action: `"**Edited:** {path}"`, `"**Appended:** {path}"`, `"**Prepended:** {path}"`, or `"**Deleted section from:** {path}"` (plus target section and surrounding heading context if a target was specified).
- **`brain_action`** — plain text status line with bold past-tense action for simple actions (e.g. `**Compiled:** N artefacts...`, `**Renamed** (method): ...`). JSON for complex responses (convert with link counts, migrate_naming with rename lists).
- **Errors** — all tools return `CallToolResult(isError=True)` with `"Error: {message}"` text content. The `isError` flag enables error-specific rendering in MCP clients.

**Backward compatibility:** Tests that do `json.loads()` on tool results will need updating to match the new plain-text format. The change is internal to the MCP layer — underlying script functions still return dicts/lists.

**Dependencies:** Python >=3.10, `mcp` SDK, `pyyaml` (config loader). The server imports functions directly from scripts — never calls their `main()` (which may `sys.exit`). Optional: dsebastien/obsidian-cli-rest running on localhost:27124 (overridable via `OBSIDIAN_CLI_URL` env var).

**Version drift:** If `.brain-core/` is upgraded while the server is running, the server detects the version change on the next tool call and exits via `os._exit(0)`. The MCP client restarts it with the new code. This replaced the previous `importlib.reload()` approach because `sys.exit()` inside an asyncio coroutine hangs the process. Read, search, create, and edit tools also auto-recompile the router when new taxonomy files appear mid-session.

**Status:** Implemented in v0.8.0. Script parity completed in v0.10.3 (read.py, rename.py). Privilege split in v0.11.0 — 5 tools with granular permissions (DD-025). Artefact CRUD now implemented: create, edit/append, delete, convert. File resource added in v0.11.2 (read artefact files by path). Module reload on version drift added in v0.11.8. In-place upgrade action added in v0.12.1 (upgrade.py). Workspace registry added in v0.12.2 (workspace_registry.py). Vault config system and operator profiles added in v0.17.0 (config.py, profile enforcement).

## MCP Response Readability (DD-026)

MCP tool results are displayed inline in agent UIs (Claude Code, Cursor, etc.). JSON blobs with escaped newlines and nested objects are hard to scan. Plain text renders cleanly.

**Problem:** When tools return `json.dumps({...})`, the MCP SDK wraps the string in a single `TextContent` block. The client renders it as a collapsed JSON blob — functional for the agent but unreadable for the human watching the session.

**Mechanism:** FastMCP's `_convert_to_content()` handles three return shapes:
1. `str` → single `TextContent` block (current behaviour — everything is this)
2. `list[TextContent]` → multiple content blocks rendered separately
3. `dict`/`list` (non-string) → auto-serialised to JSON with indent=2

Option 2 is the key lever. Returning `[TextContent(type="text", text=metadata), TextContent(type="text", text=body)]` produces two separate blocks that clients can render, collapse, or highlight independently.

**Design rules:**
- **Confirmations → plain text.** `brain_create`, `brain_edit`, simple `brain_action` results. One line, human-scannable. `"Created living/idea: Ideas/my-idea.md"`
- **Content retrieval → plain text.** `brain_read(resource="artefact")` already does this. Extend to list resources — one item per line, tab-separated key fields.
- **Structured data → JSON only when structure adds value.** Router dumps, compliance check arrays, upgrade file manifests. These are genuinely tabular/nested.
- **Errors → plain text.** `"Error: {message}"` — no JSON wrapper. The error key convention (`{"error": "..."}`) added a parse step with zero benefit.
- **Session → unchanged.** `brain_session` is agent-consumed, never human-read. Keep as compact JSON.
- **Multi-block for mixed responses.** When a tool returns both metadata and content (e.g. search results with source attribution), use `list[TextContent]` — metadata in one block, results in another.

**What this does NOT do:** It does not change the underlying script functions. Scripts still return dicts/lists. The MCP server layer formats them for readability. This is a presentation concern, not a logic change.

**Migration:** Update `server.py` handler return values + corresponding test assertions. No changes to scripts, CLI, or compiled router.

## MCP Tool Resilience Conventions (DD-027)

The MCP server is a long-running process serving multiple agents across unpredictable vault states. Tools must never crash — a traceback kills the server and orphans the agent session. These conventions exist to keep the server standing when vault data is corrupt, missing, or the wrong shape.

**Three-layer exception strategy:** Every tool handler follows the same structure:

1. **Preventive type guards** — before accessing dict keys, `isinstance()` checks confirm the loaded data is actually a dict (or list, etc.). Corrupted JSON caches can parse as valid JSON but produce the wrong type (e.g. a list instead of a dict). Guards go immediately after `json.load()` or any deserialization.
2. **Inner domain catches** — `try/except` blocks around specific operations, catching expected failure modes (`ValueError`, `KeyError`, `FileNotFoundError`, etc.) with actionable error messages via `_fmt_error()`.
3. **Outer catch-all** — every tool's top-level handler has a final `except Exception` that logs the full traceback to stderr and returns a generic `_fmt_error()`. This is the safety net — it should never be the primary error path, but it ensures the server survives unexpected failures.

All three layers are mandatory for every tool. Omitting the outer catch-all is a bug, even if you believe all exceptions are handled by inner catches.

**Literal schemas on enum-like parameters:** Every tool parameter that accepts a fixed set of values must use `Literal["a", "b", "c"]` type annotations, not bare `str`. This produces `{"enum": [...]}` in the JSON schema, so agents see valid values at tool-discovery time instead of guessing. When adding a new enum value, update the `Literal` type — the JSON schema is generated from it automatically.

**Error formatting:** All error returns use `_fmt_error(msg)` which produces `CallToolResult(isError=True)` with `"Error: {message}"` text content. Never raise exceptions as a way to signal errors to the agent — always return a `CallToolResult`. Never return raw dicts with `{"error": ...}` keys.

**Type guards after deserialization:** Any function that calls `json.load()`, `json.loads()`, `yaml.safe_load()`, or reads compiled router/index data must validate the shape before accessing it. The pattern:

```python
data = json.load(f)
if not isinstance(data, dict):
    return _fmt_error("Expected dict, got " + type(data).__name__)
```

This catches the case where a cache file contains valid JSON of the wrong type (e.g. after a partial write, encoding error, or manual edit). Without the guard, the next `.get()` or `[key]` raises `AttributeError` or `TypeError` — which is confusing to debug and, before DD-027, was not caught by inner domain catches.

**Status:** Codified in v0.18.7. All 8 tools (`brain_session`, `brain_read`, `brain_search`, `brain_list`, `brain_create`, `brain_edit`, `brain_action`, `brain_process`) conform.

## MCP Server Shutdown Lifecycle

The MCP server follows the [stdio lifecycle spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle). Three shutdown paths, all exit 0 with a descriptive stderr message:

1. **Stdin EOF** — client closes input pipe → `mcp.run()` returns → `brain-core shutdown: stdin closed`
2. **SIGTERM/SIGINT** — signal handler → `brain-core shutdown: received SIGTERM`
3. **Unexpected error** — caught, full traceback to stderr → exit 1 (the only path that indicates a real crash)

**"1 MCP server failed" in Claude Code** — This message appears in Claude Code during MCP server restarts (e.g. after an upgrade triggers a version-drift exit). It is a known Claude Code display bug: the server exits cleanly (exit 0 + stderr message per spec), but Claude Code surfaces a transient "failed" status before the client reconnects. The brain-core server behaviour is correct. Use `/mcp` to reconnect if auto-reconnect does not trigger.

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
| `archive_metadata` | warning | Files in `_Archive/` (top-level and legacy per-type archives) have `archiveddate` field, `yyyymmdd-` filename prefix, and a terminal status from `frontmatter.terminal_statuses` |
| `status_values` | warning | Status field values match `frontmatter.status_enum` from compiled router |
| `broken_wikilinks` | warning | Wikilink target file does not exist |
| `ambiguous_wikilinks` | info | Basename-only wikilink matches multiple files |
| `unconfigured_type` | info | Folder has no taxonomy file |

**Constraints:** Python 3.8+ stdlib only, self-locating, stateless, idempotent, stdout-only.

**Relationship with `compliance_check.py`:** The vault-maintenance skill ships a separate `compliance_check.py` for **session hygiene** — quick checks like "did you log today? any transcripts? backups fresh?" It runs after each work block as a sanity check. `check.py` (this design) is **structural compliance** — "do all files have correct frontmatter? naming? month folders?" Deep scan, runs on demand or during maintenance. These are complementary tools, not competing ones.

## Taxonomy Discovery (DD-018, DD-019)

**Succinct readme pattern** (DD-019): `.brain-core/taxonomy/readme.md` is a lean discovery guide (~50 tokens) that explains the classification system and points agents to `_Config/Taxonomy/`. It does not enumerate types — the filesystem is the index.

**Key derivation convention** (DD-018): type key = lowercase folder name, spaces to hyphens. e.g. `Daily Notes` → `daily-notes` → `_Config/Taxonomy/{classification}/daily-notes.md`. No manual registry needed. All MCP tools that accept a type parameter tolerate singular forms — `"report"` resolves to `"reports"`, `"idea"` to `"ideas"` — via normalised matching in `_common.match_artefact()`.

## install.sh

Top-level script for installing, upgrading, and uninstalling the brain. Handles four modes depending on what it finds at the target path.

### Usage

```bash
# Fresh install (downloads repo, creates vault, sets up MCP)
bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh)
bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh) ~/brain

# From a local clone (skips download)
bash install.sh ~/brain

# Upgrade an existing brain vault
bash install.sh ~/brain          # detects .brain-core/, offers upgrade

# Install into an existing Obsidian vault or directory
bash install.sh ~/my-vault       # detects non-empty dir, installs brain-core only

# Uninstall
bash install.sh --uninstall ~/brain

# Non-interactive (for scripts/agents)
bash install.sh --force ~/brain
bash install.sh --uninstall --force ~/brain
```

### Modes

| Mode | Trigger | What happens |
|---|---|---|
| **Fresh install** | Target is empty or doesn't exist | Copies template vault + brain-core, creates `.venv`, registers MCP server |
| **Upgrade** | Target contains `.brain-core/` | Shows installed vs source version, confirms, runs `upgrade.py`, syncs Python dependencies |
| **Existing vault** | Target is non-empty but has no `.brain-core/` | Installs brain-core + config scaffolding only — existing files are never overwritten |
| **Uninstall** | `--uninstall` flag | Removes brain system files; optionally deletes the entire vault |

### Flags

- `--force` / `-f` — skip all interactive prompts. On install/upgrade: accepts defaults, auto-registers MCP. On uninstall: skips the system-files confirmation only — vault deletion always requires interactive confirmation. Also bypasses the stdin pipe detection error.
- `--uninstall` — enter uninstall mode. Must be the first argument.
- **Path** (positional, optional) — target directory. Defaults to current directory (prompted interactively, or `pwd` with `--force`).

### Requirements

- **git** — required (for cloning the repo when not running from a local clone)
- **python3** — required (any version, for basic preflight)
- **Python 3.10+** — recommended. The script searches for `python3.13` down to `python3.10`, then falls back to `python3`. If no 3.10+ is found, the vault is still created but `.venv` and MCP server setup are skipped. The script prints guidance for installing Python later and running `init.py` manually.

### Safety guards

The script refuses dangerous target paths:

- System directories: `/`, `/usr`, `/usr/*`, `/bin`, `/sbin`, `/etc`, `/var`, `/tmp`, `/System`, `/Library`
- Home directory directly (`$HOME`) — must be a subdirectory
- The source repo itself (when running from a clone)
- Any path whose parent directory doesn't exist

**Stdin pipe detection:** running `curl ... | bash` breaks interactive prompts. The script detects this and exits with a message showing the correct `bash <(curl ...)` invocation. Pass `--force` to skip all prompts and bypass the check.

### Existing vault behaviour

When the target is a non-empty directory without `.brain-core/`, the script installs brain-core alongside existing files:

- **Always installed:** `.brain-core/` (the brain engine)
- **Created if missing:** `_Config/`, `_Assets/`, `_Temporal/`, `_Plugins/`, `_Workspaces/`, `.backups/`
- **Copied if absent:** `Agents.md`, `CLAUDE.md`
- **Always overwritten:** `.obsidian/snippets/brain-folder-colours.css` (namespaced, safe — this file is ours)

Existing files and directories are never touched. The script detects `.obsidian/` and adjusts its messaging accordingly ("Existing Obsidian vault detected" vs "Existing directory detected").

### Uninstall

Three-stage confirmation protects against accidental data loss:

1. **System files** — confirms removal of `.brain-core/`, `.brain/`, `.venv/`, `.mcp.json`, `CLAUDE.md`. Your notes and vault structure are untouched. With `--force`, this stage is skipped (auto-confirmed).
2. **Full vault deletion** — optionally offers to delete the entire directory. Counts and displays the number of artefacts that would be lost.
3. **Final confirmation** — requires typing `"farewell, cruel world"` to proceed with full deletion. This stage is always interactive, even with `--force`.

After uninstall, the script reminds you to clean up global MCP registration if applicable: `claude mcp remove brain --scope user`.

## init.py (DD-023)

Setup script at `.brain-core/scripts/init.py`. Configures Claude Code to use the brain MCP server. Self-contained (no `_common` imports), idempotent, supports four scopes:

- **Project** (default): `.mcp.json` + `CLAUDE.md` bootstrap in the current directory (or `--project <dir>`). For per-project vault binding.
- **Local** (`--local`): `.claude/settings.local.json` + `.claude/CLAUDE.local.md` in the target directory. Gitignored — for personal use without committing config.
- **User** (`--user`): `~/.claude.json` top-level `mcpServers`. Default brain for all projects.

When multiple scopes are configured, project takes priority over local, which takes priority over user. The script warns if a global registration already exists when installing at project or local scope.

Optional: `--vault <path>` overrides vault root auto-detection (used by `install.sh` when the script isn't inside the vault).

Registration strategy: `claude mcp add-json` when CLI available, direct JSON file editing otherwise. Both produce equivalent config. All writes are atomic (tmp + fsync + rename).

**Dependencies:** Python 3.8+ stdlib only. Detects a Python with `mcp` package for the server config (vault `.venv` → current Python → PATH search).

## upgrade.py

CLI-only upgrade script at `src/brain-core/scripts/upgrade.py` (runs from the repo, not shipped to vaults). Copies a source brain-core directory into a vault's `.brain-core/`, removing obsolete files, with version awareness. Self-contained (no `_common` imports) because it replaces `_common.py` during execution.

**Design decisions:**
- Source path is required (`--source`), not auto-detected — explicit is safer for an operation that overwrites system files
- **Backup + compile validation + rollback** — before modifying anything, `.brain-core/` is backed up to `/tmp/`. After copying files, `compile_router.py` runs as a validation step. If copy or compile fails, the backup is restored and the error is reported. Migrations only run after compile succeeds. Result is logged to `.brain/local/last-upgrade.json` for diagnostics
- **Dependency management** — MCP server dependencies are declared in `.brain-core/mcp/requirements.txt`. When this file changes during an upgrade, the CLI prints a reminder to run `pip install -r`. `install.sh` handles this automatically for both fresh installs and upgrades. For manual upgrades, re-run: `.venv/bin/pip install -r .brain-core/mcp/requirements.txt`
- **Not exposed via MCP** — self-upgrading MCP servers are an anti-pattern (a prompt-injected agent could point upgrade at a crafted directory). The MCP server detects version drift and exits cleanly; the client restarts it with the new code
- **Post-upgrade definition sync** — after a successful upgrade, `sync_definitions` runs automatically if `artefact_sync` is `"auto"` in `.brain/preferences.json`. If `"ask"` (default), a dry-run preview is included in the result so the caller can prompt the user. If `"skip"`, no sync runs. CLI flags `--sync` / `--no-sync` override the preference. Sync failures are captured in the result — they never crash the upgrade

**CLI:**
```bash
python3 upgrade.py --source /path/to/src/brain-core              # upgrade
python3 upgrade.py --source src/brain-core --vault /path --dry-run  # preview
python3 upgrade.py --source src/brain-core --force                  # re-apply or downgrade
python3 upgrade.py --source src/brain-core --json                   # structured output
python3 upgrade.py --source src/brain-core --sync      # upgrade + sync definitions
python3 upgrade.py --source src/brain-core --no-sync    # upgrade without sync
```

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

**Output:** `.brain/local/retrieval-index.json` — local, gitignored. Contains corpus stats (document frequencies, average document length), per-document term frequencies, and metadata (path, title, type, tags, status, modified).

**CLI:**
```bash
python3 build_index.py           # write .brain/local/retrieval-index.json
python3 build_index.py --json    # output JSON to stdout
python3 search_index.py "query"  # search and print ranked results
python3 search_index.py "query" --type living/design --tag brain-core --top-k 5
python3 search_index.py "query" --json  # structured output
```

**Tokeniser:** lowercase, split on non-alphanumeric, strip tokens < 2 chars.

**Snippets:** ~200 chars centred on first query term match in body, expanded to nearest word boundary. Falls back to first 200 chars if no match.

**Build trigger:**

- **MCP server** (implemented v0.7.0) — rebuilds on startup if stale (compares index timestamp against file mtimes), exposes `build_index` as a `brain_action` for mid-session refresh. Same pattern as compiled router auto-compile (DD-014). Incremental updates (v0.15.10): `brain_create`/`brain_edit` queue single-file upserts via `index_update()` instead of triggering full rebuilds; destructive actions (rename/delete/convert) set a dirty flag for full rebuild on next search. Filesystem staleness checks for both router and index are throttled by a 5-second TTL to reduce per-call I/O.
- **Obsidian plugin** (planned) — watch for `.md` file changes and trigger rebuild so the index stays fresh for non-agent use cases (in-Obsidian search UI, etc.). Vault files can be modified by agents, by Obsidian, or directly via the filesystem — all three paths need to result in a fresh index. The plugin would either shell out to Python or use a TypeScript reimplementation (DD-007 anticipates dual implementations).
- **Manual** — `python3 build_index.py` from the vault root.

## Pending Design

The following are accepted but not yet fully shaped:

- **upgrade.py** — migration registry for breaking changes (upgrade copy implemented in v0.12.1; migration steps deferred until concrete need)
- **CLI wrapper** — argument parsing, vault discovery, distribution
- **Plugin registry** — `plugins.json` schema, install flow
- **Obsidian plugin** — TypeScript implementation, shared test fixtures (DD-005, DD-006, DD-007)
- **Frontmatter timestamps absorption** (DD-004) — ignore rules, agent-aware stamping
- **Procedures directory** — `.brain-core/procedures/`, structured step-by-step instructions for agents without code execution
- **Init wizard** — interactive setup that helps new users choose a starting set of artefact types. Includes a vault archetype library (e.g. "Personal Knowledge Base", "Writing Studio", "Software Project") — each archetype bundles a curated set of types as a starting point, with the option to customise after selection

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
| DD-026 | MCP response readability: plain text over JSON blobs | Implemented (v0.14.4, polished v0.14.5) |
