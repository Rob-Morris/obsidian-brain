---
name: shaping:refine
description: >
  Use when an artefact is clear but has open decisions to work through.
  Convergent, decision-driven shaping for Designs, Plans, Tasks, Reports,
  Research, Presentations, Printables, Mockups.
---

# Shaping: Refine

Works through open decisions one at a time until the artefact meets its type's shaping bar.

## Prerequisites

If you don't have an artefact path and transcript path from the parent skill or brainstorm, read and follow `shaping/assess` first to set up the session.

## Set the Agenda

Review the artefact's open questions or decision table. Skip what's already resolved. Inventory what remains. For resumptions, the artefact is the source of truth — not old transcripts.

## Convergent Shaping Loop

- Each question references decisions: `Q3 [D2, D4]`
- Follow the shared Q&A rules in `shaping/assess`
- After each answer: update the artefact via `brain_edit`, update the decision table, record the turn in the transcript
- **Show progress** after every turn: "3 of 5 decisions resolved"
- **Only the user closes decisions.** Present conclusions, confirm before marking resolved.

## Completing Shaping

- **Don't assume done.** When questions are resolved, enter completion review — don't declare shaped.
- **Agent self-review (silent).** Before presenting gaps, do a quick pass: fix placeholder text, internal contradictions, vague language, scope drift. Just fix them — don't present mechanical errors as gaps.
- **Review against the bar** from the type's `## Shaping` section: internal consistency, completeness, clarity, links/provenance.
- Present gaps to the user: "Review found X potential gaps: [list]. Do any of these need more shaping?" Only flagged gaps become new questions. Do not resume shaping without confirmation.
- **When review passes:** Confirm with the user before changing status — "Set status to `ready`?" Set status via `brain_edit`. Signal: "Fully shaped — [artefact] is ready."

## Red Flags

- Asking multiple questions in one turn
- Closing a decision without user confirmation
- Inventing your own completion criteria instead of reading the type's bar
- Declaring "fully shaped" without running the completion review
- Changing status without explicit user approval
- Presenting strawman alternatives where one option is obviously better
- Jumping to solutions before framing the problem being decided
