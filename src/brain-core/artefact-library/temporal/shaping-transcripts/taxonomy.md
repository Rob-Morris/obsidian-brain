# Shaping Transcripts

Temporal artefact. Q&A refinement transcripts tied to source artefacts.

## Purpose

A record of a shaping session that refines an artefact — a design, research note, idea, plan, or anything else being refined through back-and-forth. A transcript may serve multiple source artefacts if shaping expands in scope. The file begins with a wikilink back to its source artefacts. Questions are prefixed with `Q.` and answers are blockquotes prefixed with `> A.`.

## How to Write Shaping Transcripts

- **One transcript per shaping session.** Don't merge separate sessions into one file.
- **Topic switch = new transcript.** If a new session begins, create a new transcript. The boundary is the session, not the conversation.
- **Link to the source.** The first line after frontmatter lists wikilinks to all source artefacts.
- **Preserve the flow.** Record Q&A in order. Don't reorganise or editorialize.
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
