# Journals

Living artefact. Named journal streams.

## Purpose

A journal is a named container for journal entries — a personal stream of reflections, recollections, and life updates. One file per journal. The journal file describes what the stream is for; the entries themselves are temporal artefacts grouped by the journal's nested tag.

Follows the same hub pattern as Projects: a living index that groups temporal artefacts via `journal/{slug}` nested tags.

## Examples

- **Personal** — general life journal
- **Health** — health and fitness reflections
- **Creative Writing** — writing practice and observations
- **Journal** — for single-journal users who don't need multiple streams

## Naming

`{name}.md` in `Journals/`.

Example: `Journals/Personal.md`

## Frontmatter

```yaml
---
type: living/journal
tags:
  - journal/{slug}
status: active
---
```

The nested tag (e.g. `journal/personal`) is what connects journal entries to this journal.

## Lifecycle

| Status | Meaning |
|---|---|
| `active` | Default. Accepting new entries. |
| `archived` | No longer active. Existing entries preserved. |

## Template

[[_Config/Templates/Living/Journals]]
