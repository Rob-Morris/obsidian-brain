# Brain System Guide

How the artefact system works — lifecycle, frontmatter contracts, vault structure, and extension. For a walkthrough of day-to-day use, see [Workflows](workflows.md). For the full type reference, see the [Reference](../user-reference.md).

---

## Contents

- [The Artefact Model](#the-artefact-model)
- [Vault Structure](#vault-structure)
- [Frontmatter Conventions](#frontmatter-conventions)
- [Filing Conventions](#filing-conventions)
- [Workflows](#workflows)
- [Extension](#extension)

---

## The Artefact Model

Brain classifies every file as either **living** or **temporal**.

### Living Artefacts

- Sit in root-level folders
- Evolve over time — you edit them, they grow, the current version is what matters
- May have a lifecycle with status values (e.g., `draft` → `published`)
- Some reach a terminal status and get archived; others are evergreen

#### Default living types

| Type | Folder | Lifecycle | Purpose |
|---|---|---|---|
| `living/daily-note` | `Daily Notes/` | none | End-of-day summaries distilled from logs. |
| `living/design` | `Designs/` | `proposed` → `shaping` → `ready` → `active` → terminal | Design documents and implementation proposals. |
| `living/documentation` | `Documentation/` | `new` → `shaping` → `ready` → `active` → `deprecated` | Prescriptive reference material that governs work. |
| `living/idea` | `Ideas/` | `new` → `shaping` → `ready` → `adopted`/`parked` | Concepts being articulated to clarity. |
| `living/note` | `Notes/` | none | Low-friction knowledge capture. |
| `living/person` | `People/` | `active` → `shaping` → `parked` | Living hub for what you know about a person. |
| `living/project` | `Projects/` | none | Living hub for project state, related artefacts, and release tracking. |
| `living/release` | `Releases/{Project}/` | `planned` → `active` → `shipped`/`cancelled` | Version-scoped shipment record for one planned or shipped release. |
| `living/task` | `Tasks/` | `open` → `shaping` → `in-progress` → `done`/`blocked` | Persistent unit of work linked to the artefacts it serves. |
| `living/workspace` | `Workspaces/` | `active` → `parked` → `completed` | Hub linking brain artefacts to a bounded data container. |
| `living/writing` | `Writing/` | `draft` → `editing` → `review` → `published`/`parked` | Long-form written work crafted for an audience. |

### Temporal Artefacts

- Sit under `_Temporal/` in type-specific subfolders
- Bound to a moment — written once, rarely edited afterward
- Organised in monthly subfolders (`yyyy-mm/`)
- Date-prefixed filenames
- Serve as historic record; their insights may spin out into living artefacts

### The Relationship

Temporal artefacts capture the moment. Living artefacts capture the understanding. A log entry records what happened; a wiki page explains the concept. A research doc captures findings at a point in time; a design doc carries the decisions forward.

When temporal work produces something lasting, it spins out to a living artefact with provenance links connecting the two.

---

## Vault Structure

### Folder Tiers

| Tier | Folders | Purpose |
|---|---|---|
| **Living** | Root-level type folders (`Wiki/`, `Projects/`, etc.) | Artefacts that evolve; current version is source of truth |
| **Temporal** | `_Temporal/` and its children | Point-in-time artefacts; written once, rarely edited |
| **Config** | `_Config/` | Router, taxonomy, styles, templates, skills, preferences |
| **System** | `_Assets/`, `_Plugins/`, `.brain-core/`, `.obsidian/` | Infrastructure, not content |

### System Folders

| Folder | Purpose |
|---|---|
| `_Assets/` | Non-markdown files and generated output — `Attachments/` (user-added, Obsidian target) and `Generated/` (tool-produced, reproducible from source) |
| `_Config/` | Vault configuration — router, taxonomy definitions, styles, templates, user preferences |
| `_Config/Taxonomy/` | One file per artefact type with full definition |
| `_Config/Templates/` | Obsidian templates for each type |
| `_Config/Styles/` | Writing style guide and colour assignments |
| `_Config/User/` | Your standing preferences and learned gotchas |
| `_Config/Memories/` | Reference cards agents load on demand |
| `_Config/Skills/` | Skill documents for tools and workflows |
| `_Temporal/` | Parent folder for all temporal artefact types |
| `_Plugins/` | External tool data and integrations |
| `_Workspaces/` | Freeform data containers for workspaces — not indexed, not compliance-checked |
| `.brain-core/` | The Brain system itself (versioned, upgradeable) |
| `.obsidian/` | Obsidian vault config and CSS snippets |

`.brain-core/` is committed into the vault rather than gitignored. This is intentional — it makes the vault self-describing so that any agent can read the router and understand the system without needing access to the upstream `obsidian-brain` repository.

Folders starting with `_` or `.` are infrastructure — excluded from content indexing and search.

### Archive

Artefacts with a terminal status (e.g. `adopted`, `published`, `completed`) are archived to a top-level `_Archive/` directory at the vault root, preserving type/project structure inside (e.g. `_Archive/Ideas/Brain/20260101-old-idea.md`). The archive operation requires the artefact to have reached a terminal status — the set of terminal statuses is defined per type in the taxonomy file. Archived files are excluded from the vault file index, search, and all normal artefact operations. Use `brain_action("archive")` and `brain_action("unarchive")` for archive operations; `brain_list(resource="archive")` to list archived files, `brain_read(resource="archive", name="...")` to read a specific one.

---

## Frontmatter Conventions

### Required Fields

Every artefact needs at minimum:

```yaml
type: living/wiki        # or temporal/log, etc.
tags:
  - topic-tag
```

`created` and `modified` are auto-set to the current ISO 8601 timestamp when an artefact is created via script or MCP. If either field is provided in `frontmatter_overrides`, the provided value is preserved. On subsequent edits and appends via script or MCP, `modified` is automatically updated to the current timestamp; `created` is never changed by edit operations.

### Status

Only types with a defined lifecycle have status. Status values are type-specific — see the [Reference](../user-reference.md) for the full table. The general shape is a progression from open/draft states through active work to a terminal state (implemented, published, adopted, etc.).

### Archive Fields

Added only when archiving:

```yaml
archiveddate: 2026-03-15
```

### Project Tags

Use nested tags to connect artefacts to a project:

```yaml
tags:
  - project/my-project
```

All artefacts related to that project share the tag, making them findable together.

### The Body Rule

**Frontmatter** is for queryable state: type, tags, status, dates.

**Body text** is for navigation: wikilinks, origin links, transcript references, supersession callouts.

Why? Obsidian's backlinks and graph view resolve body wikilinks. Body text is visible in reading mode. The search index tokenises body text. Keep your links where they work.

---

## Filing Conventions

### Living Artefacts

- Root-level folder, one per type
- Freeform naming for most types: `{Title}.md` (spaces and mixed case allowed)
- Some types use date prefixes — see the type's taxonomy file for the exact pattern
- Start flat; subfolders emerge organically when a single work outgrows one file
- One file acts as the index in a subfolder (`index.md` or `project-slug.md`)

### Temporal Artefacts

- All under `_Temporal/{Type Name}/`
- Monthly subfolders: `yyyy-mm/`
- Date-prefixed filenames (exact format varies by type — see individual type taxonomy files)
- Flat within month folders

### Archives

- Top-level `_Archive/` at vault root, preserving type/project structure: `_Archive/{Type}/{Project}/`
- Files renamed to `yyyymmdd-{Title}.md` before moving
- Excluded from vault file index, search, and all normal artefact operations
- Use `brain_action("archive")` / `brain_action("unarchive")` for archive operations
- Use `brain_list(resource="archive")` to list archived files, `brain_read(resource="archive", name="...")` to read a specific one

---

## Workflows

### Daily Cycle

1. Work happens
2. After meaningful work, append a timestamped entry to today's **log** (`_Temporal/Logs/`)
3. At end of day, create a **daily note** summarising the log

The log is the raw timeline. The daily note is the digest.

### Idea Adoption

Ideas progress through increasing levels of structure:

1. **Idea Log** (`_Temporal/Idea Logs/`) — raw capture, low bar
2. **Idea** (`Ideas/`) — fleshed out, explored, status: `new`
3. **Design** (`Designs/`) — shaped proposal with decisions, status: `shaping`

At each transition, use provenance links (origin on child, callout on parent). Carry forward relevant tags, especially project tags.

### Hub Pattern

Hub artefacts (a living type like People, Projects, or Workspaces) are living summaries that group related artefacts via nested tags. See `.brain-core/standards/hub-pattern` for the full standard.

**Temporal handshake:** Tagged temporal artefacts feed their hub. When a temporal changes the current picture, distil the change into the hub. Temporals preserve *when*; the hub reflects *now*.

**Contextual linking:** Weave links into prose — don't list them as changelog entries. Link text should read naturally: `Scope narrowed after the [[decision-log|March review]]` not `- See [[20260320-decision~Review]]`.

**Ingestion:** Match the effort to the input. Minimal info → create a minimal hub, no fuss, grow it later. Rich dump → decompose into artefacts first (observations, research, decisions, entries), then write or update the hub as an interpreted summary.

**Elicitation:** Hubs are natural moments to be curious. When creating or revisiting a hub, notice gaps and ask natural questions. Capture answers as temporals, then update the hub.

### Linking

Use **basename-only** wikilinks by default: `[[My Page]]`, not `[[Wiki/My Page]]`. Basename links survive folder moves, subfolder grouping, and archiving. Path-qualified links break when files move.

When `brain_create` detects a basename collision with a file in a different type folder, it automatically appends the type key to disambiguate: `Three Men in a Tub (idea).md`. The original file keeps its clean name. Temporal artefacts have date-prefixed filenames that are naturally unique — no collision risk.

The compliance checker detects broken and ambiguous wikilinks. Full rules in `.brain-core/standards/linking`.

### Provenance

When one artefact spins out of another:

**On the new artefact (child):**
```markdown
**Origin:** [[source-file|description]] (yyyy-mm-dd)
```

**On the source artefact (parent):**
```markdown
> [!info] Spun out to design
> [[new-design]] — 2026-03-15
```

If the source transfers all authority, set its terminal status and archive it. Otherwise the callout alone suffices — the source stays active.

**Transcript linking:** When an artefact is shaped through Q&A, the shaped artefact lists its transcripts: `**Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]`. Applies to any shaped artefact type.

---

## Extension

### When to Add a New Type

Before creating a new artefact type, check:

- **No existing type fits** — even with generous interpretation
- **Recurring pattern** — you expect multiple files, not just one
- **Distinct lifecycle** — different naming, frontmatter, or archiving rules from existing types
- **Worth the overhead** — each type needs taxonomy, colour, CSS, and optionally a router trigger

If it's a one-off, consider a subfolder or tag within an existing type instead.

### The Artefact Library

`.brain-core/artefact-library/` contains ready-to-install type definitions. Each type includes a README, taxonomy file, template, and CSS. Browse the library's README for the full catalogue with descriptions and recommendations.

### Adding a Living Artefact Type

1. **Create the root folder** (e.g., `Projects/`)
2. **Create taxonomy file** at `_Config/Taxonomy/Living/{key}.md`
3. **Create template** at `_Config/Templates/Living/{Type Name}.md`
4. **Reference standards** — if the type has lineage or archiving, reference `.brain-core/standards/provenance` and/or `.brain-core/standards/archiving` in the taxonomy
5. **Add router trigger** in `_Config/router.md` (if the type has a trigger condition)
6. **Run `brain_action("compile")`** — colours are auto-generated
7. **Log the addition**

### Adding a Temporal Artefact Type

1. **Create the folder** under `_Temporal/` (e.g., `_Temporal/Reports/`)
2. **Create taxonomy file** at `_Config/Taxonomy/Temporal/{key}.md`
3. **Create template** at `_Config/Templates/Temporal/{Type Name}.md`
4. **Reference standards** — if the type has lineage or archiving, reference `.brain-core/standards/provenance` and/or `.brain-core/standards/archiving` in the taxonomy
5. **Add router trigger** in `_Config/router.md` (if applicable)
6. **Run `brain_action("compile")`** — rose-blended colours are auto-generated
7. **Log the addition**

---

For the default template library (what types ship and what each is for), see [Template Library Guide](template-library-guide.md).
