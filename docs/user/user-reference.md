# Brain Reference

Complete reference for every artefact type, convention, configuration point, and system in a Brain vault. For a walkthrough of how to use the Brain day-to-day, see [Getting Started](getting-started.md) and [Workflows](workflows.md).

---

## Contents

- [Artefact Types](#artefact-types)
- [System Reference](#system-reference)
- [Configuration Reference](#configuration-reference)
- [Tooling](#tooling)
- [Colour System](#colour-system)
- [Writing Style](#writing-style)
- [Maintaining This Guide](#maintaining-this-guide)

---

## Artefact Types

For the full list of default artefact types, see [Template Library Guide](template-library-guide.md).

For type definitions (frontmatter, naming patterns, filing rules), see the artefact library at `src/brain-core/artefact-library/`.

## System Reference

For artefact system mechanics (lifecycle, frontmatter, filing, workflows), see [System Guide](system-guide.md).

---

## Configuration Reference

### Router (`_Config/router.md`)

Vault-specific routing rules used by the compiled router and degraded bootstrap path. Contains:
- **Always-rules** — vault-specific constraints that apply every session
- **Conditional triggers** — "when X happens, follow this link to the taxonomy file"

Each trigger is a condition paired with a goto pointer. The taxonomy file's `## Trigger` section contains the detailed instructions. This means triggers are defined in one place (no duplication between router and taxonomy).

### Taxonomy (`_Config/Taxonomy/`)

One file per artefact type, organised as `Living/{key}.md` and `Temporal/{key}.md`. Each taxonomy file defines:
- Purpose and description
- Naming pattern
- Frontmatter schema
- Lifecycle and status values (if applicable)
- Archiving rules (if applicable)
- Trigger section (if applicable)
- Conventions and writing guidance

### Templates (`_Config/Templates/`)

Obsidian templates for each type, organised as `Living/{Type Name}.md` and `Temporal/{Type Name}.md`. Used by Obsidian's core Templates plugin or Templater.

### Styles

- **`_Config/Styles/obsidian.md`** — colour assignments for the vault's artefact types
- **`_Config/Styles/writing.md`** — writing style guide (language preferences, conventions)

### User Preferences

- **`_Config/User/preferences-always.md`** — your standing instructions for agents (workflow preferences, quality standards, behaviour rules). Read every session.
- **`_Config/User/gotchas.md`** — learned pitfalls from previous sessions. Friction patterns that recur get distilled here. Read every session.

Both are freeform markdown. Content is entirely up to you.

### Memories (`_Config/Memories/`)

Reference cards that agents load on demand — factual context about projects, tools, and concepts. Each memory is a `.md` file with a `triggers` list in YAML frontmatter:

```yaml
---
triggers: [brain core, obsidian-brain, vault system]
---
```

**Trigger matching:** Case-insensitive substring. `brain_read(resource="memory", name="brain")` matches a memory with trigger "brain core". Falls back to exact filename match if no trigger matches.

**File format:** YAML frontmatter with `triggers` list, then a markdown reference card body. Memories answer "what is it?" — what something is, where it lives, how pieces relate, key facts. If the content is "how do I do it?" (steps, procedures, tool usage), it belongs in a skill (`_Config/Skills/`), not a memory. A memory can reference a skill but should not replicate it.

**Naive fallback:** `_Config/Memories/README.md` contains a trigger → file table for agents without MCP or the compiled router.

**Creating a memory:**
1. Create `.md` file in `_Config/Memories/` with `triggers: [...]` in frontmatter
2. Write reference card body
3. Run `python3 .brain-core/scripts/compile_router.py`
4. Update the README table

### Skills (`_Config/Skills/`)

Skill documents for MCP tools, CLI commands, or plugin workflows. One folder per skill with a `SKILL.md` file describing what the skill does and how to use it.

---

## Tooling

### MCP Tools

If your vault runs the Brain MCP server (`.brain-core/brain_mcp/server.py`), nine tools are available:

**brain_init** (safe, auto-approvable)
- Additive bootstrap/orientation snapshot for the Brain runtime
- Returns vault identity plus coarse readiness/warmup state, `next_action`, and optional cheap debug diagnostics
- Optional `warmup` parameter ensures shared background warmup is running or already complete, then returns immediately

**brain_session** (safe, auto-approvable)
- Bootstrap an agent session in one call — returns the canonical session model as compact JSON
- Includes: static core bootstrap content, structured core-doc references with MCP load instructions, always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, memory/skill/plugin/style indexes, config metadata, and when available workspace-aware bootstrap fields (`workspace`, `workspace_record`, `workspace_defaults`)
- Optional `context` parameter for scoped sessions (not yet implemented)
- Optional `operator_key` parameter for operator authentication — sets the session profile for per-call tool enforcement
- Refreshes `.brain/local/session.md`, the generated markdown bootstrap mirror, from the same model; that refresh is best-effort and runs on a background worker, so a stalled write never blocks readiness or tool calls
- If router warmup is still in progress, returns a structured progress/retry payload instead of blocking blindly

**brain_read** (safe, no side effects)
- Look up artefacts, triggers, styles, templates, skills, plugins, memories, workspaces, environment info, the compiled router, structural compliance results, or read artefact files by path
- `name` is required for named resources such as `skill`, `memory`, `workspace`, `artefact`, and `file`; optional for `compliance`; rejected for `environment` and `router`. For temporal artefacts, the display name works without the dated prefix — e.g. "Colour Theory" finds `20260404-research~Colour Theory.md`
- `resource="file"` can also read `.brain-core/` docs by vault-relative path when the agent is operating over MCP, e.g. `brain_read(resource="file", name=".brain-core/standards/provenance.md")`

**brain_search** (safe, no side effects)
- Search vault content by query text
- Optional `resource` parameter (default `"artefact"`) — also accepts `skill`, `trigger`, `style`, `memory`, `plugin` for searching non-artefact collections via text matching
- Artefact-specific filters: `type` (key, full type, or singular form), `tag`, `status`
- Returns ranked results with paths, titles, scores, and text snippets
- Uses Obsidian CLI when available, falls back to BM25 index; `resource="artefact"` stays scoped to artefacts, while non-artefact resources use text matching

**brain_list** (safe, no side effects)
- List vault artefacts exhaustively — not relevance-ranked
- `resource="artefact"` supports `type`, `parent`, `since`/`until` (ISO dates e.g. `"2026-03-20"`), `tag`, `top_k` (default 500), and sort by `"date_desc"` (default), `"date_asc"`, or `"title"`
- Non-artefact collections such as `skill`, `memory`, `template`, and `style` support only optional `query`; `workspace` and `archive` accept no filters
- Use instead of `brain_search` when completeness matters (e.g. "all research from the last 2 weeks")

**brain_create** (additive, safe to auto-approve)
- Create a new vault resource. Default `resource="artefact"` for artefact creation from type, title, and optional body/frontmatter/parent. Also creates `skill`, `memory`, `style`, and `template` resources in `_Config/` (use `name` instead of `type`/`title`)
- Artefacts: resolves template and naming pattern from the compiled router
- Non-artefact resources: `skill` → `_Config/Skills/{name}/SKILL.md`, `memory` → `_Config/Memories/{name}.md`, `style` → `_Config/Styles/{name}.md`, `template` → `_Config/Templates/{classification}/{Type}.md`
- Resource-specific fields are enforced strictly: artefact creation requires `type` + `title`, non-artefact creation requires `name`, and cross-resource extras are rejected
- Returns confirmation message with path

**brain_edit** (single-file mutation)
- `resource` parameter (default `"artefact"`) — also accepts `skill`, `memory`, `style`, `template` for editing `_Config/` resources
- `edit` — replace body content, optionally merge frontmatter changes (overwrites fields)
- `append` — add content to end of existing body
- `prepend` — insert content before existing body or before a target section's heading
- Optional `frontmatter` parameter — `edit` overwrites fields; `append`/`prepend` extend list fields (with dedup) and overwrite scalars. Set a field to `null` to delete it. All operations support frontmatter-only mutations (omit body)
- Memory trigger edits refresh `brain_read(resource="memory", ...)` immediately; editing `_Config/` resources does not make them appear in `brain_search(resource="artefact")`
- `target` identifies the structural node:
  - `":body"` for the full markdown body after frontmatter
  - a heading target such as `"### Notes"`
  - a callout target such as `"[!note] Implementation status"`
- Optional `selector` disambiguates duplicates:
  - `occurrence` — 1-based duplicate selector
  - `within` — ordered ancestor chain of `{target, occurrence?}` steps
- `scope` chooses the mutable range inside the resolved target:
  - `target=":body"`: `section`, `intro`
  - heading targets: `section`, `body`, `intro`, `heading` (`heading` is `edit`-only)
  - callout targets: `section`, `body`, `header` (`header` is `edit`-only)
  - `delete_section` uses the same `target` / `selector` model but does not take `scope`
- Body mutations are explicit. Omitted `target` no longer means "whole body"; use `target=":body", scope="section"` for full-body mutations.
- `target=":body", scope="intro"` runs from the start of the markdown body to the first heading. Callouts inside that range stay part of the intro instead of terminating it.
- Old spellings are hard-errors with guidance:
  - `:entire_body` → `target=":body", scope="section"`
  - `:body_preamble` / `:body_before_first_heading` → `target=":body", scope="intro"`
  - `:section:...` → the real heading/callout target with `scope="section"`
- Structural edit confirmations include the resolved range in the response, for example `(body section)`, `(body intro)`, `(heading body: ## Notes)`, or `(callout header: [!note] Status)`.
- For artefacts: `path` accepts canonical artefact key (for example `"design/brain"`), vault-relative path, or filename basename; for temporal artefacts the display-name portion of the dated filename also resolves (e.g. `"Colour Theory"` → `20260404-research~Colour Theory.md`); validated against the compiled router
- For non-artefact resources: `name` identifies the resource (e.g. `"my-skill"`); for templates, name is the artefact type key (e.g. `"wiki"`). No terminal status auto-move or `modified` injection
- Validation is resource/op-specific: artefacts require `path`, editable `_Config/` resources require `name`, `delete_section` requires `target`, and fields that belong to a different resource are rejected early

**brain_move** (vault-wide/destructive, requires approval)
- Flat top-level move tool for artefact path/classification transitions
- `rename` — request shape: `{op: "rename", source, dest}`; artefact-aware same-type move with automatic wikilink updates (uses Obsidian CLI when available)
- `convert` — request shape: `{op: "convert", path, target_type, parent?}`; changes artefact type, moves the file, reconciles frontmatter, and updates wikilinks
- `archive` — request shape: `{op: "archive", path}`; archives a terminal-status artefact to `_Archive/` with date-prefix rename and wikilink updates
- `unarchive` — request shape: `{op: "unarchive", path}`; restores an archived artefact to its original type folder and removes `archiveddate`

**brain_action** (vault-wide/destructive, requires approval)
- Smaller workflow/utility bucket using `action + params`
- `delete` — request shape: `{action: "delete", params: {path}}`; deletes an artefact file and replaces wikilinks with strikethrough text
- `shape-printable` — request shape: `{action: "shape-printable", params: {source, slug, render?, keep_heading_with_next?, pdf_engine?}}`; creates a printable artefact and renders `_Assets/Generated/Printables/{stem}.pdf` via pandoc
- `shape-presentation` — request shape: `{action: "shape-presentation", params: {source, slug, render?, preview?}}`; creates a presentation artefact, renders `_Assets/Generated/Presentations/{stem}.pdf`, and optionally launches Marp live preview
- `start-shaping` — request shape: `{action: "start-shaping", params: {target, title?, skill_type?}}`; bootstraps a shaping session for an existing artefact and revives `+Status/` artefacts back into the active folder when shaping resumes
- `fix-links` — request shape: `{action: "fix-links", params: {fix?, path?, links?}}`; scans for broken wikilinks and attempts auto-resolution

### Server Logging

The MCP server writes persistent logs to `.brain/local/mcp-server.log` (2 MB max, 1 backup). Startup diagnostics, tool call tracing, and errors are logged at INFO level. Startup now emits explicit begin/success/failure markers for config load, router freshness, index freshness, workspace registry load, and session-mirror refresh, so a stalled startup can be localised from the log alone. To include tool arguments in the log, set the environment variable `BRAIN_LOG_LEVEL=DEBUG`. The log file is local-only (gitignored).

### Scripts

Available in `.brain-core/scripts/`. Scripts are the source of truth for all vault operations — the MCP server imports from them.

| Script | Purpose |
|---|---|
| `compile_router.py` | Compile router, taxonomy, skills, and styles into a single JSON file |
| `compile_colours.py` | Generate folder colour CSS and graph colour groups |
| `build_index.py` | Build the BM25 retrieval index for search |
| `list_artefacts.py` | Enumerate vault artefacts and resources (library module used by MCP) |
| `search_index.py` | Search the BM25 index from the command line |
| `read.py` | Query compiled router resources (artefacts, triggers, styles, templates, skills, etc.) |
| `create.py` | Create a new artefact with template/naming resolution |
| `edit.py` | Edit artefacts via explicit `target + selector + scope`; the importable helpers also back editable `_Config/` resources |
| `rename.py` | Rename a file with automatic wikilink updates; refuses existing-destination collisions before touching links |
| `repair.py` | Explicit infrastructure repair entry point. Bootstraps from any compatible Python 3.12+ launcher, converges into the vault-local `.venv`, and then repairs one named scope: `mcp`, `router`, `index`, or `registry`. |
| `session.py` | Build the canonical session model and refresh `.brain/local/session.md` |
| `obsidian_cli.py` | IPC client for native Obsidian CLI (library module used by MCP) |
| `shape_printable.py` | Create printable + render PDF |
| `shape_presentation.py` | Create presentation + render PDF + launch preview |
| `start_shaping.py` | Bootstrap a shaping session for an existing artefact |
| `upgrade.py` | Canonical brain-core upgrade entry point from a source directory, including versioned pre-compile compatibility patches, binary-safe rollback snapshots for `.brain/` / `_Config/`, post-compile migration rollback of touched artefact roots, applied-migration tracking in `.brain/local/`, self-contained atomic writes, and best-effort vault-local MCP dependency sync when requirements change |
| `vault_registry.py` | User-home registry of installed Brain vaults |
| `workspace_registry.py` | Workspace key→path resolution and registration |
| `init.py` | Set up Claude Code and/or Codex to use this vault's MCP server; requires a Python 3.12+ runtime with the `mcp` package, folder-scoped installs also scaffold `.brain/local/workspace.yaml` (migrates legacy `.brain/workspace.yaml` automatically), and direct config writes stay atomic with unique sibling temp files. Project scope outranks user scope once the client activates the project entry: approve via `/mcp` in Claude, or trust/enable the project-scoped server in Codex. |
| `check.py` | Structural compliance checker — validates naming, frontmatter, month folders, archives, status values, and now prints exact `repair.py` commands when it detects repairable router/MCP/local-registry drift |
| `migrate_naming.py` | Migrate vault filenames from old aggressive slugs to generous naming conventions |
| `fix_links.py` | Auto-repair broken wikilinks using naming convention heuristics |
| `sync_definitions.py` | Sync artefact library definitions to vault `_Config/` using tracked source hashes plus markdown-aware comparison for `.md` files, so harmless pipe-table rewrites do not surface as conflicts |
| `config.py` | Vault configuration loader (three-layer merge: template → vault → local) |
| `generate_key.py` | Generate operator key + SHA-256 hash for pasting into `config.yaml` |

### Compliance Checks

Two complementary tools:

**`check.py`** (structural compliance) — deep scan that validates all files against the compiled router: naming patterns, frontmatter type and required fields, month folders for temporal files, archive metadata, status values, and broken or ambiguous wikilinks (including YAML frontmatter property-links like `parent: "[[foo]]"`; wikilinks inside code, HTML comments, `$$` math, and raw HTML blocks are treated as literal text). When router, MCP, or local workspace-registry drift is detected, normal output prints the exact `repair.py` command to run and JSON/compliance output includes structured `repair` metadata. Run on demand or during maintenance. Flags: `--json` (structured output), `--actionable` (fix suggestions), `--severity <level>` (filter). Also available via MCP: `brain_read(resource="compliance")`.

```bash
python3 .brain-core/scripts/check.py                    # human-readable
python3 .brain-core/scripts/check.py --json --actionable # structured with fixes
python3 .brain-core/scripts/check.py --vault /path/to/vault  # check a specific vault
```

**`repair.py`** (infrastructure recovery) — explicit repair surface for current-vault operational drift. It bootstraps from any compatible Python 3.12+ launcher, repairs the vault-local managed runtime when needed, then hands off into that `.venv` for packageful work. First-cut scopes are `mcp`, `router`, `index`, and `registry`.

```bash
python3.12 .brain-core/scripts/repair.py mcp
python3.12 .brain-core/scripts/repair.py router --dry-run
python3.12 .brain-core/scripts/repair.py index
python3.12 .brain-core/scripts/repair.py registry
```

If you are unsure which scope applies, run `check.py` first. For most broken
tooling cases, `repair.py mcp` is the right recovery path. It repairs installed
current-vault project MCP state for the clients already present; it does not
create a first-time project registration.

**`compliance_check.py`** (session hygiene) — quick checks like "did you log today?" and "are backups fresh?" Run after each work block.

### Fallback Chain

When full tooling isn't available, agents degrade gracefully:

1. **MCP tools** — `brain_session` returns the canonical session model as JSON
2. **Generated markdown bootstrap** — read `.brain-core/index.md`, then `.brain/local/session.md`
3. **Degraded fallback** — read `.brain-core/index.md`, then `.brain-core/md-bootstrap.md`, then raw config files as directed

---

## Colour System

Brain auto-generates folder colours to visually distinguish types in the Obsidian sidebar. Colours are computed by `compile_colours.py` and regenerated automatically when you run `python3 .brain-core/scripts/compile_router.py`.

### How Colours Are Assigned

- **Living artefact folders** — hues distributed evenly across available colour space (HSL with S=57%, L=72%)
- **Temporal child folders** — independent hue distribution, then blended 35% towards rose for a warm, cohesive tint
- **System folders** — fixed reserved colours: Config = Violet, Temporal = Rose, Plugins = Orchid, Assets/Archives = Slate

### Algorithm

Hues are distributed across 240° of available space (360° minus four 30° exclusion zones reserved for system colours). Types are sorted alphabetically, so colours are deterministic — same type list always produces the same colours. Adding a new type shifts existing colours by a small, predictable amount.

**System colour exclusion zones:** Slate (195–225°), Violet (255–285°), Orchid (285–315°), Rose (325–355°).

### Temporal Blend Formula

`result = base + (rose - base) × 0.35` per RGB channel.

This gives temporal folders a warm, cohesive tint while keeping each type visually distinct.

### Graph View Colours

The same colour assignments are applied to Obsidian's graph view. Graph colours are written as `colorGroups` entries in `.obsidian/graph.json`. The graph view is canvas-based (CSS doesn't apply), so colours use a `path:` query with a decimal RGB integer. System folders, living folders, temporal children, and archive folders all appear in the graph with matching colours.

The `graph.json` merge preserves all existing graph settings (scale, forces, display options) — only `colorGroups` is replaced on each compile.

### File Locations

- **Sidebar colours:** `.obsidian/snippets/brain-folder-colours.css` — auto-generated CSS snippet
- **Graph colours:** `.obsidian/graph.json` `colorGroups` — auto-generated, other settings preserved

Both files are auto-generated — do not edit colour entries manually. Regenerate with `python3 .brain-core/scripts/compile_router.py` or `python3 .brain-core/scripts/compile_colours.py`. Algorithm details and CSS selector templates are in `.brain-core/colours.md`.

---

## Writing Style

Configured in `_Config/Styles/writing.md`. Default conventions:

**Universal:** Australian English.

**External audience** (tagged `audience/external` or user-requested):

1. Point first, support underneath
2. Vary sentence length — short punches, long builds momentum, mix keeps prose alive
3. Short, familiar, specific words ("use" not "utilise")
4. Strong verbs; cut the adverb
5. No em dashes (use commas, colons, semicolons)
6. Write how a sharp person talks
7. Avoid inflated vocabulary and filler
8. Show, don't tell — concrete detail persuades
9. Every sentence earns its place; stop when done
10. Lead each sentence with the important thing; push setup to the end
