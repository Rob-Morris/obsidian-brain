# Shaping Transcripts

Temporal artefact. Q&A refinement transcripts tied to source artefacts.

## Purpose

A record of a Q&A session that shapes an artefact — a design, research note, idea, plan, or anything else being refined through back-and-forth. Each transcript is bound to a single source document. The file begins with a wikilink back to its source. Questions are prefixed with `Q.` and answers are blockquotes prefixed with `> A.`.

## How to Write Shaping Transcripts

- **One transcript per source artefact.** Don't merge separate Q&A sessions into one file.
- **Link to the source.** The first line after frontmatter is a wikilink to the source document.
- **Preserve the flow.** Record Q&A in order. Don't reorganise or editorialize.
- **Tag the source type.** Include the source document type (e.g. `design`, `research`) in tags and filename.

## Naming

`yyyymmdd-{sourcedoctype}-transcript~ {Title}.md` in `_Temporal/Shaping Transcripts/yyyy-mm/`.

Example: `_Temporal/Shaping Transcripts/2026-03/20260307-design-transcript~ Pistols at Dawn Discord Bot.md`

## Frontmatter

```yaml
---
type: temporal/shaping-transcript
tags:
  - transcript
  - source-type
---
```

## Trigger

After refining an artefact through Q&A, capture the raw Q&A as a shaping transcript.

## Template

[[_Config/Templates/Temporal/Shaping Transcripts]]
