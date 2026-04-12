# Documentation Philosophy

Brain-core documentation is structured around three composable layers, each serving
a distinct audience and purpose. The structure is deliberate: it optimises for agents
that load documentation by context, and for contributors who need to understand why
something exists before changing it.

---

## Three Composable Layers

Documentation separates into three layers that can be consumed independently or
together, depending on the task at hand.

### User layer (`docs/user/`)

How to use it. External interface, behaviour, and usage patterns. This layer describes
what a user or consuming agent sees from the outside, without exposing internal
mechanics.

### Functional layer (`docs/functional/` + co-located in `src/brain-core/`)

What it does. Internal capabilities, logic, rules, and specifications. This layer
defines the contracts that the code implements — what inputs are accepted, what
outputs are produced, what invariants hold.

### Architectural layer (`docs/architecture/`)

How and why it is built this way. Design decisions, structural trade-offs, and the
reasoning behind them. This layer explains the shape of the system and why
alternatives were rejected.

### Why composable layers matter

The layers are separable by design. An agent could load:

- **Functional only** — enough to reimplement the same capabilities in a different
  architecture.
- **Functional + architectural** — enough to reimplement with the same architecture
  and design trade-offs.
- **All three** — the complete picture, including user-facing contracts and behaviour.

This composability is primarily about token efficiency. A monolithic document forces
every consumer to load everything, regardless of what they need. Separable layers
let an agent or contributor load only the context relevant to their task.

The layers may have natural cross-over — an architectural decision might reference
a functional spec, or a user doc might link to a design decision for background.
But no fact is duplicated across layers. Cross-references connect them instead.

---

## Co-location of Functional Docs

Functional documentation lives in two places: `docs/functional/` for repo browsing,
and alongside the code in `src/brain-core/` (which ships into vaults as
`.brain-core/`).

This is deliberate. An agent working inside a vault finds the functional specs right
next to the code it is using, without navigating back to the repository. The
centralised copies in `docs/functional/` provide the same specs in a repo-browsing
context.

The principle comes from arc42: component-level documentation lives with the
component, system-level documentation lives at the root. Brain-core applies this
by co-locating specs that an in-vault agent needs, while keeping a central index
for contributors working on the repo itself.

### Runtime bootstrap vs contributor docs

Co-location does not mean every document near `src/brain-core/` is fair game for
repo contributor process guidance.

Anything that ships in `.brain-core/` and participates in agent bootstrap is part
of the runtime surface for normal Brain agents. That surface must stay focused on
operating the vault: bootstrap flow, vault conventions, user preferences, and
core runtime references.

Contributor-only process material belongs in `docs/`, even when it discusses
brain-core internals. Workflow tiers, canaries, pre-commit process, release
discipline, and repo change-management rules are contributor standards, not
runtime bootstrap content.

In short:

- shipped `.brain-core/` bootstrap docs are written for vault agents
- repo contributor process docs stay under `docs/`
- if a rule would confuse a normal vault agent, it does not belong in bootstrap

---

## Three-Way Verification

Code, documentation, and tests form a triangle where each vertex is independently
verifiable against the other two:

- **Code against docs** — does the implementation match the specification?
- **Docs against tests** — do the tests cover every documented behaviour?
- **Code against tests** — do the tests exercise the actual implementation?

Any two should catch drift in the third. This redundancy is intentional. You could
delete the code entirely and not lose any intended design, because the docs and tests
together fully describe it. You could delete the docs and still verify correctness
from the code and tests. The triangle means no single point of failure for design
knowledge.

---

## No Duplication, Cross-References Instead

The same fact lives in one place. Other documents cross-reference it rather than
restating it.

This is the single biggest maintenance discipline in the documentation structure.
Most documentation bugs come from updating one layer but not others — a functional
spec changes, the architectural rationale is updated, but the user doc still
describes the old behaviour. The pre-commit canary exists specifically to catch
this kind of drift by flagging when changes touch one layer without considering
the others.

---

## Design Decisions: Co-located Content with Central Index

Following arc42 and ADR best practice, design decision content lives alongside the
module or subsystem it governs. A decision about the security model lives near the
security documentation. A decision about archive handling lives near the archive
specification.

A lightweight central index (`docs/architecture/decisions/README.md`) acts as a
router: one-line summaries and links, not a container. The index tells you what
decisions exist and where to find them. The decisions themselves carry the full
context and reasoning.

---

## Progressive Refinement Process

The development process gates each phase on clarity rather than speed:

```
idea → design → plan → tests+docs → implement → review
```

Tests and documentation are written before implementation. This is not ceremony for
its own sake — it forces the design to be concrete enough to test before any code
is written. If you cannot write a test for a behaviour, the specification is not
clear enough. If you cannot document a feature, it is not well enough understood to
build.

The sequence also means documentation is never an afterthought. It is a first-class
deliverable that arrives before the code, not a chore that follows it.

---

## Cross-references

- [Security model](security.md) — example of an architectural document in this layer
- [Contributing guide](../contributing.md) — documentation layers table and practical workflow
- [Contributing — Agents](../contributing-agents.md) — which layer to update for each change type
- [Pre-commit canary](../../.canaries/pre-commit.md) — drift detection between layers
