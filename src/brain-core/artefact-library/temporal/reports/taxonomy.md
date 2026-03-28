# Reports

Temporal artefact. Overviews of detailed processes.

## Purpose

A report is an overview of a detailed process, written to make it easy to understand what that process *meant*. The process itself may be research, diagnosis, investigation, analysis, migration, audit, or any extended multi-step activity. The report distils it into findings, implications, and where relevant, recommended next steps. Reports are temporal because they capture the state of understanding at a particular point in time.

## When To Use

When reviewing a process performed — audit, migration, diagnosis, implementation, analysis. The question a report answers is "what did we do and what did it mean?"

**Not to be confused with Research.** If the content investigates a topic for the first time (comparing approaches, gathering sources, synthesising external findings), that's Research. A report is about what you *did*; Research is about what you *learned*.

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

## Trigger

After completing a detailed process, distil what it meant into a report.

## Template

[[_Config/Templates/Temporal/Reports]]
