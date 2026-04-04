# Reports

Temporal artefact. Overviews of detailed processes.

## Purpose

A report is an overview of a detailed process, written to make it easy to understand what that process *meant*. The process itself may be research, diagnosis, investigation, analysis, migration, audit, or any extended multi-step activity. The report distils it into findings, implications, and where relevant, recommended next steps. Reports are temporal because they capture the state of understanding at a particular point in time.

## Naming

`yyyymmdd-report~{Title}.md` in `_Temporal/Reports/yyyy-mm/`.

Example: `_Temporal/Reports/2026-03/20260320-report~API Performance Audit.md`

## Frontmatter

```yaml
---
type: temporal/report
tags:
  - report
---
```

## Shaping

**Flavour:** Convergent
**Bar:** Findings are complete and coherent.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

After completing a detailed process, distil what it meant into a report.

## Template

[[_Config/Templates/Temporal/Reports]]
