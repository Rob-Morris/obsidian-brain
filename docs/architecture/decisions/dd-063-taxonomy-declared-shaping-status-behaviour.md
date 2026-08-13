# DD-063: Let discovery shaping preserve enduring lifecycle status

**Status:** Implemented (v0.58.0)
**Extends:** DD-056, DD-057

## Context

DD-057 made every shaping session enter the `shaping` lifecycle state and made completion apply a taxonomy-declared status. That is appropriate when `shaping` means the artefact is not yet ready. It is misleading for discovery-shaped artefacts whose lifecycle describes an enduring domain state. A person card can remain `active` or `parked` while a bounded conversation makes it more correct, clear, consistent, or complete; finishing that conversation is not inherently a domain-state transition.

Removing `shaping` from those lifecycle enums would also lose a useful explicit state for artefacts deliberately held in sustained shaping. The session workflow therefore needs to distinguish automatic shaping mechanics from the lifecycle values users may assign intentionally.

## Decision

Add optional taxonomy shaping metadata `Status behaviour`, with two values:

- `transition` is the default and preserves the existing contract. Opening a session enters `shaping`; completing refine or discovery applies the required `Completion status` with user approval.
- `preserve` is valid only for discovery shaping. Opening and completing the current pass leave an existing non-terminal status unchanged. It may declare a `Completion status` as the explicit exit from `shaping`; that value is applied with user approval only when the artefact is already in `shaping`.

Both behaviours require `shaping` in the taxonomy lifecycle enum, so a user can still assign that status explicitly. The session boundary rejects a preserved target in a terminal status before creating a transcript, backlink, or other mutation; the user must explicitly restore a valid non-terminal status before shaping can resume.

The shaping skill treats completion under `preserve` as completion of the current discovery pass, not lifecycle readiness. Its candidate-exit menu distinguishes running the optional four-Cs review, completing the pass with status unchanged, and stopping without asserting completion.

People adopts `preserve` with `active` as its explicit exit from `shaping`. Its bar judges faithful, clear current-scope capture with material uncertainty or inconsistency made explicit, while allowing the user to end the current pass when they have nothing more to record. Existing maturity-shaped and bounded temporal types retain `transition`.

## Alternatives Considered

### Always enter and then restore `shaping`

Rejected because it manufactures a transient domain-state change, may fire lifecycle hooks or moves, and makes pass completion look like a substantive status decision even when the correct outcome is preservation.

### Remove `shaping` from discovery lifecycle enums

Rejected because `shaping` can still be a meaningful explicit state for sustained work. Automatic workflow behaviour and the set of intentionally assignable lifecycle values are separate concerns.

### Infer preservation from discovery flavour

Rejected because some discovery-shaped artefacts use `shaping` → `ready` as a real maturity lifecycle. The taxonomy must declare the exception explicitly.

## Consequences

- Existing shaping taxonomies and compiled metadata remain compatible because omitted status behaviour means `transition`.
- Discovery types with enduring lifecycle states can improve without lifecycle churn, while cards already in `shaping` retain an explicit completion path.
- Completing a preserved pass records a workflow outcome but does not claim a new domain state.
- Terminal records cannot be implicitly revived by opening a preserved discovery session.
- The compiler, migration compatibility path, session boundary, skill, documentation, and tests share one taxonomy-owned policy.

## Implementation Notes

- `compile_router.py` emits `status_behaviour` only when the taxonomy declares it, preserving the compiled shape of existing default contracts.
- `migrate_to_0_53_0.py` still ensures `shaping` exists for every shapeable type, but requires a completion status only when the compiled contract supplies one.
- `start_shaping_session()` reports the effective status behaviour and whether status changed so MCP callers can describe the session accurately.
