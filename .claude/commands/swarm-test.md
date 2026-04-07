---
description: Stress-test docs, implementations, or designs by dispatching a swarm of haiku agents. Each agent independently probes one aspect, reports what it found (or couldn't find), and rates clarity. Results are synthesised into gaps, gaps are fixed, and the cycle repeats until clean. Use after completing a body of work to find what you missed.
---

# Swarm Test

## Purpose

Cheap, parallel quality gate. Haiku agents are small and fast but not brilliant — if they can't figure something out from the work product alone, a real user or contributor will struggle too. Their confusion is signal.

## Input

The user provides one of:

- **A description of what to test** — e.g. "the new documentation structure", "the MCP tool implementation", "the archive workflow"
- **A file or directory** — e.g. `docs/`, `src/brain-core/scripts/create.py`
- **A recent branch or set of commits** — e.g. "everything on this branch since main"

If the argument is ambiguous, read the context (branch diff, recent files) to infer scope. Ask only if genuinely unclear.

## Process

### 1. Understand the target

Read enough to know what was built/written and what it's supposed to accomplish. Sources:

- The files themselves
- Git diff if on a branch (`git diff main...HEAD --stat`, then read changed files)
- Any plan or design doc the user references
- README or entry points in the target directory

Don't read exhaustively — read enough to generate good test scenarios.

### 2. Generate test scenarios

Create 8-12 test scenarios. Each scenario is a **task a real person would try to accomplish** using the work product. Design for coverage:

- **Happy paths** — can someone accomplish the core use cases?
- **Discovery** — can someone find the right information starting from an entry point, without being told where to look?
- **Edge cases** — what about the less obvious scenarios?
- **Cross-references** — do related parts of the system agree with each other?
- **Completeness** — is anything important missing entirely?

Each scenario should be:
- **Specific** — "explain how to add a new temporal artefact type with specific steps" not "test the docs"
- **Self-contained** — the agent gets the scenario and a starting point, nothing else
- **Answerable** — you know what a correct answer looks like, so you can judge the result

Present the scenarios to the user as a numbered list before dispatching. The user can adjust, add, remove, or approve.

### 3. Dispatch the swarm

Launch all agents in parallel using the Agent tool with `model: haiku`. Each agent gets:

```
You are testing [docs/implementation/design] quality. You are in [repo] at [path].

Your task: [specific scenario]. Start by reading [entry point] to orient yourself, then find the answer using only the [docs/code/etc] available.

Report:
1. The path you took (which files, in order)
2. The specific answer you found (or couldn't find)
3. Rate clarity: CLEAR (no confusion), MURKY (found it but it was hard), or LOST (couldn't find it or gave up)
4. One concrete suggestion to improve what you tested
```

The entry point should be whatever a real person would start from — a README, an index file, a module's `__init__.py`, the top-level directory. Never give agents the direct path to the answer.

### 4. Collect and synthesise

As agents complete, track results. When all are done, produce a summary table:

| # | Scenario | Rating | Key finding |
|---|----------|--------|-------------|

Then group findings into **gaps** — patterns across multiple agents, not just individual complaints:

- **Gaps** — things that are missing, wrong, or unreachable
- **Friction** — things that exist but are hard to find or unclear
- **Strengths** — things that worked well (don't lose these when fixing gaps)

### 5. Fix gaps

Fix the identified gaps. Apply the same judgment you would for any change:
- Don't over-fix — address the actual problem, not a hypothetical one
- Don't restructure what's working to fix what isn't
- Small, targeted edits over rewrites
- Follow the repo's existing conventions (CLAUDE.md, contributing docs)

After fixing, run tests if applicable (`make test` or equivalent).

### 6. Iterate (optional)

If fixes were significant (new sections added, structure changed, major rewrites), offer to run another round targeting the fixed areas. The second round should be smaller (4-6 agents) and focused on the gaps that were patched.

One iteration is usually enough. Two if the first round found structural problems. Never more than three — diminishing returns.

## Principles

- **Haiku confusion is signal, not noise.** If a small model can't follow the docs, a tired human won't either.
- **Test from the outside in.** Agents start from entry points, not from the file that has the answer.
- **Coverage over depth.** 10 agents each testing one thing beats 3 agents testing deeply.
- **Fix the gap, not the agent.** When an agent fails, the question is "why couldn't it find this?" not "why is this agent bad?"
- **Preserve what works.** Note strengths explicitly so fixes don't accidentally degrade them.

## Output

After each round, present:

1. The results table
2. Grouped gaps with specific file references
3. Proposed fixes (or fixes already applied, depending on auto mode)
4. Whether another round is warranted
