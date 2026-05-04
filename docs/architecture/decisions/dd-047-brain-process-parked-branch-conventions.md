# DD-047: Keep the parked `brain_process` branch explicitly unreleased while aligning with `v0.33.0` conventions

**Status:** Accepted
**Extends:** DD-046

## Context

DD-046 deliberately kept the unfinished `brain_process` / embeddings work off
`main` so `v0.33.0` could ship a smaller, cleaner surface. That decision also
preserved the exploratory implementation on the parked `brain-process-wip`
branch so the work could continue without being thrown away.

As that branch evolves, two kinds of drift become dangerous:

- release drift, where branch-local docs pretend the work is already a shipped
  release line and mint misleading version/changelog history
- convention drift, where partially implemented code keeps older patterns even
  though `v0.33.0` established newer conventions around thin MCP wrappers,
  recursive artefact resolution, pending-index updates, and branch-vs-release
  clarity

The branch needs a way to stay honest about being unreleased without becoming a
second unsupported design dialect.

## Decision

Treat `brain-process-wip` as an explicitly unreleased exploration branch that
still aligns itself to the structural conventions established on `main` in
`v0.33.0`.

- Keep the shipped release markers (`src/brain-core/VERSION`,
  `docs/CHANGELOG.md`) pinned to the last real mainline release until this work
  is ready to return through normal release bookkeeping.
- Do not mint synthetic branch-only semver versions or branch changelog entries
  that could be mistaken for shipped Brain releases.
- Let current-state docs on this branch describe the branch behaviour, but mark
  branch-only surfaces explicitly when they differ from shipped `main`.
- Continue to refit exploratory code onto the `v0.33.0` conventions where that
  lowers merge risk: thin server wrappers over script logic, recursive artefact
  handling that respects current folder conventions, lazy optional embeddings,
  and mutation flows that queue retrieval-index updates instead of introducing
  bespoke side effects.
- When `brain_process` is ready to return to `main`, do so through a separate,
  release-ready reintroduction decision plus the normal version/changelog work.

## Alternatives Considered

### 1. Mint a fake branch-only version now

Rejected. `VERSION` participates in runtime drift detection, upgrade logic, and
user-facing release provenance. A fake version would make the branch look more
shippable than it is and muddy the eventual real release history.

### 2. Leave docs and code ambiguous until the feature is complete

Rejected. Ambiguity is exactly how regressions get reintroduced later: agents
and humans stop being able to tell which behaviour is branch-only, which is
shipped, and which conventions they are supposed to preserve while iterating.

### 3. Keep the branch unreleased and also ignore `v0.33.0` conventions

Rejected. That would make future merge work needlessly expensive and increase
the risk that exploratory code reintroduces behaviours `main` already cleaned
up.

## Consequences

- The branch can keep moving without pretending it is already a release line.
- Audits on this branch should compare against `v0.33.0` conventions from
  `main`, not against whatever patterns happened to survive from the earlier
  exploratory implementation.
- A future merge still needs explicit release bookkeeping and likely another DD
  that supersedes or extends this one once the feature is actually ready.
