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

When an idea is clear enough for structured design work, you can graduate it:

1. Create the design doc — carry forward open questions as decisions, carry forward the project tag (e.g. `project/my-project`)
2. Follow [[.brain-core/standards/provenance]] for lineage between the idea and the new design
3. Set the idea's `status: graduated` and move to `Ideas/+Graduated/`

## Terminal Status

When an idea reaches `graduated` status, move it to `Ideas/+Graduated/`. Graduated ideas remain searchable and indexed. No rename, no `archiveddate`.

**Agent contract:** if you land on a graduated idea, follow the graduation callout link to find the design doc. Do not modify graduated ideas.

## Lineage

When an idea originates from another artefact, follow [[.brain-core/standards/provenance]].

## Naming

`{Title}.md` in `Ideas/`.

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
