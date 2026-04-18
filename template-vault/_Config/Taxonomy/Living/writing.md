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

When a piece reaches `published` status, `brain_edit` automatically moves it to `Writing/+Published/` and renames the file with a `yyyymmdd-` prefix (rendered from `publisheddate`). The `publisheddate` is set to today on the transition into `published` unless already present. Companion files (HTML pastes, exports) belong in `Assets/Attachments/` or `Assets/Generated/`, not alongside the writing file.

## Archiving

Published writing that has been superseded (e.g. a rewrite exists elsewhere):

1. Add `archiveddate: YYYY-MM-DD` to frontmatter
2. Add a supersession callout linking to the successor
3. Move the file from `+Published/` to `_Archive/`

The status stays `published` — archiving is an action, not a status.

## Naming

Primary folder: `Writing/`.

### Rules

| Match field | Match values | Pattern | Date source |
|---|---|---|---|
| `status` | `draft`, `editing`, `review`, `parked` | `{Title}.md` |  |
| `status` | `published` | `yyyymmdd-{Title}.md` | `publisheddate` |

Example (draft): `Writing/On Tool Use.md`
Example (published): `Writing/+Published/20260315-On Tool Use.md`

## On Status Change

When `status` transitions to `published`, set `publisheddate` to today (if not already present).

## Frontmatter

```yaml
---
type: living/writing
tags:
  - writing
status: draft
---
```

`publisheddate: YYYY-MM-DD` is required when `status: published`.

## Template

[[_Config/Templates/Living/Writing]]
