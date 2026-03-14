# Extensions

When the vault needs a new artefact type, follow the procedure for the relevant tier. Log what was added and why in the day's log.

## Adding a Living Artefact Folder

1. Create the folder at vault root.
2. Pick a palette colour (or add a new `--palette-*` variable) and add a `--color-*` variable in the Themes block of `.obsidian/snippets/folder-colours.css`. Never reuse a system folder colour (purple, steel, gold) — those are reserved for `_Config/`, `_Temporal/`, and `_Plugins/`.
3. Add three CSS selector blocks (folder + subfolders, border, files) — see [[.brain-core/v1.0/colours|Colours]] for the template.
4. Add a row to the router's Living artefact table.
5. Create a taxonomy file at `_Config/Taxonomy/{name}.md` describing the type's purpose, conventions, and template.
6. Update `_Config/Styles/obsidian.md` with the new colour assignment.
7. Log the addition.

## Adding a Temporal Child Folder

1. Create the folder under `_Temporal/`.
2. Choose a base hue and apply the blend formula (`result = base + (steel - base) × 0.35`) to derive the steel-tinted variant — see [[.brain-core/v1.0/colours|Colours]].
3. Add a `--color-temporal-*` hex in the Themes block and add CSS selectors with `background-color: var(--theme-temporal-bg)` and `border-radius: 4px`.
4. Add a row to the router's Temporal artefact table.
5. Create a taxonomy file at `_Config/Taxonomy/{name}.md` describing the type's purpose, conventions, and template.
6. Update `_Config/Styles/obsidian.md`.
7. Log the addition.

## Adding a Config Child Folder

1. Create the folder under `_Config/`.
2. No CSS changes needed — inherits config purple styling.
3. Document in the router if relevant.

## Adding a Plugin Folder

1. Create the folder under `_Plugins/`.
2. No CSS changes needed — inherits gold plugin styling.
3. Document in the router.
4. Create a skill in `_Config/Skills/` if the plugin has MCP tools or CLI commands.

---

## Example Library

Ready-to-use artefact type definitions. Pick what fits your vault.

### Living Examples

**Notes** — Flat knowledge base of interconnected notes. Intentionally flat — no subfolders. Each note has at least one tag.
- Naming: `{slug}.md`
- Suggested colour: teal (`--palette-teal`)
- Tags: topic-specific

**Designs** — Design documents, wireframes, mockups.
- Naming: `{slug}.md`
- Suggested colour: coral (`--palette-coral`)

**Documentation** — Docs, guides, standards, reference material.
- Naming: `{slug}.md`
- Suggested colour: peach (`--palette-peach`)

**Ideas** — Loose thoughts, concepts, things to explore.
- Naming: `{slug}.md`
- Suggested colour: amber (`--palette-amber`)

**Projects** — Project index files linking to related artefacts across the vault. Tag all related files with `project/{slug}`.
- Naming: `{slug}.md`
- Suggested colour: sage (`--palette-sage`)

**Daily Notes** — End-of-day summaries distilled from the day's log. The log has the detail; the daily note has the overview.
- Naming: `yyyy-mm-dd ddd.md` (e.g. `2026-03-10 Tue.md`)
- Suggested colour: sky (`--palette-sky`)
- Tag: `daily-note`

### Temporal Examples

**Plans** — Pre-work plans written before complex work begins.
- Naming: `yyyymmdd-{slug}.md`
- Suggested colour blend: coral → steel (`#C69DA8`)
- Tags: `plan`; frontmatter `status` field (`draft`, `approved`, `completed`)

**Research** — Investigation snapshots and findings on specific topics.
- Naming: `yyyymmdd-{slug}.md`
- Suggested colour blend: teal → steel (`#81BDC9`)

**Daily Notes (temporal variant)** — Date-bound alternative to the living Daily Notes folder. Use when daily notes are append-only records rather than evolving summaries.
- Naming: `yyyymmdd-{slug}.md`
- Suggested colour blend: sky → steel
