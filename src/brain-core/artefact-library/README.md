# Artefact Library

Complete, ready-to-install artefact type definitions for Brain vaults.

The template vault ships with a minimal set of types (wiki, logs, plans, transcripts). This library contains all known types — including the template vault defaults — as a reference and install source.

## Available Types

### Living

| Type | Key | Description |
|---|---|---|
| [Wiki](living/wiki/) | `wiki` | Interconnected knowledge base. One page per concept. **Template vault default.** |
| [Daily Notes](living/daily-notes/) | `daily-notes` | High-level end-of-day summaries distilled from logs. |
| [Designs](living/designs/) | `designs` | Design documents, wireframes, and specs. |
| [Documentation](living/documentation/) | `documentation` | Guides, standards, and reference material. |
| [Ideas](living/ideas/) | `ideas` | Loose thoughts and concepts to explore. |
| [Notes](living/notes/) | `notes` | Flat knowledge base of date-prefixed interconnected notes. |
| [Projects](living/projects/) | `projects` | Project index files linking to related artefacts via project tags. |
| [Writing](living/writing/) | `writing` | Long-form written works with lifecycle: draft → published. |
| [Zettelkasten](living/zettelkasten/) | `zettelkasten` | Auto-maintained atomic concept mesh. One card per concept. |

### Temporal

| Type | Key | Description |
|---|---|---|
| [Logs](temporal/logs/) | `logs` | Append-only daily activity logs. **Template vault default.** |
| [Plans](temporal/plans/) | `plans` | Pre-work plans before complex work begins. **Template vault default.** |
| [Transcripts](temporal/transcripts/) | `transcripts` | Conversation transcripts. **Template vault default.** |
| [Design Transcripts](temporal/design-transcripts/) | `design-transcripts` | Q&A refinement transcripts tied to a source artefact. |
| [Idea Logs](temporal/idea-logs/) | `idea-logs` | Quick idea captures with graduation path to ideas then designs. |
| [Research](temporal/research/) | `research` | In-depth research notes on specific topics. |
| [Thoughts](temporal/thoughts/) | `thoughts` | Raw, unformed thinking captured in the moment. |
| [Reports](temporal/reports/) | `reports` | Overviews of detailed processes — findings and implications. |
| [Snippets](temporal/snippets/) | `snippets` | Short, crafted content pieces derived from existing work. |

## Choosing a Knowledge Type

Three living types serve knowledge management. Pick based on your workflow:

- **Wiki** — Human-curated knowledge base. Comprehensive, selective, deliberately maintained. Best for a polished reference library.
- **Zettelkasten** — Auto-maintained atomic concept mesh. One card per concept, dense links. Best for surfacing implicit structure across a growing corpus. Designed as a complementary layer with wiki — see each type's taxonomy for details.
- **Notes** — Low-friction flat notes for when you just want to write something down without thinking too hard about where it fits. No automated graph, no strict structure — just date-prefixed interconnected pages.

Wiki and zettelkasten can coexist in the same vault as complementary layers (fine-grained concept mesh + coarse-grained knowledge base).

## Structure

Each type lives in its own directory under `living/` or `temporal/`:

```
artefact-library/
├── living/
│   └── {type-key}/
│       ├── README.md          # What it is, when to use it, install paths
│       ├── taxonomy.md        # → _Config/Taxonomy/{Classification}/{key}.md
│       ├── template.md        # → _Config/Templates/{Classification}/{Type Name}.md
│       └── style.css          # → merge into .obsidian/snippets/folder-colours.css
└── temporal/
    └── {type-key}/
        ├── README.md
        ├── taxonomy.md
        ├── template.md
        └── style.css
```

## Colour Recommendations

Each type includes a `style.css` with suggested default colours. These are starting points — adapt to your vault's colour scheme.

**Living types** use a palette colour directly (e.g. `var(--palette-coral)`).

**Temporal types** use a base palette colour blended 35% towards rose. See [[.brain-core/colours]] for the blend formula and full palette reference.

| Type | Base Colour | Blend Hex |
|---|---|---|
| Logs | Amber | `#F3BD93` |
| Plans | Coral | `#F198A2` |
| Research | Teal | `#A5C5CD` |
| Design Transcripts | Lavender | `#CCA9DB` |
| Transcripts | Mauve | `#D8A4C2` |
| Idea Logs | Blush | `#ECB2B7` |
| Thoughts | Mint | `#C2D2CC` |
| Reports | Lime | `#D4D29E` |
| Snippets | Gold | `#EBC49E` |

## Conventions

Rules for defining and extending artefact types.

### Frontmatter is for queryable state, not navigation

Frontmatter fields should be **queryable metadata** — fields agents and Dataview can filter on: `type`, `status`, `tags`, `created`, `modified`, `archiveddate`.

**Wikilinks and navigational references belong in the body**, not frontmatter. This includes origin links, transcript lists, "superseded by" pointers, and any other inter-doc references. Reasons:

- **Backlinks/graph:** Obsidian reliably resolves wikilinks in body text for backlinks and graph view. Frontmatter wikilinks require specific property type configuration and behave inconsistently.
- **Reading mode:** Body links are visible and clickable in reading mode. Frontmatter links are hidden unless the user opens the Properties panel.
- **Search indexing:** Body text is tokenised by BM25. Frontmatter is parsed for structured fields only — wikilink text in frontmatter is not searchable.

### Status fields

Living artefact types that have a lifecycle should include a `status` field in frontmatter. Each type defines its own status values. Common patterns:

- **Ideas:** `new` → `graduated` → `parked`
- **Designs:** `shaping` → `active` → `implemented` → `parked`
- **Plans:** `draft` → `active` → `complete` → `abandoned`

## Installing a type

1. Copy `taxonomy.md` to `_Config/Taxonomy/{Living|Temporal}/{key}.md`
2. Copy `template.md` to `_Config/Templates/{Living|Temporal}/{Type Name}.md`
3. Create the storage folder (e.g. `_Temporal/{Type Name}/` or `{Type Name}/`)
4. Merge `style.css` into `.obsidian/snippets/folder-colours.css` — add the colour variable to the Themes `:root` block and the selector blocks to the appropriate section
5. Update `_Config/Styles/obsidian.md` with the new colour assignment
6. Optionally add a conditional trigger to `_Config/router.md`

Each type's README includes the specific paths and an optional router trigger line.
