---
description: Analyse brain-core for simplification opportunities — technical debt, duplication, redundancy, token cost. Cross-references the deployed vault for real usage patterns. Produces a categorised plan with logical commits. Use when you want to identify improvement work, not when you're already executing changes.
---

# Simplify Audit

## Setup

1. Read `agents.local.md` for the vault path. If not found, ask the user for the vault path before proceeding.
2. Read `CLAUDE.md` for project conventions.

## Scope

- If an argument is provided (e.g. `/simplify-audit src/brain-core/scripts`), scope analysis to that subtree.
- Otherwise, analyse the full brain-core codebase.
- Always cross-reference against vault usage regardless of scope.

## What to Look For

- Duplicated logic that should have one source of truth
- Dead code vs partially-implemented future features — research intent before recommending removal
- Unnecessary nesting, indirection, or abstraction
- Inconsistent naming or structure across similar components
- Documentation that drifts from generated/compiled outputs
- Vestigial files no script reads or writes — trace the generation chain to confirm
- Token cost in agent-consumed files — remove what agents don't need, keep what they do
- Information in the wrong place — challenge whether each piece lives where it's most useful

## Principles

- Readability and simplicity over compactness
- Clarity over brevity — but brevity over redundancy
- Preserve separation of concerns; consolidate only related logic
- Add structure when earned, not speculatively
- Every change must preserve exact observable behaviour
- When consolidating files, integrate high-quality content — don't discard it
- Preserve stable reference targets (wikilinks, imports, re-exports) when restructuring
- Consider portability — changes must work in constrained environments
- Review recent git history before planning — account for current state

## Guardrails

- ONLY propose changes to how things are done, NEVER to what they do, without explicit discussion
- Combining unrelated concerns to save lines is not simplification
- No "clever" solutions that trade debuggability for elegance
- No removing things that look unused without researching whether they're dead code or planned features

## Output

Enter plan mode. Produce:

1. Categorised analysis with specific file references and estimated impact (lines/tokens saved, duplication removed)
2. Proposed execution order grouping related changes into logical commits
3. If a vault path was discovered, include upstream doc updates (design docs, changelogs, canaries) in the plan
