# Logs

Temporal artefact. Append-only daily activity logs.

## Purpose

One file per day. A running record of what happened, in chronological order. Logs are source material — transcripts, daily notes, and other artefacts can reference or distil them, but the log itself is the raw timeline.

## How to Write Log Entries

- **Append only.** New entries go at the bottom. Never edit or remove existing entries.
- **Timestamp each entry.** Use `HH:MM` or `HH:MM:SS` at the start of each entry.
- **Keep entries brief.** One or two sentences. Link to artefacts for detail rather than duplicating content.
- **Use wikilinks.** Reference the artefacts, pages, or concepts involved.
- **Tag cross-repo work.** When an entry relates to a different repository or project, prefix the description with the project name in italics — e.g. `*(Undertask)* Built the MCP server`. Entries without a tag are assumed to relate to the current vault.

## Relationship to Summary Artefacts

The log is the **raw chronological record** — every activity timestamped as it happens. Other artefacts (e.g. daily notes, weekly reviews) may distil or summarise logs, but the log itself is never back-edited to match a summary.

## Naming

`yyyymmdd-log.md` in `_Temporal/Logs/yyyy-mm/`, date source `date`.

Example: `_Temporal/Logs/2026-03/20260314-log.md`

The filename date is the subject day of the log, not the physical creation
time of the markdown file. Backfilled logs therefore keep the day they describe
even when the file itself was created later.

## Frontmatter

```yaml
---
type: temporal/log
tags:
  - log
date:
---
```

## Trigger

After completing meaningful work, append a timestamped entry to the day's log.

## Template

[[_Config/Templates/Temporal/Logs]]
