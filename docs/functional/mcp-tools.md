# MCP Tools

MCP tool specifications for the brain server.

`server.py` remains the MCP composition root and runtime-state owner. Tool
implementation logic may delegate through sibling `_server_*.py` handler
modules, but the external tool contracts documented here stay unchanged. The
server now logs explicit startup phases to `.brain/local/mcp-server.log`, and
the non-critical `.brain/local/session.md` refresh runs on a dedicated daemon
worker fed by a `maxsize=1` coalescing queue — startup only enqueues, rapid
successive refreshes collapse to the latest intent, and an `atexit` drain
with a bounded cap lets the last in-flight write finish on clean shutdown.
See dd-036 for the full contract.

## Tool Metadata Contract

This section is the living source of truth for the metadata emitted by every
`@mcp.tool()` registration in `src/brain-core/brain_mcp/server.py`.
[DD-044](../architecture/decisions/dd-044-mcp-tool-metadata-contract.md)
records why this contract exists; this section defines the current
implementation.

- Tool-level docstrings are summary-only. They explain what the tool does, when
  to use it, major behavioural invariants, and what it returns. They do not
  contain `Args:`, `Parameters:`, or `Returns:` sections, parameter tables, or
  per-parameter prose.
- Every exposed parameter must carry a non-empty schema description via
  `Annotated[..., Field(description="...")]` or an equivalent shared constant
  passed into `Field(description=...)`.
- When a nested object has a stable shape, encode that shape in the generated
  schema with named properties and nested descriptions rather than exposing it
  as a generic `object` plus prose.
- Prefer flat, first-class top-level parameters for primary tools. When
  operation-specific cross-field rules would require unsupported schema hacks,
  keep the schema ergonomic and enforce the contract at runtime with precise
  validation errors instead.
- Parameter descriptions carry the semantics the schema cannot: purpose,
  conditional requirements, mutual exclusion, format notes, per-value meaning,
  and worked examples where needed.
- Parameter descriptions do not restate facts the schema already carries, such
  as default values, nullability, or bare enum membership. Add prose only where
  it adds meaning.
- Keep schema descriptions concise on the discovery hot path. When a field
  starts accumulating examples, migration notes, or long behavioural walkthroughs,
  keep the load-bearing semantics in the schema and move the rest into this
  document.
- Validation errors for load-bearing constraints should name the parameter, the
  trigger condition, and the valid values or required condition so an MCP
  client can recover from the error without depending on a long docstring.
- `tests/test_mcp_tool_contract.py` enforces this generated-metadata contract
  against the registered tool set. Update this section and the tests together
  when the contract changes.

## Tool Overview

Branch note: this parked `brain-process-wip` branch documents an unreleased
nine-tool exploratory surface. Shipped `v0.33.0` mainline remains the
eight-tool surface from DD-045 and DD-046.

| Tool | Safety Level | Purpose |
|---|---|---|
| `brain_session` | Safe — auto-approvable | Agent bootstrap: session payload, authentication, profile resolution |
| `brain_read` | Safe — auto-approvable | Read a specific vault resource by name |
| `brain_search` | Safe — auto-approvable | Relevance-ranked search over artefacts and config resources |
| `brain_list` | Safe — auto-approvable | Exhaustive enumeration of artefacts or config collections |
| `brain_create` | Additive — safe to auto-approve | Create a new vault artefact or config resource |
| `brain_edit` | Single-file mutation | Edit, append, prepend, or delete a section from one file |
| `brain_move` | Vault-wide / destructive — require explicit approval | Rename, convert, archive, or unarchive an artefact with flat top-level fields |
| `brain_action` | Vault-wide / destructive — require explicit approval | Workflow/utility bucket for delete, shaping helpers, and fix-links |
| `brain_process` | Classify/resolve: read-only; ingest: creates/updates files | Experimental content classification, duplicate resolution, and ingestion; embedding-backed behavior is controlled by `defaults.flags.semantic_processing`, but degraded non-embedding behavior remains available by default |

Mutating MCP calls are serialized within one server process. This applies to
`brain_create`, `brain_edit`, `brain_move`, `brain_action`, and `brain_process(operation="ingest")`. The lock lives in the MCP wrapper only: scripts remain
the source of truth for mutation behavior, and direct script callers must still
coordinate their own parallel writes.

## Tool Specifications

### brain_session

Agent bootstrap tool — safe, auto-approvable. Builds the canonical session model in one call: static core bootstrap content (`core_bootstrap`), structured core-doc references with explicit MCP load instructions (`core_docs`), always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, memory/skill/plugin/style indexes, and config/profile metadata when known. When the caller supplies a workspace directory, the payload also includes raw `workspace` identity plus optional `workspace_record` and `workspace_defaults` derived from `.brain/local/workspace.yaml` (with legacy `.brain/workspace.yaml` fallback) and any resolvable workspace binding. The server actively compiles this — strips frontmatter from user files, condenses artefact metadata, merges runtime environment state, and refreshes the generated markdown mirror at `.brain/local/session.md` from the same model. That refresh is best-effort: the MCP server enqueues it onto a single long-lived daemon worker so a stalled write only degrades the markdown mirror, never startup or subsequent tool calls.

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
- `name` — required for `type`, `trigger`, `style`, `template`, `skill`, `plugin`, `memory`, `workspace`, `artefact`, `file`, and `archive`; optional for `compliance`; rejected for `environment` and `router`

**Resource behaviours:**
- **Singletons** (`environment`, `router`, `compliance`) — no `name` required
- **Aliases** (`template`, `file`) — work as before; `file` is a smart resolver that delegates to the correct handler
- **`file`** — can also read `.brain-core/` docs by vault-relative path, e.g. `brain_read(resource="file", name=".brain-core/standards/provenance.md")`
- **`artefact`** — reads by canonical artefact key (e.g. `name="design/brain"`), relative path, or basename/display name. Canonical keys resolve via the compiled artefact index; full relative paths read directly; bare names resolve via wikilink-style lookup (case-insensitive, `.md`-optional) validated against the compiled router — for living artefacts the filename is the display name, and for temporal artefacts the display name works too (e.g. `name="Colour Theory"` resolves `20260404-research~Colour Theory.md`). Archive paths are rejected with a helpful error.
- **`compliance`** — runs `check.py` checks; `name` filters by severity (`error`/`warning`/`info`). Repairable router/MCP/local-registry findings now include a structured `repair` object (`scope`, `description`, `command`) in the JSON payload.
- **`environment`** — enriched server-side with `obsidian_cli_available`
- **`workspace`** — resolves a specific slug to its data folder path (handled by server, not router state)

Normal artefact/file resources reject archive paths with a helpful error. If a basename resolves to `_Config/`, the error suggests the correct dedicated resource (e.g. `memory`, `skill`).

**Response format:** Resource-dependent. Artefact/file content returned as plain text. Single-item resources (`type`, `trigger`, `memory`) returned as JSON. Complex resources (`router`, `compliance`) remain JSON where structure aids comprehension. Compliance findings may now carry structured `repair` hints when a shaped repair scope applies. Environment returned as formatted `key=value` pairs.

Resource-specific request validation is strict: missing required fields and resource-incompatible extras fail early with a contract hint. `brain_read(resource="workspace")` without `name`, for example, points callers at `brain_list(resource="workspace")`.

---

### brain_search

Safe, no side effects, auto-approvable. Relevance-ranked search — not exhaustive.

**Parameters:**
- `query` (required)
- `resource` (default `"artefact"`) — also accepts `skill`, `trigger`, `style`, `memory`, `plugin`
- `type`, `tag`, `status` (artefact filters — only apply when `resource="artefact"`)
- `mode` (optional) — artefact search only: `lexical`, `semantic`, `hybrid`
- `top_k` (default 10)

**Behaviour:**
- For artefacts: omitted `mode` picks the best local default — `hybrid` when semantic retrieval is enabled and usable, otherwise `lexical`
- `mode="lexical"` may use Obsidian CLI in MCP when available, with BM25 fallback; editable `_Config/` resources are excluded from `resource="artefact"` search results
- `mode="semantic"` uses persisted document vectors only
- `mode="hybrid"` fuses BM25 + vectors with RRF and deliberately does not use Obsidian CLI as its lexical leg
- Explicit `mode="semantic"` or `mode="hybrid"` fails clearly when `defaults.flags.semantic_retrieval` is off or embeddings sidecars/dependencies are unavailable; the branch-standard runtime is installed with `make install-semantic`
- For non-artefact resources: lexical text matching on name and file content only; `mode="semantic"` and `mode="hybrid"` are rejected

**Response format:** Multi-block: bold past-tense metadata block (`**Searched:** N results (source)`) + results as a readable text list (one result per line: title, path, type, optional status). Includes `source` field (`"obsidian_cli"`, `"bm25"`, `"semantic"`, `"hybrid"`, or `"text"`).

---

### brain_list

Safe, no side effects, auto-approvable. Exhaustive enumeration — not relevance-ranked. Use instead of `brain_search` when completeness matters (e.g. "all research from the last 2 weeks").

**Parameters:**
- `resource` (default `"artefact"`) — also accepts `skill`, `trigger`, `style`, `plugin`, `memory`, `template`, `type`, `workspace`, `archive`
- `query` (optional text filter for `type`, `template`, `skill`, `trigger`, `style`, `plugin`, and `memory`)
- `type`, `parent`, `since`, `until` (ISO date strings), `tag`, `top_k` (default 500), `sort` (`"date_desc"` default, `"date_asc"`, `"title"`) — artefact-only filters
- `workspace` and `archive` accept no extra filters

**Behaviour:**
- For artefacts: filters the in-memory BM25 index directly — no filesystem walk
- For other resources: reads from the compiled router's small collections with optional `query` substring filtering

Use `resource` to list non-artefact collections — this replaces the previous `brain_read` listing behaviour.

**Response format:** Multi-block: bold past-tense metadata block (`**Listed:** N results`) + results as a readable text list. For artefacts: date, title, path, type, status. For non-artefact resources: name per line.

Resource-specific request validation is strict here too: passing artefact-only filters such as `type` or `sort` to `resource="skill"` returns a clear error instead of being silently ignored.

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
- `parent` (optional) — parent artefact reference for child artefacts. Accepts canonical artefact key form (`"project/brain"`), or a resolvable name/path; persisted canonically as `{type}/{key}` for artefacts. Living children use it for owner-derived folder placement; temporal children keep their normal `yyyy-mm/` folder layout. Ignored for non-artefact resources
- `key` (optional) — explicit key override for living artefacts; must be lowercase ASCII alnum plus single hyphens
- `fix_links` (optional, default `false`) — when `true`, resolvable broken wikilinks in the written artefact are auto-rewritten to their canonical target immediately after creation; remaining unresolvable or ambiguous links are still reported as warnings

**Behaviour:**
- For artefacts: resolves type from compiled router, reads template, generates filename from naming pattern, writes file with merged frontmatter; naming patterns can also consume matching frontmatter/template values such as `{Version}`; unresolved placeholders return an error instead of writing a broken filename; auto-injects `created` and `modified` ISO 8601 timestamps (respects overrides); living artefacts also get a platform-owned `key`; any resolved `parent` is persisted canonically and stamped into tags, with owner-derived folder placement for living children only; temporal children keep date-based filing; auto-disambiguates basename collisions by appending `(type)`
- For non-artefact resources: creates in the appropriate `_Config/` subfolder — skills at `_Config/Skills/{name}/SKILL.md`, memories at `_Config/Memories/{name}.md`, styles at `_Config/Styles/{name}.md`, templates at `_Config/Templates/{classification}/{Type}.md`
- Every artefact write runs a per-file wikilink check; broken, resolvable, and ambiguous links are appended to the response as `⚠` warning lines (and auto-applied fixes as a `✔` block when `fix_links=true`)
- Resource-specific request validation is strict: artefact-only fields (`type`, `title`, `parent`, `key`, `fix_links`) are rejected for non-artefact resources, and `name` is rejected for artefact creation

**Response format:** Plain text confirmation: `"**Created** {type}: {path}"` for artefacts, `"**Created** {resource}: {path}"` for non-artefact resources.

---

### brain_edit

Single-file mutation. Write-guarded: same folder restrictions as `brain_create`.

**Parameters:**
- `resource` (default `"artefact"`) — also accepts `skill`, `memory`, `style`, `template`
- `operation` (required) — `"edit"`, `"append"`, `"prepend"`, or `"delete_section"`
- `path` (required when `resource="artefact"`) — canonical artefact key (e.g. `"design/brain"`), vault-relative path, or filename basename. For temporal artefacts the display-name portion of the dated filename also resolves (e.g. `"Colour Theory"` → `20260404-research~Colour Theory.md`)
- `name` (required when resource is `skill`, `memory`, `style`, or `template`) — for templates, name is the artefact type key
- `body` — omit for frontmatter-only changes; ignored for `delete_section`
- `body_file` (optional) — same semantics as `brain_create`'s `body_file`
- `frontmatter` (optional) — merge strategy depends on operation: edit overwrites fields; append/prepend extend list fields with dedup and overwrite scalars; set a field to `null` to delete it
- `target` (optional for frontmatter-only edits; required for structural mutations and `delete_section`) — one of:
  - `":body"` — the markdown body after frontmatter
  - a heading target such as `"## Notes"`
  - a callout target such as `"[!note] Status"`
- `selector` (optional) — disambiguates duplicate targets after `target` selection:
  - `occurrence` — 1-based duplicate selector in the current search space
  - `within` — ordered ancestor chain of `{target, occurrence?}` steps from outermost to innermost
  - `":body"` is only valid as the top-level `target`, never inside `selector.within`
- `scope` (required for structural `edit` / `append` / `prepend`) — mutable range inside the resolved target:
  - `target=":body"`: `section`, `intro`
  - heading targets: `section`, `body`, `intro`, `heading` (`heading` is `edit`-only)
  - callout targets: `section`, `body`, `header` (`header` is `edit`-only)
  - `delete_section` does not accept `scope`; it deletes the resolved heading section or callout block
- Legacy spellings are migration errors, not aliases:
  - `target=":entire_body"` → use `target=":body", scope="section"`
  - `target=":body_preamble"` / `target=":body_before_first_heading"` → use `target=":body", scope="intro"`
  - `target=":section:..."` → use the real heading/callout target with `scope="section"`
- `fix_links` (optional, default `false`) — when `true`, resolvable broken wikilinks in the edited artefact are auto-rewritten to their canonical target after the edit completes; remaining unresolvable or ambiguous links are still reported as warnings

**Behaviour:**
- For artefacts: path validated against compiled router — wrong folder or naming rejected with helpful error; auto-updates `modified` frontmatter field on every write; auto-sets `statusdate` (YYYY-MM-DD) whenever `status` actually changes; terminal status auto-moves to `+Status/` subfolder with vault-wide wikilink updates, reverts on non-terminal
- For non-artefact resources: resolves via `_Config/` conventions; no terminal status auto-move or `modified` injection. Memory edits dirty the in-memory router immediately so trigger lookups reflect the write on the next call; non-artefact `_Config/` edits do not queue artefact-index updates
- Body mutations are explicit: omitted `target` no longer means "whole body". Use `target=":body"` plus `scope`.
- Heading structure defines intro/section boundaries. Callouts are individually targetable, but they do not terminate `target=":body", scope="intro"`.
- Ambiguous structural matches hard-error with candidate context. Use `selector.occurrence` or `selector.within` to disambiguate.
- Every artefact edit runs a per-file wikilink check; broken, resolvable, and ambiguous links are appended to the response as `⚠` warning lines (and auto-applied fixes as a `✔` block when `fix_links=true`)
- Resource/op-specific request validation is strict before the structural preflight layer runs: artefacts require `path`, editable `_Config/` resources require `name`, `delete_section` requires `target`, and cross-resource extras such as `fix_links` on a skill edit are rejected

**Response format:** Plain text confirmation: `"**Edited:** {path}"`, `"**Appended:** {path}"`, `"**Prepended:** {path}"`, or `"**Deleted section from:** {path}"`. Structural mutations append the resolved range in parentheses, e.g. `(body section)`, `(body intro)`, `(heading body: ## Notes)`, `(heading section: # API [2] > ## Notes)`, or `(callout header: [!note] Status)`.

---

### brain_move

Vault-wide and destructive content-move operations, gated by explicit approval.

**Parameters:**
- `op` (required) — one of: `rename`, `convert`, `archive`, `unarchive`
- `source` — used only when `op="rename"`
- `dest` — used only when `op="rename"`
- `path` — used by `convert`, `archive`, and `unarchive`
- `target_type` — used only when `op="convert"`
- `parent` — optional parent artefact reference used only when `op="convert"`

**Behaviour:**
- Flat top-level request shape for caller ergonomics, matching the `brain_edit` pattern: field-level schema plus explicit runtime validation of op-specific requirements
- **`rename`** — request shape: `{op: "rename", source, dest}`. Artefact-aware same-type move only: source and destination must both live in configured artefact folders for the same type, and destination naming is validated against the type contract. Delegates to `rename.py`'s `rename_and_update_links()`, with Obsidian CLI override when available. Wikilink updates match full-path (`[[Wiki/topic-a]]`), filename-only (`[[topic-a]]`), heading anchors, block references, embeds, and aliases while preserving the original format; filename-only matching is skipped when basename is ambiguous
- **`convert`** — request shape: `{op: "convert", path, target_type, parent?}`. Changes artefact type, moves file, reconciles frontmatter, and updates wikilinks vault-wide. Crossing the living/temporal boundary reconciles the key contract: temporal→living generates a canonical `key:`; living→temporal drops the key and heals descendants by removing their `parent:` field plus the owner-tag and relocating them out of the owner-derived folder
- **`archive`** — request shape: `{op: "archive", path}`. Moves a terminal-status artefact to `_Archive/{Type}/{Project}/` with date-prefix rename, sets `archiveddate`, and updates vault-wide wikilinks
- **`unarchive`** — request shape: `{op: "unarchive", path}`. Restores an archived artefact to its original type folder, strips the date prefix, removes `archiveddate`, and updates vault-wide wikilinks

**Response format:** Plain text status lines for rename/archive/unarchive and JSON for convert, where the path and link-update counts are part of the structured payload.

---

### brain_action

Vault-wide and destructive operations, gated by explicit approval.

**Parameters:**
- `action` (required) — one of: `delete`, `shape-printable`, `shape-presentation`, `start-shaping`, `fix-links`
- `params` (optional) — action-specific parameter object. The generated schema publishes five named variants:
  - `delete={path}`
  - `shape-printable={source, slug, render?, keep_heading_with_next?, pdf_engine?}`
  - `shape-presentation={source, slug, render?, preview?}`
  - `start-shaping={target, title?, skill_type?}`
  - `fix-links={fix?, path?, links?}`
  - The schema exposes those nested field sets for discoverability, but it does not structurally discriminate `action -> params` pairings. Mismatched pairings are rejected by runtime validation in the handler layer.

**Actions:**
- **`delete`** — request shape: `{action: "delete", params: {path}}`. Removes an artefact file and replaces wikilinks with strikethrough text
- **`shape-printable`** — request shape: `{action: "shape-printable", params: {source, slug, render?, keep_heading_with_next?, pdf_engine?}}`. Creates a printable artefact, queues it for incremental retrieval-index refresh, and renders `_Assets/Generated/Printables/{stem}.pdf` via pandoc
- **`shape-presentation`** — request shape: `{action: "shape-presentation", params: {source, slug, render?, preview?}}`. Creates a Marp presentation artefact, queues it for incremental retrieval-index refresh, renders `_Assets/Generated/Presentations/{stem}.pdf`, and optionally launches live preview
- **`start-shaping`** — request shape: `{action: "start-shaping", params: {target, title?, skill_type?}}`. Bootstraps a shaping session against an existing artefact, creating or appending the transcript, reviving `+Status/` artefacts back into the active folder when it sets `status: shaping`, and queuing the touched artefacts for incremental retrieval-index refresh
- **`fix-links`** — request shape: `{action: "fix-links", params: {fix?, path?, links?}}`. Scans for broken wikilinks and attempts auto-resolution using naming convention heuristics (slug→title, double-dash→tilde, temporal prefix matching). `fix: true` applies unambiguous fixes; `path: "..."` scopes scan/fix to a single file; `links: [...]` narrows a single-file fix to specific target stems. `brain_create` and `brain_edit` accept a `fix_links: true` convenience flag that runs the single-file fixer on the written artefact

**Response format:** Plain text status lines for delete and JSON for the shaping/fix-links flows where structured payloads add value.

---

### brain_process

Experimental content processing operations for the parked branch. The tool
remains registered for MCP discovery here. Set
`defaults.flags.semantic_processing: true` to enable embedding-backed process
behavior. Shared retrieval embeddings may also be kept warm by
`defaults.flags.semantic_retrieval`, but that flag does not enable
embedding-backed `brain_process` behavior by itself.

**Parameters:**
- `operation` (required) — `classify`, `resolve`, or `ingest`
- `content` (required)
- `type` (resolve required, ingest optional hint) — not accepted for `classify`
- `title` (resolve required, ingest optional hint) — not accepted for `classify`
- `mode` (optional, `classify`/`ingest` only) — `"auto"` (default), `"embedding"`, `"bm25_only"`, `"context_assembly"`; not accepted for `resolve`

**Operations:**
- **`classify`** — determines the best artefact type for content using three-tier fallback (embedding → BM25 → context_assembly); returns ranked type matches with confidence scores. Read-only.
- **`resolve`** — checks if content should create a new artefact or update an existing one (requires `type` and `title`); matches against generous filenames, legacy slugs, BM25 search, and optional embeddings; returns create/update/ambiguous decision. Read-only.
- **`ingest`** — runs the full pipeline: classify → infer title → resolve → create/update; optional `type`/`title` hints skip their respective steps. Can create or update files — treat like `brain_create`/`brain_edit` combined.

Successful mutations queue the shared incremental index refresh path used by
other MCP writers.
## Permission Configuration

Recommended auto-approve settings:

- **`brain_session`**, **`brain_read`**, **`brain_search`**, **`brain_list`** — safe to auto-approve always
- **`brain_create`** — additive-only (creates files, never destroys) — safe to auto-approve for most workflows
- **`brain_edit`** — mutates a single validated file — approve-once or auto-approve depending on trust level
- **`brain_move`** — destructive vault moves — require explicit approval per call
- **`brain_process`** — experimental; `classify`/`resolve` are read-only and `ingest` can create/update files, so treat it like `brain_create`/`brain_edit` combined. `defaults.flags.semantic_processing` only controls whether embedding-backed behavior is available.
- **`brain_action`** — delete, shaping, and fix-links utilities — require explicit approval per call

## Response Format Conventions

MCP tool results are displayed inline in agent UIs (Claude Code, Cursor, etc.). JSON blobs with escaped newlines and nested objects are hard to scan. Plain text renders cleanly.

**Design rules:**

- **Confirmations → plain text.** `brain_create`, `brain_edit`, simple `brain_move`, simple `brain_action` results. One line, human-scannable.
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

**Status:** `v0.33.0` codified the released eight-tool mainline contract. This
parked branch carries an unreleased ninth tool (`brain_process`) and the
corresponding contract updates ahead of any future reintroduction decision.

## Server Runtime

### Startup

Loads vault config via three-layer merge (template → `.brain/config.yaml` → `.brain/local/config.yaml`). Auto-compiles router and auto-builds index if stale (compares timestamps against source file mtimes). Both artefacts loaded into memory for the session lifetime. Loads workspace registry from `.brain/local/workspaces.json` (empty dict if absent). Derives vault name from config `brain_name`, then `BRAIN_VAULT_NAME` env var, then directory basename. Obsidian CLI availability is probed lazily on demand rather than during startup.

Router freshness is also enforced mid-session when needed: `brain_session`, `brain_read`, `brain_search`, `brain_list`, `brain_create`, `brain_edit`, all `brain_move` ops, and `brain_action` flows that depend on current router state (`delete`, `start-shaping`).

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

If `.brain-core/` is upgraded while the server is running, the server detects the version change on the next tool call and exits via `os._exit(10)`. The proxy catches this exit code, relaunches the server with the new code, and **replays the triggering request** to the new child — the client gets a success response instead of an error. `os._exit()` is used instead of `sys.exit()` because `SystemExit` raised inside an MCP tool handler gets wrapped in `BaseExceptionGroup` by anyio task groups, losing the exit code. Replay is safe because `_check_version_drift()` is the first line of every tool handler, before any side effects. Replay depth is capped at 1 to prevent infinite loops if the replayed request triggers another drift.

The proxy also detects its own code drift (file hash comparison) after child restarts and injects a note into responses advising an MCP restart. All child-loss detection paths — reader-thread EOF, pre-send `poll()!=None`, `BrokenPipeError` while writing to child stdin, and initial startup failure — feed the same restart coordinator. If every backoff attempt fails, the proxy flips into an explicit recovery-exhausted state with `/mcp` guidance instead of returning a soft "server restarting, please retry" forever.

The backoff loop now runs on a dedicated recovery thread rather than on the reader thread, main loop, or `main()` itself. While that thread sleeps or retries, the proxy keeps reading stdin and rejects incoming requests immediately with `server restarting, please retry`. That restores the wrapper's intended "no blocking, no queuing" behaviour even during initial-start failure. If the recovery thread itself crashes, dead-child requests fail hard with `MCP unrecoverable — ...` plus `/mcp` guidance.

### Shutdown Lifecycle

The MCP server follows the [stdio lifecycle spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle). Four exit paths:

1. **Stdin EOF** — client closes input pipe → `mcp.run()` returns → `brain-core shutdown: stdin closed` → exit 0
2. **SIGTERM/SIGINT** — signal handler → `brain-core shutdown: received SIGTERM` → exit 0
3. **Version drift** — `_check_version_drift()` detects `.brain-core/VERSION` changed on disk → exit 10. The proxy catches this, relaunches the server, and replays any in-flight requests.
4. **Hang detection** — the proxy's reader thread uses `select()` with a configurable timeout (`BRAIN_PROXY_READ_TIMEOUT`, default 30s). If the child is unresponsive with in-flight requests for 3 consecutive timeouts, the proxy kills the child and routes the loss through the same restart/backoff coordinator.
5. **Unexpected error** — caught, full traceback to stderr → exit 1 (the only path that indicates a real crash)

## Obsidian CLI Integration

The Obsidian CLI is an internal dependency of the MCP server, not a separate agent-facing tier. The server delegates to the CLI for search and rename when available; agents interact only with MCP tools or scripts.

When MCP is unavailable, scripts provide full functionality (read, search, rename, compile, check). The CLI is an optimisation layer, not a requirement. The CLI endpoint is overridable via `OBSIDIAN_CLI_URL` env var (default: `localhost:27124`).

## Dependencies

- **Python** >=3.12
- **`mcp` SDK** — MCP transport
- **`pyyaml`** — config loader
- **`obsidian-cli`** (optional) — dsebastien/obsidian-cli-rest running on localhost:27124; used for CLI-first search and rename when available

The server imports functions directly from scripts — never calls their `main()` (which may `sys.exit`).

---

## Bootstrap Strategy

`brain_session` is the primary bootstrap mechanism. Agents call it first to receive the canonical session model as JSON. The `init.py` script installs a SessionStart hook that calls `session.py --json` automatically; the same script also refreshes `.brain/local/session.md`, the markdown mirror used by no-MCP bootstrap flows.

`brain_read(resource="router")` is not a bootstrap tool — it returns raw router state, not a session payload. Its primary use is as a staleness probe: agents or tooling can call it to check whether the router has changed since the last compile.

---

> For design decisions behind the MCP architecture, see [Design Decisions](../architecture/decisions/).
> For the architecture overview, see [Architecture Overview](../architecture/overview.md).
