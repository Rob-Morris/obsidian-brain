# Mockups

Temporal artefact. Visual or interactive prototypes generated to explore a design direction.

## Purpose

A mockup is a concrete visualisation of an idea — a UI component, an app shell, a layout, a data flow diagram. Mockups are temporal because each one captures a design direction at a point in time. The same design might produce very different mockups as thinking evolves, tools change, or constraints shift.

Mockups bridge the gap between abstract design documents and real implementation. They're cheap to produce (especially with AI-assisted generation) and valuable for validating assumptions before committing to code.

## How to Write Mockups

- **Link to the design.** Every mockup should reference the design or project it's exploring. Follow [[.brain-core/standards/provenance]].
- **Record the prompt.** If the mockup was AI-generated, include the prompt that produced it. This makes the mockup reproducible and helps refine the generation process.
- **Note the verdict.** Was this a keeper? What needs iteration? What design decisions does it validate or invalidate?
- **One direction per file.** If you're exploring multiple alternatives, give each its own file. Comparing mockups is easier when they're separate artefacts.

## Naming

`yyyymmdd-mockup~{Title}.md` in `_Temporal/Mockups/yyyy-mm/`.

Example: `_Temporal/Mockups/2026-03/20260321-mockup~Brain App Main Shell.md`

## Frontmatter

```yaml
---
type: temporal/mockup
tags:
  - mockup
---
```

## Shaping

**Flavour:** Convergent
**Bar:** Visual intent is clear.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

When exploring a visual or interactive design direction — UI layouts, component designs, app shells, data visualisations. Particularly useful when paired with AI code generation tools that can produce working prototypes from a prompt.

## Template

[[_Config/Templates/Temporal/Mockups]]
