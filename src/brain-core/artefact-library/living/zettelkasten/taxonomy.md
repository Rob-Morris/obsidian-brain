# Zettelkasten

Living artefact. Auto-maintained atomic concept mesh.

## Purpose

One card per concept. The zettelkasten maps the vault's conceptual landscape automatically — every idea that appears across artefacts gets an atomic index card. Not for deep explanation (that's the wiki) or project tracking. The zettelkasten makes implicit knowledge structure explicit.

## What a Zettel Is

One card = one thought. A zettel contains enough to express a single concept clearly, but no more:

- **One idea per card.** If you need an "and" to describe what's on the card, it should probably be two cards.
- **Roughly a paragraph or two.** ~200–400 words. Enough to state the idea and briefly develop it, not enough for a full essay.
- **Written in your own words.** Not a raw quote or copy-paste — a reformulation that shows the thought has been processed.
- **Self-contained but linked.** A reader should understand the card without needing to read its neighbours, but links point to related ideas and sources.

The guiding principle is atomicity: one card = one thought, expressed as concisely as possible while remaining understandable in isolation. If it takes more space, break it into linked cards.

## How Cards Are Created

The zettelkasten has two layers of authorship:

**Maintenance** (deterministic, no LLM) handles the **graph**:
- Discovers which concepts exist in the vault
- Creates stub zettels with frontmatter + extracted context
- Keeps `sources`, `related` links, and cross-links current
- SHA-based change detection to skip unchanged files
- Builds thought-chains from co-occurrence patterns

**Enrichment** (LLM-assisted, separate step) handles the **content**:
- Develops stubs into proper cards (200–400 words, in own words)
- Maintenance provides context (sources, related concepts, extracted sentences) so enrichment has everything it needs
- Never has to figure out *what* needs writing or *what links where* — maintenance does that

**No lifecycle status field.** Stubs are identified by body word count (< 50 words), not a frontmatter field. Once enriched, maintenance never rewrites card body — it only updates frontmatter and structured body links.

## Thought-Chains

For natural thought-sequences, use an optional body link:

```
**Follows:** [[Zettelkasten/ownership]]
```

This captures thought-lineage without numeric IDs. For notes that form explicit sequences (tutorials, argument chains), optional `parent` + `sequence` frontmatter fields give sequencing where it matters without forcing it everywhere.

## Relationship Metadata

Frontmatter carries machine-maintained relationship fields:
- `sources` — which artefacts mention this concept (provenance)
- `related` — lateral connections to other zettels (derived from co-occurrence)

Body carries navigational links:
- `**Follows:**` — thought-lineage (optional)
- `**Depth:**` — pointer to wiki page if one exists
- Inline wikilinks — contextual references within the card text

## Relationship with Wiki

Zettelkasten and wiki form a two-layer semantic graph:

| Dimension | Zettelkasten | Wiki |
|---|---|---|
| **Grain** | Atomic — one discrete idea per file | Comprehensive — a topic page synthesising multiple ideas |
| **Authorship** | Graph auto-maintained; card content by enrichment | Human-curated, agent-assisted |
| **Purpose** | Makes implicit knowledge structure explicit | Human-readable reference material |
| **Volume** | Many (hundreds+), small | Fewer, richer |
| **Editorial stance** | Unopinionated — indexes everything | Selective — only what's worth explaining in depth |

- A zettel **discovers** a concept; a wiki page **explains** it in depth
- Zettels link to wiki pages when deeper explanation exists (`**Depth:** [[Wiki/slug]]`)
- Wiki pages link to zettels when referencing atomic concepts
- Not every zettel needs a wiki page (most won't); not every wiki page needs a zettel
- When both exist for a concept, the zettel stays atomic — it defers depth to the wiki

## Naming

`{slug}.md` in `Zettelkasten/`.

Example: `Zettelkasten/ownership.md`

## Frontmatter

```yaml
---
type: living/zettelkasten
tags:
  - topic-tag
sources:
  - "[[Wiki/rust-lifetimes]]"
related:
  - "[[Zettelkasten/borrowing]]"
---
```

## Template

[[_Config/Templates/Living/Zettelkasten]]
