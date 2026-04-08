# Writing

Living artefact. Atomic pieces of written work.

## Purpose

Each file is one self-contained piece of writing — an essay, a blog post, a chapter, a snippet, a letter, a script. Writing is the atom; complex writing projects (a book, a series, a long-form guide) compose atoms together using an index file that links to its constituent pieces. Subfolders within `Writing/` can group related files (e.g. `Writing/my-novel/index.md`, `Writing/my-novel/chapter-1.md`). Other artefact types like Projects can serve as the organising hub — Writing doesn't prescribe the orchestration layer, only the atoms.

## When To Use

When crafting a self-contained piece of written work — essay, post, chapter, letter, script. For short, derivative text (tweets, blurbs, taglines), use Snippets instead.

## Lifecycle

| Status | Meaning |
|---|---|
| `draft` | Work in progress. The default state. |
| `editing` | Structure is set, refining language and flow. |
| `review` | Ready for external eyes or final self-review. |
| `published` | Released or delivered. Stays as canonical source. |
| `parked` | Set aside — not abandoned, but not being worked on. |

## Terminal Status

When a piece reaches `published` status, `brain_edit` automatically moves it to `Writing/+Published/`. Additional steps before or after the move:

1. Add `publisheddate: YYYY-MM-DD` to frontmatter
2. Optionally date-prefix the filename (`yyyymmdd-{slug}.md`) using `brain_action("rename")`

Published writing remains searchable and indexed in `+Published/`. Companion files (HTML pastes, exports) belong in `Assets/Attachments/` or `Assets/Generated/`, not alongside the writing file.

## Archiving

Published writing that has been superseded (e.g. a rewrite exists elsewhere):

1. Add `archiveddate: YYYY-MM-DD` to frontmatter
2. Add a supersession callout linking to the successor
3. Move the file from `+Published/` to `_Archive/`

The status stays `published` — archiving is an action, not a status.

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
