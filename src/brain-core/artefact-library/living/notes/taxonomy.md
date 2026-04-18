# Notes

Living artefact. Flat knowledge base of interconnected notes.

## Purpose

One page per concept. Intentionally flat — no subfolders. Each note should have at least one tag. Link liberally using wikilinks whenever you mention a concept that has or should have its own page.

## When To Use

When recording standalone knowledge about a concept — more personal and informal than Wiki pages, more about your understanding than objective reference. Use Notes for your perspective; use Wiki for shared, authoritative knowledge.

## How to Write Notes

- **Title is the concept name.** Short noun-phrase.
- **One page per concept.** If two ideas are distinct, they get separate pages. If they're the same thing, merge them.
- **Link liberally.** Links are how the knowledge base builds structure organically.
- **Update over create.** Before making a new page, check if one already exists.
- **Self-contained.** Each page should make sense on its own.

## Naming

`yyyymmdd - {Title}.md` in `Notes/`, date source `created`.

Example: `Notes/20260315 - Rust Lifetimes.md`

The `yyyymmdd` prefix is rendered from `created`. Backdate a note by setting `created` in frontmatter before saving.

## Frontmatter

```yaml
---
type: living/note
tags:
  - topic-tag
---
```

## Template

[[_Config/Templates/Living/Notes]]
