# Logs

Temporal artefact. Append-only daily activity logs.

## Purpose

One file per day. A running record of what happened, in chronological order. Logs are source material — transcripts, daily notes, and other artefacts can reference or distil them, but the log itself is the raw timeline.

## How to Write Log Entries

- **Append only.** New entries go at the bottom. Never edit or remove existing entries.
- **Timestamp each entry.** Use `HH:MM` or `HH:MM:SS` at the start of each entry.
- **Keep entries brief.** One or two sentences. Link to artefacts for detail rather than duplicating content.
- **Use wikilinks.** Reference the artefacts, pages, or concepts involved.

## Naming

`log--yyyy-mm-dd.md` in `_Temporal/Logs/yyyy-mm/`.

Example: `_Temporal/Logs/2026-03/log--2026-03-14.md`

## Frontmatter

```yaml
---
type: temporal/log
tags:
  - log
---
```

## Template

[[_Config/Templates/Temporal/Logs|Logs]]
