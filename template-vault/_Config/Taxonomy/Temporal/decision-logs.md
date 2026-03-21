# Decision Logs

Temporal artefact. Agent-generated point-in-time records of decisions.

## Purpose

A decision log captures the "why" behind choices — the question, the options considered, the tradeoffs, and the reasoning that led to the decision. Wiki captures concepts, logs capture activity, but neither captures decision rationale. Decision logs fill that gap. They are temporal because each is a snapshot of the reasoning at the moment the decision was made — not a living document to be revised. A new decision superseding an old one is just a new file with a provenance Origin link back.

## How to Write Decision Logs

- **State the question clearly.** What decision needed to be made? Frame it as a question.
- **List options considered.** Even options that were rejected — especially those. Future readers need to know what was weighed.
- **Be honest about tradeoffs.** Every option has downsides. Name them.
- **Link to origin.** What prompted the decision? A task, a friction log, a conversation — link to it.
- **Write it before the reasoning fades.** The decision is easy to remember; the reasoning is not.

## Naming

`yyyymmdd-decision--{slug}.md` in `_Temporal/Decision Logs/yyyy-mm/`.

Example: `_Temporal/Decision Logs/2026-03/20260321-decision--temporal-vs-living-friction-logs.md`

## Frontmatter

```yaml
---
type: temporal/decision-log
tags:
  - decision
---
```

## Trigger

After making a significant decision during work, capture the reasoning before it fades.

## Template

[[_Config/Templates/Temporal/Decision Logs]]
