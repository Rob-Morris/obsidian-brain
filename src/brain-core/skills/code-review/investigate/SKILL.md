---
name: code-review:investigate
description: >
  Identify changed code and dispatch reuse, quality, and efficiency reviewer
  agents in parallel; return the raw findings list.
---

# Code Review: Investigate

Run a code review and return the raw findings list — no triage, no edits. Run all reviewers in parallel using subagents. Used by `code-review` and `code-review:fix`; can also be called directly.

## Phase 1: Identify Changes

Run `git diff HEAD` to see uncommitted changes (staged and unstaged together). If there are no git changes, review the most recently modified files the user mentioned or that you edited earlier in this conversation.

## Phase 2: Launch Reviewer Agents in Parallel

Dispatch all three reviewers as parallel subagents in a single message. Pass each subagent the full diff so it has the complete context. Each reviewer's briefing is the corresponding section below.

Each reviewer must return findings as a list — one item per finding, citing `file:line`, naming the issue, and proposing the cleanup. Reviewers do not edit files.

### Reviewer: Reuse

For each change:

1. **Search for existing utilities and helpers** that could replace newly written code. Look for similar patterns elsewhere in the codebase — common locations are utility directories, shared modules, and files adjacent to the changed ones.
2. **Flag any new function that duplicates existing functionality.** Suggest the existing function to use instead.
3. **Flag any inline logic that could use an existing utility** — hand-rolled string manipulation, manual path handling, custom environment checks, ad-hoc type guards, and similar patterns are common candidates.

### Reviewer: Quality

Review for hacky patterns:

1. **Redundant state** — state that duplicates existing state, cached values that could be derived, observers/effects that could be direct calls.
2. **Parameter sprawl** — adding new parameters to a function instead of generalising or restructuring existing ones.
3. **Copy-paste with slight variation** — near-duplicate code blocks that should be unified with a shared abstraction.
4. **Leaky abstractions** — exposing internal details that should be encapsulated, or breaking existing abstraction boundaries.
5. **Stringly-typed code** — raw strings where constants, enums, or typed values already exist in the codebase.
6. **Unnecessary wrappers** — functions, classes, components, or elements that exist only to pass through to an inner thing that already supports the needed behaviour. Check if the inner thing's parameters, props, or arguments already cover what the wrapper is doing — if so, drop the wrapper and use the inner thing directly.
7. **Nested conditionals** — ternary chains, nested if/else, or nested match/switch 3+ levels deep. Flatten with early returns, guard clauses, a lookup table, or an if/elif cascade.
8. **Unnecessary comments** — comments explaining WHAT the code does (well-named identifiers already do that), narrating the change, or referencing the task/caller. Delete; keep only non-obvious WHY (hidden constraints, subtle invariants, workarounds).

### Reviewer: Efficiency

Review for efficiency:

1. **Unnecessary work** — redundant computations, repeated file reads, duplicate network/API calls, N+1 patterns.
2. **Missed concurrency** — independent operations run sequentially when they could run in parallel.
3. **Hot-path bloat** — new blocking work added to startup or per-request/per-render hot paths.
4. **Recurring no-op updates** — state/store updates inside polling loops, intervals, or event handlers that fire unconditionally; add a change-detection guard so downstream consumers aren't notified when nothing changed. Also: if a wrapper function takes an updater/reducer callback, verify it honours same-reference returns (or whatever the "no change" signal is) — otherwise callers' early-return no-ops are silently defeated.
5. **Unnecessary existence checks** — pre-checking file/resource existence before operating (TOCTOU anti-pattern). Operate directly and handle the error.
6. **Memory** — unbounded data structures, missing cleanup, listener/handle leaks.
7. **Overly broad operations** — reading entire files when only a portion is needed, loading all items when filtering for one.

## Phase 3: Aggregate and Return

Wait for all reviewers to complete, then return every finding from every reviewer — each citing `file:line`, the issue, and the proposed cleanup. No triage, no fixes, no summary. If no findings, say so in one line.
