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

## Lineage

Add lineage links at the top of the body, below the heading:

```markdown
**Origin:** [[idea-slug|Source idea]]
**Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]
```

## How to Write Designs

- **Link to source.** Add an **Origin** line linking to the source idea doc.
- **List transcripts.** Reference Q&A sessions that shaped the design.
- **Track decisions.** Use a decisions table for open and resolved choices.
- **Tag the project.** Use a nested project tag (e.g. `project/my-project`).

## Naming

`{slug}.md` in `Designs/`.

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
