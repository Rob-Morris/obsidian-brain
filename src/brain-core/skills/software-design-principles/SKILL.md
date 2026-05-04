---
name: software-design-principles
description: >
  Provides guidance on good software design principles, with trade-off calibration, examples, and common failure modes.
  Applies when making decisions while writing, designing, or refactoring code, or evaluating trivial code for clarity, maintainability, and common mistakes.
---

# Software Design Principles Reference

This is a reference, not a workflow. Best used for evaluating trivial code changes, or for making better quality decisions during the process of designing or writing code. For reviewing complex or already-written code, diffs or software design, use `software-design-review` instead.

## How to use this in the moment

Use as a guide when a software design question surfaces — "should I extract this?", "is this guard reachable?", "am I over-abstracting?"; scan the relevant section. Principles are heuristics, the calibration table covers tensions between them, the "Watch for these" section captures common agent-prone failure modes. Use whichever frames the decision; ignore the rest.

If a heuristic and the local code disagree, the local code wins. If you can't articulate why a principle applies *to this exact code*, it probably doesn't.

## Foundation

**Understand before changing.** Read the code before applying any rule. Trace the call sites. Verify the factual premise of any guard, check, abstraction, or refactor before recommending one. Treat user assertions about the code as hypotheses to verify, not as established facts — if the code contradicts the claim, surface the conflict.

## Principles

### Structure

1. **Depend toward stability.** Volatile code (frameworks, I/O, UI, libraries) depends on stable code (domain rules), not the reverse.
2. **Hide what is likely to change** behind module boundaries. Boundaries follow expected change, not data shape.
3. **One reason to change per module.** Two stakeholders independently demanding edits → split.
4. **Things that change together belong together.** Things that change for different reasons belong apart.
5. **Make the structure scream the domain**, not the framework.
6. **Don't distribute prematurely.** Cross-process calls cost orders of magnitude more than in-process.

### Code

7. **Optimise for the reader.** Names, ordering, and structure should make intent obvious without comments.
8. **DRY applies to *knowledge*, not similar-looking code.** Same rule encoded twice = violation. Same shape, different rules = not.
9. **YAGNI by default.** Extract abstractions on the **third** occurrence — or on the second when the same rule would silently drift if changed in only one place.
10. **Simplest design that works.** Passes tests; expresses intent; no knowledge duplication; fewest parts.
11. **Functions do one thing at one level of abstraction.**
12. **Push state to the edges.** Prefer values, immutability, and pure functions.
13. **Crash on broken invariants.** Enforce what cannot happen — `raise` at I/O, deserialisation, or trust-boundary code; `assert` only for purely-internal invariants that survive `-O`.
14. **Default to no comments.** Comments earn their place by explaining *why*, never *what*.
15. **Talk only to your neighbours.** Reach-through chains couple you to a topology you do not own.

### Verification

16. **Code without tests is unverified.**
17. **Test behaviour, not implementation.**
18. **"Done" means observed working, not believed working.** A passing build is not evidence the feature works.
19. **Use the Humble Object pattern at boundaries** — UI, network, time, randomness get a thin shell; logic underneath stays testable.

### Domain

20. **Speak the domain's language in the code.**
21. **Persistence is a detail.** Direction of coupling: domain does not import storage. (Returned values may carry infrastructure metadata; that's a separate composition question.)
22. **Bound your contexts.** Different parts of the business mean different things by the same word — draw those boundaries explicitly.

## Calibration — when principles conflict

| Tension | Default | Override condition |
|---|---|---|
| **DRY vs YAGNI** | Duplicate until 3rd occurrence | Same *rule* across sites with high change-correlation → extract on 2nd |
| **Simple design vs extensibility** | Simplest that works now | The axis of change is *known* and has moved at least once |
| **Heavy architecture vs straight-through** | Match weight to domain complexity | Domain logic is genuinely complex *or* swap of an external concern is anticipated |
| **Refactor now vs ship now** | Refactor in same change if it touches code you're already editing | Out-of-scope refactor → separate change |
| **Test-first vs explore-first** | Test-first when behaviour is known | Spike to learn, then **delete the spike** and write tested code |
| **Reduce coupling vs avoid abstraction** | Concrete first; abstract on second real caller | Stability difference (e.g. domain ↔ I/O) AND a concrete second implementation in flight |
| **Validate inputs vs trust callers** | Validate at system boundaries; trust internal callers | Internal invariant is load-bearing → enforce by raising. Wrap I/O exceptions with diagnostic context (P7 still applies) |
| **Fix root cause vs patch symptom** | Always pursue root cause first | Genuine emergency *with* explicit user approval to patch |
| **Add error handling vs let it fail** | Crash early; only handle errors with a real recovery | Boundary to a system you cannot trust (network, user input, third-party) |
| **Backwards compatibility vs clean change** | Clean change when no consumers exist | Real consumers depend on the surface |
| **Pattern (Repository, Factory…) vs plain code** | Plain code | Two real use cases already exist that the pattern would unify |

**Tie-breakers:** When still unsure, choose the option **easier to reverse**. When a principle and a deadline conflict, prefer the principle that keeps the next change cheap. When two principles conflict, prefer the one that protects an axis of change you have actually seen.

## Examples

### A — `assert` vs `raise` at an I/O boundary

```python
def load_artefact(name: str) -> dict:
    path = ARTEFACT_DIR / f"{name}.json"
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not load artefact '{name}' from {path}") from exc
    if "kind" not in data:
        raise ValueError(f"artefact file is corrupt — 'kind' missing: {path}")
    return data
```

`assert "kind" in data` would be wrong: stripped under `-O`, and corrupt JSON is a real failure mode. P13 says enforce — at I/O boundaries, that means `raise`, with diagnostic context, chained `from exc`.

### B — P21 governs direction, not metadata

```python
@dataclass
class Artefact:
    name: str
    kind: str
    body: str
    loaded_at: datetime  # OK — infrastructure annotation; does not couple domain to storage
```

P21 forbids the domain *depending on* storage modules; it does not forbid returned values carrying infrastructure metadata.

### C — When to extract a helper

Three modules each validate an artefact name with the same rule → extract now (3rd occurrence threshold).

Two modules formatting wikilinks identically — if one is updated for a new edge case, the other must change in lockstep → extract on 2nd (override: same rule, drift risk).

A single occurrence of similar-looking validation? Leave it — duplication is cheaper than the wrong abstraction.

## Watch for these as you work

Common agent-prone failure modes. If you notice yourself moving toward one, check whether the corresponding calibration applies before committing.

### Premise & verification
- Citing a principle without confirming the factual premise (call sites, upstream validation, caller count)
- Adding error handling for a condition that cannot occur from internal callers
- Adding a guard inside a module when an upstream boundary already validates the same condition

### Scope & out-of-task work
- Renaming or moving code unrelated to the task
- Adding endpoints, validation rules, configuration options, or behaviours the task did not request — "the user probably wants this too" is not a requirement
- Adding a parameter "in case we need it later"
- Adding backwards-compatibility shims for code that has no users

### Speculative abstraction
- Adding an interface for a class with one implementation
- Building a helper with one caller
- Adding a feature flag, config option, or extension point with no current consumer
- Reaching for a pattern (Repository, Factory, Strategy, Observer) before the second use case exists
- Wrapping a stable library "for flexibility" with no second implementation in sight
- Re-implementing logic that already exists elsewhere in the codebase

### Defensive coding & hacks
- Catching an exception you do not know how to handle
- Adding a retry loop, sleep, or backoff to deterministic (non-I/O) code to make a failure go away

### Verification & completion
- Mocking the dependency the test was supposed to verify
- Deleting, skipping, or commenting out a failing test instead of diagnosing why it fails
- Calling a method, library export, or CLI flag without confirming it exists in the installed version
- Leaving `TODO`, `FIXME`, `NotImplementedError`, `pass`, or hardcoded stub values in code paths that will execute at runtime
- Claiming "done" without having verified the change works as intended (running the tests, exercising the feature, observing behaviour)

### Comments & docs
- Adding a comment that says what the next line does
- Writing a multi-paragraph docstring for a function whose name is self-explanatory

## The Point

> Make the system honest about what it is, ignorant of what it doesn't need to know, easy to verify, and cheap to change. Pay attention to the parts likely to change; hide them behind stable interfaces; let the rest be simple. Verify by observing, not by believing.
