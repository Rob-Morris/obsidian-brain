# Brain Reference

Complete reference for every artefact type, convention, configuration point, and system in a Brain vault. For a walkthrough of how to use the Brain day-to-day, see the [User Guide](user-guide.md).

---

## Contents

- [Vault Structure](#vault-structure)
- [The Artefact Model](#the-artefact-model)
- [Living Artefact Types](#living-artefact-types)
- [Temporal Artefact Types](#temporal-artefact-types)
- [Filing Conventions](#filing-conventions)
- [Frontmatter Conventions](#frontmatter-conventions)
- [Workflows](#workflows)
- [Extending Your Vault](#extending-your-vault)
- [Configuration Reference](#configuration-reference)
- [Tooling](#tooling)
- [Colour System](#colour-system)
- [Writing Style](#writing-style)
- [Maintaining This Guide](#maintaining-this-guide)

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

Folders starting with `_` or `.` are infrastructure — excluded from content indexing and search.

### Archive Subfolders

Living artefact folders can contain an `_Archive/` subfolder for terminal artefacts. Archived files are renamed with a date prefix and styled in slate to signal "inactive."

---

## The Artefact Model

Brain classifies every file as either **living** or **temporal**.

### Living Artefacts

- Sit in root-level folders
- Evolve over time — you edit them, they grow, the current version is what matters
- May have a lifecycle with status values (e.g., `draft` → `published`)
- Some reach a terminal status and get archived; others are evergreen

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

## Living Artefact Types

### Wiki

**Folder:** `Wiki/` · **Naming:** `{name}.md` · **Colour:** Rose

Interconnected knowledge base. One page per concept. Deliberately human-curated — selective, polished, comprehensive reference.

```yaml
type: living/wiki
tags:
  - topic-tag
```

No status field. Evergreen. Update pages as understanding deepens. One page per concept; merge duplicates. Link liberally.

**Relationship to Zettelkasten:** Wiki is coarse-grained — topic pages that synthesise multiple ideas. Zettelkasten is fine-grained — atomic concept cards. Wiki pages explain in depth; zettels index atomically. They complement each other.

### Zettelkasten

**Folder:** `Zettelkasten/` · **Naming:** `{name}.md` · **Colour:** Mint

Auto-maintained atomic concept mesh. One card per concept (~200–400 words), densely linked to sources and related cards. Makes implicit knowledge structure explicit.

```yaml
type: living/zettelkasten
tags:
  - topic-tag
sources:
  - "[[Wiki/some-concept]]"
related:
  - "[[Zettelkasten/related-concept]]"
```

No status field. Stubs (< 50 words) are identified automatically.

**Two-layer authorship:**
- **Maintenance layer** (deterministic): discovers concepts from your other artefacts, creates stub cards, maintains `sources` and `related` links
- **Enrichment layer** (LLM-assisted): develops stubs into proper cards (200–400 words, in your own words)

**Optional body links:**
- `**Follows:** [[Zettelkasten/ownership]]` — thought-lineage chain
- `**Depth:** [[Wiki/some-concept]]` — pointer to wiki page for deeper explanation

### Notes

**Folder:** `Notes/` · **Naming:** `yyyymmdd - {Title}.md`

Flat knowledge base of date-prefixed notes. Low-friction alternative to wiki when deliberate curation feels like overhead.

```yaml
type: living/note
tags:
  - topic-tag
```

No status field. Evergreen. Intentionally flat (no subfolders). One page per concept. Update existing pages rather than creating duplicates. Link liberally. Self-contained — readable without following links.

### Daily Notes

**Folder:** `Daily Notes/` · **Naming:** `yyyy-mm-dd ddd.md` (e.g., `2026-03-15 Sun.md`)

End-of-day summaries distilled from the day's log. The log has detail; the daily note has overview.

```yaml
type: living/daily-note
tags:
  - daily-note
```

**Format:** `## Tasks` (checkbox list of what got done) + `## Notes` (short topic sections summarising the day).

### Designs

**Folder:** `Designs/` · **Naming:** `{name}.md`

Design documents, wireframes, proposals for features, products, or concepts.

```yaml
type: living/design
tags:
  - design
status: shaping
```

**Lifecycle:**

| Status | Meaning |
|---|---|
| `shaping` | Default. Being explored; decisions open. |
| `active` | Agreed and being implemented. |
| `implemented` | Fully built; authority transfers to implementation. Terminal — archive. |
| `parked` | Set aside; not abandoned, not being pursued. |

**Body lineage:**
```markdown
**Origin:** [[source-idea|The original idea]] (2026-03-10)
**Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]
```

**Archiving:** When `implemented` → add `archiveddate` → add supersession callout → rename to `yyyymmdd-{slug}.md` → move to `Designs/_Archive/`.

### Ideas

**Folder:** `Ideas/` · **Naming:** `{name}.md`

Loose thoughts and concepts to explore. Loose structure; no prescribed format beyond title and tags.

```yaml
type: living/idea
tags:
  - idea
status: new
```

**Lifecycle:**

| Status | Meaning |
|---|---|
| `new` | Default. Exists but not developed. |
| `developing` | Actively being shaped and refined. |
| `graduated` | Promoted to design doc. Terminal — archive. |
| `parked` | Set aside; not abandoned. |

**Graduating to Design:** Follow [[.brain-core/standards/provenance]] for lineage and [[.brain-core/standards/archiving]] for the archive workflow. Set idea `status: graduated`, carry forward open questions as design decisions, carry forward project tag.

### Journals

**Folder:** `Journals/` · **Naming:** `{name}.md`

Named journal streams. One file per journal, grouping personal journal entries via nested tags. Follows the same hub pattern as Projects.

```yaml
type: living/journal
tags:
  - journal/{slug}
status: active
```

**Lifecycle:**

| Status | Meaning |
|---|---|
| `active` | Default. Accepting new entries. |
| `archived` | No longer active. Existing entries preserved. |

**Convention:** All journal entries use the nested journal tag (e.g., `journal/personal`), connecting entries to their journal stream.

### People

**Folder:** `People/` · **Naming:** `{name}.md`

Person index files. One per person, serving as the living source of truth for what you know about them. The hub. Updated as you learn new things; superseded facts are replaced, not accumulated.

```yaml
type: living/person
tags:
  - person/{slug}
status: active
```

**Lifecycle:**

| Status | Meaning |
|---|---|
| `active` | Default. Actively maintained. |
| `archived` | No longer in regular contact. Preserved for reference. |

**Convention:** All related files across the vault use a nested person tag (e.g., `person/alice-smith`) so you can find everything connected to a person.

### Projects

**Folder:** `Projects/` · **Naming:** `{name}.md`

Project index files. One per project, linking to all related artefacts — designs, research, plans, transcripts. The hub.

```yaml
type: living/project
tags:
  - project/{slug}
```

**Convention:** All related files across the vault use a nested project tag (e.g., `project/pistols-at-dawn`) so you can find everything connected to a project.

### Workspaces

**Folder:** `Workspaces/` · **Naming:** `{name}.md`

Workspace hub files. One per workspace, linking brain artefacts to a bounded container of working files (`_Workspaces/`) that fall outside the vault's artefact taxonomy. Follows the same hub pattern as Projects.

```yaml
type: living/workspace
tags:
  - workspace/{slug}
status: active
workspace_mode: embedded
```

**Lifecycle:**

| Status | Meaning |
|---|---|
| `active` | Default. Workspace is in use. |
| `paused` | Set aside temporarily. |
| `completed` | Work is done. Terminal — archive. |
| `archived` | Preserved for reference. Terminal — archive. |

**Data folder:** `_Workspaces/{slug}/` is a freeform data bucket (embedded mode). Any file type — no frontmatter, naming, or taxonomy rules. Not indexed or compliance-checked. For linked mode (`workspace_mode: linked`), data lives in an external folder connected via `.brain/config`.

**Convention:** All related brain artefacts use the nested workspace tag (e.g., `workspace/yearly-taxes-2026`).

### Writing

**Folder:** `Writing/` · **Naming:** `{name}.md`

Atomic pieces of written work — essays, blog posts, chapters, letters, scripts.

```yaml
type: living/writing
tags:
  - writing
status: draft
```

**Lifecycle:**

| Status | Meaning |
|---|---|
| `draft` | Work in progress. Default. |
| `editing` | Structure set; refining language and flow. |
| `review` | Ready for external or final self-review. |
| `published` | Released or delivered. Stays as canonical source. |
| `parked` | Set aside; not being worked on. |

Complex writing projects use subfolders: `Writing/my-novel/index.md` with chapter files alongside.

### Documentation

**Folder:** `Documentation/` · **Naming:** `{name}.md`

Technical docs, style guides, prescriptive reference that governs how work gets done.

```yaml
type: living/documentation
tags:
  - documentation
```

No status field. Evergreen. Evolves over time as understanding deepens.

---

## Temporal Artefact Types

All temporal artefacts live under `_Temporal/` in type-specific subfolders, organised by month (`yyyy-mm/`).

### Logs

**Folder:** `_Temporal/Logs/yyyy-mm/` · **Naming:** `yyyymmdd-log.md`

Append-only daily activity logs. Running chronological record of what happened.

```yaml
type: temporal/log
tags:
  - log
```

**Conventions:**
- Append only — never edit or remove entries
- Timestamp each entry: `HH:MM` or `HH:MM:SS`
- Keep entries brief (1–2 sentences)
- Use wikilinks to reference artefacts and concepts
- Tag cross-repo work with project name in italics: `*(My Project)* Did the thing`

**Trigger:** After meaningful work, append a timestamped entry to today's log.

### Plans

**Folder:** `_Temporal/Plans/yyyy-mm/` · **Naming:** `yyyymmdd-plan--{slug}.md`

Pre-work plans written before complex work begins. Records intended approach, goal, and strategy.

```yaml
type: temporal/plan
tags:
  - plan
status: draft
```

**Lifecycle:** `draft` → `approved` → `implementing` → `completed`

**Conventions:** Write before starting. Keep concise — align on approach, not full spec. Link to relevant artefacts.

**Trigger:** Before complex work, write the plan.

### Transcripts

**Folder:** `_Temporal/Transcripts/yyyy-mm/` · **Naming:** `yyyymmdd-transcript--{slug}.md`

Conversation transcripts — person-to-person, AI conversations, Q&A sessions.

```yaml
type: temporal/transcript
tags:
  - transcript
```

**Conventions:** One transcript per conversation. Identify participants consistently. Preserve flow — record in order, don't reorganise.

**Trigger:** After a conversation worth preserving, capture as transcript.

### Shaping Transcripts

**Folder:** `_Temporal/Shaping Transcripts/yyyy-mm/` · **Naming:** `yyyymmdd-{sourcedoctype}-transcript--{slug}.md`

Q&A refinement transcripts tied to a source artefact. Each transcript is bound to one source document.

```yaml
type: temporal/shaping-transcript
tags:
  - transcript
  - source-type
```

File begins with a wikilink to the source. Q&A format: `Q.` prefix for questions, `> A.` blockquote for answers.

**Trigger:** After refining an artefact through Q&A, capture the raw Q&A.

### Research

**Folder:** `_Temporal/Research/yyyy-mm/` · **Naming:** `yyyymmdd-research--{slug}.md`

In-depth research notes and findings on specific topics.

```yaml
type: temporal/research
tags:
  - research
```

**Conventions:** One topic per file. Link to the context that prompted the research (project, design, idea). Include sources.

### Idea Logs

**Folder:** `_Temporal/Idea Logs/yyyy-mm/` · **Naming:** `yyyymmdd-idea-log--{slug}.md`

Low-friction idea captures. Raw, quick captures with a deliberately low bar for entry.

```yaml
type: temporal/idea-log
tags:
  - idea
  - topic-tag
```

**Graduation path:** Idea log (raw capture) → living Idea (fleshed out) → Design (shaped proposal). At each transition, use provenance links.

**Trigger:** When a new idea strikes, capture before it slips away.

### Thoughts

**Folder:** `_Temporal/Thoughts/yyyy-mm/` · **Naming:** `yyyymmdd-thought--{slug}.md`

Raw, unformed thinking captured in the moment. Precursor to ideas. Deliberately low bar — if it crosses your mind and feels worth noting, write it down. Most thoughts won't go anywhere, and that's fine.

```yaml
type: temporal/thought
tags:
  - thought
```

**Trigger:** When a raw thought surfaces, capture before it slips away.

### Journal Entries

**Folder:** `_Temporal/Journal Entries/yyyy-mm/` · **Naming:** `yyyymmdd-journal--{journal-slug}.md` or `yyyymmdd-journal--{journal-slug}--{topic}.md`

Personal journal entries — reflections, recollections, and life updates. Always in the user's own words unless the user explicitly asks the agent to write on their behalf. Each entry belongs to a journal stream via the `journal/{slug}` nested tag.

```yaml
type: temporal/journal-entry
tags:
  - journal-entry
  - journal/{journal-slug}
```

**Workflows:** Casual sharing (agent captures user's words from conversation), directed creation (user dictates), shaping/drafting (agent helps draft, user approves), or manual (user writes directly).

**Voice:** Preserve the user's language and phrasing. Do not paraphrase, summarise, or polish.

**Naming variants:**
- General: `20260322-journal--personal.md`
- Topic: `20260322-journal--personal--moving-house.md`

**Trigger:** When the user wants to journal, reflect on their life, or share something personal.

### Decision Logs

**Folder:** `_Temporal/Decision Logs/yyyy-mm/` · **Naming:** `yyyymmdd-decision--{slug}.md`

Point-in-time records of decisions, capturing the "why" behind choices.

```yaml
type: temporal/decision-log
tags:
  - decision
```

**Conventions:**
- State the question clearly — what decision needed making?
- List options considered, especially rejected ones
- Be honest about tradeoffs
- Link to what prompted the decision
- Write before the reasoning fades

**Trigger:** After a significant decision, capture the reasoning.

### Friction Logs

**Folder:** `_Temporal/Friction Logs/yyyy-mm/` · **Naming:** `yyyymmdd-friction--{slug}.md`

Signal accumulator for maintenance. Logs moments where context was missing, information conflicted, or assumptions had to be made. Not a bug tracker — a pattern detector. Individual entries are low-cost; value emerges when signals accumulate.

```yaml
type: temporal/friction-log
tags:
  - friction
```

**Conventions:** Capture in the moment. Be specific about where friction occurred. Include impact. Suggest a fix.

**Review pattern:** When friction patterns recur across multiple logs, distil them into a gotcha in `_Config/User/gotchas.md`.

**Trigger:** When encountering missing context, conflicting info, or making assumptions.

### Reports

**Folder:** `_Temporal/Reports/yyyy-mm/` · **Naming:** `yyyymmdd-report--{slug}.md`

Overviews of detailed processes. Distils a process (research, diagnosis, investigation, audit) into findings, implications, and recommended next steps.

```yaml
type: temporal/report
tags:
  - report
```

**Trigger:** After completing a detailed process, distil what it meant.

### Snippets

**Folder:** `_Temporal/Snippets/yyyy-mm/` · **Naming:** `yyyymmdd-snippet--{slug}.md`

Short, crafted content pieces derived from existing work — tweets, blurbs, product descriptions, taglines, bios.

```yaml
type: temporal/snippet
tags:
  - snippet
```

**Conventions:** Derive from a source (use provenance convention). Keep tight — a paragraph, a tweet, a tagline. One piece per file.

**Trigger:** When crafting a shareable or reusable piece from existing work.

### 🍪 Cookies

**Folder:** `_Temporal/Cookies/yyyy-mm/` · **Naming:** `yyyymmdd-cookie--{slug}.md`

A measure of user satisfaction. When work lands well, the user awards a 🍪. Over time, the 🍪 log reveals what kinds of work resonate and what approaches are worth repeating.

```yaml
type: temporal/cookie
tags:
  - cookie
```

**Template fields:**
- **What:** What was done
- **Flavour:** What made it satisfying (speed, elegance, understanding, surprise)
- **Why it earned a cookie:** Why this work stood out

**Conventions:** One file per 🍪. Be specific about what was done. Note the flavour — the insight is in *why* it was satisfying, not just *that* it was. Don't fish for 🍪s on trivial work; the value comes from them being genuine. Always use 🍪 when referring to cookies.

**Trigger:** After completing work the user is happy with. Look for signals: explicit praise, "ship it", "that's perfect", or the word "cookie." Agents should ask honestly: "Was that good enough to earn a 🍪? Because you know I'd do aaaanything for a 🍪, so be straight with me."

### Mockups

**Folder:** `_Temporal/Mockups/yyyy-mm/` · **Naming:** `yyyymmdd-mockup--{slug}.md`

Visual or interactive prototypes generated to explore a design direction. Mockups bridge the gap between abstract design documents and real implementation.

```yaml
type: temporal/mockup
tags:
  - mockup
```

**Conventions:** Link to the design or project being explored. If AI-generated, include the prompt. Note the verdict — what works, what needs iteration. One direction per file.

**Trigger:** When exploring a visual or interactive design direction — UI layouts, component designs, app shells.

### Observations

**Folder:** `_Temporal/Observations/yyyy-mm/` · **Naming:** `yyyymmdd-observation--{slug}.md`

Timestamped facts, impressions, and things noticed. Can be as short as a single sentence. Factual rather than speculative — captures what is or was, not what might be. Use tags to connect observations to relevant hubs (e.g. `person/alice-smith`).

```yaml
type: temporal/observation
tags:
  - observation
```

**Trigger:** When you notice or learn a discrete fact worth recording.

### Captures

**Folder:** `_Temporal/Captures/yyyy-mm/` · **Naming:** `yyyymmdd-capture--{slug}.md`

External material ingested into the vault verbatim — emails, meeting notes, Slack threads, data extracts, documents. Preserved exactly as received, frozen on ingest. Never edited after creation. Downstream artefacts link back to captures as source material.

```yaml
type: temporal/capture
source:
tags:
  - capture
```

**Conventions:** Preserve the original — don't summarise or restructure. Note the source in the `source` field (e.g. "Email from James Ward", "Slack #ops channel"). Link to at least one vault artefact for context via wikilinks in the body.

**Trigger:** When ingesting external material into the vault.

### Presentations

**Folder:** `_Temporal/Presentations/yyyy-mm/` · **Naming:** `yyyymmdd-presentation--{slug}.md`

Slide decks generated from markdown content using Marp CLI. The markdown source is the artefact; the PDF is output. Presentations draw from existing artefacts (designs, research, reports) and distil them into a structured narrative.

```yaml
type: temporal/presentation
tags:
  - presentation
```

**Conventions:** Link to the source artefact(s) via provenance. One idea per slide. Use the Brain theme for consistent styling (title slides, callouts, risk colours, card layouts). Regenerate the PDF after any edits — the markdown is the source of truth.

**Shaping workflow:** `brain_action("shape-presentation", {source, slug})` creates the artefact and launches a live preview. The agent iterates on slides while the user watches in real time.

**Trigger:** When creating a slide deck or presentation from vault content.

---

## Filing Conventions

### Living Artefacts

- Root-level folder, one per type
- Freeform naming for most types: `{name}.md` (spaces and mixed case allowed)
- Some types use date prefixes: Notes (`yyyymmdd - {Title}.md`), Daily Notes (`yyyy-mm-dd ddd.md`)
- Start flat; subfolders emerge organically when a single work outgrows one file
- One file acts as the index in a subfolder (`index.md` or `project-slug.md`)

### Temporal Artefacts

- All under `_Temporal/{Type Name}/`
- Monthly subfolders: `yyyy-mm/`
- Date-prefixed filenames (exact format varies by type — see individual type sections)
- Flat within month folders

### Archives

- `{Type}/_Archive/` for living artefacts that reach terminal status
- Files renamed to `yyyymmdd-{slug}.md` before moving
- Styled in slate to signal "inactive infrastructure"

---

## Frontmatter Conventions

### Required Fields

Every artefact needs at minimum:

```yaml
type: living/wiki        # or temporal/log, etc.
tags:
  - topic-tag
```

### Status

Only types with a defined lifecycle have status. Current types with status:

| Type | Status values |
|---|---|
| Designs | `shaping`, `active`, `implemented`, `parked` |
| Ideas | `new`, `developing`, `graduated`, `parked` |
| Journals | `active`, `archived` |
| Workspaces | `active`, `paused`, `completed`, `archived` |
| Writing | `draft`, `editing`, `review`, `published`, `parked` |
| Plans | `draft`, `approved`, `implementing`, `completed` |

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

## Workflows

### Daily Cycle

1. Work happens
2. After meaningful work, append a timestamped entry to today's **log** (`_Temporal/Logs/`)
3. At end of day, create a **daily note** (`Daily Notes/`) summarising the log

The log is the raw timeline. The daily note is the digest.

### Idea Graduation

Ideas progress through increasing levels of structure:

1. **Idea Log** (`_Temporal/Idea Logs/`) — raw capture, low bar
2. **Idea** (`Ideas/`) — fleshed out, explored, status: `new`
3. **Design** (`Designs/`) — shaped proposal with decisions, status: `shaping`

At each transition, use provenance links (origin on child, callout on parent). Carry forward relevant tags, especially project tags.

### Hub Pattern

Hub artefacts (People, Projects, Journals, Workspaces) are living summaries that group related artefacts via nested tags. See `.brain-core/standards/hub-pattern` for the full standard.

**Temporal handshake:** Tagged temporal artefacts feed their hub. When a temporal changes the current picture, distil the change into the hub. Temporals preserve *when*; the hub reflects *now*.

**Contextual linking:** Weave links into prose — don't list them as changelog entries. Link text should read naturally: `Scope narrowed after the [[decision-log|March review]]` not `- See [[20260320-decision--review]]`.

**Ingestion:** When receiving a dump of information, decompose into artefacts first (observations, research, decisions, entries), then write or update the hub as an interpreted summary.

**Elicitation:** Hubs are natural moments to be curious. When creating or revisiting a hub, notice gaps and ask natural questions. Capture answers as temporals, then update the hub.

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

### Archiving

For living artefacts reaching terminal status:

1. Set the terminal status in frontmatter (e.g., `status: implemented`)
2. Add `archiveddate: YYYY-MM-DD` to frontmatter
3. Add a supersession callout at the top of the body linking to the successor
4. Rename to `yyyymmdd-{slug}.md` (use `brain_action("rename")` for automatic wikilink updates)
5. Move to `{Type}/_Archive/`

**Wikilink hygiene:** The rename disambiguates the archived file from any successor that reuses the original slug. Use a path-qualified wikilink in the supersession callout (e.g. `[[Designs/brain-workspaces]]`) and the renamed identifier in the origin link on the successor (e.g. `[[20260324-brain-workspaces]]`). See `archiving.md` for details.

### Friction to Gotcha

1. Encounter friction during work → create a **friction log**
2. Notice the same friction recurring → distil it into a **gotcha** in `_Config/User/gotchas.md`
3. Gotchas get read every session, preventing repeat friction

### Organic Growth

Artefacts start as single files. When a piece of work outgrows one file:

1. Create a subfolder within the type folder (e.g., `Writing/my-novel/`)
2. One file acts as the index (`index.md` or the project-slug file)
3. Related files sit alongside
4. The subfolder inherits the parent type — no separate taxonomy or CSS needed

---

## Extending Your Vault

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

## Configuration Reference

### Router (`_Config/router.md`)

The entry point for agents. Contains:
- A pointer to `.brain-core/index.md` (read every session)
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
3. Run `brain_action("compile")`
4. Update the README table

### Skills (`_Config/Skills/`)

Skill documents for MCP tools, CLI commands, or plugin workflows. One folder per skill with a `SKILL.md` file describing what the skill does and how to use it.

---

## Tooling

### MCP Tools

If your vault runs the Brain MCP server (`.brain-core/mcp/server.py`), six tools are available:

**brain_session** (safe, auto-approvable)
- Bootstrap an agent session in one call — returns a compiled, token-efficient payload
- Includes: always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, memory/skill/plugin/style indexes
- Optional `context` parameter for scoped sessions (not yet implemented)

**brain_read** (safe, no side effects)
- Look up artefacts, triggers, styles, templates, skills, plugins, memories, workspaces, environment info, the compiled router, structural compliance results, or read artefact files by path
- Optional name filter to narrow results (for workspace, resolves a slug; for compliance, filters by severity; for file, the relative path from vault root)

**brain_search** (safe, no side effects)
- Search vault content by query text
- Filter by `type` and/or `tag`
- Returns ranked results with paths, titles, scores, and text snippets
- Uses Obsidian CLI when available, falls back to BM25 index

**brain_create** (additive, safe to auto-approve)
- Create a new vault artefact from type, title, and optional body/frontmatter overrides
- Resolves template and naming pattern from the compiled router
- Returns the created file's path, type, and title

**brain_edit** (single-file mutation)
- `edit` — replace body content, optionally merge frontmatter changes
- `append` — add content to end of existing body
- Path validated against compiled router (folder + naming pattern)

**brain_action** (vault-wide/destructive, requires approval)
- `compile` — rebuild the compiled router from source files
- `build_index` — rebuild the BM25 search index
- `rename` — rename a file with automatic wikilink updates (uses Obsidian CLI when available)
- `delete` — delete a file and replace wikilinks with strikethrough text
- `convert` — change artefact type, move file, reconcile frontmatter, update wikilinks
- `shape-presentation` — create a presentation artefact and launch Marp live preview (params: `{source, slug}`)
- `upgrade` — upgrade brain-core from a source directory (params: `{source}`, optional `{dry_run, force}`)
- `register_workspace` — register a linked workspace (params: `{slug, path}`)
- `unregister_workspace` — remove a linked workspace registration (params: `{slug}`)

### Scripts

Available in `.brain-core/scripts/`. Scripts are the source of truth for all vault operations — the MCP server imports from them.

| Script | Purpose |
|---|---|
| `compile_router.py` | Compile router, taxonomy, skills, and styles into a single JSON file |
| `build_index.py` | Build the BM25 retrieval index for search |
| `search_index.py` | Search the BM25 index from the command line |
| `read.py` | Query compiled router resources (artefacts, triggers, styles, templates, skills, etc.) |
| `create.py` | Create a new artefact with template/naming resolution |
| `edit.py` | Edit, append to, or convert an existing artefact |
| `rename.py` | Rename a file with automatic wikilink updates |
| `upgrade.py` | Upgrade brain-core in-place from a source directory |
| `workspace_registry.py` | Workspace slug→path resolution and registration |
| `init.py` | Set up Claude Code to use this vault's MCP server |
| `check.py` | Structural compliance checker — validates naming, frontmatter, month folders, archives, status values |

### Compliance Checks

Two complementary tools:

**`check.py`** (structural compliance) — deep scan that validates all files against the compiled router: naming patterns, frontmatter type and required fields, month folders for temporal files, archive metadata, and status values. Run on demand or during maintenance. Flags: `--json` (structured output), `--actionable` (fix suggestions), `--severity <level>` (filter). Also available via MCP: `brain_read(resource="compliance")`.

```bash
python3 .brain-core/scripts/check.py                    # human-readable
python3 .brain-core/scripts/check.py --json --actionable # structured with fixes
python3 .brain-core/scripts/check.py --vault /path/to/vault  # check a specific vault
```

**`compliance_check.py`** (session hygiene) — quick checks like "did you log today?" and "are backups fresh?" Run after each work block.

### Fallback Chain

When full tooling isn't available, agents degrade gracefully:

1. **MCP tools** — lowest token cost, structured responses, in-memory caching
2. **Scripts** — `.brain-core/scripts/` provides full functionality (read, search, create, edit, rename, compile, check)
3. **Lean router** — `_Config/router.md` (~45 tokens)
4. **Naive fallback** — read `index.md` → `router.md` → follow wikilinks

---

## Colour System

Brain auto-generates folder colours to visually distinguish types in the Obsidian sidebar. Colours are computed by `compile_colours.py` and regenerated automatically via `brain_action("compile")`.

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

- **Sidebar colours:** `.obsidian/snippets/folder-colours.css` — auto-generated CSS snippet
- **Graph colours:** `.obsidian/graph.json` `colorGroups` — auto-generated, other settings preserved

Both files are auto-generated — do not edit colour entries manually. Regenerate with `brain_action("compile")` or `python3 compile_colours.py`. Algorithm details and CSS selector templates are in `.brain-core/colours.md`.

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

---

## Maintaining This Guide

This guide should be updated when:

- **New artefact types** are added to the template vault defaults or the artefact library
- **Core conventions change** — naming patterns, frontmatter rules, filing conventions
- **Workflows are added or modified** — new graduation paths, changed archiving rules
- **New user-facing tooling** is introduced — new MCP tools, scripts, or skills
- **Configuration points change** — new config files, changed file locations

The [User Guide](user-guide.md) and [Quick-Start Guide](../src/brain-core/guide.md) (`src/brain-core/guide.md`) should be updated in tandem.
