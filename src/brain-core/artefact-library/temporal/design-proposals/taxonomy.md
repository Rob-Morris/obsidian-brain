# Design Proposals

Temporal artefact. A structured recommendation for a contemplated change that needs a decision before action.

## Purpose

A design proposal captures something the brain agent identifies during vault work that should change — in a design, a codebase, a system, or an upstream library — but where the decision isn't being made right now. It packages the finding, recommendation, and decision context so someone (human or agent) can come back to it later and act on it.

Design proposals sit between ideas and plans in the pipeline. An idea is exploratory ("what if..."). A plan is ready to execute ("do this"). A design proposal is in between: "we found something concrete that should change, here's what we recommend, but it needs a decision first."

## When To Use

When vault work surfaces a contemplated change to a design, codebase, or system that needs a decision before action — and you want to capture it to come back to later.

Not for in-flow design edits. If you're actively shaping a design and making decisions, just edit the design and log decisions in-flow. A design proposal is for when you identify something that should change but the decision isn't being made right now.

Not for vague ideas (use idea logs) or ready-to-execute work (use plans).

Common triggers:
- Vault work validates an improvement that should propagate to a codebase or upstream library the brain agent can't directly modify
- An ingestion or friction log surfaces a design gap that's out of scope for the current session
- Shaping in one area reveals a needed change in another area's design

## Lifecycle

proposed → accepted / rejected / deferred

Full chain when accepted: Design proposal (proposed) → Decision log (records the verdict and reasoning) → Proposal status updated (accepted) → Plan created (actionable steps for the implementing agent) → Plan executed → Plan marked implemented → Design doc updated to reflect the new reality.

Each step produces its own artefact with provenance links back through the chain.

## Structure

A design proposal has five sections:

1. Origin — what prompted this. Link to the work, friction log, ingestion, or session that surfaced the finding.
2. Target — what it affects. A design doc, a codebase, a system, an upstream library. A proposal may have multiple targets — e.g. a cross-cutting concern that touches a master design and several sub-designs. List all affected targets; the proposal is one artefact even when the change spans many.
3. Finding — what was discovered, with enough context for the decision-maker to understand without needing the original conversation.
4. Recommendation — what we think should happen. Specific enough to act on.
5. Decision needed — what the decision-maker needs to weigh. Options if applicable. What changes if accepted, what stays the same if rejected.

## How to Write Design Proposals

- Be self-contained. The proposal should be readable by an agent or human with no context of the session that produced it.
- Link to evidence. Reference the friction logs, idea logs, or work that validated the finding.
- Be specific about the target. Name the files, systems, or designs affected. For multi-target proposals, list each target explicitly — don't say "various designs"; say which ones.
- Separate finding from recommendation. The finding is factual. The recommendation is a judgement. Keep them distinct.
- Link from the target. When creating a proposal, add a link to it from each target design doc's open questions section (or equivalent). For multi-target proposals, link from every target.
- **Acceptance requires a decision log.** When a design proposal is accepted, rejected, or deferred, create a decision log recording the verdict and reasoning before updating the proposal's status. The decision log is the authoritative record of *why*; the proposal status is just the outcome.

## Naming

`yyyymmdd-design-proposal~{Title}.md` in `_Temporal/Design Proposals/yyyy-mm/`.

## Frontmatter

```yaml
---
type: temporal/design-proposal
tags:
  - design-proposal
status: proposed
---
```

## Trigger

When vault work surfaces a contemplated design change that needs a decision but isn't in scope for the current session, or when the brain agent identifies a change needed in a system it can't directly modify.

## Template

[[_Config/Templates/Temporal/Design Proposals]]
