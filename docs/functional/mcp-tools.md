# MCP Tools

MCP tool specifications for the brain server.

`server.py` remains the MCP composition root and runtime-state owner. Tool
implementation logic may delegate through sibling `_server_*.py` handler
modules, but the external tool contracts documented here stay unchanged. The
server now answers MCP `initialize` from a minimal startup skeleton, then runs
router/index/workspace maintenance as background warmup with explicit warmup
phase logging in `.brain/local/mcp-server.log`. Warmup-dependent tools return
structured progress/retry payloads instead of blocking blindly. The non-critical
`.brain/local/session.md` refresh still runs on a dedicated daemon worker fed
by a `maxsize=1` coalescing queue — startup and warmup only enqueue, rapid
successive refreshes collapse to the latest intent, and an `atexit` drain with
a bounded cap lets the last in-flight write finish on clean shutdown. See
dd-036 for the full contract.

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
- When an observed client collapses a load-bearing nested schema to an opaque
  type, the summary may include one compact canonical request fragment as a
  compatibility fallback. The full contract still belongs in the generated
  schema; the fallback must stay within the summary budget and must not grow
  into a second parameter manual.
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

Content processing remains experimental, but read-only and mutating operations
now have separate tools and permissions.

| Tool | Safety Level | Purpose |
|---|---|---|
| `brain_init` | Safe — auto-approvable | Additive bootstrap/orientation snapshot with readiness and optional warmup hinting |
| `brain_session` | Safe — auto-approvable | Agent bootstrap: session payload, authentication, profile resolution |
| `brain_read` | Safe — auto-approvable | Read a specific vault resource by name |
| `brain_outline` | Safe — auto-approvable | Discover exact structural selectors accepted by `brain_edit` |
| `brain_check` | Safe — auto-approvable | Return filtered structured Doctor findings without repairs |
| `brain_search` | Safe — auto-approvable | Relevance-ranked search over artefacts and config resources |
| `brain_list` | Safe — auto-approvable | Exhaustive enumeration of artefacts or config collections |
| `brain_stage`, `brain_discard_stage` | Local staging | Stage a large body under an opaque retry-safe handle, or discard it |
| `brain_upload_attachment` | Additive asset creation | Add a non-markdown file beneath a required artefact or standalone attachment scope |
| `brain_create` | Additive — safe to auto-approve | Create a new vault artefact or config resource |
| `brain_edit` | Single-file mutation | Edit, append, prepend, exact-replace, or delete a section |
| `brain_define` | Guarded definition mutation — operator only | Create/replace types and plugins; create/replace/delete triggers |
| `brain_reparent`, `brain_set_status`, `brain_set_key`, `brain_set_naming_field` | Lifecycle mutation | Change handler-owned metadata and apply its derived changes |
| `brain_move` | Vault-wide / destructive — require explicit approval | Rename, convert, archive, or unarchive an artefact with flat top-level fields |
| `brain_action` | Vault-wide / destructive — require explicit approval | Workflow/utility bucket for delete, reparent, shaping helpers, and fix-links |
| `brain_classify`, `brain_resolve` | Safe — auto-approvable | Read-only classification and duplicate resolution |
| `brain_ingest` | Creates/updates files | Full classify/resolve/create-update pipeline |

Mutating MCP and Brain CLI calls share a vault-scoped cross-process lock under
`.brain/local/`. Attachment upload, create, edit, lifecycle, move, action,
ingest, and ownership repair workflows hold it across validation and writes.
Lock waits are bounded and identify the current owner so callers can retry.

Warmup-dependent tools may return a structured `isError=true` progress payload
while background warmup is still running or has failed. The payload includes
coarse readiness state, tool-specific retry guidance, and the capability still needed
(`router`, `index`, or `semantic`) instead of relying on silence or long
blocking waits.

## Tool Specifications

### brain_init

Safe, no heavy work by default, auto-approvable. Additive bootstrap/orientation
snapshot for the Brain runtime. `brain_init` is intentionally lighter than
`brain_session`: it reports vault identity plus coarse readiness/warmup state,
and it can optionally ensure warmup is underway without waiting for completion.
It does not replace the canonical bootstrap line yet — callers still use
`brain_session` when they are actually starting Brain work.

**Parameters:**
- `warmup` (optional) — when `true`, ensure shared background warmup is running or already complete, then return immediately
- `debug` (optional) — include only already-known cheap diagnostics such as active phase and capability readiness; never triggers deep inspection or forced rebuilds

**Response format:** Single JSON string, no indentation. Includes version/vault identity, `readiness`, `warmup_state`, `bootstrap_hint`, `next_action`, and optional cheap debug diagnostics.

---

### brain_session

Agent bootstrap tool — safe, auto-approvable. Builds the canonical session
model in one call: static core bootstrap content (`core_bootstrap`),
structured core-doc references with explicit MCP load instructions
(`core_docs`), always-rules, user preferences, gotchas, triggers, condensed
artefact types, environment, memory/skill/plugin/style indexes, and
config/profile metadata when known. When the caller supplies a workspace
directory, the payload also includes raw `workspace` identity plus optional
`workspace_record` and `workspace_defaults` derived from
`.brain/local/workspace.yaml` (with legacy `.brain/workspace.yaml` fallback)
and any resolvable workspace binding. Every successful session also includes a
compact `workspace_configuration` capability record with the local
`brain configure workspace binding` command template, current binding status when
known, the local Brain-selection options, and explicit statements that binding
does not create a Brain project or workspace artefact and that MCP cannot
configure the connecting agent's filesystem. Server paths are never
substituted into this client-local command. The server actively compiles this —
strips frontmatter from user files, condenses artefact metadata, merges runtime
environment state, and refreshes the generated markdown mirror at
`.brain/local/session.md` from the same model. That refresh is best-effort: the
MCP server enqueues it onto a single long-lived daemon worker so a stalled
write only degrades the markdown mirror, never startup or subsequent tool
calls. If router warmup is still running, `brain_session` ensures warmup is in
flight and returns a structured progress/retry payload instead of blocking on
startup work.

**Parameters:**
- `context` (optional) — scoped session hint (forward-compatible, not yet implemented)
- `operator_key` (optional) — SHA-256 key for operator authentication; matches against registered operators in config and sets the session profile for per-call enforcement. If omitted, uses the default profile from config.

**Response format:** Single JSON string, no indentation (token efficiency). Agent-consumed bootstrap payload; readability not a priority.

Delegates to `session.py`.

---

### brain_read

Safe, no side effects, auto-approvable. Reads a specific resource by name. Delegates to `read.py` resource handlers.

**Parameters:**
- `resource` (required) — one of: `type`, `trigger`, `style`, `template`, `skill`, `plugin`, `memory`, `workspace`, `environment`, `router`, `artefact`, `file`, `archive`
- `name` — required for `type`, `trigger`, `style`, `template`, `skill`, `plugin`, `memory`, `workspace`, `artefact`, `file`, and `archive`; rejected for `environment` and `router`

**Resource behaviours:**
- **Singletons** (`environment`, `router`) — no `name` required
- **Aliases** (`template`, `file`) — work as before; `file` is a smart resolver that delegates to the correct handler
- **`file`** — can also read `.brain-core/` docs by vault-relative path, e.g. `brain_read(resource="file", name=".brain-core/standards/provenance.md")`
- **`artefact`** — reads by canonical artefact key (e.g. `name="design/brain"`), relative path, or basename/display name. Canonical keys resolve via the compiled artefact index; full relative paths read directly; bare names resolve via wikilink-style lookup (case-insensitive, `.md`-optional) validated against the compiled router — for living artefacts the filename is the display name, and for temporal artefacts the display name works too (e.g. `name="Colour Theory"` resolves `20260404-research~Colour Theory.md`). Archive paths are rejected with a helpful error.
- **`trigger`** — reads one compiled trigger by its exact condition, passed through `name` (for example `After meaningful work`)
- **`environment`** — enriched server-side with `obsidian_cli_available`
- **`workspace`** — resolves a specific slug to its data folder path (handled by server, not router state)

Normal artefact/file resources reject archive paths with a helpful error. If a basename resolves to `_Config/`, the error suggests the correct dedicated resource (e.g. `memory`, `skill`).

**Response format:** Resource-dependent. Artefact/file content returned as plain text. Single-item resources (`type`, `trigger`, `memory`) returned as JSON. Complex resources such as `router` remain JSON where structure aids comprehension. Environment returned as formatted `key=value` pairs.

Resource-specific request validation is strict: missing required fields and resource-incompatible extras fail early with a contract hint. `brain_read(resource="workspace")` without `name`, for example, points callers at `brain_list(resource="workspace")`.

---

### brain_outline and brain_check

Both are read-only and safe to auto-approve. `brain_outline(path)` returns the
headings and callouts in an artefact as exact `target`, `occurrence`, and
`within` selectors for `brain_edit`. `brain_check` returns structured Doctor
findings and can filter by `severity`, `check`, `path`, and `actionable`; it
never applies a repair.

---

### brain_stage

`brain_stage(content)` stores a large mutation body in Brain-owned local
staging and returns an opaque `body_handle`. Pass it to exactly one create or
edit request. Failed mutations preserve it for retry; successful mutations
consume it. Cleanup failure is reported as a post-commit warning and never
masks a successful mutation. Handles expire after 24 hours and staging has
per-body/count/total-size bounds; `brain_discard_stage(handle)` releases an
unused handle immediately.

---

### brain_upload_attachment

`brain_upload_attachment(destination_key, name, content_base64)` adds binary or
text content at `_Assets/Attachments/<scope>/<name>`. `destination_key` is
required: pass an active living artefact's canonical `type/key` or `type~key`
to derive its `type~key` scope, or pass a bare validated key for a standalone
reusable folder. Temporal artefacts use a standalone folder key because they do
not have canonical living keys. Arbitrary paths and unknown artefact keys are
rejected. The decoded payload is limited to 16 MiB. The filename must be a
single non-dot-prefixed name, cannot end in
`.md` or a period, cannot be a Windows-reserved device name, and cannot contain
path separators, control characters, Windows-invalid filename characters, or
`#[]` characters that would make the returned Obsidian embed ambiguous.

The operation is additive and retry-safe. If the same filename already contains
the same bytes, the call succeeds with `created: false`; different existing
content returns an error and is never overwritten. Successful results contain
resolved `destination` metadata plus `path`, `embed`, `bytes`, `sha256`, and
`created`. General writes to `_Assets/` remain blocked: this tool can write only
beneath the derived attachment scope and refuses symlinks throughout it.

Changing a living artefact key or converting it to another living type moves
the derived scope and rewrites explicit embeds. Delete and living-to-temporal
conversion preserve the old scope and report it as orphaned. Other lifecycle
moves do not change the scope.

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
- Explicit `mode="semantic"` or `mode="hybrid"` fails clearly when `defaults.flags.semantic_retrieval` is off or when the configured vault is missing semantic runtime packages, the pinned local model snapshot/manifest, or embeddings sidecars; configure the local semantic runtime with `python3 .brain-core/scripts/configure.py semantic --enable`
- If persisted retrieval state cannot be refreshed honestly because of an unreadable source file, compiled-router embeddings drift, or a retrieval-index persistence failure, index-backed artefact search returns that explicit error instead of silently serving stale results
- For non-artefact resources: lexical text matching on name and file content only; `mode="semantic"` and `mode="hybrid"` are rejected

**Response format:** Structured content containing the source and complete result
objects, plus a concise readable text rendering. Fields are not dropped merely
to shorten the display.

---

### brain_list

Safe, no side effects, auto-approvable. Exhaustive enumeration — not relevance-ranked. Use instead of `brain_search` when completeness matters (e.g. "all research from the last 2 weeks").

**Parameters:**
- `resource` (default `"artefact"`) — also accepts `skill`, `trigger`, `style`, `plugin`, `memory`, `template`, `type`, `workspace`, `archive`
- `query` (optional text filter for `type`, `template`, `skill`, `trigger`, `style`, `plugin`, and `memory`)
- `type`, `parent`, `since`, `until` (creation timestamps), `modified_since`, `modified_until`, `tag`, `top_k` (page size), `sort` (`"date_desc"`, `"date_asc"`, `"modified_desc"`, `"modified_asc"`, `"title"`), and opaque `cursor` — artefact-only filters
- `workspace` and `archive` accept no extra filters

**Behaviour:**
- For artefacts: filters the in-memory index directly. Creation and modification
  filters use their corresponding metadata; unknown types are errors rather
  than successful empty pages. A creation-bounded page reports
  `omitted_missing_created` when otherwise-matching malformed artefacts have no
  authoritative creation timestamp; run Brain Doctor to identify those files.
- If index-backed retrieval state is blocked by an unreadable source file, compiled-router embeddings drift, or a retrieval-index persistence failure, artefact listing returns that explicit error instead of stale results
- For other resources: reads from the compiled router's small collections with optional `query` substring filtering; trigger queries search category, condition, detail and target

Use `resource` to list non-artefact collections — this replaces the previous `brain_read` listing behaviour.

**Response format:** Structured page envelope with `items`, `total`, `returned`,
`truncated`, and `next_cursor`, plus a concise readable rendering. Stable sort
keys make cursor continuation deterministic.

Resource-specific request validation is strict here too: passing artefact-only filters such as `type` or `sort` to `resource="skill"` returns a clear error instead of being silently ignored.

---

### brain_create

Additive, safe to auto-approve. Creates a new vault resource. Write-guarded: rejects paths targeting dot-prefixed folders (`.brain/`, `.obsidian/`, etc.) and protected underscore folders (`_Archive/`, `_Plugins/`, `_Workspaces/`, `_Assets/`); only `_Temporal/` and `_Config/` are writable.

**Request:** one required `request` object discriminated by `resource`.

- Artefact variant: `{resource: "artefact", type, title, content?, frontmatter?, parent?, key?, fix_links?}`. `type` accepts a key, full type, or singular form (for example `ideas`, `living/ideas`, or `idea`).
- Named skill, memory, and style variants: `{resource, name, content, frontmatter?}`.
  The template variant is `{resource: "template", name, content}`: `name` is
  the artefact type key, and separate `frontmatter` is absent because the full
  template document carries its own frontmatter. Cross-resource fields are
  absent from every variant.
- `content` is itself discriminated: `{source: "inline", content: "..."}`, `{source: "stage", handle: "..."}`, or the legacy caller-owned `{source: "file", path: "/absolute/path"}`. Staged content is consumed only after success and preserved after failure; Brain never deletes caller-owned files.
- Clients that render the nested variants opaquely can recover the inline shape
  from the tool summary: `"content": {"source": "inline", "content": "..."}`.
  Legacy-looking `body` and `content.kind` inputs remain invalid rather than
  becoming aliases, but validation names `content` and `source` explicitly.
- `parent` accepts a canonical artefact key (`project/brain`) or a resolvable name/path. Living children project it into owner folders; temporal children project the same owner chain before their normal `yyyy-mm/` folder.
- `key` is an optional living key override and must be lowercase ASCII alphanumeric text separated by single hyphens.
- `fix_links` defaults to `false`. When true, resolvable broken wikilinks are rewritten immediately; remaining unresolvable or ambiguous links are reported.

**Behaviour:**
- For artefacts: resolves type from compiled router, reads template, generates filename from naming pattern, writes file with merged frontmatter; naming patterns can also consume matching frontmatter/template values such as `{Version}`; unresolved placeholders return an error instead of writing a broken filename; auto-injects `created` and `modified` ISO 8601 timestamps (respects overrides); living artefacts also get a platform-owned `key` selected from the clearest free title-derived words before using a random suffix; any resolved `parent` is persisted canonically and stamped into tags, with same-type `{key}/` or cross-type `{scope}/` placement for living children and owner-scoped date folders for temporal children; auto-disambiguates basename collisions by appending `(type)`
- For non-artefact resources: creates in the appropriate `_Config/` subfolder — skills at `_Config/Skills/{name}/SKILL.md`, memories at `_Config/Memories/{name}.md`, styles at `_Config/Styles/{name}.md`, templates at `_Config/Templates/{classification}/{Type}.md`. `skill` / `memory` / `style` bodies are serialized with separate frontmatter; template bodies are written as supplied full documents and must begin with frontmatter
- Every artefact write runs a per-file wikilink check; broken, resolvable, and ambiguous links are appended to the response as `⚠` warning lines (and auto-applied fixes as a `✔` block when `fix_links=true`)
- Resource-specific request validation is strict: artefact-only fields (`type`, `title`, `parent`, `key`, `fix_links`) are rejected for non-artefact resources, and `name` is rejected for artefact creation

**Response format:** Structured mutation result containing the path and
resource/type metadata, plus a concise text confirmation.

---

### brain_edit

Single-file mutation. Write-guarded: same folder restrictions as `brain_create`.

**Request:** `{request: {subject, mutation}}`. The tool has one required
top-level `request` argument. Its `subject` and `mutation` members are
discriminated objects, so invalid operation/resource parameter combinations are
rejected by the generated schema before the handler runs.

- Artefact subject: `{resource: "artefact", path, fix_links?}`. `path` accepts a canonical key, vault-relative path, basename, or temporal display name.
- Named-resource subject: `{resource: "skill" | "memory" | "style" | "template", name}`. For templates, `name` is the artefact type key.
- Mutation is one of `edit`, `append`, `prepend`, `replace_text`, or `delete_section`. Edit/append/prepend accept `content?`, `frontmatter?`, `target?`, `selector?`, and `scope?`; delete-section requires `target` and rejects body content rather than silently ignoring it; exact replacement requires `old_text` and `new_text` and optionally accepts `match_occurrence` or `replace_all`.
- `content` uses the same inline/stage/file discriminator as `brain_create`. Omit it for frontmatter-only changes. Inline content is always markdown after frontmatter, not a full document.
- `frontmatter` merge strategy depends on operation: edit overwrites fields; append/prepend extend list fields with deduplication and overwrite scalars; set a field to `null` to delete it.
- `target` is one of:
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
- Artefact-subject `fix_links` defaults to `false`; when true, resolvable broken wikilinks in the edited artefact are rewritten after the edit completes and remaining warnings are reported.

**Behaviour:**
- For artefacts: path validated against compiled router — wrong folder or naming rejected with helpful error; auto-updates `modified` frontmatter field on every write; auto-sets `statusdate` (YYYY-MM-DD) whenever `status` actually changes; terminal status auto-moves to `+Status/` subfolder with vault-wide wikilink updates, reverts on non-terminal
- Generic frontmatter edits reject `parent`, `key`, `status`, and naming-driving
  fields with the dedicated lifecycle command to use. Those commands apply the
  field update and all derived path, link, tag, timestamp, and descendant work.
- Ownership mutations fail before writes when the compiled router/index, scanned parent references, and rendered owner paths disagree. These stale-router/index failures are returned as actionable parent-chain errors that name the stale or unindexed artefact; refresh or rebuild the router/index before retrying.
- Requests that would create a parent cycle are rejected before writes. This includes setting an artefact's parent to itself or to one of its descendants.
- For non-artefact resources: resolves via `_Config/` conventions; no terminal status auto-move or `modified` injection. Memory edits dirty the in-memory router immediately so trigger lookups reflect the write on the next call; non-artefact `_Config/` edits do not queue artefact-index updates
- Body mutations are explicit: omitted `target` no longer means "whole body". Use `target=":body"` plus `scope`.
- Heading structure defines intro/section boundaries. Callouts are individually targetable, but they do not terminate `target=":body", scope="intro"`.
- Ambiguous structural matches hard-error with candidate context. Use `selector.occurrence` or `selector.within` to disambiguate.
- Every artefact edit runs a per-file wikilink check; broken, resolvable, and ambiguous links are appended to the response as `⚠` warning lines (and auto-applied fixes as a `✔` block when `fix_links=true`)
- Resource/op-specific request validation is encoded in the public schema: artefact subjects require `path`, editable `_Config/` subjects require `name`, `delete_section` requires `target`, and cross-resource extras such as `fix_links` on a skill subject are rejected

**Response format:** Structured mutation result with operation, resolved path,
resolved selector, move details, and replacement counts where applicable, plus
a concise text confirmation.

---

### brain_define

Operator-only authoring for the runtime definitions that generic file editing
must not mutate. The required `request` is discriminated by `kind`:

- `type`: `{kind: "type", name, classification, mutation}` manages the
  taxonomy document, its linked template, and the discoverable artefact folder
  as one bundle. The complete markdown is parsed before writing; its declared
  type, classification, template link, frontmatter, and naming contract must
  agree. A missing artefact folder is created; an existing one is preserved.
- `plugin`: `{kind: "plugin", name, mutation}` writes only
  `_Plugins/{name}/SKILL.md`.
- `trigger`: `{kind: "trigger", mutation}` edits one exact entry in the
  `Conditional:` section of `_Config/router.md`. Targets must already exist.

Plugin `mutation` is either `{operation: "create", definition}` or
`{operation: "replace", definition, expected_sha256}`. Type creation also
requires `template`; type replacement requires the complete definition and
template plus `expected_sha256` and `expected_template_sha256`. Replacement
therefore cannot overwrite either component after review. Trigger mutations
have distinct create/replace/delete schemas. Replacement requires the exact
current condition and target, while delete can optionally use the target as a
precondition.

All variants run under the shared per-vault mutation lock, use bounded atomic
writes, reject unknown fields before mutation, dirty compiled router/index
state, and return the before/after hashes and resolved path.

---

### brain_reparent and brain_set_*

These commands own lifecycle metadata that has derived invariants:
`brain_reparent(path, parent)` (`parent` is required; pass null explicitly to clear),
`brain_set_status(path, status)`, `brain_set_key(path, key)`, and
`brain_set_naming_field(path, field, value)`. They validate the field against
the type definition and preflight the complete candidate filename before any
write. Required placeholder values and declared regexes must pass. Successful
commands update metadata, move the artefact and descendants when required,
rewrite links/tags, and return the complete structured change set.
Direct edits of these fields through `brain_edit.frontmatter` are rejected.

External editors such as Obsidian may still change frontmatter directly. The
metadata field remains authoritative: `brain_check` reports parent-folder
drift, and `brain repair ownership --dry-run` previews the complete filesystem
projection before an explicit repair. A missing parent field is not inferred
from folder structure.

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
- `recursive` — optional boolean used by `archive`, `unarchive`, and living→temporal `convert`; required when the operation would otherwise strand or remove ownership metadata from living descendants

**Behaviour:**
- Flat top-level request shape for caller ergonomics: field-level schema plus explicit runtime validation of op-specific requirements
- **`rename`** — request shape: `{op: "rename", source, dest}`. Artefact-aware same-type move only: source and destination must both live in configured artefact folders for the same type, and destination naming is validated against the type contract. Delegates to `rename.py`'s `rename_and_update_links()`, with Obsidian CLI override when available. Wikilink updates match full-path (`[[Wiki/topic-a]]`), filename-only (`[[topic-a]]`), heading anchors, block references, embeds, and aliases while preserving the original format; filename-only matching is skipped when basename is ambiguous
- **`convert`** — request shape: `{op: "convert", path, target_type, parent?, recursive?}`. Changes artefact type, moves file, reconciles frontmatter, and updates wikilinks vault-wide. Crossing the living/temporal boundary reconciles the key contract: temporal→living generates a canonical `key:` from the clearest free title-derived words before using a random suffix. Living→temporal conversion of an artefact with living descendants returns `HAS_DESCENDANTS` by default; pass `recursive: true` to drop the source key and heal descendants by removing their `parent:` field plus owner-tag and relocating them out of the parent's key- or scope-based child folder.
- **`archive`** — request shape: `{op: "archive", path, recursive?}`. Moves a terminal-status artefact to `_Archive/{Type}/{Project}/` with date-prefix rename, sets `archiveddate`, and updates vault-wide wikilinks. If the artefact has living descendants, the default is a `HAS_DESCENDANTS` error with a descendant list; pass `recursive: true` to archive the subtree in one move set.
- **`unarchive`** — request shape: `{op: "unarchive", path, recursive?}`. Restores one archived artefact or an archived subtree through current metadata/status projection, removes `archiveddate`, and updates vault-wide wikilinks. The complete known move set is preflighted before metadata changes. Recursive results report `uninspected` archived candidates with their paths and reasons when an unreadable or unknown-type file prevents Brain from proving that the subtree is complete; MCP surfaces the same condition as a warning.
- All move operations share the move-set preflight used by `rename.py`: duplicate sources/destinations, destination collisions, cyclic move sets, path bounds, protected folders, symlink endpoints, and destination parent components that are files or broken symlinks fail before link rewrites or filesystem mutation.
- Nested ownership move planning is fail-loud. A stale compiled router/index, an indexed descendant missing on disk, or a scanned child `parent:` reference absent from the compiled living index returns an actionable stale-index/parent-chain error rather than `Unexpected error`.
- Documented partial-apply failures return repair context and mark the MCP router/index dirty because durable state may already have changed. The error text names the operation, committed metadata/files when known, and the underlying move failure.

**Response format:** Plain text status lines for rename/archive/unarchive and JSON for convert, where the path and link-update counts are part of the structured payload.

---

### brain_action

Vault-wide and destructive operations, gated by explicit approval.

**Parameters:**
- `request` (required) — discriminated action object. Its `action` selects the exact `params` schema:
  - `delete={path, recursive?}`
  - `reparent-children={source, to?}`
  - `shape-printable={source, slug, render?, keep_heading_with_next?, pdf_engine?}`
  - `shape-presentation={source, slug, render?, preview?}`
  - `shape={target, mode}` where `mode` is `brainstorm`, `refine`, or `discover`
  - `fix-links={fix?, path?, links?}`
  - Mismatched action/parameter combinations are rejected by the MCP schema.

**Actions:**
- **`delete`** — request shape: `{request: {action: "delete", params: {path, recursive?}}}`. Removes an artefact file and replaces wikilinks with strikethrough text. Living artefacts with descendants return `HAS_DESCENDANTS` unless `recursive: true` is supplied; recursive delete removes the descendant subtree and rewrites links in one batch.
- **`reparent-children`** — request shape: `{request: {action: "reparent-children", params: {source, to?}}}`. Moves the direct children of a living source artefact. `brain_reparent` is reserved for changing one artefact's own authoritative parent.
- **`shape-printable`** — request shape: `{request: {action: "shape-printable", params: {source, slug, render?, keep_heading_with_next?, pdf_engine?}}}`. Creates a printable artefact, queues it for incremental retrieval-index refresh, and renders `_Assets/Generated/Printables/{stem}.pdf` via pandoc
- **`shape-presentation`** — request shape: `{request: {action: "shape-presentation", params: {source, slug, render?, preview?}}}`. Creates a Marp presentation artefact, queues it for incremental retrieval-index refresh, renders `_Assets/Generated/Presentations/{stem}.pdf`, and optionally launches live preview
- **`shape`** — request shape: `{request: {action: "shape", params: {target, mode}}}`. Opens or continues a shaping session for a taxonomy-declared shapeable artefact. The shaping skill selects `brainstorm`, `refine`, or `discover` before calling the action. The action validates the compiled shaping contract, creates or appends the source's same-day transcript, transitions to `status: shaping` through the canonical lifecycle handler (including `+Status/` revival and hooks), and queues only changed artefacts for incremental retrieval-index refresh.
- **`fix-links`** — request shape: `{request: {action: "fix-links", params: {fix?, path?, links?}}}`. Scans for broken wikilinks and attempts auto-resolution using naming convention heuristics (slug→title, double-dash→tilde, temporal prefix matching). `fix: true` applies unambiguous fixes; `path: "..."` scopes scan/fix to a single file; `links: [...]` narrows a single-file fix to specific target stems. `brain_create` and `brain_edit` accept a `fix_links: true` convenience flag that runs the single-file fixer on the written artefact

**Response format:** Plain text status lines for delete and JSON for reparent plus the shaping/fix-links flows where structured payloads add value.

---

### brain_classify, brain_resolve, brain_ingest

Experimental content processing operations. Set
`defaults.flags.semantic_processing: true` to enable embedding-backed process
behavior. Shared retrieval embeddings may also be kept warm by
`defaults.flags.semantic_retrieval`, but that flag does not enable
embedding-backed processing behavior by itself.

Each operation is a separate tool so read-only profiles can use classification
and resolution without receiving ingest permission. `content` is always
required; `brain_resolve` also requires `type` and `title`; `brain_ingest`
accepts those as optional hints; classify/ingest accept `mode`.

**Operations:**
- **`brain_classify`** — determines the best artefact type using embedding → BM25 → context assembly fallback. Read-only.
- **`brain_resolve`** — returns create/update/ambiguous by matching classified content against existing artefacts. Exact filename identity and high-confidence semantic cosine similarity can authorise update; BM25 results are advisory candidates only. Read-only.
- **`brain_ingest`** — runs classify → infer title → resolve → create/update and may mutate files. Its declared `mode` controls classification rather than being ignored.
- Retrieval-state failures are returned explicitly instead of serving stale results.

Successful mutations queue the shared incremental index refresh path used by
other MCP writers.
## Permission Configuration

Recommended auto-approve settings:

- **`brain_session`**, **`brain_read`**, **`brain_search`**, **`brain_list`** — safe to auto-approve always
- **`brain_upload_attachment`** — additive-only within a caller-selected, Brain-validated scope under `_Assets/Attachments/`; safe to auto-approve for trusted contributor/operator workflows
- **`brain_create`** — additive-only (creates files, never destroys) — safe to auto-approve for most workflows
- **`brain_edit`** — mutates a single validated file — approve-once or auto-approve depending on trust level
- **`brain_define`** — changes runtime definitions; operator-only with optimistic replacement preconditions
- **`brain_move`** — destructive vault moves — require explicit approval per call
- **`brain_classify`**, **`brain_resolve`** — safe to auto-approve; read-only
- **`brain_ingest`** — may create/update files; treat like `brain_create`/`brain_edit` combined
- **`brain_action`** — delete, reparent, shaping, and fix-links utilities — require explicit approval per call

## Response Format Conventions

MCP tool results are displayed inline in agent UIs (Claude Code, Cursor, etc.). JSON blobs with escaped newlines and nested objects are hard to scan. Plain text renders cleanly.

**Design rules:**

- **Confirmations → plain text.** `brain_upload_attachment`, `brain_create`, `brain_edit`, simple `brain_move`, simple `brain_action` results. Human-scannable, with structured content retained where useful.
- **Content retrieval → plain text.** `brain_read(resource="artefact")` returns the file content as-is. List resources use one item per line with tab-separated key fields.
- **Structured data → JSON only when structure adds value.** Router dumps and upgrade file manifests are genuinely tabular/nested.
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

**Status:** the split content-processing tools are released but remain
experimental while their ranking and ingestion contracts settle.

## Server Runtime

### Startup

Loads vault config via three-layer merge (template → `.brain/config.yaml` → `.brain/local/config.yaml`). Config freshness is rechecked before profile enforcement and before `brain_session` authentication, so on-disk config edits take effect without restarting the MCP server. Malformed or unreadable config fails closed for guarded tools and is reported through `brain_init(debug=true)`. Auto-compiles router and auto-builds index if stale (compares timestamps against source file mtimes). Both artefacts loaded into memory for the session lifetime. Loads workspace registry from `.brain/local/workspaces.json` (empty dict if absent). Derives vault name from config `brain_name`, then `BRAIN_VAULT_NAME` env var, then directory basename. Obsidian CLI availability is probed lazily on demand rather than during startup.

Router freshness is also enforced mid-session when needed: `brain_session`, `brain_read`, `brain_search`, `brain_list`, `brain_create`, `brain_edit`, all `brain_move` ops, and `brain_action` flows that depend on current router state (`delete`, `reparent-children`, `shape`). Direct mutation scripts enforce the same stale-router boundary before local writes, so non-MCP agents do not get a weaker safety contract.

### Logging

The server writes persistent logs to `.brain/local/mcp-server.log` using Python's `RotatingFileHandler` (2 MB max, 1 backup).

**Log levels:**
- Startup diagnostics and tool call tracing — INFO
- Tool arguments — DEBUG
- Errors — ERROR
- Version drift warnings — WARN

Stderr receives WARN+ messages only (for MCP client visibility). Set `BRAIN_LOG_LEVEL=DEBUG` to include tool arguments in the log. The log file is gitignored (inside `.brain/local/`).

### Operator Profiles

The config system defines three built-in profiles (`reader`, `contributor`, `operator`) with per-tool allow-lists. `brain_session` refreshes config before authenticating operators via SHA-256 key hashing. All tools except `brain_session` enforce the active profile — denied calls return a `CallToolResult` error. Config load errors fail closed for guarded tools, and an active profile removed from config becomes an error until the operator runs `brain_session` again or fixes config. Fresh config with no active session profile still runs without per-call enforcement for the unauthenticated bootstrap path.

### Version Drift

If `.brain-core/` is upgraded while the server is running, the server detects the version change on the next tool call and exits via `os._exit(10)`. The proxy catches this exit code, relaunches the server with the new code, and **replays the triggering request** to the new child — the client gets a success response instead of an error. `os._exit()` is used instead of `sys.exit()` because `SystemExit` raised inside an MCP tool handler gets wrapped in `BaseExceptionGroup` by anyio task groups, losing the exit code. Replay is safe because `_check_version_drift()` is the first line of every tool handler, before any side effects. Replay depth is capped at 1 to prevent infinite loops if the replayed request triggers another drift.

The proxy also detects its own code drift (file hash comparison) after child restarts and injects a note into responses advising an MCP restart. All child-loss detection paths — reader-thread EOF, pre-send `poll()!=None`, `BrokenPipeError` while writing to child stdin, and initial startup failure — feed the same restart coordinator. If every backoff attempt fails, the proxy flips into an explicit recovery-exhausted state with restart-MCP guidance instead of returning a soft "server restarting, please retry" forever.

The backoff loop now runs on a dedicated recovery thread rather than on the reader thread, main loop, or `main()` itself. While that thread sleeps or retries, the proxy keeps reading stdin and rejects incoming requests immediately with `server restarting, please retry`. That restores the wrapper's intended "no blocking, no queuing" behaviour even during initial-start failure. If the recovery thread itself crashes, dead-child requests fail hard with `MCP unrecoverable — ...` plus restart-MCP guidance.

### Shutdown Lifecycle

The MCP server follows the [stdio lifecycle spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle). Five exit paths:

1. **Stdin EOF** — client closes input pipe → `mcp.run()` returns → `brain-core shutdown: stdin closed` → exit 0
2. **SIGTERM/SIGINT** — signal handler → `brain-core shutdown: received SIGTERM` → exit 0
3. **Version drift** — `_check_version_drift()` detects `.brain-core/VERSION` changed on disk → exit 10. The proxy catches this, relaunches the server, and replays any in-flight requests.
4. **Hang detection** — on Unix, the proxy's reader thread uses `select()` with a configurable timeout (`BRAIN_PROXY_READ_TIMEOUT`, default 30s). If the child is unresponsive with in-flight requests for 3 consecutive timeouts, the proxy kills the child and routes the loss through the same restart/backoff coordinator. On Windows, anonymous pipe reads block directly, so crash/EOF recovery remains available but timeout-based hang detection is not.
5. **Unexpected error** — caught, full traceback to stderr → exit 1 (the only path that indicates a real crash)

## Obsidian CLI Integration

The Obsidian CLI is an internal dependency of the MCP server, not a separate agent-facing tier. The server delegates to the CLI for search and rename when available; agents interact only with MCP tools or scripts.

When MCP is unavailable, scripts provide full functionality (read, search, rename, compile, check). The CLI is an optimisation layer, not a requirement. The CLI endpoint is overridable via `OBSIDIAN_CLI_URL` env var (default: `localhost:27124`).

## Dependencies

- **Python** >=3.12
- **`mcp` SDK** — MCP transport
- **Brain-owned YAML seam** — standalone config/workspace/manifest loader for Brain's supported YAML subset
- **`obsidian-cli`** (optional) — dsebastien/obsidian-cli-rest running on localhost:27124; used for CLI-first search and rename when available

The server imports functions directly from scripts — never calls their `main()` (which may `sys.exit`).

---

## Bootstrap Strategy

`brain_session` is the primary bootstrap mechanism. Agents call it first to receive the canonical session model as JSON. The shared MCP transport engine (`configure.py mcp` and installer flows) installs a SessionStart hook that calls `session.py --json` automatically; the same transport layer also refreshes `.brain/local/session.md`, the markdown mirror used by no-MCP bootstrap flows. When MCP is unavailable and a JSON session payload is still needed, the CLI fallback is `brain session --json`, which resolves the bound/default Brain through the machine-level resolution runtime before dispatching to that Brain's own `session.py`. There is no non-MCP `brain_init` twin.

`brain_read(resource="router")` is not a bootstrap tool — it returns raw router state, not a session payload. Its primary use is as a staleness probe: agents or tooling can call it to check whether the router has changed since the last compile.

---

> For design decisions behind the MCP architecture, see [Design Decisions](../architecture/decisions/).
> For the architecture overview, see [Architecture Overview](../architecture/overview.md).
