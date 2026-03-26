# Writing

Living artefact. Atomic pieces of written work.

## Purpose

Each file is one self-contained piece of writing — an essay, a blog post, a chapter, a snippet, a letter, a script. Writing is the atom; complex writing projects (a book, a series, a long-form guide) compose atoms together using an index file that links to its constituent pieces. Subfolders within `Writing/` can group related files (e.g. `Writing/my-novel/index.md`, `Writing/my-novel/chapter-1.md`). Other artefact types like Projects can serve as the organising hub — Writing doesn't prescribe the orchestration layer, only the atoms.

## Lifecycle

| Status | Meaning |
|---|---|
| `draft` | Work in progress. The default state. |
| `editing` | Structure is set, refining language and flow. |
| `review` | Ready for external eyes or final self-review. |
| `published` | Released or delivered. Stays as canonical source. |
| `parked` | Set aside — not abandoned, but not being worked on. |

## Publishing

When a piece reaches `published` status:

1. Set `status: published` in frontmatter
2. Add `publisheddate: YYYY-MM-DD` to frontmatter
3. Rename the file to `yyyymmdd-{slug}.md` using `brain_action("rename")` — this updates all wikilinks vault-wide automatically
4. Move the file to `Writing/_Published/`
5. Companion files (HTML pastes, exports) move alongside the main file and keep the same prefix

## Archiving

Published writing that has been superseded (e.g. a rewrite exists elsewhere):

1. Set `status: archived` in frontmatter
2. Add `archiveddate: YYYY-MM-DD` to frontmatter
3. Add a supersession callout linking to the successor
4. Move the file from `_Published/` to `_Archive/`

Published writing that remains canonical stays in `_Published/`.

## Naming

`{Title}.md` in `Writing/` (or `Writing/{project-slug}/{Title}.md` for grouped pieces).

Example: `Writing/On Tool Use.md`
Example: `Writing/my-novel/Chapter 1.md`

## Frontmatter

```yaml
---
type: living/writing
tags:
  - writing
status: draft
---
```

## Template

[[_Config/Templates/Living/Writing]]
