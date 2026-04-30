# DD-046: Park unfinished `brain_process` / embeddings work off-main until it is ready to ship

**Status:** Implemented (v0.33.0)
**Extends:** DD-033, DD-045

## Context

The exploratory `brain_process` work touched a wide area of the system:

- an experimental MCP tool
- embeddings sidecars under `.brain/local/`
- retrieval/index lifecycle hooks
- config flags and docs

That work is promising, but it is not yet a finished shipped feature. The
current implementation is not in active day-to-day use, the scripts-only story
for content processing is incomplete, and the embeddings design may still
change materially before the feature is ready.

At the same time, simply deleting the exploratory work would throw away useful
progress. The code, tests, and docs should be preserved somewhere durable while
`main` converges on the cleaner `v0.33.0` surface defined in DD-045.

## Decision

Keep the unfinished `brain_process` / embeddings work off the shipped mainline
until it is ready, and preserve the current exploratory implementation on a
dedicated feature branch.

- `main` does not ship `brain_process` as part of the `v0.33.0` release line.
- `main` does not ship the embeddings lifecycle or vector freshness plumbing
  that only exists to support the unfinished `brain_process` feature.
- The current exploratory implementation is preserved on feature branch
  `brain-process-wip`, with the corresponding development worktree parked at
  `~/Development/obsidian-brain-brain-process-wip`.
- The parked branch is explicitly incomplete. It is a place to continue the
  design later, not a second supported release line.
- When `brain_process` returns, it should do so by an explicit reintroduction
  decision rather than by silently carrying unfinished surface area on `main`.

## Alternatives Considered

### 1. Leave `brain_process` shipped but disabled behind a feature flag

Rejected for the current release line. A feature flag reduces user impact, but
it still leaves incomplete API surface, lifecycle plumbing, docs, and tests in
the shipped mainline. For `v0.33.0`, the cleaner choice is to keep the release
surface aligned with what is actually ready.

### 2. Delete the experimental work entirely

Rejected. The exploratory work is substantial and potentially useful. Throwing
it away would make future process/semantic-search work slower and would lose
the implementation notes embodied in the code and tests.

### 3. Keep the code on `main` but strip only the docs

Rejected. That would make the release line less honest, not more. Either the
feature is a supported part of the product surface, or it is parked for later.

## Consequences

- `main` can ship `v0.33.0` with a smaller, more coherent MCP and scripts
  surface.
- The experimental process/embeddings work remains recoverable on
  `brain-process-wip`.
- Current-state docs on `main` should describe the shipped surface only; any
  experimental `brain_process` docs that still matter should live with the
  parked branch until the feature is reintroduced.
- A later return of `brain_process` should include a deliberate design pass on
  scripts-only parity, embeddings lifecycle, and the final user-facing surface.
