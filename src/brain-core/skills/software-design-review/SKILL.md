---
name: software-design-review
description: > 
  Reviews code with a team against established software design principles, and returns triaged findings (no edits).
  Use when evaluating code, or after writing or refactoring complex code, systems or technical designs:
  for code that is clearer, easier to maintain, and avoids common mistakes.
---

# Software Design Principles: Code Review

Drawing on best-practice software design principles, evaluates code or a software design (existing or proposed), and produces a triaged list of findings with `file:line` references (without making any edits). For trivial code or making decisions during design or implementation, use `software-design-principles` as a light reference instead.

## How this skill works

Orchestrates a multi-concern review with a team of five reviewer subagents, who each look at the code through a single lens; their findings are combined and triaged by the calling agent. Each reviewer focusses only on the principles relevant to its concern.

## Reference

Load as needed; reviewers receive their relevant subset:

- [reference/principles.md](reference/principles.md) — the 22 design principles + Foundation
- [reference/calibration.md](reference/calibration.md) — calibration table for resolving cross-concern tensions
- [reference/examples.md](reference/examples.md) — worked examples (assert vs raise, P21 direction-vs-metadata, when to extract)

## When to skip orchestration

For trivial evaluations — single-line fix, mechanical edit, ≤2 files, ≤50 changed lines, no architectural surface — read [reference/principles.md](reference/principles.md) and [reference/calibration.md](reference/calibration.md) directly and apply inline. The dispatch overhead is not worth it.

For everything else, run Phases 1–3 below.

## Phase 1: Identify the evaluation surface

Determine what is being evaluated. Three common shapes:

- **Existing code** — paths or modules in the codebase to review.
- **A diff** — `git diff HEAD`, or a diff supplied in context.
- **A proposed change** — a code chunk or design that has not been written to disk yet.

If the user hasn't named a surface, default to recent work: run `git diff HEAD` to see uncommitted changes. If there are no git changes, review the most recently modified files the user mentioned or that were edited earlier in this conversation. Ask the user if these defaults yield nothing, or if the surface to review is unclear.

If the surface is large (e.g. multi-thousand-line diff or many files), confirm scope with the user before dispatching — each of the five reviewers receives the full surface, so cost scales with size.

## Phase 2: Dispatch reviewers in parallel

Dispatch all five reviewers as parallel subagents in a single message. Pass each subagent:

1. The full evaluation surface (paths, diff content, or proposed change).
2. The corresponding briefing file: `reviewers/<concern>.md` (read it and include the content in the subagent's prompt; or instruct the subagent to read it).
3. Pointers to `reference/principles.md` and `reference/calibration.md` so they can cross-reference.

The reviewers and their concerns:

| Concern | Briefing | Looks for |
|---|---|---|
| Premise & verification | [reviewers/premise.md](reviewers/premise.md) | Guards/abstractions whose factual premise is unverified; redundant validation; principle-citation tunnel vision |
| Structural design | [reviewers/structural.md](reviewers/structural.md) | Dependency direction, layering, module boundaries, knowledge duplication, persistence leaks |
| Code-level smells | [reviewers/code-smells.md](reviewers/code-smells.md) | Speculative abstraction, mixed abstraction, naming, primitive obsession, swallowed errors, magic constants, parameter shape |
| Defensive coding & hacks | [reviewers/defensive.md](reviewers/defensive.md) | Retry/sleep masking, broad except, defensive layers without a real boundary |
| Verification & completion | [reviewers/completion.md](reviewers/completion.md) | Tests, stubs (`TODO`/`NotImplementedError`/`pass`), hallucinated APIs, claiming done without observed behaviour |

Each reviewer returns findings in a uniform table:

| file:line | concern | finding | calibration anchor | proposed fix |

If a reviewer finds nothing, it returns `No findings.`

## Phase 3: Aggregate and triage

When all reviewers return:

1. **Aggregate** every finding into a single list.
2. **De-duplicate** — if two reviewers found the same issue, keep one row and tag both concerns. **Note convergence as a confidence signal**: 4-of-5 reviewer convergence on a finding is strong evidence; 1-of-5 warrants a re-check against the code.
3. **Resolve cross-concern tensions** using [reference/calibration.md](reference/calibration.md). Example: a "speculative abstraction" finding from code-smells may conflict with a "stability boundary" finding from structural — the row "Reduce coupling vs avoid abstraction" decides which applies. Also surface order-of-operations dependencies (e.g. "fix L1 first, then re-check whether M3 is still needed").
4. **Self-check** the aggregated list before presenting:
   - For every guard/validation finding: was the call path traced?
   - For every extraction/abstraction finding: is the second-caller threshold met *and* is the "same rule" claim explicit and falsifiable?
   - For every principle citation: does it apply to *this exact* code, or did the reviewer reach for the closest match?
   - Remove or revise any finding that fails these checks.
5. **Collect positive observations** — every reviewer is also looking for calibration already done correctly. Aggregate these into a separate section. Recognising correct-by-design code is calibration evidence; an empty positives list is itself a signal worth noticing.
6. **Triage** by priority:
   - **High** — real bug, user-visible failure mode, or violates a load-bearing invariant.
   - **Medium** — real smell, defensible to fix.
   - **Low** — nit, defensible to leave.
7. **Present** to the user: priority groups with brief rationale; positive observations as a distinct section; anything notable being skipped (and why); for each finding, the convergence count and confidence band.

If the code is already clean, say so in one line.

## The Point

> Make the system honest about what it is, ignorant of what it doesn't need to know, easy to verify, and cheap to change. Pay attention to the parts likely to change; hide them behind stable interfaces; let the rest be simple. Ship in small steps and verify by observing, not by believing.
