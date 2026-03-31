# Designs

Living artefact. Design documents, wireframes, mockups, and design-related notes.

## Purpose

A home for visual and structural design work. Each file captures a design for a specific feature, product, or concept. Link to the relevant project file if one exists.

## Lifecycle

| Status | Meaning |
|---|---|
| `proposed` | Candidate design awaiting a decision on whether to proceed. May still be shaped. |
| `shaping` | Default. The design is being explored and shaped — decisions are open. |
| `ready` | Design decisions closed — fully shaped and agreed, but implementation not yet started. |
| `active` | Implementation is in progress. |
| `implemented` | The design has been fully built. |
| `parked` | Set aside — not abandoned, but not being pursued. |
| `rejected` | Evaluated and declined. Kept as a record. |

When a plan targeting this design reaches `completed`, update the design to reflect the implemented changes. When all planned work is done, transition the design from `active` to `implemented`.

## Graduating from Proposed

When a design at `proposed` status is ready for a decision:

1. Create a decision log recording the verdict and reasoning
2. If accepted: set `status: shaping` and begin active design work
3. If rejected: set `status: rejected` — the design stays as a record of what was considered and why it was declined

## Terminal Status

When a design reaches a terminal status (`implemented` or `rejected`), move it to the corresponding `+Status` folder:

- **Implemented:** set `status: implemented`, add a supersession callout, move to `Designs/+Implemented/`
  ```markdown
  > [!info] Implemented
  > This design has been implemented. See [[link|title]] for the current source of truth.
  ```
- **Rejected:** set `status: rejected`, move to `Designs/+Rejected/`

Terminal designs remain searchable and indexed in their `+Status` folder. No rename, no `archiveddate`.

**Agent contract:** if you land on a terminal design, follow the supersession link (if present) to find the current source of truth. Do not modify terminal designs.

## Lineage

Follow [[.brain-core/standards/provenance]], including transcript linking for Q&A sessions:

```markdown
**Origin:** [[idea-slug|Source idea]]
**Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]
```

## How to Write Designs

- **Link to source.** Add an **Origin** line linking to the source idea doc.
- **List transcripts.** Reference Q&A sessions that shaped the design.
- **Track decisions.** Use a decisions table for open and resolved choices.
- **Maintain an Open questions section** for unresolved choices. When a question is resolved, move its entry to the decisions table.
- **Tag the project.** Use a nested project tag (e.g. `project/my-project`).

## Naming

`{Title}.md` in `Designs/`.

Example: `Designs/pistols-at-dawn-discord-bot.md`

## Frontmatter

```yaml
---
type: living/design
tags:
  - design
status: shaping             # proposed | shaping | ready | active | implemented | parked | rejected
---
```

## Template

[[_Config/Templates/Living/Designs]]
