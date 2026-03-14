# Wiki

Living artefact. Interconnected knowledge base.

## Purpose

One page per concept. The wiki is for things you want to understand, reference, or build on over time. Not for projects, sessions, or events — those belong in temporal artefacts or other living types.

## How to Write Wiki Pages

- **Title is the concept name.** Short noun-phrase. "Rust Lifetimes", not "Notes on Rust Lifetimes from March".
- **One page per concept.** If two ideas are distinct, they get separate pages. If they're the same thing, merge them.
- **Link liberally.** Use wikilinks inline whenever you mention a concept that has or should have its own page. Links are how the wiki builds structure organically.
- **Update over create.** Before making a new page, check if one already exists. Prefer editing an existing page to starting fresh.
- **Self-contained.** Each page should make sense on its own. A reader shouldn't need to follow links to understand the core idea.
- **Tags for broad categories, links for specific relationships.** Tags group pages by domain (`programming`, `design`). Links connect pages by meaning.
- **No prescribed structure.** Pages shape themselves around their subject. A page about a tool looks different from a page about a principle.

## Naming

`{slug}.md` in `Wiki/`.

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

[[_Config/Templates/Living/Wiki|Wiki]]
