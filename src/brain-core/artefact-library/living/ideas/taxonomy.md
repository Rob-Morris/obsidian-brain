# Ideas

Living artefact. Where concepts are articulated and shaped to clarity.

## Purpose

Ideas are where you work out the conceptual *what* — what the problem is, what a solution might look like, and what's still open. An idea operates at the level of the problem and the concept, not the concrete mechanics (that's a design). Through iterative refinement an idea becomes clear enough to act on or design. It might eventually be adopted into a design, become a project, or stay here as a living reference.

## When To Use

When exploring a problem or developing a concept that isn't concrete enough for design work yet. Ideas and designs can both be shaped iteratively, but ideas are about *what* and *why*, while designs are about *how*. For quick, unformed sparks use Idea Logs instead — Ideas are for concepts being actively developed.

## Lifecycle

| Status | Meaning |
|---|---|
| `new` | Default. The idea exists but hasn't been developed. |
| `shaping` | The idea is being shaped and refined through Q&A. |
| `ready` | Fully shaped — clear enough to act on. |
| `adopted` | Adopted into a downstream artefact (e.g. design, project). The idea is no longer actively developed here. |
| `parked` | Set aside — not abandoned, but not being pursued. |

## Shaping

**Flavour:** Discovery
**Bar:** The problem is clearly articulated and the concept is specific enough to evaluate or design against.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Adoption

Adoption is a provenance pattern. When an idea is adopted into a downstream artefact (design, project, etc.):

1. Create the downstream artefact — carry forward open questions, carry forward the project tag (e.g. `project/my-project`)
2. Follow [[.brain-core/standards/provenance]] for lineage between the idea and the new artefact
3. Set the idea's `status: adopted` and move to `Ideas/+Adopted/`

## Terminal Status

When an idea reaches `adopted` status, move it to `Ideas/+Adopted/`. Adopted ideas remain searchable and indexed. No rename, no `archiveddate`.

**Agent contract:** if you land on an adopted idea, follow the adoption callout link to find the downstream artefact. Do not modify adopted ideas.

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
status: new                 # new | shaping | ready | adopted | parked
---
```

## Template

[[_Config/Templates/Living/Ideas]]
