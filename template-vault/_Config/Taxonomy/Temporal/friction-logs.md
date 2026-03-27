# Friction Logs

Temporal artefact. Signal accumulator for maintenance.

## Purpose

A friction log captures any moment where something generated resistance — an agent (or human) couldn't find something, hit conflicting information, had to make an assumption, encountered an inconsistency, or experienced something unintended or suboptimal. Each friction log is an instance of something which generated friction, and a signal about an opportunity for reducing it.

Friction logs are not a bug tracker — they're a signal accumulator. Individual friction entries are small and low-cost to create. Their value emerges when signals accumulate into patterns that point to maintenance tasks.

## What Counts as Friction

- **Missing context** — couldn't find something, had to guess, made an assumption
- **Conflicting information** — two sources disagree, a convention isn't followed consistently
- **Inconsistencies** — naming doesn't match, structure deviates from the pattern, metadata is wrong
- **Unintended outcomes** — something worked but produced a result that wasn't expected or desired
- **Suboptimal experience** — a workflow was clunkier than it should be, something took longer than expected

If you felt resistance, it's friction. Log it.

## How to Write Friction Logs

- **Capture in the moment.** Write the friction log as soon as you encounter the friction, while the context is fresh.
- **Be specific about file/section.** Name the exact file, section, or concept where the friction occurred. Vague friction is hard to act on.
- **Include impact.** What happened because of the friction? Did you guess wrong? Waste time searching? Produce incorrect output? Get a suboptimal result?
- **Suggest a fix.** Even a rough suggestion helps. The person doing maintenance doesn't need to rediscover the problem.

## Review

When reviewing accumulated friction logs (end of session or periodic maintenance), look for *recurring* patterns — the same friction appearing across multiple logs. A one-off friction is just a signal; a recurring friction is a lesson. When the same issue keeps surfacing, distil it into a gotcha in `[[_Config/User/gotchas]]` so future sessions avoid the pattern entirely.

## Naming

`yyyymmdd-friction~{Title}.md` in `_Temporal/Friction Logs/yyyy-mm/`.

Example: `_Temporal/Friction Logs/2026-03/20260321-friction~Missing Archive Convention.md`

## Frontmatter

```yaml
---
type: temporal/friction-log
tags:
  - friction
---
```

## Trigger

When encountering missing context, conflicting information, inconsistencies, unexpected behaviour, or suboptimal outcomes during work.

## Template

[[_Config/Templates/Temporal/Friction Logs]]
