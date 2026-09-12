# Research

Temporal artefact. Investigation into a subject and capture of what is found.

## Purpose

Research captures what was found when investigating a subject at a point in time. Connect it to the project, design, or other context that prompted the investigation through ownership where structural, or links and tags where associative.

## When To Use

When investigating a subject in depth and/or capturing what was found from investigation — comparing approaches, gathering sources, or synthesising findings. The question research answers is "what did we learn about X?"

**Not to be confused with Reports.** If the content reviews a process you performed (audit, migration, diagnosis, implementation), that's a Report. Research is about what you *learned*; a Report is about what you *did*.

## How to Write Research

- **One topic per file.** Keep the scope focused.
- **Link to context.** Reference the project, design, or idea that prompted the research.
- **Include sources.** Link or cite where findings came from — external references, vault artefacts, code paths, conversations.

## Naming

`yyyymmdd-research~{Title}.md` in `_Temporal/Research/yyyy-mm/`.

Example: `_Temporal/Research/2026-03/20260307-research~Discord Animation Research.md`

## Frontmatter

```yaml
---
type: temporal/research
tags:
  - research
---
```

## Lifecycle

| Status | Meaning |
|---|---|
| `shaping` | The artefact is being shaped through structured Q&A. |
| `ready` | Shaping is complete and the artefact meets its bar. |

## Shaping

**Flavour:** Convergent
**Bar:** Investigation is thorough and conclusions clear.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

When investigating a subject in depth and/or capturing what was found from investigation — comparing approaches, gathering sources, or synthesising findings.

## Template

[[_Config/Templates/Temporal/Research]]
