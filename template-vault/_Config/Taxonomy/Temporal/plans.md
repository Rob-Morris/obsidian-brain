# Plans

Temporal artefact. Pre-work plans written before complex work begins.

## Purpose

A plan captures the intended approach before implementation starts. It records the goal, strategy, and key files involved — giving future readers (and agents) context for why decisions were made.

## How to Write Plans

- **Write before you start.** The plan comes first; implementation follows.
- **Keep it concise.** Enough detail to align on approach, not a full specification.
- **Link to artefacts.** Reference the files, wiki pages, or tasks involved.
- **Update status.** Move from `draft` → `approved` → `implementing` → `completed` as the work progresses.

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

Status values: `draft`, `approved`, `implementing`, `completed`.

## Trigger

Before complex work, write the detailed plan.

## Template

[[_Config/Templates/Temporal/Plans]]
