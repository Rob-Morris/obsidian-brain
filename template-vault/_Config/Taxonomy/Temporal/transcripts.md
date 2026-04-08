# Transcripts

Temporal artefact. Conversation transcripts.

## Purpose

A record of a conversation — with a person, an AI, or during a meeting. Captures the exchange as it happened, bound to the moment it occurred. For Q&A sessions that refine a specific artefact, use Shaping Transcripts instead.

## How to Write Transcripts

- **One transcript per conversation.** Don't merge separate conversations into one file.
- **Identify participants.** Use consistent labels (names, roles, or abbreviations) for each speaker.
- **Preserve the flow.** Record the conversation in order. Don't reorganise or editorialize.
- **Link to context.** If the conversation is about a specific artefact, link to it in the body or frontmatter tags.

## Naming

`yyyymmdd-transcript~{Title}.md` in `_Temporal/Transcripts/yyyy-mm/`.

Example: `_Temporal/Transcripts/2026-03/20260314-transcript~Rust API Design.md`

## Frontmatter

```yaml
---
type: temporal/transcript
tags:
  - transcript
---
```

## Trigger

After a conversation worth preserving — with a person, an AI, or during a meeting — capture it as a transcript. For Q&A sessions that refine a specific artefact, use Shaping Transcripts instead.

## Template

[[_Config/Templates/Temporal/Transcripts]]
