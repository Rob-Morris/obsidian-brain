# Wiki

Living artefact. Interconnected knowledge base.

## Purpose

One page per concept. The wiki is for things you want to understand, reference, or build on over time. Not for projects, sessions, or events — those belong in temporal artefacts or other living types.

## When To Use

When building reference knowledge about a concept you want to understand, reference, or build on over time. Wiki pages are authoritative and objective — for personal understanding, use Notes instead.

## How to Write Wiki Pages

- **Title is the concept name.** Short noun-phrase. "Rust Lifetimes", not "Notes on Rust Lifetimes from March".
- **One page per concept.** If two ideas are distinct, they get separate pages. If they're the same thing, merge them.
- **Link liberally.** Use wikilinks inline whenever you mention a concept that has or should have its own page. Links are how the wiki builds structure organically.
- **Update over create.** Before making a new page, check if one already exists. Prefer editing an existing page to starting fresh.
- **Self-contained.** Each page should make sense on its own. A reader shouldn't need to follow links to understand the core idea.
- **Tags for broad categories, links for specific relationships.** Tags group pages by domain (`programming`, `design`). Links connect pages by meaning.
- **No prescribed structure.** Pages shape themselves around their subject. A page about a tool looks different from a page about a principle.

## Relationship with Zettelkasten

Wiki and zettelkasten form a two-layer semantic graph: a fine-grained concept mesh (zettelkasten, automatic) and a coarse-grained knowledge base (wiki, deliberate).

- A zettel **discovers** a concept; a wiki page **explains** it in depth
- Zettels link to wiki pages when deeper explanation exists
- Wiki pages link to zettels when referencing atomic concepts
- Not every zettel needs a wiki page (most won't); not every wiki page needs a zettel
- When both exist for a concept, the zettel stays atomic — it defers depth to the wiki

## Naming

`{Title}.md` in `Wiki/`.

Example: `Wiki/rust-lifetimes.md`

## Frontmatter

```yaml
---
type: living/wiki
tags:
  - topic-tag
---
```

## Template

[[_Config/Templates/Living/Wiki]]
