# Documentation Philosophy

Obsidian Brain's repo documentation conforms to the [Agent-Ready Documentation Standard v1.0.0](../standards/agent-ready-documentation.md). This document describes the local implementation of that standard.

## Four Documentation Layers

Brain organises repo-facing documentation into four default layers, each with a fixed subtree and layer index:

- **User** (`docs/user/README.md`) — how to use the brain: setup, workflows, and user-facing reference
- **Functional** (`docs/functional/README.md`) — what the system does: tool contracts, script behaviour, and configuration rules
- **Architecture** (`docs/architecture/README.md`) — how and why the system is structured this way
- **Contributor** (`docs/contributor/README.md`) — how to contribute to the repo, including contributor-facing product docs and workflow guidance

The layer model matters because agents and contributors should be able to load only the context relevant to the task at hand. A user-flow question should not require architectural rationale; a contributor workflow question should not require shipped runtime docs.

## Index-First Discovery

Brain's repo docs are navigated through an explicit index chain:

1. `AGENTS.md` or `README.md` at the repo root
2. `docs/README.md`
3. `docs/{layer}/README.md`
4. The target document

Agents should be able to reach any repo doc through that chain without crawling the filesystem. The root docs index routes to layer indexes first; layer indexes then route to individual artefacts.

Convention-based exceptions still exist. In this repo, `docs/CONTRIBUTING.md` and the changelog entry point `docs/CHANGELOG.md` live under `docs/` rather than the repo root, but they are still listed from `docs/README.md` so they remain part of the explicit discovery surface.

## Co-location of Functional Docs

Functional documentation lives in two places:

- `docs/functional/` for repo browsing
- `src/brain-core/` for shipped, in-vault functional docs

This is deliberate. An agent working inside a vault finds the functional specifications next to the shipped code it is using, without having to navigate back to the repo. The centralised copies in `docs/functional/` provide a stable repo-facing entry point for contributors.

This is Brain's main local extension of the general standard: functional documentation is both indexed centrally and co-located where normal vault agents actually need it.

## Shipped Runtime Docs vs Repo Contributor Docs

Co-location does not mean every document near `src/brain-core/` is a valid home for contributor process guidance.

Anything that ships in `.brain-core/` and participates in vault-agent bootstrap is part of the runtime surface for normal Brain agents. That surface must stay focused on operating the vault: bootstrap flow, vault conventions, user preferences, and core runtime references.

Contributor-only process material belongs in `docs/`, even when it discusses brain-core internals. Workflow tiers, canaries, pre-commit process, release discipline, and repo change-management rules are contributor documentation, not runtime bootstrap content.

In practice:

- shipped `.brain-core/` docs are written for normal vault agents
- repo contributor workflow docs stay under `docs/`
- if a rule would confuse a normal vault agent, it does not belong in shipped bootstrap surfaces

## Architecture and Decision Records

Architectural documentation is routed from `docs/architecture/README.md`, which acts as the architecture-layer entry point for this repo.

Within that layer:

- overview documents describe system structure, boundaries, and cross-cutting patterns
- decision records live under `docs/architecture/decisions/`
- the decisions subtree keeps its own local index at `docs/architecture/decisions/README.md`

The important rule is not the exact nesting shape; it is that decision records are explicitly indexed and reachable through the architecture layer rather than discovered by crawling.

## Three-Way Reconstruction as the Maturity Bar

Brain follows the standard's three-way reconstruction bar: code, docs, and tests are three views of one system, and any two should be sufficient to reconstruct the third.

That matters here because Brain's behaviour is expressed across several surfaces at once:

- code implements the tool and runtime behaviour
- docs describe the intended contracts, workflows, and rationale
- tests assert the behavioural claims

Most documentation bugs in this repo are drift bugs: one of those views changes while another does not. The pre-commit canary and contributor guidance exist largely to catch that failure mode.

## No Duplication, Cross-Reference Instead

The same fact should live in one canonical place. Other documents should link to it, summarise it briefly when needed for orientation, and avoid restating it in full.

For Brain, this is especially important because the same topic can easily appear in:

- repo-facing docs under `docs/`
- shipped docs under `src/brain-core/`
- tests
- installer or migration text

If a fact is duplicated across those surfaces without a clear ownership boundary, it will drift.

## Progressive Refinement

Brain development follows a progressive refinement path:

```text
idea -> design -> plan -> tests+docs -> implement -> review
```

Documentation is not an afterthought in that sequence. It is part of making the design concrete enough to implement and verify. If a behaviour cannot be documented or tested clearly, the design is usually not ready.

## Cross-References

- [Architecture README](README.md) — architecture-layer index
- [Contributing guide](../CONTRIBUTING.md) — contributor rules, maintenance surfaces, and testing workflow
- [Contributing — Agents](../contributor/agents.md) — contributor guidance for agents and layer-update routing
- [Pre-commit canary](../../.canaries/pre-commit.md) — drift detection and cross-check expectations
