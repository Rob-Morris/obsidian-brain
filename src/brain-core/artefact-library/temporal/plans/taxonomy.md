# Plans

Temporal artefact. Pre-work plans written before complex work begins.

## Purpose

A plan captures the intended approach before implementation starts. It records the goal, strategy, and key files involved — giving future readers (and agents) context for why decisions were made.

## How to Write Plans

- **Write before you start.** The plan comes first; implementation follows. A plan may originate from a design doc (including one accepted from `proposed` status), or from scratch. If the design was accepted from `proposed`, link to both the design and the decision log that accepted it in the plan's Origin line.
- **Keep it concise.** Enough detail to align on approach, not a full specification.
- **Link to artefacts.** Reference the files, wiki pages, or tasks involved.
- **Update status.** Move from `draft` → `shaping` → `approved` → `implementing` → `completed` as the work progresses.
- **Close the loop on completion.** When marking a plan `completed`: if the plan targets a design doc, update the design to reflect what was implemented. Link the completed plan from any artefacts it fulfilled.

## Naming

`yyyymmdd-plan~{Title}.md` in `_Temporal/Plans/yyyy-mm/`.

Example: `_Temporal/Plans/2026-03/20260315-plan~API Refactor.md`

## Frontmatter

```yaml
---
type: temporal/plan
tags:
  - plan
status: draft
---
```

Status values: `draft`, `shaping`, `approved`, `implementing`, `completed`.

## Shaping

**Flavour:** Convergent
**Bar:** Approach is clear and agreed.
**Completion status:** `approved`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

Before complex work, write the detailed plan.

## Template

[[_Config/Templates/Temporal/Plans]]
