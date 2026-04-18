# Daily Notes

Living artefact. High-level end-of-day summaries.

## Purpose

One file per day. When writing a daily note, use the day's log (`_Temporal/Logs/`) as source material but distil it into a brief recap — the log has the detail; the daily note has the overview. Format uses `## Tasks` (checkbox list) and `## Notes` (short topic sections).

## When To Use

When writing an end-of-day summary. Use the day's log as source material — distil activity into a brief recap with tasks and notes.

## Naming

`yyyy-mm-dd ddd.md` in `Daily Notes/`, date source `date`.

Example: `Daily Notes/2026-03-15 Sun.md`

The `date` frontmatter field is the subject date of the note — the day the note covers, which may differ from `created` when backfilling a missed day. The filename is a rendering of `date`.

## Frontmatter

```yaml
---
type: living/daily-note
tags:
  - daily-note
date:
---
```

## Template

[[_Config/Templates/Living/Daily Notes]]
