---
name: shaping:brainstorm
description: >
  Use when an artefact is new or a stub without enough information to start
  making specific decisions. Explores the idea through collaborative Q&A,
  writes content into the artefact, then hands off to refine.
---

# Shaping: Brainstorm

Takes an artefact that has no shape yet and explores it through collaborative Q&A. The output is an artefact with enough content to start making specific decisions, at which point it hands off to refine.

<HARD-GATE>
Do NOT write any code, scaffold any project, or take any implementation action until you have presented a design and the user has approved it.
</HARD-GATE>

## Prerequisites

If you don't have an artefact path and transcript path from the parent skill, read and follow `shaping/assess` first to set up the session.

## Process

1. **Understand context.** Read the vault router (`brain_read(resource="router")`). If the idea relates to existing artefacts, read them. Assess scope — if the idea describes multiple independent things, help decompose into separate artefacts before diving in.
2. **Ask clarifying questions** one at a time to understand: purpose, constraints, success criteria. Focus on *what* and *why* before *how*. Follow the shared Q&A rules in `shaping/assess`.
3. **Explore approaches.** Propose 2-3 approaches with trade-offs. Follow the options format from assess's shared Q&A rules.
4. **Present the design in sections**, scaled to complexity. Ask after each section whether it looks right. Cover what's relevant: architecture, components, data flow, trade-offs, open questions. Be ready to go back and revise.
5. **Write content into the artefact** via `brain_edit` once the user approves each section.

## Handoff to Refine

When the artefact has enough shape to start working through specific decisions, suggest the transition in plain language: "The design has enough shape to start working through specific decisions — want to continue?" If yes, read and follow `shaping/refine`.

## Red Flags

- Jumping to approaches before understanding the idea
- Asking multiple questions in one turn
- Writing code or scaffolding before the design is approved
- Presenting strawman alternatives where one option is obviously better
- Jumping to solutions before framing the problem being decided
- Forgetting to record Q&A in the transcript
