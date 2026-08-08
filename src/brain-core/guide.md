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
| Release | `Releases/{scope}/` | `{Title}.md` pre-ship; `{Version} - {Title}.md` when `shipped` |
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

Every file needs frontmatter. Temporal artefacts need at least `type` and `tags`; living artefacts also need a `key` (see [[.brain-core/standards/keys]] for the key contract):

```yaml
---
type: living/note
key: auth-redesign
tags:
  - topic-tag
---
```

When a living artefact owns cross-type children, its canonical key also projects to a folder `scope`. For example, `parent: project/brain` gives scope `project~brain`, so a release owned by that project files under `Releases/project~brain/`. Parented temporal artefacts use the same owner chain before their month folder, so a report owned by that project files under `_Temporal/Reports/project~brain/yyyy-mm/`.

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

- **Designs:** `proposed` → `shaping` → `ready` → `active` → `implemented` | `deprecated` | `parked`
- **Documentation:** `new` → `shaping` → `ready` → `active` → `deprecated`
- **Ideas:** `new` → `shaping` → `ready` → `adopted` | `deprecated` | `parked`
- **Idea Logs:** `open` → `graduated` | `deprecated` | `parked`
- **People:** `active` → `shaping` → `parked` | `deprecated`
- **Releases:** `planned` → `active` → `shipped` | `deprecated`
- **Tasks:** `open` → `shaping` → `in-progress` → `done` | `parked` | `deprecated`
- **Writing:** `draft` → `editing` → `review` → `published` | `deprecated` | `parked`
- **Plans:** `draft` → `shaping` → `approved` → `implementing` → `completed` | `deprecated` | `parked`
- **Shapeable temporal artefacts:** `shaping` → `ready` (optional status introduced when shaping begins)

Closure vocab is unified across types: a type-specific success terminal (e.g. `implemented`, `published`, `done`), a single `deprecated` abandonment terminal (with reason in a `> [!info] Deprecated — <reason>` callout), and an optional `parked` non-terminal pause.

Not every type has status. Wiki and Notes are evergreen; temporal types without a lifecycle remain so as well.

## Linking

Use **basename-only** wikilinks: `[[My Page]]`, not `[[Wiki/My Page]]`. Basename links survive folder moves and archiving. Path-qualified links break when files move into subfolders. Avoid aliased wikilinks inside markdown tables (`[[Target|Alias]]`) because the alias separator is also a table column separator; Brain drops those aliases during table-row link rewrites and `check.py` warns on existing table aliases.

Only wikilink to targets that already exist. If the artefact doesn't exist yet, write plain text — create the artefact first, then link. `brain_create` and `brain_edit` warn about broken or resolvable wikilinks in every write, and `brain_action(request={"action": "fix-links", "params": {...}})` repairs them one file or vault-wide at a time. Full rules are in the [wikilinks standard](standards/wikilinks.md); resolution mechanics are in the [linking standard](standards/linking.md).

`brain_create` auto-disambiguates basename collisions across type folders by appending the type key (e.g. `My Page (idea).md`).

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

For normal publishing, call `brain_set_status(path="...", status="published")`.
The lifecycle handler sets a missing `publisheddate` to today, applies the
date-prefixed rename, and moves the file into `Writing/+Published/`.

To use a different publication date, first call
`brain_set_naming_field(path="...", field="publisheddate", value="YYYY-MM-DD")`,
then set the published status.

## Terminal Status and Archiving

Living artefacts that reach a terminal status move to a `+Status/` folder within their type directory. These files remain searchable and indexed — no rename, no `archiveddate`. Every status change (terminal or not) auto-sets `statusdate: YYYY-MM-DD` in frontmatter. Each type defines its own terminal statuses and `+Status` folders:

- **Designs:** `+Implemented/`, `+Deprecated/`
- **Documentation:** `+Deprecated/`
- **Ideas:** `+Adopted/`, `+Deprecated/`
- **Releases:** `+Shipped/`, `+Deprecated/`
- **Tasks:** `+Done/`, `+Deprecated/`
- **Workspaces:** `+Completed/`, `+Deprecated/`
- **Writing:** `+Published/`, `+Deprecated/`

`+Deprecated/` is the unified abandonment folder; the reason (superseded, rejected, cancelled, retired, duplicate) is captured in a `> [!info] Deprecated — <reason>` callout in the artefact body.

`_Archive/` is reserved for deliberate removal — a "soft delete" that takes files completely out of the active vault namespace (index, search, and all normal operations). Use `brain_move(op="archive", path="...", recursive=true)` to archive an ownership subtree and `brain_move(op="unarchive", path="...", recursive=true)` to restore one; omit `recursive` for a single artefact. Use `brain_list(resource="archive")` to list archived files, `brain_read(resource="archive", name="...")` to read a specific one. Full details are in the [archiving standard](standards/archiving.md).

## Extending Your Vault

Your vault ships with a starter set of types. The artefact library (`.brain-core/artefact-library/`) has more you can install, or you can create your own.

Before adding a type, check:
- No existing type fits (even generously)
- You'll create multiple files of this type (not just one)
- It needs different naming, frontmatter, or lifecycle rules

To add a living type: create the root folder, create the taxonomy file in `_Config/Taxonomy/Living/`, optionally add a router trigger, then run `python3 .brain-core/scripts/compile_router.py` — colours are auto-generated.

To add a temporal type: create the folder under `_Temporal/`, create taxonomy in `_Config/Taxonomy/Temporal/`, then run `python3 .brain-core/scripts/compile_router.py` — rose-blended colours are auto-generated.

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

To bind a workspace and optionally configure Claude Code and Codex to use this vault's MCP server:

```bash
# Bind the current directory to this Brain
cd /my/project && python3 /path/to/vault/.brain-core/scripts/setup.py workspace . --vault /path/to/vault

# Bind only, through the targeted CLI surface
brain configure workspace binding --vault /path/to/vault --path /my/project --slug my-project

# Configure project-scoped MCP transport for both clients
python3 /path/to/vault/.brain-core/scripts/configure.py mcp --vault /path/to/vault --workspace /my/project --client all

# Claude-only local scope (gitignored; Codex has no local scope)
python3 /path/to/vault/.brain-core/scripts/configure.py mcp --vault /path/to/vault --workspace /my/project --client claude --local

# Register as your default brain for all projects for both clients
python3 /path/to/vault/.brain-core/scripts/configure.py mcp --vault /path/to/vault --user --client all

# Install the active-Brain shaping discovery adapter for both clients
python3 /path/to/vault/.brain-core/scripts/configure.py agent-skills --vault /path/to/vault --client all
```

`setup.py workspace` and `configure.py mcp` are the public setup and transport surfaces. Older automation that used the retired `init.py` compatibility shell should move to the targeted `setup.py` / `configure.py` command for the concern it owns.

For project scope, registration is not the whole story. Claude still needs the project's `.mcp.json` entry approved via `/mcp`, and Codex still needs the project trusted with the project-scoped `brain` MCP enabled. Once that project-scoped entry is active, it outranks the user-scoped one. Until then, either client may keep routing `mcp__brain__*` calls to a user-scoped `brain`.

The optional shaping adapter is a stable discovery shim, not a copied workflow.
At invocation time it calls `brain_session` and loads the active Brain's
`.brain-core/skills/shaping/SKILL.md` through `brain_read`. Re-run the command to
update a Brain-owned adapter; use `--replace` only after reviewing an existing
unmanaged skill, which is archived first. Restart the affected clients after a
change.

## Tooling

If your vault has the Brain MCP server running, you get twenty-two focused tools:

- **brain_init** — additive bootstrap/orientation snapshot with readiness, warmup status, and optional cheap debug output. `warmup=true` ensures background warmup is underway, then returns immediately.
- **brain_session** — bootstrap an agent session in one call (static core bootstrap content, structured core-doc references with explicit `brain_read(resource="file", ...)` load instructions, local workspace-configuration CLI guidance, always-rules, preferences, gotchas, triggers, artefact types, environment); also refreshes `.brain/local/session.md`
- **brain_read** — read a specific resource by name: artefact content (by relative path, basename, or display name — resolves like wikilinks), type definitions, triggers, styles, templates, skills, plugins, memories, or workspaces. Name is required for collection resources; use brain_list to enumerate collections.
- **brain_search** — find files by query, type, tag, status, and retrieval mode (`lexical`, `semantic`, `hybrid`). Omitted mode prefers hybrid when semantic retrieval is enabled and usable; lexical may use Obsidian CLI, while non-artefact collections stay lexical-only.
- **brain_list** — enumerate resources exhaustively with honest creation/modified filters and stable cursor pagination.
- **brain_outline / brain_check** — discover exact edit selectors and inspect structured Doctor findings without mutation.
- **brain_stage / brain_discard_stage** — hold large bodies under bounded retry-safe handles or release unused handles.
- **brain_upload_attachment** — add base64-encoded non-markdown files beneath a required living-artefact or standalone attachment scope and receive resolved destination metadata, the vault path, and Obsidian embed.
- **brain_create** — create a new artefact or _Config/ resource (additive, safe to auto-approve). Its resource-discriminated request has exact artefact versus skill, memory, style, and template variants. Inline bodies use `"content": {"source": "inline", "content": "..."}`; `body` and `kind` are not aliases.
- **brain_edit** — explicit structural edits and exact-text replacement. Generic edits reject lifecycle-owned metadata; use **brain_reparent**, **brain_set_status**, **brain_set_key**, and **brain_set_naming_field** so derived paths, links, tags, descendants, and timestamps remain consistent. `brain_reparent` requires `parent`; pass null explicitly to clear ownership.
- **brain_define** — operator-only, guarded authoring for coherent type bundles, triggers, and plugins. Type replacement checks both taxonomy and template hashes; plugin replacement checks its definition hash; trigger changes identify exact current entries.
- **brain_move** — rename, convert, archive, or unarchive artefacts via a flat top-level move contract
- **brain_action** — schema-discriminated workflow bucket for delete, reparent-children, `shape` session mechanics, printable/presentation shaping helpers, and fix-links
- **brain_classify / brain_resolve / brain_ingest** — split experimental content tools; read-only classification/resolution no longer grants ingest permission.

The MCP server logs to `.brain/local/mcp-server.log` — startup diagnostics, tool call tracing, and errors. Set `BRAIN_LOG_LEVEL=DEBUG` for tool argument details.

For structural compliance (naming, frontmatter, archives), run `python3 .brain-core/scripts/check.py`.

Without MCP, read `.brain-core/index.md` first. The scripts in `.brain-core/scripts/` remain authoritative and the `brain` CLI dispatches to them, including `outline.py`, `stage.py`, `discard_stage.py`, `upload_attachment.py`, and `lifecycle.py`. Direct mutation scripts share the vault mutation lock; artefact mutations also refuse stale compiled router state.

## Further Reading

- [Workflows](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/workflows.md) — day-to-day usage patterns with examples
- [Reference](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/user-reference.md) — every artefact type, configuration point, and system in detail
- `.brain-core/standards/extending/` — extension procedures (developer reference)
- `.brain-core/index.md` — bootstrap entry point for MCP, generated markdown, and degraded fallback paths
