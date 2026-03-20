# Design Transcripts

Temporal artefact. Q&A refinement transcripts tied to source artefacts.

## Purpose

A record of a Q&A session that refines a design, research note, or other artefact. Each transcript is bound to a single source document. The file begins with a wikilink back to its source. Questions are prefixed with `Q.` and answers are blockquotes prefixed with `> A.`.

## How to Write Design Transcripts

- **One transcript per source artefact.** Don't merge separate Q&A sessions into one file.
- **Link to the source.** The first line after frontmatter is a wikilink to the source document.
- **Preserve the flow.** Record Q&A in order. Don't reorganise or editorialize.
- **Tag the source type.** Include the source document type (e.g. `design`, `research`) in tags and filename.

## Naming

`yyyymmdd-{sourcedoctype}-transcript--{slug}.md` in `_Temporal/Design Transcripts/yyyy-mm/`.

Example: `_Temporal/Design Transcripts/2026-03/20260307-design-transcript--pistols-at-dawn-discord-bot.md`

## Frontmatter

```yaml
---
type: temporal/design-transcript
tags:
  - transcript
  - source-type
---
```

## Trigger

After refining an artefact through Q&A, capture the raw Q&A as a design transcript.

## Template

[[_Config/Templates/Temporal/Design Transcripts]]
