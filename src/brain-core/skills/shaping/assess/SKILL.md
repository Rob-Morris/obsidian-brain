---
name: shaping:assess
description: >
  Shared setup for shaping sessions. Determines if an artefact exists or needs
  creating, creates it if needed, starts the transcript, reads the type taxonomy.
  Called by the parent router or by any sub-skill invoked directly.
---

# Shaping: Assess

Handles session setup for all shaping sub-skills. Called by the parent `shaping` skill before routing, or by any sub-skill invoked directly that doesn't have context yet.

## Session Start

1. If the user named a specific artefact: read it via `brain_read(resource="artefact", name="...")`. If it doesn't resolve, ask the user what type to create, then create via `brain_create`.
2. If the user described an idea with no artefact: ask what artefact type fits, then create via `brain_create`.
3. Read the type taxonomy: `brain_read(resource="type", name="{type-key}")`. Require complete `shaping` metadata: `flavour`, `bar`, and `completion_status`. If it is absent, explain that the type is not shapeable and stop before creating a transcript.
4. Check for prior sessions before opening this one: if the artefact has a `**Transcripts:**` line, this is a resumption. The artefact is the source of truth for current state — read it, not old transcripts. Only consult prior transcripts if you need to understand *why* something was decided.
5. **Select the shaping mode** from the taxonomy and artefact content:
   - `flavour: discovery` → **discover**
   - `flavour: convergent` and the artefact is new, empty, or too incomplete for specific decisions → **brainstorm**
   - `flavour: convergent` with concrete open decisions → **refine**
6. Call `brain_action(request={"action": "shape", "params": {"target": "{path}", "mode": "{mode}"}})`. This validates shapeability, transitions lifecycle status through the canonical handler, and creates or continues today's transcript.
7. Report context: "Artefact: `{path}`. Transcript: `{path}`. Type: `{type}`. Shaping flavour: `{flavour}`. Bar: `{bar}`. Completion status: `{completion_status}`. Mode: `{mode}`."

## Shared Q&A Rules

These rules apply to all shaping sub-skills. Sub-skills reference this section instead of defining their own.

- **One question at a time.** Numbered Q1, Q2, ... Choose next by highest impact, not a pre-made list. Wait for the user to finish answering before moving on.
- **Prefer multiple-choice when options are enumerable.** Frame the tension first — state what's being decided and why it matters. Present options with lettered labels (A, B, C). State your recommendation after the options: "I recommend B because..." The user can always override with something else entirely.
- **Every alternative must be genuinely viable.** Each option must have a real upside the others lack. If you can't articulate why a reasonable person would choose it, drop it. When one option is obviously best, don't pad with weak alternatives — just recommend it and ask if the user sees it differently.
- **Research spikes.** If the user seems uncertain about a question, offer a spike: "Sounds like you're not sure — want me to research this and propose some options?" If they agree, do it inline — create a child research artefact via `brain_create(request={"resource": "artefact", "type": "research", ...})`, investigate, fold findings back into the parent artefact, and resume shaping. The spike is an offer, not a claim — the user might just need to think.
- **Deferred questions.** The user can defer, reorder, or request research. Follow their lead.
- **Recording.** Append each turn to the transcript following the shaping transcript format: `### Agent` heading with your exact text, `### User` heading with the user's exact response. Record what was said, nothing more — synthesis belongs in the artefact.
