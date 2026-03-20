# Idea Logs

Temporal artefact. Quick captures of new ideas in rough form.

## Purpose

A low-friction place to jot down ideas as they arrive. Each entry is tagged and date-prefixed. Ideas that gain traction graduate into a living artefact for fleshing out, and fleshed-out ideas may graduate into designs.

## How to Write Idea Logs

- **Capture immediately.** Write the idea down before it slips away. Polish comes later.
- **One idea per file.** Keep entries focused. Multiple related ideas can share a file, but separate concepts get separate files.
- **Tag generously.** Tags make ideas findable later when context has faded.
- **Link to context.** If the idea came from something specific, link to it.

## Graduation Path

1. **Idea log entry** — raw capture, a few sentences
2. **Living artefact** — fleshed out, explored, living document
3. **Design** — shaped proposal with decisions and structure

When an idea graduates, link back to the original idea log from the new artefact.

## Spinning Out to an Idea

When an idea log entry has enough substance to become a living idea:

1. Create a new idea doc in `Ideas/` with an **Origin** line in the body linking back to this idea log
2. Add an inline note in the idea log body linking to the new idea:
   ```markdown
   > [!info] Spun out
   > This idea has been spun out to [[Ideas/slug|Title]].
   ```
3. Carry forward relevant tags (especially project tags)

## Naming

`yyyymmdd-idea-log--{slug}.md` in `_Temporal/Idea Logs/yyyy-mm/`.

Example: `_Temporal/Idea Logs/2026-03/20260316-voice-memo-transcriber.md`

## Frontmatter

```yaml
---
type: temporal/idea-log
tags:
  - idea
  - topic-tag
---
```

## Trigger

When a new idea strikes during a session, capture it as an idea log entry before it slips away.

## Template

[[_Config/Templates/Temporal/Idea Logs]]
