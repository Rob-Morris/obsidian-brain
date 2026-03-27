# Snippets

Temporal artefact. Short, crafted content pieces derived from existing work.

## Purpose

A snippet is a small, polished piece of text crafted from existing vault content — an X post, a blurb, a product description, a tagline, a bio. Snippets are temporal because each attempt is a point-in-time capture: the same source material might produce different snippets on different days as thinking evolves.

## How to Write Snippets

- **Derive from source.** Every snippet comes from an existing artefact. Link to it via [[.brain-core/standards/provenance]].
- **Keep it tight.** Snippets are short by definition — a paragraph, a tweet, a tagline. If it grows longer, it's probably writing, not a snippet.
- **One piece per file.** Each snippet is its own file, even if multiple snippets derive from the same source.

## Naming

`yyyymmdd-snippet~{Title}.md` in `_Temporal/Snippets/yyyy-mm/`.

Example: `_Temporal/Snippets/2026-03/20260320-snippet~Brain Launch Post.md`

## Frontmatter

```yaml
---
type: temporal/snippet
tags:
  - snippet
---
```

## Trigger

When crafting a shareable or reusable piece of text derived from existing work.

## Template

[[_Config/Templates/Temporal/Snippets]]
