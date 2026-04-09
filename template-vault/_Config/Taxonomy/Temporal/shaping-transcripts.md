# Shaping Transcripts

Temporal artefact. Q&A refinement transcripts tied to source artefacts.

## Purpose

A record of shaping sessions that refine an artefact — a design, research note, idea, plan, or anything else being refined through back-and-forth. A transcript may serve multiple source artefacts if shaping expands in scope.

## How to Write Shaping Transcripts

- **One file per day per artefact.** If multiple shaping sessions happen on the same artefact in one day, they share a file. `start-shaping` handles this — it appends to an existing file or creates a new one.
- **Heading hierarchy:**
  - `#` — transcript title (set by template)
  - `##` — session boundary: `## Refine session start — 14:30` (set by `start-shaping`)
  - `###` — speaker turn: `### Agent` or `### User`
- **Both speakers treated equally.** Agent text and user text are both prose under `###` headings. No blockquotes.
- **Record what was said, nothing more.** The transcript is a literal record of the conversation. Decision references, progress tracking, and resolution markers appear naturally because the agent said them. Do not add editorial synthesis, summaries, or after-the-fact commentary — that belongs in the artefact.
- **Link to the source.** The `**Source:**` line (set by template) lists wikilinks to all source artefacts.
- **Multi-source.** As shaping expands to touch additional artefacts, append them to the source line.

## Naming

`yyyymmdd-shaping-transcript~{Title}.md` in `_Temporal/Shaping Transcripts/yyyy-mm/`.

Example: `_Temporal/Shaping Transcripts/2026-03/20260307-shaping-transcript~Pistols at Dawn Discord Bot.md`

## Frontmatter

```yaml
---
type: temporal/shaping-transcript
tags:
  - transcript
---
```

## Trigger

At the start of shaping, create a shaping transcript linked to the source artefact(s).

## Template

[[_Config/Templates/Temporal/Shaping Transcripts]]

## See Also

[[.brain-core/standards/shaping]]
