# Bug Logs

Temporal artefact. Point-in-time records of broken behaviour.

## Purpose

A bug log captures something that is objectively broken — wrong output, failed behaviour, a correctness failure. Unlike a friction log (which accumulates signals about suboptimal experiences), a single bug is individually actionable and needs resolution.

## Bug vs Friction

- **Bug** = correctness failure. It's wrong. A broken template, a script that drops data, a process that fails silently.
- **Friction** = quality/experience issue. It's harder or worse than it should be. Confusing docs, inconsistent naming, clunky workflow.

If you're unsure, it's probably friction. Bugs should be clearly identifiable as "this is broken."

## How to Write Bug Logs

- **Be specific.** Name the exact file, command, or behaviour that is broken.
- **Describe expected behaviour.** What should have happened?
- **Include steps to reproduce.** Even rough steps help. The person fixing the bug shouldn't have to rediscover it.
- **Add context.** What were you doing when you found it? What's the impact?

## Lifecycle

Bug logs track status in frontmatter: `open` → `resolved`. When you fix a bug, update the status and fill in the Resolution section.

## Naming

`yyyymmdd-bug~{Title}.md` in `_Temporal/Bug Logs/yyyy-mm/`.

Example: `_Temporal/Bug Logs/2026-03/20260328-bug~Router Compile Drops Empty Triggers.md`

## Frontmatter

```yaml
---
type: temporal/bug-log
status: open
tags:
  - bug
---
```

## Trigger

When encountering something that is objectively broken or producing wrong output.

## Template

[[_Config/Templates/Temporal/Bug Logs]]
