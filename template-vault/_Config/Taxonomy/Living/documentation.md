# Documentation

Living artefact. Guides, standards, and reference material.

## Purpose

Technical docs, style guides, and any prescriptive reference that governs how work gets done. These evolve over time as understanding deepens or requirements change.

## When To Use

When writing prescriptive reference material — guides, standards, procedures, or style guides that govern how work gets done. Not for explanatory knowledge (use Wiki) or personal notes (use Notes).

## Lifecycle

| Status | Meaning |
|---|---|
| `new` | Stub. Placeholder, little or no content. |
| `shaping` | Being actively written or reworked. Not yet authoritative. |
| `ready` | Fully shaped, not yet in effect. |
| `active` | Default. Current and authoritative. |
| `deprecated` | Superseded or no longer applicable. |

## Shaping

**Flavour:** Convergent
**Bar:** Content is complete, accurate, and clear enough to govern work.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Terminal Status

When a doc reaches `deprecated` status:

1. Set `status: deprecated`
2. Add a supersession callout linking to the replacement (if any):
   ```markdown
   > [!info] Deprecated
   > This document has been superseded. See [[link|title]] for the current version.
   ```
3. Move to `Documentation/+Deprecated/`

Deprecated docs remain searchable and indexed in `+Deprecated/`. No rename, no `archiveddate`.

**Agent contract:** if you land on a deprecated doc, follow the supersession link (if present) to find the current source of truth. Do not treat deprecated docs as authoritative.

## Naming

`{Title}.md` in `Documentation/`.

Example: `Documentation/ai-writing-style-guide.md`

## Frontmatter

```yaml
---
type: living/documentation
tags:
  - documentation
status: active              # new | shaping | ready | active | deprecated
---
```

## Template

[[_Config/Templates/Living/Documentation]]
