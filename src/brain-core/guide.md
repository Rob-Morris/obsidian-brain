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
  Captures/
  Cookies/
  Decision Logs/
  Logs/
  Observations/
  Plans/
  Research/
  ...
_Assets/                  ← images, PDFs, non-markdown files
_Config/                  ← router, taxonomy, styles, templates, memories, preferences
_Plugins/                 ← external tool integrations
_Workspaces/              ← workspace data (infrastructure)
.brain-core/              ← this system (versioned, upgradeable)
```

**Living artefacts** sit in root-level folders. They evolve over time — the current version is what matters. Designs, ideas, projects, writing.

**Temporal artefacts** sit under `_Temporal/`. They're snapshots bound to a moment — logs, plans, transcripts, research. They file flat under their type folder, ordered by their dated filenames.

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
| Note | `Notes/` | `{Title}.md` |
| Person | `People/` | `{Title}.md` |
| Project | `Projects/` | `{Title}.md` |
| Release | `Releases/` | `{Title}.md` pre-ship; `{Version} - {Title}.md` when `shipped` |
| Task | `Tasks/` | `{Title}.md` |
| Workspace | `Workspaces/` | `{Title}.md` |
| Writing | `Writing/` | `{Title}.md` |
| Bug log | `_Temporal/Bug Logs/` | `yyyymmdd-bug~{Title}.md` |
| Capture | `_Temporal/Captures/` | `yyyymmdd-capture~{Title}.md` |
| Cookie | `_Temporal/Cookies/` | `yyyymmdd-cookie~{Title}.md` |
| Ingestion | `_Temporal/Ingestions/` | `yyyymmdd-ingestion~{Title}.md` |
| Decision log | `_Temporal/Decision Logs/` | `yyyymmdd-decision~{Title}.md` |
| Friction log | `_Temporal/Friction Logs/` | `yyyymmdd-friction~{Title}.md` |
| Log entry | `_Temporal/Logs/` | `yyyymmdd-log.md` |
| Mockup | `_Temporal/Mockups/` | `yyyymmdd-mockup~{Title}.md` |
| Observation | `_Temporal/Observations/` | `yyyymmdd-observation~{Title}.md` |
| Plan | `_Temporal/Plans/` | `yyyymmdd-plan~{Title}.md` |
| Presentation | `_Temporal/Presentations/` | `yyyymmdd-presentation~{Title}.md` |
| Report | `_Temporal/Reports/` | `yyyymmdd-report~{Title}.md` |
| Research | `_Temporal/Research/` | `yyyymmdd-research~{Title}.md` |
| Shaping transcript | `_Temporal/Shaping Transcripts/` | `yyyymmdd-shaping-transcript~{Title}.md` |
| Snippet | `_Temporal/Snippets/` | `yyyymmdd-snippet~{Title}.md` |
| Thought | `_Temporal/Thoughts/` | `yyyymmdd-thought~{Title}.md` |
| Transcript | `_Temporal/Transcripts/` | `yyyymmdd-transcript~{Title}.md` |

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

When a living artefact owns cross-type children, its canonical key also projects to a folder `scope`. For example, `parent: project/brain` gives scope `project~brain`, so a release owned by that project files under `Releases/project~brain/`. Parented temporal artefacts file flat under the same owner chain, so a report owned by that project files under `_Temporal/Reports/project~brain/`.

### Logging

After meaningful work, append a timestamped entry to today's log (`_Temporal/Logs/yyyymmdd-log.md`). Keep entries brief — one or two sentences with a timestamp:

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
- **People:** `active` | `parked` | explicit `shaping` → `deprecated` (discovery preserves the current non-terminal status)
- **Releases:** `planned` → `active` → `shipped` | `deprecated`
- **Tasks:** `open` → `shaping` → `in-progress` → `done` | `parked` | `deprecated`
- **Writing:** `draft` → `editing` → `review` → `published` | `deprecated` | `parked`
- **Plans:** `draft` → `shaping` → `approved` → `implementing` → `completed` | `deprecated` | `parked`
- **Shapeable temporal artefacts:** `shaping` → `ready` (optional status introduced when shaping begins)

Closure vocab is unified across types: a type-specific success terminal (e.g. `implemented`, `published`, `done`), a single `deprecated` abandonment terminal (with reason in a `> [!info] Deprecated — <reason>` callout), and an optional `parked` non-terminal pause.

Not every type has status. Wiki and Notes are evergreen; temporal types without a lifecycle remain so as well.

## Linking

Use **basename-only** wikilinks: `[[My Page]]`, not `[[Wiki/My Page]]`. Basename links survive folder moves and archiving. Path-qualified links break when files move into subfolders. Avoid aliased wikilinks inside markdown tables (`[[Target|Alias]]`) because the alias separator is also a table column separator; Brain drops those aliases during table-row link rewrites and `check.py` warns on existing table aliases.

Only wikilink to targets that already exist. If the artefact doesn't exist yet, write plain text — create the artefact first, then link. `artefact.create` and the granular artefact mutations warn about broken or resolvable wikilinks, and `links.fix` repairs them one file or vault-wide at a time. Full rules are in the [wikilinks standard](standards/wikilinks.md); resolution mechanics are in the [linking standard](standards/linking.md).

`artefact.create` auto-disambiguates basename collisions across type folders by appending the type key (e.g. `My Page (idea).md`).

For existing Markdown, read the document first and pass its returned revision to
the mutation: `document.write-body` changes the complete body, `document.replace-text`
replaces literal text, `document.structured-edit` targets Markdown structures, and
`document.update-frontmatter` changes metadata. A stale revision is rejected so
an agent cannot silently overwrite a newer human or agent edit.

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

For normal publishing, call `artefact.set-status(path="...", status="published")`.
The lifecycle handler sets a missing `publisheddate` to today, applies the
date-prefixed rename, and moves the file into `Writing/+Published/`.

To use a different publication date, first call
`artefact.set-naming-field(path="...", field="publisheddate", value="YYYY-MM-DD")`,
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

`_Archive/` is reserved for deliberate removal — a "soft delete" that takes files completely out of the active vault namespace. Use `artefact.archive` and `artefact.unarchive` for transitions, `artefact.list(location="archived")` to list the archive namespace, and `artefact.read(location="archived")` to read from it. Full details are in the [archiving standard](standards/archiving.md).

## Extending Your Vault

Your vault ships with a starter set of types. The artefact library (`.brain-core/artefact-library/`) has more you can install, or you can create your own.

Before adding a type, check:
- No existing type fits (even generously)
- You'll create multiple files of this type (not just one)
- It needs different naming, frontmatter, or lifecycle rules

To add a living type: use `type.create` with the reviewed taxonomy and template, optionally add a `trigger.create`, then run `runtime.refresh-router` — colours are auto-generated.

To add a temporal type: use `type.create` with classification `temporal`, then run `runtime.refresh-router` — rose-blended colours are auto-generated.

Full details in the [Template Library Guide — Extending Your Vault](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/template-library-guide.md).

## Configuration

| What | Where |
|---|---|
| Workflow triggers | `_Config/router.md` |
| Type definitions | `_Config/Taxonomy/` |
| Templates | `_Config/Templates/` |
| Skills (reusable procedures) | `_Config/Skills/` |
| Writing style | `_Config/Styles/writing.md` |
| Folder colours | `_Config/Styles/obsidian.md` |
| Memories | `_Config/Memories/` |
| Your preferences | `_Config/User/preferences-always.md` |
| Known gotchas | `_Config/User/gotchas.md` |

## Setup

To bind a workspace and optionally configure Claude Code, Codex and Grok to use this vault's MCP server:

```bash
# Bind one workspace to this Brain
brain workspace bind --vault /path/to/vault --workspace /my/project --request-json '{}'

# Configure project-scoped MCP transport for all three clients
brain mcp configure --vault /path/to/vault --workspace /my/project --request-json '{"scope":"project","client":"all"}'

# Claude-only local scope (gitignored; Codex and Grok have no local scope)
brain mcp configure --vault /path/to/vault --workspace /my/project --request-json '{"scope":"local","client":"claude"}'

# Register as your default brain for all projects for all three clients
brain mcp configure --vault /path/to/vault --request-json '{"scope":"user","client":"all"}'

# Install the active-Brain shaping discovery adapter for all three clients
brain agent-skill configure --vault /path/to/vault --request-json '{"client":"all"}'
```

`workspace.bind`, `workspace.configure-bootstrap` and `mcp.configure` are separate public setup and transport owners. Use `brain command describe` for their exact request contracts.

For project scope, registration is not the whole story. Claude still needs the project's `.mcp.json` entry approved via `/mcp`, and Codex still needs the project trusted with the project-scoped `brain` MCP enabled. Once that project-scoped entry is active, it outranks the user-scoped one. Until then, either client may keep routing `mcp__brain__*` calls to a user-scoped `brain`.

The optional shaping adapter is a stable discovery shim, not a copied workflow.
At invocation time it calls `session.start` and loads the active Brain's
`.brain-core/skills/shaping/SKILL.md` through `vault.read-file`. Re-run the command to
update a Brain-owned adapter; use `--replace` only after reviewing an existing
unmanaged skill, which is archived first. Restart the affected clients after a
change.

## Tooling

If your vault has the Brain MCP server running, every command within the authenticated MCP ceiling appears under its projected `<noun>_<verb>` name. Start with `session_start`, discover with `command_list`, and inspect an exact schema and minimal request with `command_describe`. Canonical command IDs in results and permission arguments remain dotted. Normal content and observations start authorised within credential permissions. Use `access_prepare` for a specific operation or `access_status` for a command-wide review, then explicitly echo it to `access_request`. Consent belongs to this Brain and MCP instance, ends with that instance, and never raises credential permissions. `access_reduce` narrows or revokes it. Commands above the maximum remain statically discoverable. The removed aggregate 1.x tools are not aliases.

Common families include `artefact.*`, `document.*`, `resource.*` (skills, memories, styles and templates all resolve through this family), `plugin.*`, `trigger.*`, `type.*`, `content.*`, `retrieval.*`, `links.*`, `shaping.*`, `workspace.*`, `vault.*`, `runtime.*`, `stage.*`, `access.*` and `attachment.upload`. Profiles authorise exact leaves rather than aggregate buckets.

After an installed Core upgrade, an idle MCP proxy refreshes its child server
before the next command. Use `brain_proxy_status` to inspect loaded/installed
versions or `brain_proxy_refresh` to request that refresh explicitly. Both take
`{}` and work even when the child is unavailable. Busy work is left running;
finish it before refreshing. For proxy code changes, `brain_proxy_restart({})`
loads the installed proxy while preserving POSIX stdio. It ends exceptional
consent; unchanged proxy code is a no-op. Unsupported platforms or failed
preflight may require restarting MCP through the host. These are MCP transport tools; `runtime_status`
reports application warm-up instead.

Brain's MCP processes keep an always-on, content-free operational log under `.brain/local/diagnostics/` (bounded NDJSON: lifecycle, tool spans, command failures). On a development machine, set `BRAIN_LOG_BODIES=1` (or `true`) before starting MCP to additionally capture raw request/response bodies to `diagnostics/debug-bodies.log`.

For structural compliance, run `brain vault check --json`.

Without MCP, read `.brain-core/index.md` first. Use `brain <noun> <verb>` or the selected Brain's `command.py <noun> <verb>` direct projection. Both share the same typed request, semantic owner, structural result, profile gate and vault mutation lock.

## Further Reading

- [Workflows](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/workflows.md) — day-to-day usage patterns with examples
- [Reference](https://github.com/rob-morris/obsidian-brain/blob/main/docs/user/user-reference.md) — every artefact type, configuration point, and system in detail
- `.brain-core/standards/extending/` — extension procedures (developer reference)
- `.brain-core/index.md` — routes to MCP session bootstrap, CLI and direct scripts, the generated mirror, or the authored Markdown fallback

Any artefact can be archived independently of its status, including Thoughts.
Use `artefact_archive` and `artefact_unarchive` through MCP. These transitions
preserve lifecycle state and keep the active lexical index consistent.

### Recovering a blocked write

For a stale-router error, follow its `next_action` to `runtime.refresh-router`.
Retry the content operation only after repair succeeds. `force` requests an
unconditional rebuild and is normally unnecessary. A partial result means some
work committed: inspect the result before retrying. Lifecycle moves maintain
router and listing indexes themselves; no manual refresh between moves is needed.

`runtime.status` records warm-up progress. Its `ready` state is not proof that
caches are still current; use its `router_check` action for a blocked write.
