# Friction Logs

Temporal artefact. Signal accumulator for maintenance.

## Purpose

A friction log captures a moment where an agent (or human) couldn't find something, hit conflicting information, or had to make an assumption. It is not a bug tracker — it's a signal accumulator. Individual friction entries are small and low-cost to create. Their value emerges when signals accumulate into patterns that point to maintenance tasks.

## How to Write Friction Logs

- **Capture in the moment.** Write the friction log as soon as you encounter the friction, while the context is fresh.
- **Be specific about file/section.** Name the exact file, section, or concept where the friction occurred. Vague friction is hard to act on.
- **Include impact.** What happened because of the friction? Did you guess wrong? Waste time searching? Produce incorrect output?
- **Suggest a fix.** Even a rough suggestion helps. The person doing maintenance doesn't need to rediscover the problem.

## Review

When reviewing accumulated friction logs (end of session or periodic maintenance), look for *recurring* patterns — the same friction appearing across multiple logs. A one-off friction is just a signal; a recurring friction is a lesson. When the same issue keeps surfacing, distil it into a gotcha in `[[_Config/User/gotchas]]` so future sessions avoid the pattern entirely.

## Naming

`yyyymmdd-friction--{slug}.md` in `_Temporal/Friction Logs/yyyy-mm/`.

Example: `_Temporal/Friction Logs/2026-03/20260321-friction--missing-archive-convention.md`

## Frontmatter

```yaml
---
type: temporal/friction-log
tags:
  - friction
---
```

## Trigger

When encountering missing context, conflicting information, or making assumptions during work.

## Template

[[_Config/Templates/Temporal/Friction Logs]]
