# MCP Tools

MCP tool specifications for the brain server.

## Tool Overview

| Tool | Safety Level | Purpose |
|---|---|---|
| `brain_session` | Safe — auto-approvable | Agent bootstrap: session payload, authentication, profile resolution |
| `brain_read` | Safe — auto-approvable | Read a specific vault resource by name |
| `brain_search` | Safe — auto-approvable | Relevance-ranked search over artefacts and config resources |
| `brain_list` | Safe — auto-approvable | Exhaustive enumeration of artefacts or config collections |
| `brain_create` | Additive — safe to auto-approve | Create a new vault artefact or config resource |
| `brain_edit` | Single-file mutation | Edit, append, prepend, or delete a section from one file |
| `brain_action` | Vault-wide / destructive — require explicit approval | Compile, rename, delete, convert, archive, and other multi-file ops |
| `brain_process` | Classify/resolve: read-only; ingest: creates/updates files | Content classification, duplicate resolution, ingestion |

## Tool Specifications

### brain_session

Agent bootstrap tool — safe, auto-approvable. Compiles a token-efficient session payload in one call: always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, memory/skill/plugin/style indexes, config metadata (profiles, brain_name). The server actively compiles this — strips frontmatter from user files, condenses artefact metadata, merges environment state.

**Parameters:**
- `context` (optional) — scoped session hint (forward-compatible, not yet implemented)
- `operator_key` (optional) — SHA-256 key for operator authentication; matches against registered operators in config and sets the session profile for per-call enforcement. If omitted, uses the default profile from config.

**Response format:** Single JSON string, no indentation (token efficiency). Agent-consumed bootstrap payload; readability not a priority.

Delegates to `session.py`.

---

### brain_read

Safe, no side effects, auto-approvable. Reads a specific resource by name. Delegates to `read.py` resource handlers.

**Parameters:**
- `resource` (required) — one of: `type`, `trigger`, `style`, `template`, `skill`, `plugin`, `memory`, `workspace`, `environment`, `router`, `compliance`, `artefact`, `file`, `archive`
- `name` (required for collection resources) — calling without name on collection resources returns an error directing to `brain_list(resource=...)`

**Resource behaviours:**
- **Singletons** (`environment`, `router`, `compliance`) — no `name` required
- **Aliases** (`template`, `file`) — work as before; `file` is a smart resolver that delegates to the correct handler
- **`artefact`** — reads by relative path or basename. Full relative paths read directly; bare basenames resolve via wikilink-style lookup (case-insensitive, `.md`-optional) validated against the compiled router. For temporal artefacts, the display name works too — e.g. `name="Colour Theory"` resolves `20260404-research~Colour Theory.md`. Archive paths are rejected with a helpful error.
- **`compliance`** — runs `check.py` checks; `name` filters by severity (`error`/`warning`/`info`)
- **`environment`** — enriched server-side with `obsidian_cli_available`
- **`workspace`** — resolves a specific slug to its data folder path (handled by server, not router state)

Normal artefact/file resources reject archive paths with a helpful error. If a basename resolves to `_Config/`, the error suggests the correct dedicated resource (e.g. `memory`, `skill`).

**Response format:** Resource-dependent. Artefact/file content returned as plain text. Single-item resources (`type`, `trigger`, `memory`) returned as JSON. Complex resources (`router`, `compliance`) remain JSON where structure aids comprehension. Environment returned as formatted `key=value` pairs.

---

### brain_search

Safe, no side effects, auto-approvable. Relevance-ranked search — not exhaustive.

**Parameters:**
- `query` (required)
- `resource` (default `"artefact"`) — also accepts `skill`, `trigger`, `style`, `memory`, `plugin`
- `type`, `tag`, `status` (artefact filters — only apply when `resource="artefact"`)
- `top_k` (default 10)

**Behaviour:**
- For artefacts: CLI-first with BM25 fallback
- For non-artefact resources: text matching on name and file content

**Response format:** Multi-block: bold past-tense metadata block (`**Searched:** N results (source)`) + results as a readable text list (one result per line: title, path, type, score). Includes `source` field (`"obsidian_cli"`, `"bm25"`, or `"text"`).

---

### brain_list

Safe, no side effects, auto-approvable. Exhaustive enumeration — not relevance-ranked. Use instead of `brain_search` when completeness matters (e.g. "all research from the last 2 weeks").

**Parameters:**
- `resource` (default `"artefact"`) — also accepts `skill`, `trigger`, `style`, `plugin`, `memory`, `template`, `type`, `workspace`, `archive`
- `query` (optional text filter for non-artefact resources)
- `type`, `since`, `until` (ISO date strings), `tag`, `sort` (artefact filters — only apply when `resource="artefact"`)
- `top_k` (default 500)
- `sort` — `"date_desc"` (default), `"date_asc"`, `"title"`

**Behaviour:**
- For artefacts: filters the in-memory BM25 index directly — no filesystem walk
- For other resources: reads from the compiled router's small collections with optional `query` substring filtering

Use `resource` to list non-artefact collections — this replaces the previous `brain_read` listing behaviour.

**Response format:** Multi-block: bold past-tense metadata block (`**Listed:** N results`) + results as a readable text list. For artefacts: date, title, path, type, status. For non-artefact resources: name per line.

---

### brain_create

Additive, safe to auto-approve. Creates a new vault resource. Write-guarded: rejects paths targeting dot-prefixed folders (`.brain/`, `.obsidian/`, etc.) and protected underscore folders (`_Archive/`, `_Plugins/`, `_Workspaces/`, `_Assets/`); only `_Temporal/` and `_Config/` are writable.

**Parameters:**
- `resource` (default `"artefact"`) — also accepts `skill`, `memory`, `style`, `template`
- `type` (required for artefacts) — key, full type, or singular form (e.g. `"ideas"`, `"living/ideas"`, or `"idea"`)
- `title` (required for artefacts)
- `name` (required for non-artefact resources) — slugified for filesystem paths; for templates, name is the artefact type key
- `body` (optional; required for non-artefact resources)
- `body_file` (optional) — absolute path to a file containing body content; must be inside the vault or system temp directory; temp files deleted after reading, vault files left in place; mutually exclusive with `body`; use for large content to keep MCP call displays compact; to stage content, run `mktemp /tmp/brain-body-XXXXXX` to get a safe temp path, write content there, then pass that path here
- `frontmatter` (optional overrides) — for memories, use `{"triggers": ["keyword1", "keyword2"]}`
- `parent` (optional) — project subfolder name for living types (e.g. `"Brain"`); ignored for temporal types and non-artefact resources

**Behaviour:**
- For artefacts: resolves type from compiled router, reads template, generates filename from naming pattern, writes file with merged frontmatter; auto-injects `created` and `modified` ISO 8601 timestamps (respects overrides); auto-disambiguates basename collisions by appending `(type)`
- For non-artefact resources: creates in the appropriate `_Config/` subfolder — skills at `_Config/Skills/{name}/SKILL.md`, memories at `_Config/Memories/{name}.md`, styles at `_Config/Styles/{name}.md`, templates at `_Config/Templates/{classification}/{Type}.md`

**Response format:** Plain text confirmation: `"**Created** {type}: {path}"` for artefacts, `"**Created** {resource}: {path}"` for non-artefact resources.

---

### brain_edit

Single-file mutation. Write-guarded: same folder restrictions as `brain_create`.

**Parameters:**
- `resource` (default `"artefact"`) — also accepts `skill`, `memory`, `style`, `template`
- `operation` (required) — `"edit"`, `"append"`, `"prepend"`, or `"delete_section"`
- `path` (required when `resource="artefact"`) — relative path or basename; resolves like wikilinks
- `name` (required when resource is `skill`, `memory`, `style`, or `template`) — for templates, name is the artefact type key
- `body` — omit for frontmatter-only changes; ignored for `delete_section`
- `body_file` (optional) — same semantics as `brain_create`'s `body_file`
- `frontmatter` (optional) — merge strategy depends on operation: edit overwrites fields; append/prepend extend list fields with dedup and overwrite scalars; set a field to `null` to delete it
- `target` (optional) — heading, callout title, or `:body` for whole-body targeting:
  - `edit` replaces the section's content
  - `append` inserts at the end of the section
  - `prepend` inserts before the section's heading line
  - `delete_section` removes the heading and all its content (requires `target`)
  - Include `#` markers to disambiguate duplicate headings
  - Use `[!type]` prefix for callouts, e.g. `"[!note] Status"`
  - Use `target=":body"` to explicitly target the entire body

**Behaviour:**
- For artefacts: path validated against compiled router — wrong folder or naming rejected with helpful error; auto-updates `modified` frontmatter field on every write; auto-sets `statusdate` (YYYY-MM-DD) whenever `status` actually changes; terminal status auto-moves to `+Status/` subfolder with vault-wide wikilink updates, reverts on non-terminal
- For non-artefact resources: resolves via `_Config/` conventions; no terminal status auto-move or `modified` injection

**Response format:** Plain text confirmation: `"**Edited:** {path}"`, `"**Appended:** {path}"`, `"**Prepended:** {path}"`, or `"**Deleted section from:** {path}"` (plus target section and surrounding heading context if a target was specified). Returns `{path, resolved_path, operation}`.

---

### brain_action

Vault-wide and destructive operations, gated by explicit approval.

**Parameters:**
- `action` (required) — one of: `compile`, `build_index`, `rename`, `delete`, `convert`, `shape-presentation`, `migrate_naming`, `register_workspace`, `unregister_workspace`, `fix-links`, `sync_definitions`, `archive`, `unarchive`
- `params` (optional object)

**Actions:**
- **`compile`** — recompile the router
- **`build_index`** — rebuild the BM25 search index
- **`rename`** — delegates to `rename.py`'s `rename_and_update_links()`, with Obsidian CLI override when available. Wikilink updates match full-path (`[[Wiki/topic-a]]`), filename-only (`[[topic-a]]`), heading anchors, block references, embeds, and aliases — preserving the original format; filename-only matching skipped when basename is ambiguous
- **`delete`** — removes a file and replaces wikilinks with strikethrough (same matching as rename)
- **`convert`** — changes artefact type, moves file, reconciles frontmatter, and updates wikilinks vault-wide
- **`shape-presentation`** — creates a Marp presentation artefact and launches live preview (`params: {source, slug}`)
- **`archive`** — moves a terminal-status artefact to `_Archive/{Type}/{Project}/` with date-prefix rename, sets `archiveddate`, and updates vault-wide wikilinks (`params: {path}`)
- **`unarchive`** — restores an archived artefact to its original type folder, strips date prefix, removes `archiveddate` (`params: {path}`)
- **`migrate_naming`** — migrate filenames to generous naming conventions
- **`register_workspace`** — registers a linked workspace in `.brain/local/workspaces.json` (`params: {slug, path}`)
- **`unregister_workspace`** — removes a linked workspace registration (`params: {slug}`)
- **`fix-links`** — scans for broken wikilinks and attempts auto-resolution using naming convention heuristics (slug→title, double-dash→tilde, temporal prefix matching); optional `params: {fix: true}` applies unambiguous fixes; returns JSON with fixed/ambiguous/unresolvable breakdown
- **`sync_definitions`** — syncs artefact library definitions to vault `_Config/` using three-way hash comparison (upstream vs installed vs local); optional `params: {dry_run, force, types}`. Safe updates (upstream changed, no local changes) always apply. Conflicts (both sides changed) are returned as warnings; `force` overwrites despite them. Per-file exclusions via `defaults.exclude.artefact_sync` in `.brain/config.yaml`. Set `artefact_sync: skip` in preferences to disable post-upgrade sync entirely

**Response format:** Plain text status line with bold past-tense action for simple actions (e.g. `**Compiled:** N artefacts...`, `**Renamed** (method): ...`). JSON for complex responses (convert with link counts, migrate_naming with rename lists).

---

### brain_process

Content processing operations.

**Parameters:**
- `operation` (required) — `classify`, `resolve`, or `ingest`
- `content` (required)
- `type` (optional hint)
- `title` (optional hint)
- `mode` (optional, for classify/ingest) — `"auto"` (default), `"embedding"`, `"bm25_only"`, `"context_assembly"`

**Operations:**
- **`classify`** — determines the best artefact type for content using three-tier fallback (embedding → BM25 → context_assembly); returns ranked type matches with confidence scores. Read-only.
- **`resolve`** — checks if content should create a new artefact or update an existing one (requires `type` and `title`); matches against generous filenames, legacy slugs, BM25 search, and optional embeddings; returns create/update/ambiguous decision. Read-only.
- **`ingest`** — runs the full pipeline: classify → infer title → resolve → create/update; optional `type`/`title` hints skip their respective steps. Can create or update files — treat like `brain_create`/`brain_edit` combined.

Index auto-refreshes after successful mutations.

## Permission Configuration

Recommended auto-approve settings:

- **`brain_session`**, **`brain_read`**, **`brain_search`**, **`brain_list`** — safe to auto-approve always
- **`brain_create`** — additive-only (creates files, never destroys) — safe to auto-approve for most workflows
- **`brain_edit`** — mutates a single validated file — approve-once or auto-approve depending on trust level
- **`brain_process`** with `classify`/`resolve` — read-only; `ingest` can create/update files — treat like `brain_create`/`brain_edit` combined
- **`brain_action`** — affects multiple files or system state — require explicit approval per call

## Response Format Conventions

MCP tool results are displayed inline in agent UIs (Claude Code, Cursor, etc.). JSON blobs with escaped newlines and nested objects are hard to scan. Plain text renders cleanly.

**Design rules:**

- **Confirmations → plain text.** `brain_create`, `brain_edit`, simple `brain_action` results. One line, human-scannable.
- **Content retrieval → plain text.** `brain_read(resource="artefact")` returns the file content as-is. List resources use one item per line with tab-separated key fields.
- **Structured data → JSON only when structure adds value.** Router dumps, compliance check arrays, upgrade file manifests. These are genuinely tabular/nested.
- **Errors → plain text.** `"Error: {message}"` — no JSON wrapper.
- **Session → unchanged.** `brain_session` is agent-consumed, never human-read. Stays as compact JSON.
- **Multi-block for mixed responses.** When a tool returns both metadata and content (e.g. search results with source attribution), use `list[TextContent]` — metadata in one block, results in another.

**Mechanism:** FastMCP's `_convert_to_content()` handles three return shapes:
1. `str` → single `TextContent` block
2. `list[TextContent]` → multiple content blocks rendered separately
3. `dict`/`list` (non-string) → auto-serialised to JSON with indent=2

Option 2 is the key lever for producing multi-block responses.

**What this does NOT change:** The underlying script functions still return dicts/lists. Formatting is a presentation concern handled in the MCP server layer only — no changes to scripts, CLI, or compiled router.

**Errors:** All tools return `CallToolResult(isError=True)` with `"Error: {message}"` text content. The `isError` flag enables error-specific rendering in MCP clients. Never return raw dicts with `{"error": ...}` keys.

## Resilience Conventions

The MCP server is a long-running process serving multiple agents across unpredictable vault states. Tools must never crash — a traceback kills the server and orphans the agent session.

**Three-layer exception strategy:** Every tool handler follows the same structure:

1. **Preventive type guards** — before accessing dict keys, `isinstance()` checks confirm the loaded data is the expected type. Corrupted JSON caches can parse as valid JSON but produce the wrong type. Guards go immediately after `json.load()` or any deserialisation.
2. **Inner domain catches** — `try/except` blocks around specific operations, catching expected failure modes (`ValueError`, `KeyError`, `FileNotFoundError`, etc.) with actionable error messages via `_fmt_error()`.
3. **Outer catch-all** — every tool's top-level handler has a final `except Exception` that logs the full traceback to stderr and returns a generic `_fmt_error()`. This is the safety net — it should never be the primary error path, but it ensures the server survives unexpected failures.

All three layers are mandatory for every tool. Omitting the outer catch-all is a bug, even if all exceptions appear to be handled by inner catches.

**Literal schemas on enum-like parameters:** Every tool parameter that accepts a fixed set of values must use `Literal["a", "b", "c"]` type annotations, not bare `str`. This produces `{"enum": [...]}` in the JSON schema so agents see valid values at tool-discovery time.

**Error formatting:** All error returns use `_fmt_error(msg)` which produces `CallToolResult(isError=True)` with `"Error: {message}"` text content. Never raise exceptions to signal errors to the agent — always return a `CallToolResult`.

**Type guards after deserialisation:**

```python
data = json.load(f)
if not isinstance(data, dict):
    return _fmt_error("Expected dict, got " + type(data).__name__)
```

This catches the case where a cache file contains valid JSON of the wrong type (e.g. after a partial write, encoding error, or manual edit).

**Status:** Codified in v0.18.7. All 8 tools conform.

## Server Runtime

### Startup

Loads vault config via three-layer merge (template → `.brain/config.yaml` → `.brain/local/config.yaml`). Auto-compiles router and auto-builds index if stale (compares timestamps against source file mtimes). Both artefacts loaded into memory for the session lifetime. Loads workspace registry from `.brain/local/workspaces.json` (empty dict if absent). Probes Obsidian CLI availability and derives vault name from config `brain_name`, then `BRAIN_VAULT_NAME` env var, then directory basename.

Read, search, create, and edit tools also auto-recompile the router when new taxonomy files appear mid-session.

### Logging

The server writes persistent logs to `.brain/local/mcp-server.log` using Python's `RotatingFileHandler` (2 MB max, 1 backup).

**Log levels:**
- Startup diagnostics and tool call tracing — INFO
- Tool arguments — DEBUG
- Errors — ERROR
- Version drift warnings — WARN

Stderr receives WARN+ messages only (for MCP client visibility). Set `BRAIN_LOG_LEVEL=DEBUG` to include tool arguments in the log. The log file is gitignored (inside `.brain/local/`).

### Operator Profiles

The config system defines three built-in profiles (`reader`, `contributor`, `operator`) with per-tool allow-lists. `brain_session` authenticates operators via SHA-256 key hashing. All tools except `brain_session` enforce the active profile — denied calls return a `CallToolResult` error. No config loaded = no enforcement (backward compatible with existing vaults).

### Version Drift

If `.brain-core/` is upgraded while the server is running, the server detects the version change on the next tool call and exits via `os._exit(10)`. The proxy catches this exit code and relaunches the server with the new code. `os._exit()` is used instead of `sys.exit()` because `SystemExit` raised inside an MCP tool handler gets wrapped in `BaseExceptionGroup` by anyio task groups, losing the exit code.

### Shutdown Lifecycle

The MCP server follows the [stdio lifecycle spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle). Four exit paths:

1. **Stdin EOF** — client closes input pipe → `mcp.run()` returns → `brain-core shutdown: stdin closed` → exit 0
2. **SIGTERM/SIGINT** — signal handler → `brain-core shutdown: received SIGTERM` → exit 0
3. **Version drift** — `_check_version_drift()` detects `.brain-core/VERSION` changed on disk → exit 10. The proxy catches this and relaunches the server with new code.
4. **Unexpected error** — caught, full traceback to stderr → exit 1 (the only path that indicates a real crash)

## Obsidian CLI Integration

The Obsidian CLI is an internal dependency of the MCP server, not a separate agent-facing tier. The server delegates to the CLI for search and rename when available; agents interact only with MCP tools or scripts.

When MCP is unavailable, scripts provide full functionality (read, search, rename, compile, check). The CLI is an optimisation layer, not a requirement. The CLI endpoint is overridable via `OBSIDIAN_CLI_URL` env var (default: `localhost:27124`).

## Dependencies

- **Python** >=3.10
- **`mcp` SDK** — MCP transport
- **`pyyaml`** — config loader
- **`obsidian-cli`** (optional) — dsebastien/obsidian-cli-rest running on localhost:27124; used for CLI-first search and rename when available

The server imports functions directly from scripts — never calls their `main()` (which may `sys.exit`).

---

## Bootstrap Strategy

`brain_session` is the primary bootstrap mechanism. Agents call it first to receive the full session payload (always-rules, user preferences, triggers, artefact types, environment). The `init.py` script installs a SessionStart hook that calls `session.py --json` automatically, so agents receive session context before their first turn.

`brain_read(resource="router")` is not a bootstrap tool — it returns raw router state, not a session payload. Its primary use is as a staleness probe: agents or tooling can call it to check whether the router has changed since the last compile.

---

> For design decisions behind the MCP architecture, see [Design Decisions](../architecture/decisions/).
> For the architecture overview, see [Architecture Overview](../architecture/overview.md).
