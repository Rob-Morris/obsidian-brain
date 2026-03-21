# Brain Quick-Start Guide

Brain is a system for organising your Obsidian vault. It gives every file a home, keeps things findable, and grows with you.

This guide covers what you need to know day-to-day. For the full reference, see the [Brain User Guide](https://github.com/robmorris/obsidian-brain/blob/main/docs/user-guide.md).

## Your Vault at a Glance

```
Wiki/                     ← living artefacts (root folders)
Projects/
Designs/
...
_Temporal/                ← temporal artefacts (dated, point-in-time)
  Logs/2026-03/
  Plans/2026-03/
  Research/2026-03/
  ...
_Attachments/             ← images, PDFs, non-markdown files
_Config/                  ← router, taxonomy, styles, templates, preferences
_Plugins/                 ← external tool integrations
.brain-core/              ← this system (versioned, upgradeable)
```

**Living artefacts** sit in root-level folders. They evolve over time — the current version is what matters. Wiki pages, designs, projects, writing.

**Temporal artefacts** sit under `_Temporal/`. They're snapshots bound to a moment — logs, plans, transcripts, research. Organised in monthly subfolders (`yyyy-mm/`).

**Everything else** (`_Attachments/`, `_Config/`, `_Plugins/`, `.brain-core/`) is infrastructure.

## The Golden Rule

Every file belongs in a typed folder. Nothing goes in the vault root. If your content doesn't fit an existing type, add a new one first (see [Extending Your Vault](#extending-your-vault)).

## Day-to-Day Workflow

### Creating Files

Pick the artefact type that fits, create the file in the right folder with the right naming pattern:

| Type | Where | Naming |
|---|---|---|
| Wiki page | `Wiki/` | `{slug}.md` |
| Design doc | `Designs/` | `{slug}.md` |
| Project index | `Projects/` | `{slug}.md` |
| Writing piece | `Writing/` | `{slug}.md` |
| Idea | `Ideas/` | `{slug}.md` |
| Note | `Notes/` | `yyyymmdd - {Title}.md` |
| Daily note | `Daily Notes/` | `yyyy-mm-dd ddd.md` |
| Log entry | `_Temporal/Logs/yyyy-mm/` | `log--yyyy-mm-dd.md` |
| Plan | `_Temporal/Plans/yyyy-mm/` | `yyyymmdd-{slug}.md` |
| Research | `_Temporal/Research/yyyy-mm/` | `yyyymmdd-{slug}.md` |
| Transcript | `_Temporal/Transcripts/yyyy-mm/` | `yyyymmdd-{slug}.md` |

Every file needs frontmatter with at least `type` and `tags`:

```yaml
---
type: living/wiki
tags:
  - topic-tag
---
```

### Logging

After meaningful work, append a timestamped entry to today's log (`_Temporal/Logs/yyyy-mm/log--yyyy-mm-dd.md`). Keep entries brief — one or two sentences with a timestamp:

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

- **Designs:** `shaping` → `active` → `implemented` | `parked`
- **Ideas:** `new` → `graduated` | `parked`
- **Writing:** `draft` → `editing` → `review` → `published` | `parked`
- **Plans:** `draft` → `approved` → `completed`

Not every type has status. Wiki, Notes, Documentation, and most temporal types are evergreen.

## Provenance

When one artefact spins out of another, link them:

**On the new artefact:** `**Origin:** [[source-file|description]] (yyyy-mm-dd)`

**On the source:** Add a callout at the top of the body:
```markdown
> [!info] Spun out to design
> [[new-design]] — 2026-03-15
```

## Archiving

Living artefacts that reach a terminal status (e.g., `implemented`, `graduated`) get archived:

1. Set the terminal status
2. Add `archiveddate: YYYY-MM-DD` to frontmatter
3. Add a supersession callout linking to the successor
4. Rename to `yyyymmdd-{slug}.md`
5. Move to `{Type}/_Archive/`

## Extending Your Vault

Your vault ships with a starter set of types. The artefact library (`.brain-core/artefact-library/`) has more you can install, or you can create your own.

Before adding a type, check:
- No existing type fits (even generously)
- You'll create multiple files of this type (not just one)
- It needs different naming, frontmatter, or lifecycle rules

To add a living type: create the root folder, pick a colour, add CSS, create the taxonomy file in `_Config/Taxonomy/Living/`, and optionally add a router trigger.

To add a temporal type: create the folder under `_Temporal/`, blend the colour 35% towards rose, add CSS, create taxonomy in `_Config/Taxonomy/Temporal/`.

Full details in the [User Guide — Extending Your Vault](https://github.com/robmorris/obsidian-brain/blob/main/docs/user-guide.md#extending-your-vault).

## Configuration

| What | Where |
|---|---|
| Workflow triggers | `_Config/router.md` |
| Type definitions | `_Config/Taxonomy/` |
| Templates | `_Config/Templates/` |
| Writing style | `_Config/Styles/writing.md` |
| Folder colours | `_Config/Styles/obsidian.md` |
| Your preferences | `_Config/User/preferences-always.md` |
| Known gotchas | `_Config/User/gotchas.md` |

## Tooling

If your vault has the Brain MCP server running, you get three tools:

- **brain_read** — look up artefacts, triggers, styles, templates
- **brain_search** — find files by query, type, or tag
- **brain_action** — compile the router, build the search index, rename files with wikilink updates

Without MCP, agents fall back to the compiled router (`_Config/.compiled-router.json`), then the lean router (`_Config/router.md`), then plain file navigation.

## Further Reading

- [User Guide](https://github.com/robmorris/obsidian-brain/blob/main/docs/user-guide.md) — walkthrough of how the Brain works day-to-day, with examples
- [Reference](https://github.com/robmorris/obsidian-brain/blob/main/docs/user-reference.md) — every artefact type, configuration point, and system in detail
- `.brain-core/extensions.md` — detailed extension procedures (developer reference)
- `.brain-core/index.md` — system principles and always-rules

## Maintaining This Guide

This guide should be updated when:
- New artefact types are added to the template vault defaults
- Core conventions change (naming, frontmatter, filing)
- New user-facing tooling is introduced
- Workflows are added or modified
