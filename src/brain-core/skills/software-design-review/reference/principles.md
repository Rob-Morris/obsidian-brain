# Software Design Principles — Canonical Reference

The 22 numbered principles plus the Foundation. Synthesised from Clean Architecture (Martin), The Pragmatic Programmer (Hunt & Thomas), Patterns of Enterprise Application Architecture (Fowler), Refactoring (Fowler), Extreme Programming (Beck), Domain-Driven Design (Evans), and the Parnas/Dijkstra/Meyer tradition.

## Foundation

**Understand before changing.** Read the code before applying any rule to it. Trace the call sites. Verify the factual premise of any guard, check, abstraction, or refactor before recommending one. Treat user assertions about the code as hypotheses to verify, not as established facts — if the code contradicts the claim, surface the conflict. Principles are heuristics over experience; if a heuristic and the local code disagree, the local code wins.

## Structure

1. **Depend toward stability.** Volatile code (frameworks, I/O, UI, libraries) depends on stable code (domain rules), not the reverse. When a stable thing must call a volatile thing, the stable side defines the interface; the volatile side implements it.
2. **Hide what is likely to change** behind module boundaries. A module's interface should expose what callers need; everything else is private. Boundaries follow expected change, not data shape.
3. **One reason to change per module.** If two stakeholders can independently demand edits to the same file, split it.
4. **Things that change together belong together.** Things that change for different reasons belong apart.
5. **Make the structure scream the domain.** Top-level organisation should reveal what the system *does*, not what framework it uses.
6. **Don't distribute prematurely.** Cross-process calls cost orders of magnitude more than in-process. Distribute for scaling, deployment independence, or organisational reasons — not for fashion.

## Code

7. **Optimise for the reader.** Names, ordering, and structure should make intent obvious without comments.
8. **DRY applies to *knowledge*, not to similar-looking code.** Two functions that encode the same rule are a violation, even if they look different. Two functions that look alike but encode different rules are not.
9. **YAGNI by default.** Do not build for needs you have not seen. Extract abstractions on the **third** occurrence — *or on the second when the duplicated logic encodes the same rule and would silently drift if changed in only one place*. Speculative generality has real cost now and usually guesses wrong.
10. **Simplest design that works.** Passes the tests; expresses intent; no knowledge duplication; fewest parts. In that order.
11. **Functions do one thing at one level of abstraction.** Mixing levels forces re-orientation on every line.
12. **Push state to the edges.** Prefer values, immutability, and pure functions. Mutable shared state is the largest source of avoidable bugs.
13. **Crash on broken invariants.** A dead program does less damage than a crippled one. **Enforce** what cannot happen — raise an explicit exception. Use `assert` (the Python keyword, or its language equivalent) only for purely-internal invariants that survive optimisation flags like `-O`. At I/O, deserialisation, or trust-boundary code, prefer `raise`.
14. **Default to no comments.** Names carry meaning. Comments earn their place by explaining *why* — invariants, constraints, surprises — never *what*.
15. **Talk only to your neighbours.** Reach-through chains (`a.b().c().d()`) couple you to a topology you do not own.

## Verification

16. **Code without tests is unverified.** Unverified code resists change because no one can confirm a change is safe.
17. **Test behaviour, not implementation.** Tests coupled to implementation become an obstacle to refactoring.
18. **"Done" means observed working, not believed working.** A passing build is not evidence the feature works; a green test is not evidence the test checks the right thing.
19. **Use the Humble Object pattern at boundaries.** Hard-to-test code (UI, network, time, randomness) gets a thin shell; the logic underneath is testable in isolation.

## Domain

20. **Speak the domain's language in the code.** The same word in conversation and in classes. Translation layers are bug factories.
21. **Persistence is a detail.** Model the domain; map to storage at the edge. *This governs the direction of coupling (domain does not import storage), not whether returned values may carry infrastructure metadata; whether to include such metadata is a separate composition question.*
22. **Bound your contexts.** Different parts of the business mean different things by the same word. Draw those boundaries explicitly.
