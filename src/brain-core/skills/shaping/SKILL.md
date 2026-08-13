---
name: shaping
description: >
  Shape an artefact through adaptive, structured Q&A. Routes convergent work
  through brainstorm or refine and discovery-shaped artefacts through discover,
  while propagating answers, reconciling remaining ambiguity, recording material
  body and agenda mutations in the transcript, and recommending an optional four-Cs
  review at candidate exits.
---

# Shaping

Develop an artefact through collaborative Q&A. Apply each answer throughout the artefact and any related in-scope artefacts, then reconsider what remains before asking again.

## Active workflows

- **Brainstorm:** Use for a new or skeletal convergent artefact whose concrete decisions are not clear yet. Read [references/brainstorm.md](references/brainstorm.md).
- **Refine:** Use for a formed convergent artefact, including one whose agenda appears complete and needs exit handling. Read [references/refine.md](references/refine.md).
- **Discover:** Use when the taxonomy declares discovery shaping. Treat the conversation as open-ended development while respecting whether the artefact is a revisitable living picture or a bounded temporal record. Read [references/discover.md](references/discover.md).

## Shared workflows

- **Session assessment and Q&A rules:** Read [references/assess.md](references/assess.md) first for every shaping request.
- **Four-Cs review:** When an active workflow reaches a candidate stopping or handoff point, recommend the optional review in [references/review.md](references/review.md). The user may bypass it.

All paths are relative to this skill's root. These referenced Markdown files are workflow instructions, not independently discoverable skills. Read each selected file completely.

## Routing

1. Read and follow [references/assess.md](references/assess.md). It resolves the artefact, reads the taxonomy's `## Shaping` metadata, selects an active workflow, and opens the session.
2. Read and follow the selected workflow.
3. At that workflow's candidate exit, recommend [references/review.md](references/review.md) as the next step. If the user bypasses it, proceed with the active workflow's handoff or exact status approval. If they accept, reconcile the review outcome as specified and return to the active workflow when refinement continues.

## Examples

- Existing design with decisions -> assess -> **refine**
- “I want to build X” with no formed artefact -> assess -> **brainstorm**
- Any discovery-shaped artefact, living or temporal -> assess -> **discover**
- Skeletal design -> assess -> **brainstorm**
