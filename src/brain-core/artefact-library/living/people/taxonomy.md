# People

Living artefact. Person index files.

## Purpose

One file per person, serving as the living source of truth for what you know about them. The person file is the hub — observations, journal entries, transcripts, and other artefacts link back to it via the person tag. Updated as you learn new things; superseded facts are replaced, not accumulated.

## Naming

`{name}.md` in `People/`.

Example: `People/Alice Smith.md`

## Frontmatter

```yaml
---
type: living/person
tags:
  - person/{slug}
status: active
---
```

Every file related to a person should use the nested person tag, e.g. `person/alice-smith`.

## Lifecycle

| Status | Meaning |
|---|---|
| `active` | Default. Actively maintained. |
| `archived` | No longer in regular contact. Preserved for reference. |

## Template

[[_Config/Templates/Living/People]]
