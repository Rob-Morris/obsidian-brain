# Example Library

Ready-to-use artefact type definitions. Pick what fits your vault.

## Choosing a Knowledge Type

Three living types serve knowledge management. Pick based on your workflow:

- **Wiki** — Human-curated knowledge base. Comprehensive, selective, deliberately maintained. Best for a polished reference library.
- **Zettelkasten** — Auto-maintained atomic concept mesh. One card per concept, dense links. Best for surfacing implicit structure across a growing corpus. Designed as a complementary layer with wiki — see each type's taxonomy for details.
- **Notes** — Low-friction flat notes for when you just want to write something down without thinking too hard about where it fits. No automated graph, no strict structure — just date-prefixed interconnected pages.

Wiki and zettelkasten can coexist in the same vault as complementary layers (fine-grained concept mesh + coarse-grained knowledge base).

## Living Examples

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

**Zettelkasten** — Auto-maintained atomic concept mesh. One card per concept, dense links to sources and related ideas. Graph maintained by deterministic maintenance layer; card content by separate enrichment step.
- Naming: `{slug}.md`
- Suggested colour: mint (`--palette-mint`)
- Frontmatter: `sources` (machine-maintained provenance), `related` (lateral connections)

**Daily Notes** — End-of-day summaries distilled from the day's log. The log has the detail; the daily note has the overview.
- Naming: `yyyy-mm-dd ddd.md` (e.g. `2026-03-10 Tue.md`)
- Suggested colour: sky (`--palette-sky`)
- Tag: `daily-note`

## Temporal Examples

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
