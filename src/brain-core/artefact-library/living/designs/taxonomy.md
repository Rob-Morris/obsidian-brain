# Designs

Living artefact. Design documents, wireframes, mockups, and design-related notes.

## Purpose

A home for visual and structural design work. Each file captures a design for a specific feature, product, or concept. Link to the relevant project file if one exists.

## Lifecycle

| Status | Meaning |
|---|---|
| `shaping` | Default. The design is being explored and shaped — decisions are open. |
| `active` | The design is agreed and being implemented. |
| `implemented` | The design has been fully built. |
| `parked` | Set aside — not abandoned, but not being pursued. |

When a plan targeting this design reaches `completed`, update the design to reflect the implemented changes. When all planned work is done, transition the design from `active` to `implemented`.

When you identify a contemplated change to an existing design that needs a decision but isn't being resolved right now, capture it as a design proposal rather than editing the design directly.

## Archiving

When a design reaches `implemented` status, authority transfers from the design to the implementation. Follow [[.brain-core/standards/archiving]] with these design-specific details:

- Set `status: implemented`
- Use this supersession callout:
  ```markdown
  > [!info] Implemented
  > This design has been implemented. See [[link|title]] for the current source of truth.
  ```
- Move to `Designs/_Archive/`

**Agent contract:** if you land on an archived design, follow the supersession link to find the current source of truth. Do not modify archived designs.

## Lineage

Follow [[.brain-core/standards/provenance]]. Designs additionally track transcripts:

```markdown
**Origin:** [[idea-slug|Source idea]]
**Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]
```

## How to Write Designs

- **Link to source.** Add an **Origin** line linking to the source idea doc.
- **List transcripts.** Reference Q&A sessions that shaped the design.
- **Track decisions.** Use a decisions table for open and resolved choices.
- **Maintain an Open questions section** for unresolved choices and pending design proposals. When a proposal is accepted and implemented, move its entry from open questions to the decisions table.
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
status: shaping             # shaping | active | implemented | parked
---
```

## Template

[[_Config/Templates/Living/Designs]]
