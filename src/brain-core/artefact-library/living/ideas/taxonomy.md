# Ideas

Living artefact. Where concepts are articulated and shaped to clarity.

## Purpose

Ideas are where you develop a concept — what it is, what problem it solves, and what's still open — through iterative refinement until it's clear enough to act on or design. An idea might eventually become a design, a project, or a note — or it might stay here as a living reference.

## Lifecycle

| Status | Meaning |
|---|---|
| `new` | Default. The idea exists but hasn't been developed. |
| `developing` | The idea is actively being shaped and refined. |
| `graduated` | Promoted to a design doc. The idea is no longer actively developed here. |
| `parked` | Set aside — not abandoned, but not being pursued. |

## Graduating to Design

When an idea is clear enough for structured design work, you can graduate it. Follow [[.brain-core/standards/provenance]] for lineage and [[.brain-core/standards/archiving]] for the archive workflow. Additionally:

1. Set the idea's `status: graduated`
2. Carry forward open questions from the idea as decisions in the design doc
3. Carry forward the project tag (e.g. `project/my-project`)

**Agent contract:** if you land on an archived idea, follow the graduation callout link to find the design doc. Do not modify archived ideas.

## Lineage

When an idea originates from another artefact, follow [[.brain-core/standards/provenance]].

## Naming

`{name}.md` in `Ideas/`.

Example: `Ideas/voice-controlled-task-manager.md`

## Frontmatter

```yaml
---
type: living/idea
tags:
  - idea
status: new                 # new | developing | graduated | parked
---
```

## Template

[[_Config/Templates/Living/Ideas]]
