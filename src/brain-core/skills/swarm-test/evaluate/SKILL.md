---
name: swarm-test:evaluate
description: >
  Structured evaluation using a swarm of small agents. The orchestrator assesses
  the target, designs a test plan mixing verifiable questions, trick questions,
  and navigation tasks, proposes it for user approval, then dispatches and
  synthesises scored results.
---

# Swarm Test: Evaluate

## Purpose

Cheap, parallel quality gate. Haiku agents are small and fast but not brilliant — if they can't figure something out from the work product alone, a real user or contributor will struggle too. Their confusion is the measuring instrument.

## Orchestrator

The orchestrator (you) reads the target, assesses what's testable, and designs a test plan by composing from the scenario types and test shapes below. The user approves the plan before dispatch.

### Assessment

1. **Read the target** — enough to understand what was built and what it's supposed to accomplish. Don't read exhaustively.
2. **Identify what's testable:**
   - Can agents navigate and understand it? → comprehension scenarios
   - Can you derive verifiable answers from it? → factual scenarios
   - Can you identify plausible-but-absent features? → counter-factual scenarios
   - Is the user comparing two approaches? → comparison shape
3. **Design the test plan:**
   - Pick scenario types based on what's testable (can combine freely)
   - Pick the test shape (single group or comparison)
   - Set the agent count (see guidelines under each type)
   - Write the scenarios, expected answers, and entry points

### Proposing the test plan

Present the plan to the user for approval before dispatching. The proposal has four parts, in this order:

**1. Goal** — What are we testing, and what does success look like? One or two sentences.

**2. Approach** — How do you propose to test it? What you'll do, concretely — the shape, the mix, the agent count. Keep it short and plain. No framework jargon (the user doesn't know or care about "comprehension scenarios" or "counter-factual"). Describe what the agents will do in plain language.

**3. Reasoning** — Write exactly 2-3 bullets explaining why this approach is effective for this specific target. Each bullet: one sentence, under 15 words. Name the mechanism, then what it proves or what risk it catches.

**4. Scenarios** — The specific tests. Factual scenarios show expected answers inline. Counter-factuals show `(expected: does not exist)` — don't label them as counter-factuals, but do mark the expected answer so the user can verify. Group scenarios whichever way makes them easiest to scan (by theme, by area, or flat list — whatever fits).

Each part is doing one job. The user reads the goal to check scope, the approach to understand what's going to happen, the reasoning to evaluate whether it's well-designed, and the scenarios to approve the specifics.

### Deciding what to test

The user's request guides the orchestrator:

- "Test the docs" → comprehension-heavy, some factual if verifiable answers exist
- "Can agents answer these questions?" → factual + counter-factual
- "Which is better, A or B?" → comparison shape
- "Smoke test this module" → comprehension + factual mix
- "Fuzz this" → edge-case-heavy comprehension, counter-factuals probing boundaries

The orchestrator uses judgment, not rules. A documentation test might be pure comprehension if the docs are procedural, or mostly factual if the docs are reference material. The user's intent and the target's nature determine the mix.

---

## Scenario Types

Each type is defined once. The orchestrator composes from these. Dispatch all agents using the smallest available model (in Claude Code: `model: haiku`).

### Comprehension

Tests whether agents can navigate, find, and understand information. Each scenario is a unique task a real person would try to accomplish.

**When to use:** Always applicable. The baseline layer.

**Agent count:** 1 agent per scenario (adjust count to the target's scope).

**Prompt template:**

```
You are testing [docs/code/design] quality. You are in [repo] at [path].

Your task: [specific scenario]. Start by reading [entry point] to orient
yourself, then find the answer using only the [docs/code/etc] available.

Report:
1. The path you took (which files, in order)
2. What you found (or couldn't find)
3. Rate navigation: DIRECT / EXPLORED / STRUGGLED / LOST
4. One concrete suggestion to improve what you tested
```

**Scoring (navigation):**

| Score | Label | Meaning |
|-------|-------|---------|
| 3 | DIRECT | Straight to it |
| 2 | EXPLORED | Some searching, got there |
| 1 | STRUGGLED | Many wrong turns |
| 0 | LOST | Couldn't find it or gave up |

**Aggregate:** `(sum of scores) / (scenarios × 3)` as a percentage.

**Design guidance:**
- Scenarios should cover: happy paths, discovery from entry points, edge cases, cross-references, completeness gaps
- Each scenario is specific ("find the naming pattern for research artefacts") not vague ("test the docs")
- Entry point is what a real person would start from — never the file that has the answer

### Factual

Tests whether agents can retrieve a specific, verifiable answer from the source material.

**When to use:** When the orchestrator can derive single correct answers from the target. Reference docs, config files, taxonomies, APIs with defined behaviour — all good candidates. Procedural or subjective content is not.

**Agent count:** 1 agent per question for a quick check. For meaningful measurement, note in the test plan that a Monte Carlo approach (separate skill) can run the same questions at scale.

**Prompt template:**

```
You are testing [docs/code/design] quality. You are in [repo] at [path].

Your task: [specific question]. Start by reading [entry point] to orient
yourself, then find the answer using only the [docs/code/etc] available.

Reply in this exact format:
ANSWER: [your specific answer]
CONFIDENCE: [HIGH / MEDIUM / LOW]
PATH: [files you read, in order]
SUGGESTION: [one concrete improvement]
```

**Scoring (outcome):** The orchestrator compares the agent's answer against the expected answer and scores as a percentage — how much of the expected answer was captured? "Got 4 of 5 status values" = 80%. This gives more signal than a coarse pass/fail across runs.

**Design guidance:**
- Expected answers are drawn directly from the source material before dispatch
- Include the location where the answer lives so scoring is unambiguous
- Good questions have single correct answers, not judgment calls

### Counter-factual

Tests whether agents confabulate — inventing answers for things that don't exist. Uses the same prompt template as factual — the agent has no hint that absence is a valid answer. The orchestrator knows the answer doesn't exist; the agent doesn't.

**When to use:** Whenever factual scenarios are used. Counter-factuals are the control group — without them, you can't distinguish "the agent found the right answer" from "the agent is good at sounding confident."

**Agent count:** Same as factual — paired with them in the test plan.

**Scoring:** The orchestrator evaluates the response — this tests the material's clarity, not the agent's behaviour:
- **Clear boundary** — agent confidently says it doesn't exist
- **Ambiguous boundary** — agent is uncertain, hedges, or finds something adjacent and stretches it to fit
- **Confabulation** — agent presents a fabricated answer as fact

**Design guidance:**
- Questions should be plausible — things that sound like they could exist given the target's domain
- Agents get the exact same prompt format as factual questions — no priming
- Flag them in the plan shown to the user so expected answers are clear
- A fabricated answer is a stronger signal than a factual miss — it means the material actively permits confabulation

---

## Test Shapes

How scenarios are arranged and dispatched.

### Single Group

The default. A mix of scenario types dispatched to independent agents. Each agent gets one scenario.

**When to use:** Most tests. Documentation quality, code comprehension, smoke tests, fuzz tests.

**Agent count:** Sum of all scenarios across types. Typical range: 8-14.

**Output — comprehension scenarios:**

```
┌─────┬──────────────────────────────────────────┬────────────┬──────────────────────────────────────┐
│  #  │              Scenario                    │ Navigation │            Path / Finding            │
├─────┼──────────────────────────────────────────┼────────────┼──────────────────────────────────────┤
│  1  │ Find how to add a temporal type          │ STRUGGLED  │ index → standards → naming → colours │
│     │                                          │            │ → extending (4 wrong turns)          │
├─────┼──────────────────────────────────────────┼────────────┼──────────────────────────────────────┤
│  2  │ How to archive a living artefact         │ DIRECT     │ index → archiving standards          │
└─────┴──────────────────────────────────────────┴────────────┴──────────────────────────────────────┘

Navigation: 63% (19/30)
```

**Output — factual scenarios:**

```
┌─────┬──────────────────────────────┬───────────────────────────────────┬─────────────────────────────────────────────┬───────┐
│  #  │          Question            │          Expected Answer          │               Agent Answer                  │ Score │
├─────┼──────────────────────────────┼───────────────────────────────────┼─────────────────────────────────────────────┼───────┤
│  3  │ Research naming pattern      │ yyyymmdd-research~{Title}.md      │ yyyymmdd-research~{Title}.md                │ 100%  │
├─────┼──────────────────────────────┼───────────────────────────────────┼─────────────────────────────────────────────┼───────┤
│  4  │ Design status values         │ 8 values (proposed...rejected)    │ Got 6 of 8, missed superseded & parked      │  75%  │
└─────┴──────────────────────────────┴───────────────────────────────────┴─────────────────────────────────────────────┴───────┘

Factual accuracy: 88% (avg across 4 questions)
```

**Output — counter-factual scenarios:**

```
┌─────┬──────────────────────────────┬─────────────────────────────────────────────────────┬───────────────────┐
│  #  │          Question            │               Agent Answer                          │ Boundary          │
├─────┼──────────────────────────────┼─────────────────────────────────────────────────────┼───────────────────┤
│  5  │ Vault sync schedule config   │ sync_interval in preferences (fabricated)           │ Confabulation     │
├─────┼──────────────────────────────┼─────────────────────────────────────────────────────┼───────────────────┤
│  6  │ Plugin rename process        │ "I couldn't find a rename process"                  │ Clear boundary    │
├─────┼──────────────────────────────┼─────────────────────────────────────────────────────┼───────────────────┤
│  7  │ Retrospective naming pattern │ "No retrospective type, but maybe captures?"        │ Ambiguous boundary│
└─────┴──────────────────────────────┴─────────────────────────────────────────────────────┴───────────────────┘

Counter-factual: 1 clear boundary, 1 ambiguous, 1 confabulation
```

### Comparison

Two groups receive identical questions. Only one variable differs between them. Tests whether the variable matters.

**When to use:** When the user is comparing two approaches, configurations, bootstrap methods, or documentation structures.

**Agent count:** Questions x 2 (one per group). Typical range: 12-20 (6-10 questions, 2 groups). The question set should include both factual and counter-factual.

**Output:**

```
┌─────┬──────────────────────────────┬───────────────────────────────────┬──────────────────┬──────────────────────────────────┐
│  #  │          Question            │          Correct Answer           │    Group A       │           Group B                │
├─────┼──────────────────────────────┼───────────────────────────────────┼──────────────────┼──────────────────────────────────┤
│  1  │ Research naming pattern      │ yyyymmdd-research~{Title}.md      │ CANNOT DETERMINE │ yyyymmdd-research~{Title}.md     │
├─────┼──────────────────────────────┼───────────────────────────────────┼──────────────────┼──────────────────────────────────┤
│  2  │ Vault sync schedule config   │ DOES NOT EXIST                    │ CANNOT DETERMINE │ DOES NOT EXIST                   │
├─────┼──────────────────────────────┼───────────────────────────────────┼──────────────────┼──────────────────────────────────┤
│  3  │ Plugin rename process        │ DOES NOT EXIST                    │ Has rename tool  │ DOES NOT EXIST                   │
│     │                              │                                   │ (fabricated)     │                                  │
└─────┴──────────────────────────────┴───────────────────────────────────┴──────────────────┴──────────────────────────────────┘

┌─────┬──────────────────────────────┬──────────┬──────────┐
│  #  │          Question            │ Group A  │ Group B  │
├─────┼──────────────────────────────┼──────────┼──────────┤
│  1  │ Research naming pattern      │    ✗     │    ✓     │
├─────┼──────────────────────────────┼──────────┼──────────┤
│  2  │ Vault sync schedule config   │    ✗     │    ✓     │
├─────┼──────────────────────────────┼──────────┼──────────┤
│  3  │ Plugin rename process        │    ✗✗    │    ✓     │
├─────┼──────────────────────────────┼──────────┼──────────┤
│     │                     Accuracy │  0/3 0%  │ 3/3 100% │
└─────┴──────────────────────────────┴──────────┴──────────┘

✗ = incorrect/unknown   ✗✗ = fabricated answer (confabulation)
```

**Design guidance:**
- The prompts for both groups must be identical except for the one variable
- Both groups get the same entry point unless the entry point IS the variable
- Equal number of factual and counter-factual questions

---

## After Results

### Synthesise

Group findings into patterns, not individual complaints:

- **Gaps** — missing, wrong, or unreachable
- **Confabulation risks** — agents invented answers (counter-factual failures or confident wrong answers on factual questions)
- **Friction** — exists but hard to find or unclear
- **Strengths** — what worked well (preserve these when fixing)

### Findings & Recommendations

After synthesis, present a concise summary of actionable findings — what's broken, what's risky, and what to do about it. This is the section the user reads first. Structure as a numbered list of findings, each with a one-line description and a concrete recommendation. Example:

1. **Ideas terminal status is ambiguous.** `parked` appears alongside `adopted` in the lifecycle table but only `adopted` has a +Status/ folder. Recommend making the distinction explicit in the taxonomy.
2. **Post-research workflow requires 5 hops.** No direct link from triggers to the research workflow. Recommend adding a cross-reference from the log trigger to the research → report chain.

### Next Steps

After presenting findings, ask the user how they'd like to proceed:

- **(a) Fix & re-evaluate** — propose fixes, implement after approval, then run a smaller follow-up round targeting the patched areas
- **(b) Fix only** — propose and implement fixes without re-evaluating
- **(c) No action** — findings are informational, no fixes needed

**Agent failures:** If an agent times out, crashes, or returns garbage, drop the result and note it in findings. Do not retry — a failure is data (the scenario may be too complex for a single haiku agent).

When fixing:
- Present proposed fixes to the user for approval before implementing
- Don't over-fix — address the actual problem
- Don't restructure what's working to fix what isn't
- Small, targeted edits over rewrites
- Follow the repo's existing conventions
- Run tests if applicable

When re-evaluating: use a smaller, focused round (4-6 agents) targeting the fixed areas. One iteration is usually enough. Two if the first round found structural problems. Never more than three.

---

## Principles

- **Haiku confusion is signal, not noise.** If a small model can't follow it, a tired human won't either.
- **Test from the outside in.** Agents start from entry points, not from the answer.
- **Coverage over depth.** Many agents each testing one thing beats few agents testing deeply.
- **Fix the gap, not the agent.** "Why couldn't it find this?" not "why is this agent bad?"
- **Preserve what works.** Note strengths so fixes don't degrade them.
- **Counter-factuals catch confabulation.** If an agent invents an answer for something that doesn't exist, the material is too ambiguous.
- **One agent, one job.** Never give a haiku agent two tasks. Their limitations are the instrument.
