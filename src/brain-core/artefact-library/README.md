# Artefact Library

Complete, ready-to-install artefact type definitions for Brain vaults.

The template vault ships with a curated set of defaults (marked below). This library contains all known types — including the template vault defaults — as a reference and install source.

## Available Types

### Living

| Type | Key | Description |
|---|---|---|
| [Daily Notes](living/daily-notes/) | `daily-notes` | High-level end-of-day summaries distilled from logs. **Template vault default.** |
| [Designs](living/designs/) | `designs` | Design documents, wireframes, and specs. **Template vault default.** |
| [Documentation](living/documentation/) | `documentation` | Guides, standards, and reference material. **Template vault default.** |
| [Ideas](living/ideas/) | `ideas` | Concepts articulated and shaped to clarity through iterative refinement. **Template vault default.** |
| [Journals](living/journals/) | `journals` | Named journal streams grouping personal journal entries via nested tags. |
| [Notes](living/notes/) | `notes` | Flat knowledge base of date-prefixed interconnected notes. **Template vault default.** |
| [People](living/people/) | `people` | Person index files — living hub for everything you know about someone. **Template vault default.** |
| [Projects](living/projects/) | `projects` | Project index files linking to related artefacts via project tags. **Template vault default.** |
| [Tasks](living/tasks/) | `tasks` | Persistent units of work — tracked, prioritised, and linked to artefacts. **Template vault default.** |
| [Wiki](living/wiki/) | `wiki` | Interconnected knowledge base. One page per concept. |
| [Workspaces](living/workspaces/) | `workspaces` | Workspace hub files linking brain artefacts to bounded data containers (`_Workspaces/`). **Template vault default.** |
| [Writing](living/writing/) | `writing` | Long-form written works with lifecycle: draft → published. **Template vault default.** |
| [Zettelkasten](living/zettelkasten/) | `zettelkasten` | Auto-maintained atomic concept mesh. One card per concept. |

### Temporal

| Type | Key | Description |
|---|---|---|
| [Logs](temporal/logs/) | `logs` | Append-only daily activity logs. **Template vault default.** |
| [Plans](temporal/plans/) | `plans` | Pre-work plans before complex work begins. **Template vault default.** |
| [Transcripts](temporal/transcripts/) | `transcripts` | Conversation transcripts. **Template vault default.** |
| [Decision Logs](temporal/decision-logs/) | `decision-logs` | Point-in-time records of decisions — captures the "why" behind choices. **Template vault default.** |
| [Shaping Transcripts](temporal/shaping-transcripts/) | `shaping-transcripts` | Q&A refinement transcripts tied to a source artefact. **Template vault default.** |
| [Friction Logs](temporal/friction-logs/) | `friction-logs` | Signal accumulator — logs friction: missing context, inconsistencies, suboptimal outcomes. **Template vault default.** |
| [Bug Logs](temporal/bug-logs/) | `bug-logs` | Point-in-time records of broken behaviour — correctness failures that need resolution. |
| [Idea Logs](temporal/idea-logs/) | `idea-logs` | Quick idea captures in rough form. |
| [Research](temporal/research/) | `research` | In-depth research notes on specific topics. **Template vault default.** |
| [Thoughts](temporal/thoughts/) | `thoughts` | Raw, unformed thinking captured in the moment. **Template vault default.** |
| [Reports](temporal/reports/) | `reports` | Overviews of detailed processes — findings and implications. **Template vault default.** |
| [Snippets](temporal/snippets/) | `snippets` | Short, crafted content pieces derived from existing work. **Template vault default.** |
| [Cookies](temporal/cookies/) | `cookies` | A measure of user satisfaction — awarded when work lands well. **Template vault default.** |
| [Journal Entries](temporal/journal-entries/) | `journal-entries` | Personal journal entries — reflections, recollections, and life updates. |
| [Mockups](temporal/mockups/) | `mockups` | Visual or interactive prototypes generated to explore a design direction. |
| [Observations](temporal/observations/) | `observations` | Timestamped facts, impressions, and things noticed. Low bar for capture. **Template vault default.** |
| [Captures](temporal/captures/) | `captures` | External material ingested verbatim — emails, meeting notes, data extracts. Frozen on ingest. **Template vault default.** |
| [Ingestions](temporal/ingestions/) | `ingestions` | Processing records for content decomposition — links captures to created artefacts. **Template vault default.** |
| [Presentations](temporal/presentations/) | `presentations` | Slide decks generated from markdown using Marp CLI. Source is markdown; output is PDF. |

## Choosing a Knowledge Type

Three living types serve knowledge management. Pick based on your workflow:

- **Wiki** — Human-curated knowledge base. Comprehensive, selective, deliberately maintained. Best for a polished reference library.
- **Zettelkasten** — Auto-maintained atomic concept mesh. One card per concept, dense links. Best for surfacing implicit structure across a growing corpus. Designed as a complementary layer with wiki — see each type's taxonomy for details.
- **Notes** — Low-friction flat notes for when you just want to write something down without thinking too hard about where it fits. No automated graph, no strict structure — just date-prefixed interconnected pages.

Wiki and zettelkasten can coexist in the same vault as complementary layers (fine-grained concept mesh + coarse-grained knowledge base).

## Browsing the Library

Each type has its own directory with three standard files:

| File | Purpose |
|---|---|
| `README.md` | Overview, install paths, optional router trigger |
| `taxonomy.md` | Conventions, naming, frontmatter — copy to `_Config/Taxonomy/` |
| `template.md` | Obsidian template — copy to `_Config/Templates/` |

Some types include additional files (e.g. `SKILL.md`, `theme.css`). Check the type's README for the full install list.

## Colours

Colours are auto-generated by `compile_colours.py` from the compiled router — no manual colour picking needed. See [[.brain-core/colours]] for the algorithm, palette, and blend formula.

## Conventions

Rules for defining and extending artefact types.

### Frontmatter is for queryable state, not navigation

Frontmatter fields should be **queryable metadata** — fields agents and Dataview can filter on: `type`, `status`, `tags`, `created`, `modified`, `archiveddate`.

**Wikilinks and navigational references belong in the body**, not frontmatter. This includes origin links, transcript lists, "superseded by" pointers, and any other inter-doc references. Reasons:

- **Backlinks/graph:** Obsidian reliably resolves wikilinks in body text for backlinks and graph view. Frontmatter wikilinks require specific property type configuration and behave inconsistently.
- **Reading mode:** Body links are visible and clickable in reading mode. Frontmatter links are hidden unless the user opens the Properties panel.
- **Search indexing:** Body text is tokenised by BM25. Frontmatter is parsed for structured fields only — wikilink text in frontmatter is not searchable.

### Status fields

Artefact types that have a lifecycle should include a `status` field in frontmatter. Each type defines its own status values. Common patterns:

- **Ideas:** `new` → `shaping` → `ready` → `adopted` | `parked`
- **Designs:** `proposed` → `shaping` → `ready` → `active` → `implemented` | `parked` | `rejected`
- **Documentation:** `new` → `shaping` → `ready` → `active` → `deprecated`
- **Tasks:** `open` → `shaping` → `in-progress` → `done` | `blocked`
- **Workspaces:** `active` → `parked` → `completed` (terminal → `+Completed/`)
- **Plans:** `draft` → `shaping` → `approved` → `implementing` → `completed`
- **Idea Logs:** `open` → `adopted` / `parked`

## Installing a type

**Automated:** `brain_action("sync_definitions")` syncs all library definitions to vault `_Config/` using three-way hash comparison. Chains automatically after CLI upgrade (`install.sh` or `upgrade.py`). Auto-updates unmodified files; preserves user customisations; returns warnings for conflicts. Use `force` to override, or `artefact_sync_exclude` in `.brain/preferences.json` to permanently skip specific files.

**Manual:**

1. Copy `taxonomy.md` to `_Config/Taxonomy/{Living|Temporal}/{key}.md`
2. Copy `template.md` to `_Config/Templates/{Living|Temporal}/{Type Name}.md`
3. Create the storage folder (e.g. `_Temporal/{Type Name}/` or `{Type Name}/`)
4. Optionally add a conditional trigger to `_Config/router.md`
5. Run `brain_action("compile")` — colours are auto-generated from the compiled router

Each type's README includes the specific paths and an optional router trigger line.
