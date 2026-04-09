---
name: swarm-test
description: >
  Dispatch a swarm of small agents to test docs, implementations, or designs.
  Two modes: 'review' (quick comprehension check with fix-and-iterate) and
  'evaluate' (structured evaluation with scored results and recommendations).
---

# Swarm Test

Two modes, one purpose: find gaps before users do.

## Modes

### review (default)

Quick quality gate. Dispatches 8-12 haiku agents, each trying to accomplish a
real task using the work product. Agents report where they got stuck. Gaps are
identified, proposed to the user, and fixed. Use after completing a body of work.

**Skill:** `swarm-test:review`

### evaluate

Structured evaluation with an intelligent orchestrator. Assesses the target,
designs a test plan mixing comprehension, factual, and counter-factual scenarios,
proposes it for approval, dispatches agents, and synthesises scored results with
findings and recommendations.

**Skill:** `swarm-test:evaluate`

## Routing

- `swarm-test <target>` → **review** (the quick default)
- `swarm-test evaluate <target>` → **evaluate**
- "Which is better, A or B?" → **evaluate** (comparison shape)
- "Smoke test this" → **review**
- "How good are these docs?" → **evaluate**

When a mode is selected, read and follow the full skill file for that subskill.
