# Agent-Ready Documentation Standard

> **Version 1.0.0.** See [Versioning](#versioning) and [Changelog](#changelog).

This standard describes how a project structures, writes, and maintains documentation so that agents — human or automated — can work in the codebase effectively. It is an **improvement standard**: a framework and quality target projects adopt incrementally, not a pass/fail compliance gate.

Projects cite conformance to a specific version — e.g. *"Conforms to Agent-Ready Documentation Standard v1.0"*.

## Principles

Seven principles organise the rest of the standard.

1. **Layered composition.** Documentation is organised into composable, non-entangled layers so agents can load only what they need. The layer vocabulary is extensible — projects adopt the defaults and add layers where their shape warrants it.
2. **Three-way reconstruction.** Code, docs, and tests are three views of one system. Any two suffice to reconstruct the third. This is the mature-state bar.
3. **Self-contained.** Documentation is self-sufficient at every scale. Each artefact makes sense on its own — cross-reference rather than duplicate. The project as a whole is complete for understanding — an agent with no external context can answer every architectural question from it alone. External context augments; it does not replace.
4. **Discoverable structure.** Every artefact is reachable through an explicit path from the bootstrap. Agents do not crawl.
5. **Contextual placement.** Documentation lives where it is relevant: co-located with the subsystem it describes, or centralised once when it is cross-cutting.
6. **Ratcheting improvement.** Documentation maturity climbs incrementally and does not slide back. Projects adopt the standard at their own pace, self-assess their current position, and commit to not regressing from it. The standard prescribes the direction of travel, not the speed.
7. **Structure over tooling.** The standard prescribes what kinds of documentation exist, where they sit, and how they are indexed. Format, syntax, frameworks, and tooling are project choices.

## The layer model

The default vocabulary is four layers:

- **User** — required; every project has users who need to know how to use it
- **Functional** — required; every project does something, and documenting what it does is functional
- **Architecture** — required; every project has structure and decisions worth recording
- **Contributor** — required; agent-ready documentation must instruct contributors (human or agent) how to work in the project

All four default layers are required in every project. Content depth may vary — a small project's content in any layer may be brief — but the layer, its folder, and its `README.md` are always present.

Projects adopt these defaults and extend them when the project's shape warrants it. A project may add a layer (e.g. a *grammar* layer for a DSL, a *data* layer for a data-heavy project). Any added layer must preserve separation of concerns, be documented in the bootstrap, and follow the same structure as the defaults — a `docs/{layer}/` subtree with a `README.md` index.

### User layer

User documentation covers the project's **usage surface**: how to install and set the system up, and how to accomplish tasks with it — workflows, guides, tutorials, and usage reference. The distinction from the functional layer is perspective: the user layer is task-oriented (*to do X, do Y*), while the functional layer is reference-oriented (*feature X does Y*).

The usage surface exists regardless of whether users are external consumers, internal teams, or a single author. Content depth varies with its size and complexity.

**Canonical realisation:** `docs/user/README.md` as the layer index, with further user documentation in that subtree.

### Functional layer

What the system does and how its parts work. The layer has four kinds of artefact:

- **Layer index** — `docs/functional/README.md`, listing every functional artefact with a one-line summary and link.
- **Module-level co-located docs** — per-module `README.md` (or equivalent) describing what the module does, its public surface, and its dependencies. Linked from the layer index.
- **Inline documentation** — docstrings, comments, and language-specific equivalents. Public symbols (e.g. publicly exposed functions, types, and methods) carry behavioural documentation; format and style are the project's choice.
- **Tests that assert behaviour** — executable specifications of what the system does. Co-located with the modules they exercise, structured and named so each test reads as a behavioural claim. See [Tests as documentation](#tests-as-documentation) for the full treatment.

Per P7 (structure over tooling), docstring syntax, comment conventions, test frameworks, and language-specific style guides remain project concerns.

### Architecture layer

How the system is structured, and why. Contains three kinds of artefact:

- **Architectural overviews** — subsystem structure, component boundaries, data flow, operational constraints.
- **Decision records** — captured per architectural decision, co-located with the subsystem each decision governs. See [Decision records](#decision-records).
- **Cross-cutting technical patterns** — reusable design schemes the architecture embodies across subsystems (e.g. a shared format for configurable attribute lists, an API response contract).

**Canonical realisation:** a `docs/architecture/` subtree with a root index at `docs/architecture/README.md` that lists every overview, cross-cutting pattern, and the decisions sub-index. Decision records themselves are indexed through a dedicated router at `docs/architecture/decisions/README.md` (see [Decision records](#decision-records)) which the layer root index links to. One routing chain per layer; sub-indexes within a layer are the right pattern when a cluster of artefacts carries its own conventions.

### Contributor layer

How to contribute to the project: board discipline, commit conventions, versioning policy, workflow rules, review practices. Required in every project:

- In **solo projects**, it records the standards the sole contributor adheres to
- In **agent-assisted projects**, the agent is a contributor and must read it to work correctly
- In **multi-author projects**, it onboards humans

**Canonical realisation:** `docs/contributor/README.md` as the layer index, with further contributor documentation in that subtree. A `CONTRIBUTING.md` at a recognised location is the layer's public-facing landing page — it carries the baseline contributor content and links to `docs/contributor/README.md` for the full routing chain. The layer index lists `CONTRIBUTING.md` as an artefact of the layer, same as any other. See [Convention-based exceptions](#convention-based-exceptions).

When the layer is small, `docs/contributor/` may contain little more than the layer index pointing to `CONTRIBUTING.md`; as content grows, further artefacts (versioning policy, review practices, commit conventions) populate the subtree. `CONTRIBUTING.md` remains the baseline landing page throughout.

## The bootstrap

Every conforming project provides an **agent-facing bootstrap** — the orientation surface that routes agents into the layers.

**Canonical discovery order:** agents arriving cold try `AGENTS.md`, then `README.md`. Filename matching is case-insensitive — `Agents.md`, `agents.md`, `Readme.md` all satisfy discovery. A project provides one of these — or an equivalent entry point a cold agent can locate deterministically.

**What the bootstrap does:**

- Names the project in one line
- Links to each layer of documentation
- Names any convention-based exceptions (see [Convention-based exceptions](#convention-based-exceptions)) at their chosen locations, so non-layer artefacts like `CHANGELOG.md` and `SECURITY.md` remain reachable from the bootstrap
- Notes any out-of-repo context, when publicly available (see [External context](#external-context))

**What the bootstrap does not do:**

- Inline layer content — it routes, it does not contain
- Inline workflow prescriptions — those belong in the contributor layer

Projects may use alternative techniques — hooks, generated indexes, skill systems — provided the discoverable entry point exists and the routing function is served.

When `AGENTS.md` is present, `README.md` is not the bootstrap and inherits no bootstrap obligations — its shape remains the project's choice, governed only by the single prescription in [Convention-based exceptions](#convention-based-exceptions).

## Documentation indexes

Documentation is navigated through a hierarchy of `README.md` files — one in each folder of the chain. A `README.md` has two jobs: it **introduces the folder** (what is here, what it is for) and **indexes the folder's contents** (one-line summary and link per artefact or sub-folder). It does not inline the content of the artefacts it points to — those live in their own files.

**The docs root index — `docs/README.md`.** Introduces the project's documentation as a whole, and indexes each layer subfolder (linking to its `README.md`) along with any convention-based exceptions (see [Convention-based exceptions](#convention-based-exceptions)). It is the documentation's front door.

**The layer indexes — `docs/{layer}/README.md`.** Each layer has its own index at a fixed location: `docs/user/README.md`, `docs/functional/README.md`, `docs/architecture/README.md`, `docs/contributor/README.md`. The folder and its `README.md` are always present, regardless of how much content exists. Consistent scaffolding prevents agents from propagating an ad-hoc structure as the project grows.

Each layer index introduces the layer and lists every artefact in it — directly, or through a sub-index. Co-located content (per-module docs, subsystem-local decision records, co-located tests) is *additional* to the index, not instead of it. The chain of indexes is how P4 (discoverable structure) is realised: every artefact is reachable from the bootstrap through an index entry.

## Convention-based exceptions

Some files are fixed by ecosystem convention — filename and location recognised by tooling and humans before they encounter the standard. The standard's job in this section is to describe how it relates to each, not to re-specify what they are. Entries divide into two kinds: **referential** — ecosystem conventions the standard acknowledges but does not prescribe — and **prescriptive** — conventions the standard assigns a standard-level role. Structurally, some exceptions (like `CONTRIBUTING.md`) are artefacts of a layer that live at a conventional location; others (like `README.md`) sit outside the layer model as landing pages for the project as a whole.

**README.md** *(referential, with one prescription)*. Repository-root landing page by ecosystem convention; the standard defers on its shape and contents. Single prescription: it must link to `docs/README.md` so agents and humans arriving at the repo's default landing page can reach the documentation system (P4, discoverable structure). Bootstrap-role behaviour, when `AGENTS.md` is absent, is governed entirely by [The bootstrap](#the-bootstrap) — no additional prescription attaches here.

**CONTRIBUTING.md** *(prescriptive)*. The contributor layer's public-facing landing page — carries baseline contributor content and must link to `docs/contributor/README.md` so agents and humans arriving via the ecosystem's contributor surface can reach the rest of the layer. An artefact of the contributor layer at a conventional location; `docs/contributor/README.md` lists it as such. Recognised at the repository root, in `docs/`, or in `.github/`. See [Contributor layer](#contributor-layer).

**CHANGELOG.md, SECURITY.md, CODE_OF_CONDUCT.md, and similar platform conventions** *(referential)*. The standard acknowledges them as ecosystem conventions and does not prescribe their shape. The `docs/README.md` root index lists each at its chosen location so they remain discoverable; beyond that, their contents are the project's business.

Projects pick one location per file — no duplicates.

## Decision records

Architectural decisions are first-class documentation. Taken together, a project's decision records are its **architectural history** — an append-only sequence of moments, each preserving the reasoning, forces, and alternatives that were in play when a decision landed. Individual records are historical artefacts; the indexed sequence is the history.

**Co-location.** Per P5 (contextual placement), each decision record lives with the subsystem it governs. A decision affecting the auth module lives alongside it (in `src/auth/` or `docs/architecture/auth/`, depending on the project's convention). A cross-cutting decision lives at the architecture layer root.

**Indexing.** Every decision record is reachable through a dedicated **decision router** at `docs/architecture/decisions/README.md`, which the architecture layer's root index links to. The router is a sub-index within the architecture layer, not a parallel router — it carries DD-specific conventions (numbering, supersede/extend chains, status tracking, topic grouping) that don't belong in the broader layer index. Like the layer indexes themselves, the router is present from the outset (per P6 — structure exists for content to grow into) rather than introduced once a project accumulates enough decisions to "warrant" it. This satisfies P4 (discoverable structure) through a two-level index chain; "one routing chain per layer" is the rule, not "one index file per layer".

**Record format.** Each decision record has three required fields:

- **Context** — why the decision needed making; the forces at play at the time the decision was made
- **Decision** — what was decided
- **Consequences** — what follows from the decision, both positive and negative

All three are required. Per P3 (self-contained), a record missing any of them fails to stand alone. Projects may extend the template with further fields (status, alternatives considered, stakeholders) but the three above are mandatory.

This format follows Michael Nygard's Architecture Decision Record (ADR) pattern.

**Sequential numbering.** Each decision record is assigned a sequential number at creation (`dd-001`, `dd-002`, …) carried in both its filename (`dd-NNN-slug.md`) and its heading (`# DD-NNN: Title`). Numbers are permanent and never reused — a superseded DD keeps its number. Numbering gives decision records stable identifiers that survive title changes and slug rewrites, underwrites the supersede/extend pointers described below, and makes cross-references terse and unambiguous ("see DD-012"). Project-specific conventions (separator, prefix length, per-subsystem number spaces) are project choices; sequential numbering itself is not.

**Immutability.** A decision record preserves the reasoning *at the moment the decision was made*, with the forces and alternatives that were in play then. The body of the record — context, decision, consequences, and any extended fields that capture historical reasoning (alternatives considered, constraints) — is frozen once the decision lands. It is not rewritten as the project evolves. Historical reasoning is expensive to reconstruct and impossible to recover once overwritten; immutability is what makes a decision record a decision record rather than a living doc.

**Supersede and extend.** When a later decision changes direction, the old record is not edited. Instead, a new decision record is written that **supersedes** the old one (replaces its decision) or **extends** it (builds on top without replacing). Pointer metadata — status, supersedes, superseded by, extended by — sits in a navigational header that may be updated; the frozen body is never touched. Supersede chains form the historical spine of the architecture; agents and humans trace them backwards to recover context.

**Decision records vs living docs.** Decision records explain *why* the project is the way it is; living architecture and functional docs describe *what* the project is today. The distinguishing test is rewrite discipline: if an artefact should be rewritten every time behaviour changes, it is a living doc; if it should be preserved as written and succeeded by a new record, it is a decision record. Keeping this boundary clean prevents two common failure modes — architecture overviews accreting historical reasoning they will later rot by updating, and decision records drifting into current-state descriptions that rot as code evolves.

## Three-way reconstruction

Code, docs, and tests are three views of one system — each describing what the system does, from a different angle and in a different medium. Keeping them in step is the standard's central concern.

**The bar.** Any two of code, docs, and tests should be sufficient to reconstruct the missing third:

- Code + docs → reconstruct the tests
- Tests + code → reconstruct the docs
- Tests + docs → reconstruct the code

A project that meets the bar makes three failure modes detectable that would otherwise go unnoticed:

- **Documentation drift** — tests and code agree; docs describe behaviour that is no longer true
- **Untested documented behaviour** — docs and code agree; no test covers the behaviour
- **Undocumented tested behaviour** — tests and code agree; docs do not describe the behaviour

**Reconstruction is a matter of degree.** Meeting the bar fully is Level 3 of the maturity ladder (see [Maturity ladder](#maturity-ladder)), but reconstructability exists on a continuum: the more of each view can be recovered from the other two, the more mature the documentation. Reconstructability is one of the dimensions the ladder measures.

## Tests as documentation

Tests that assert behaviour are part of the functional layer. They document what the system does, executably. For tests to hold up their third of the reconstruction bar (see [Three-way reconstruction](#three-way-reconstruction)), they must read as specifications.

- **Per-test:** each test is named and structured so a reader can see what behaviour *it* covers without inspecting implementation. `test_migration_handles_null_user` in a Given / When / Then structure qualifies; `testCase1()` asserting `result == 42` does not.
- **Corpus-level:** the test suite collectively encodes enough behaviour that, together with the code or the docs, it recovers the third view. A suite of opaque tests fails this property even at high coverage.

**Co-location.** Per P5 (contextual placement), tests co-locate with the modules they exercise, following the project's convention (e.g. `auth_test.rs` alongside `auth.rs`, or a parallel `tests/` tree that mirrors source layout).

Per P7 (structure over tooling), test frameworks, DSLs, naming conventions, and coverage thresholds are project choices. The standard prescribes only that tests carry behavioural meaning in their structure and names.

## Change discipline

P6 (ratcheting improvement) expresses as a simple operational norm: changes should not regress the project's current position on the maturity ladder. The floor is the level the project has reached, not the top of the ladder.

Docs and tests are maintained concurrently with the code change that affects them, either landed together or written first (documentation-driven, test-driven). Projects choose the timing; the norm is that changes do not leave docs or tests to drift behind.

Per P7 (structure over tooling), commit-message conventions, review rules, and gating (pre-commit hooks, CI) are project choices.

## Maturity ladder

The maturity ladder is a progression in documentation maturity. Each level reflects two correlated dimensions: the **completeness and quality of the documentation itself** — structural conformance, substantive content, clarity, maintenance discipline — and the resulting **degree of three-way reconstructability** between code, docs, and tests (see [Three-way reconstruction](#three-way-reconstruction)). Thorough, conforming, well-maintained documentation is what enables reconstruction; the two move together. Per P6 (ratcheting improvement), projects adopt incrementally and self-assess their current level.

### Level 1 — Bootstrapped

- A discoverable entry point (per [The bootstrap](#the-bootstrap)) exists
- All layers are populated at least as stubs, with one-line index entries
- Structure exists for content to grow into

*Structural scaffolding is in place; content is minimal; reconstruction is not yet possible.*

### Level 2 — Documented

- All layers carry substantive content
- Decision records capture decisions made to date
- Code and docs are kept in step as changes land

*Documentation is substantive and actively maintained; partial reconstruction is possible from any pair of views, though gaps and ambiguities remain.*

### Level 3 — Reconstructed

- The documentation meets the three-way reconstruction bar across all layers
- Code, docs, and tests are mutually reinforcing; any two suffice to reconstruct the third
- The mature form of agent-ready documentation

*Documentation is comprehensive and mutually reinforcing with code and tests; reconstruction is complete and the failure modes named in [Three-way reconstruction](#three-way-reconstruction) are all detectable.*

## External context

Per P3 (self-contained), the project is the conformance boundary. A conforming project passes the **agent-reconstruction test**: an agent with no external context can answer every architectural question about the project from its documentation alone.

Projects that maintain out-of-repo context — vaults, wikis, knowledge bases — may link to that context from the bootstrap, but only when it is publicly available. Private knowledge bases must not be linked from public-facing bootstraps.

## Plans vs decision records

**Plans** are temporal pre-work artefacts. Projects use whatever plan workflow suits them. This standard does not standardise plans.

**Decision records** (see [Decision records](#decision-records)) are durable post-work artefacts and are standardised.

Any architectural decision embedded in a plan is lifted into a co-located decision record when the work it guided lands. What happens to the plan itself is a project concern.

## Versioning

This standard is versioned with [semver](https://semver.org/). The version lives in this document's frontmatter — single source of truth.

- **Major** — breaking changes: removing a default layer, changing bootstrap discovery order, altering a decision record requirement
- **Minor** — additive: new default layer, new named principle
- **Patch** — clarifications only

Projects cite conformance against a specific version. Published changes carry changelog entries.

## Changelog

### v1.0.0 — 2026-04-20

Initial release. Establishes:

- Seven principles
- Four-layer default vocabulary
- Bootstrap with canonical discovery order
- Documentation indexes
- Convention-based exceptions
- Decision records as architectural history
- Three-way reconstruction as the mature-state bar
- Tests as documentation
- Change discipline
- Three-level maturity ladder
- External context boundary
- Plans-vs-decisions distinction
- Semver versioning for the standard itself
