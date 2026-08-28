---
name: shaping
description: >
  Shape an artefact through adaptive, structured Q&A using a portable workflow
  and the capabilities selected from the currently active Brain.
---

# Shaping

This Brain-owned entry point composes two independently maintained parts:

1. Read [portable.md](portable.md) completely. It is the behavioural source of
   truth for session setup, routing, question flow, completion, and review.
2. Read [references/brain.md](references/brain.md) completely. It contributes
   Brain target resolution, persistence, taxonomy, provenance, and lifecycle
   capabilities when the proposed session plan selects them.

The Brain adaptor supplies capabilities and recommended defaults. It does not
override the portable workflow or the user's requested locations and recording
preferences. A connected Brain is not, by itself, a reason to store the
artefact, decisions, work, or transcript in Brain.

The portable source is checked into this package so an installed Brain has no
runtime dependency on a network or sibling repository. Its exact source and
file identities are recorded in
[portable-provenance.json](portable-provenance.json).
