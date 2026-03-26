# Journals

Living artefact. Named journal streams.

## Purpose

A journal is a living summary of a personal stream — its themes, patterns, and the current arc. One file per journal. The journal hub evolves as entries accumulate, reflecting what the stream is about *now*, not just what it started as. Journal entries are the moments; the hub is the interpreted picture of the whole.

Follows the same hub pattern as Projects and People: a living summary that groups temporal artefacts via `journal/{slug}` nested tags.

## Examples

- **Personal** — general life journal
- **Health** — health and fitness reflections
- **Creative Writing** — writing practice and observations
- **Journal** — for single-journal users who don't need multiple streams

## Naming

`{Title}.md` in `Journals/`.

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

## Temporal Handshake

Journal entries tagged `journal/{slug}` feed this hub. As entries accumulate, the hub evolves to reflect emerging themes, shifts in focus, recurring topics. Entries preserve the moments; the hub reflects the arc.

Not every entry triggers a hub update. But when entries reveal a shift — a new theme emerging, a concern resolving, a focus changing — distil it into the hub.

## Ingestion

Match the effort to the input. Don't ask unnecessary questions — just create what you can and grow it later.

### Minimal input → minimal hub, no fuss

If the user wants to start a journal stream, create the hub with a name and a line about what the stream is for. Don't ask for more detail. The first few entries will shape it naturally.

### Rich input → decompose into artefacts

If the user dumps a mix of reflections and observations, decompose:

- **Personal reflections and recollections** → journal entries, tagged `journal/{slug}`
- **Discrete facts learned** → observations, tagged with relevant hubs
- **Ideas surfaced** → idea logs
- **Timeline** → log entry

Write entries first, then update the hub with what's shifted.

## Contextual Linking

Weave links to notable entries in prose where they illuminate the arc. Don't list entries chronologically — the tag query handles that. The hub adds interpretation: what the entries mean together, not what they are individually.

## Template

[[_Config/Templates/Living/Journals]]
