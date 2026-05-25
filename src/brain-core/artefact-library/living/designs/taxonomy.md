# Designs

Living artefact. Design documents, wireframes, mockups, and design-related notes.

## Purpose

A home for visual and structural design work. Each file captures a design for a specific feature, product, or concept. Link to the relevant project file if one exists.

## When To Use

When working out the concrete shape of something — structure, mechanics, trade-offs. Designs typically focus on *how*, but they can also explore *what* and *why* when the problem space needs structured thinking. Ideas are more conceptual and exploratory; designs are more concrete and specific. Either can be shaped iteratively. For a contemplated change that needs a decision before proceeding, create a design at `proposed` status.

## Lifecycle

| Status | Meaning |
|---|---|
| `proposed` | Candidate design awaiting a decision on whether to proceed. May still be shaped. |
| `shaping` | Default. The design is being explored and shaped — decisions are open. |
| `ready` | Design decisions closed — fully shaped and agreed, but implementation not yet started. |
| `active` | Implementation is in progress. |
| `implemented` | The design has been fully built. Terminal — move to `+Implemented/`. |
| `deprecated` | The design is no longer the source of truth. Reason (superseded, rejected, retired, etc.) is captured in a callout in the body. Terminal — move to `+Deprecated/`. |
| `parked` | Set aside — not abandoned, but not being pursued. Non-terminal; may resume. |

When a plan targeting this design reaches `completed`, update the design to reflect the implemented changes. When all planned work is done, transition the design from `active` to `implemented`.

## Graduating from Proposed

When a design at `proposed` status is ready for a decision:

1. Create a decision log recording the verdict and reasoning
2. If accepted: set `status: shaping` and begin active design work
3. If rejected: set `status: deprecated` and add a `> [!info] Deprecated — rejected` callout — the design stays as a record of what was considered and why it was declined

## Terminal Status

When a design reaches a terminal status (`implemented` or `deprecated`), move it to the corresponding `+Status` folder:

- **Implemented:** set `status: implemented`, add a context callout, move to `Designs/+Implemented/`
  ```markdown
  > [!info] Implemented
  > This design has been implemented. See [[link|title]] for the current source of truth.
  ```
- **Deprecated:** set `status: deprecated`, add a reason callout, move to `Designs/+Deprecated/`. The reason captures why the design is no longer the source of truth:
  ```markdown
  > [!info] Deprecated — superseded by [[link|title]]
  > [!info] Deprecated — rejected: design failed shaping bar
  > [!info] Deprecated — retired: no longer maintained
  ```

Terminal designs remain searchable and indexed in their `+Status` folder. No rename, no `archiveddate`.

**Agent contract:** if you land on a terminal design, follow the callout link (if present) to find the current source of truth. Do not modify terminal designs.

## Lineage

Follow [[.brain-core/standards/provenance]], including transcript linking for Q&A sessions:

```markdown
**Origin:** [[idea-key|Source idea]]
**Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]
```

## Shaping

**Flavour:** Convergent
**Bar:** All design decisions are resolved, trade-offs documented, and the design is specific enough to plan against.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## How to Write Designs

- **Link to source.** Add an **Origin** line linking to the source idea doc.
- **List transcripts.** Reference Q&A sessions that shaped the design.
- **Track decisions.** Use a decisions table for open and resolved choices.
- **Maintain an Open questions section** for unresolved choices. When a question is resolved, move its entry to the decisions table.
- **Relate or own deliberately.** Use a `project/{key}` tag when the design is related to a project. If the design is structurally owned by a project, also set `parent: project/{key}`.

## Naming

`{Title}.md` in `Designs/`.

Example: `Designs/pistols-at-dawn-discord-bot.md`

## Frontmatter

```yaml
---
type: living/design
tags:
  - design
status: shaping             # proposed | shaping | ready | active | implemented | deprecated | parked
---
```

## Template

[[_Config/Templates/Living/Designs]]
