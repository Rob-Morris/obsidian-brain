# Brain Quick-Start Guide

Brain is a system for organising your Obsidian vault. It gives every file a home, keeps things findable, and grows with you.

This guide covers what you need to know day-to-day. For the full reference, see the [Brain User Guide](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/getting-started.md).

## Your Vault at a Glance

```
Daily Notes/              ← living artefacts (root folders)
Designs/
Documentation/
Ideas/
Notes/
People/
Projects/
Releases/
Tasks/
Workspaces/
Writing/
...
_Temporal/                ← temporal artefacts (dated, point-in-time)
  Captures/2026-03/
  Cookies/2026-03/
  Decision Logs/2026-03/
  Logs/2026-03/
  Observations/2026-03/
  Plans/2026-03/
  Research/2026-03/
  ...
_Assets/                  ← images, PDFs, non-markdown files
_Config/                  ← router, taxonomy, styles, templates, memories, preferences
_Plugins/                 ← external tool integrations
_Workspaces/              ← workspace data (infrastructure)
.brain-core/              ← this system (versioned, upgradeable)
```

**Living artefacts** sit in root-level folders. They evolve over time — the current version is what matters. Designs, ideas, projects, writing.

**Temporal artefacts** sit under `_Temporal/`. They're snapshots bound to a moment — logs, plans, transcripts, research. Organised in monthly subfolders (`yyyy-mm/`).

**Everything else** (`_Assets/`, `_Config/`, `_Plugins/`, `.brain-core/`) is infrastructure.

## The Golden Rule

Every file belongs in a typed folder. Nothing goes in the vault root. If your content doesn't fit an existing type, add a new one first (see [Extending Your Vault](#extending-your-vault)).

## Day-to-Day Workflow

### Creating Files

Pick the artefact type that fits, create the file in the right folder with the right naming pattern:

| Type | Where | Naming |
|---|---|---|
| Daily note | `Daily Notes/` | `yyyy-mm-dd ddd.md` |
| Design | `Designs/` | `{Title}.md` |
| Documentation | `Documentation/` | `{Title}.md` |
| Idea | `Ideas/` | `{Title}.md` |
| Note | `Notes/` | `yyyymmdd - {Title}.md` |
| Person | `People/` | `{Title}.md` |
| Project | `Projects/` | `{Title}.md` |
| Release | `Releases/{Project}/` | `{Version} - {Title}.md` |
| Task | `Tasks/` | `{Title}.md` |
| Workspace | `Workspaces/` | `{Title}.md` |
| Writing | `Writing/` | `{Title}.md` |
| Bug log | `_Temporal/Bug Logs/yyyy-mm/` | `yyyymmdd-bug~{Title}.md` |
| Capture | `_Temporal/Captures/yyyy-mm/` | `yyyymmdd-capture~{Title}.md` |
| Cookie | `_Temporal/Cookies/yyyy-mm/` | `yyyymmdd-cookie~{Title}.md` |
| Ingestion | `_Temporal/Ingestions/yyyy-mm/` | `yyyymmdd-ingestion~{Title}.md` |
| Decision log | `_Temporal/Decision Logs/yyyy-mm/` | `yyyymmdd-decision~{Title}.md` |
| Friction log | `_Temporal/Friction Logs/yyyy-mm/` | `yyyymmdd-friction~{Title}.md` |
| Log entry | `_Temporal/Logs/yyyy-mm/` | `yyyymmdd-log.md` |
| Mockup | `_Temporal/Mockups/yyyy-mm/` | `yyyymmdd-mockup~{Title}.md` |
| Observation | `_Temporal/Observations/yyyy-mm/` | `yyyymmdd-observation~{Title}.md` |
| Plan | `_Temporal/Plans/yyyy-mm/` | `yyyymmdd-plan~{Title}.md` |
| Presentation | `_Temporal/Presentations/yyyy-mm/` | `yyyymmdd-presentation~{Title}.md` |
| Report | `_Temporal/Reports/yyyy-mm/` | `yyyymmdd-report~{Title}.md` |
| Research | `_Temporal/Research/yyyy-mm/` | `yyyymmdd-research~{Title}.md` |
| Shaping transcript | `_Temporal/Shaping Transcripts/yyyy-mm/` | `yyyymmdd-shaping-transcript~{Title}.md` |
| Snippet | `_Temporal/Snippets/yyyy-mm/` | `yyyymmdd-snippet~{Title}.md` |
| Thought | `_Temporal/Thoughts/yyyy-mm/` | `yyyymmdd-thought~{Title}.md` |
| Transcript | `_Temporal/Transcripts/yyyy-mm/` | `yyyymmdd-transcript~{Title}.md` |

Additional types available from the artefact library: Wiki, Journals, Zettelkasten (living); Idea Logs, Journal Entries, Printables (temporal).

Every file needs frontmatter with at least `type` and `tags`:

```yaml
---
type: living/note
tags:
  - topic-tag
---
```

### Logging

After meaningful work, append a timestamped entry to today's log (`_Temporal/Logs/yyyy-mm/yyyymmdd-log.md`). Keep entries brief — one or two sentences with a timestamp:

```
14:30 Refactored the auth middleware. See [[auth-redesign]].
```

### Daily Notes

At the end of the day, create a daily note that distils the log into an overview: a task checklist and short topic summaries.

### Capturing Ideas

Low bar, high speed. Use **Idea Logs** (`_Temporal/Idea Logs/`) for raw captures. When an idea gains substance, spin it out to a living **Idea** in `Ideas/`. When it's ready for structured work, graduate it to a **Design** in `Designs/`.

## Frontmatter Basics

**Frontmatter** holds queryable state: `type`, `tags`, `status`, dates.

**Body text** holds navigation: wikilinks, origin links, transcript references.

Why the split? Obsidian's backlinks and graph view work from body wikilinks. Search indexes body text. Keep links in the body where they're visible and functional.

### Status

Some types have a lifecycle. Status values are defined per type:

- **Designs:** `proposed` → `shaping` → `ready` → `active` → `implemented` | `superseded` | `parked` | `rejected`
- **Documentation:** `new` → `shaping` → `ready` → `active` → `deprecated`
- **Ideas:** `new` → `shaping` → `ready` → `adopted` | `parked`
- **Idea Logs:** `open` → `adopted` | `parked`
- **People:** `active` → `shaping` → `parked`
- **Releases:** `planned` → `active` → `shipped` | `cancelled`
- **Tasks:** `open` → `shaping` → `in-progress` → `done` | `blocked`
- **Writing:** `draft` → `editing` → `review` → `published` | `parked`
- **Plans:** `draft` → `shaping` → `approved` → `implementing` → `completed`

Not every type has status. Wiki, Notes, and most temporal types are evergreen.

## Linking

Use **basename-only** wikilinks: `[[My Page]]`, not `[[Wiki/My Page]]`. Basename links survive folder moves and archiving. Path-qualified links break when files move into subfolders.

`brain_create` auto-disambiguates basename collisions across type folders by appending the type key (e.g. `My Page (idea).md`). Full details are in the [linking standard](standards/linking.md).

## Provenance

When one artefact spins out of another, link them. Full details are in the [provenance standard](standards/provenance.md).

**On the new artefact:** `**Origin:** [[source-file|description]] (yyyy-mm-dd)`

**On the source:** Add a callout at the top of the body:
```markdown
> [!info] Spun out to design
> [[new-design]] — 2026-03-15
```

## Publishing (Writing)

Published writing moves to `Writing/+Published/` with date-prefixed filenames. Full details in the writing taxonomy.

1. Set `status: published` and add `publisheddate: YYYY-MM-DD`
2. Rename to `yyyymmdd-{Title}.md` via `brain_action("rename")`
3. Move to `Writing/+Published/`

## Terminal Status and Archiving

Living artefacts that reach a terminal status move to a `+Status/` folder within their type directory. These files remain searchable and indexed — no rename, no `archiveddate`. Every status change (terminal or not) auto-sets `statusdate: YYYY-MM-DD` in frontmatter. Each type defines its own terminal statuses and `+Status` folders:

- **Designs:** `+Implemented/`, `+Rejected/`
- **Ideas:** `+Adopted/`
- **Releases:** `+Shipped/`, `+Cancelled/`
- **Tasks:** `+Done/`
- **Workspaces:** `+Completed/`
- **Writing:** `+Published/`

`_Archive/` is reserved for deliberate removal — a "soft delete" that takes files completely out of the active vault namespace (index, search, and all normal operations). Use `brain_action("archive")` to archive and `brain_action("unarchive")` to restore. Use `brain_list(resource="archive")` to list archived files, `brain_read(resource="archive", name="...")` to read a specific one. Full details are in the [archiving standard](standards/archiving.md).

## Extending Your Vault

Your vault ships with a starter set of types. The artefact library (`.brain-core/artefact-library/`) has more you can install, or you can create your own.

Before adding a type, check:
- No existing type fits (even generously)
- You'll create multiple files of this type (not just one)
- It needs different naming, frontmatter, or lifecycle rules

To add a living type: create the root folder, create the taxonomy file in `_Config/Taxonomy/Living/`, optionally add a router trigger, then run `brain_action("compile")` — colours are auto-generated.

To add a temporal type: create the folder under `_Temporal/`, create taxonomy in `_Config/Taxonomy/Temporal/`, then run `brain_action("compile")` — rose-blended colours are auto-generated.

Full details in the [Template Library Guide — Extending Your Vault](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/template-library-guide.md).

## Configuration

| What | Where |
|---|---|
| Workflow triggers | `_Config/router.md` |
| Type definitions | `_Config/Taxonomy/` |
| Templates | `_Config/Templates/` |
| Writing style | `_Config/Styles/writing.md` |
| Folder colours | `_Config/Styles/obsidian.md` |
| Memories | `_Config/Memories/` |
| Your preferences | `_Config/User/preferences-always.md` |
| Known gotchas | `_Config/User/gotchas.md` |

## Setup

To configure Claude Code and Codex to use this vault's MCP server:

```bash
# Configure current directory for both clients
cd /my/project && python3 /path/to/vault/.brain-core/scripts/init.py

# Claude-only local scope (gitignored; Codex has no local scope)
python3 /path/to/vault/.brain-core/scripts/init.py --client claude --local

# Register as your default brain for all projects for both clients
python3 /path/to/vault/.brain-core/scripts/init.py --user --client all

# Configure a specific folder for both clients without cd-ing into it
python3 /path/to/vault/.brain-core/scripts/init.py --project /path/to/project --client all
```

For project scope, registration is not the whole story. Claude still needs the project's `.mcp.json` entry approved via `/mcp`, and Codex still needs the project trusted with the project-scoped `brain` MCP enabled. Once that project-scoped entry is active, it outranks the user-scoped one. Until then, either client may keep routing `mcp__brain__*` calls to a user-scoped `brain`.

## Tooling

If your vault has the Brain MCP server running, you get eight tools:

- **brain_session** — bootstrap an agent session in one call (static core bootstrap content, structured core-doc references with explicit `brain_read(resource="file", ...)` load instructions, always-rules, preferences, gotchas, triggers, artefact types, environment); also refreshes `.brain/local/session.md`
- **brain_read** — read a specific resource by name: artefact content (by relative path, basename, or display name — resolves like wikilinks), type definitions, triggers, styles, templates, skills, plugins, memories, workspaces, or compliance checks. Name is required for collection resources; use brain_list to enumerate collections.
- **brain_search** — find files by query, type, or tag (relevance-ranked, BM25). Also searches non-artefact collections (skills, triggers, styles, memories, plugins) via text matching (use `resource` parameter).
- **brain_list** — enumerate resources exhaustively. For artefacts: filter by type, date range, or tag (not relevance-ranked; use when completeness matters). Also lists non-artefact collections: skills, triggers, styles, plugins, memories, templates, types, workspaces, archives (use `resource` parameter).
- **brain_create** — create a new artefact or _Config/ resource (additive, safe to auto-approve). Use `resource` parameter for skill, memory, style, or template creation.
- **brain_edit** — edit, append, prepend, or delete_section on an existing artefact or _Config/ resource (by path/basename for artefacts, by name for skill/memory/style/template); optional `target` parameter for section-level operations (required for delete_section), including `:entire_body` for explicit whole-body targeting, `:body_preamble` for the leading body content before the first targetable section (heading or callout) during `edit`, and `:section:...` for explicit whole-section replacement during `edit`; legacy `:body` is rejected; plain targeted `edit` remains content-only and strips one exact copied outer wrapper while rejecting same-level or higher structural replacements that should use `:section:`; frontmatter merge strategy follows the operation verb (edit overwrites, append/prepend extend lists, null deletes field); artefacts auto-move to `+Status/` folders on terminal status change and back out on revive; config resources skip auto-move and modified injection
- **brain_action** — compile the router, build the search index, rename, delete, convert files, fix broken links, sync definitions, register/unregister workspaces, start shaping sessions
- **brain_process** — classify content against artefact types, resolve duplicates, or run the full ingest pipeline (classify → resolve → create/update)

The MCP server logs to `.brain/local/mcp-server.log` — startup diagnostics, tool call tracing, and errors. Set `BRAIN_LOG_LEVEL=DEBUG` for tool argument details.

For structural compliance (naming, frontmatter, archives), run `python3 .brain-core/scripts/check.py` or use `brain_read(resource="compliance")` via MCP.

Without MCP, read `.brain-core/index.md` first. It routes to the generated markdown session mirror at `.brain/local/session.md` when available, or to `.brain-core/md-bootstrap.md` for the degraded raw-file fallback. The scripts in `.brain-core/scripts/` remain available directly (`read.py`, `search_index.py`, `create.py`, `edit.py`, `rename.py`, `compile_router.py`, `check.py`, `fix_links.py`, `sync_definitions.py`, `workspace_registry.py`, `migrate_naming.py`, `process.py`, `session.py`, `build_index.py`, `shape_presentation.py`, `start_shaping.py`, `config.py`, `generate_key.py`).

## Further Reading

- [Workflows](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/workflows.md) — day-to-day usage patterns with examples
- [Reference](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user-reference.md) — every artefact type, configuration point, and system in detail
- `.brain-core/standards/extending/` — extension procedures (developer reference)
- `.brain-core/index.md` — bootstrap entry point for MCP, generated markdown, and degraded fallback paths
