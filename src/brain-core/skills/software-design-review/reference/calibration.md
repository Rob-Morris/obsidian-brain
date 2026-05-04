# Calibration Table — When Principles Conflict

When two principles point in different directions, use this table. Cite by row name (e.g. "Calibration: 'Validate inputs vs trust callers'") in findings.

| Tension | Default | Override condition |
|---|---|---|
| **DRY vs YAGNI** (extract or duplicate?) | Duplicate until the **3rd** occurrence; then extract | Same *rule* across sites with high change-correlation evidence — extract on 2nd occurrence |
| **Simple design vs extensibility (OCP)** | Simplest thing that works now | The axis of change is *known* and has moved at least once |
| **Heavy architecture (layers/ports) vs straight-through code** | Match weight to domain complexity; CRUD doesn't need hexagonal | Domain logic is genuinely complex *or* swap of an external concern is anticipated |
| **Refactor now vs ship now** | Refactor in the same change if it touches code you're already editing | Out-of-scope refactor → separate change |
| **Test-first vs explore-first** | Test-first when behaviour is known | Spike to learn, then **delete the spike** and write tested code |
| **Reduce coupling vs avoid abstraction** | Concrete first; abstract when a second real caller appears | The interface protects a stability difference (e.g. domain ↔ I/O) **AND** a concrete second implementation is in flight or a volatility event has already been observed — not merely "this kind of thing tends to change in principle" |
| **Validate inputs vs trust callers** | Validate at system boundaries; trust internal callers | Internal invariant is load-bearing → enforce by raising, don't add user-input-style validation. Trusting the caller does not mean propagating raw exceptions: when raising from an I/O boundary, wrap with diagnostic context (path, name, expected shape) — P7 still applies |
| **Fix root cause vs patch symptom** | Always pursue root cause first | Genuine emergency *with* explicit user approval to patch |
| **Add error handling vs let it fail** | Crash early on invariant violations; only handle errors you have a real recovery for | The boundary is to a system you cannot trust (network, user input, third-party) |
| **Backwards compatibility vs clean change** | Clean change when no consumers exist | Real consumers depend on the surface — preserve or migrate |
| **Pattern (Repository, Factory…) vs plain code** | Plain code | Two real use cases already exist that the pattern would unify |

## Tie-breakers

- When still unsure, choose the option that is **easier to reverse**.
- When a principle and a deadline conflict, prefer the principle that keeps the next change cheap.
- When two principles conflict, prefer the one that protects an axis of change you have actually seen.
