# DD-068: Skill ownership is separate from source and client exposure

**Status:** Implemented (v0.62.0)
**Extends:** DD-024, DD-052, DD-058, DD-061

## Context

Brain already had two skill ownership locations: immutable system packages in
`.brain-core/skills/` and user-owned packages in `_Config/Skills/`. The original
shaping-only client adapter proved that client discovery can remain separate
from workflow ownership, but it did not provide a general source lifecycle or
exposure policy. Treating Git as a third ownership location would conflate who
may edit a package with where one version came from.

## Decision

Core and user remain the only skill substrates. Git is optional provenance and
update capability for a package in either substrate. Brain-owned source
descriptors live outside package trees: bundled descriptors in
`.brain-core/skill-sources.json` and user lifecycle state in
`.brain/skill-sources.json`.

Unqualified resolution is user-first. `user:<name>` and `core:<name>` explicitly
select a substrate. Core packages are never mutated. A supported edit of a
core-only skill materialises an identical user package first. Likewise, a user
update of a core-only Git-backed skill installs the updated package into user
space. When a user package already exists, it is the only possible update
target; Brain never falls through to core provenance.

Updates compare the installed baseline, current user package, and fetched source
package. Clean upstream changes replace the complete package. Concurrent local
and upstream changes stage the upstream package and path-level comparison under
`.brain/skill-conflicts/` while preserving the installed package. Explicit
replacement archives local content first; detachment keeps the package and
removes only source tracking. A core upgrade collapses a clean tracked
core-lineage override when its complete package identity exactly matches the new
core package, archiving the redundant copy and reporting the reconciliation.

Client exposure is a separate explicit launcher policy. `skill.expose` installs
a thin Brain-owned adapter for any valid effective skill at global or project
scope. The adapter resolves the unqualified skill from the active
Brain on every invocation, so later user overrides automatically take
precedence. It never copies or executes package assets. `skill.unexpose` removes
only an unmodified Brain-owned adapter.

## Alternatives Considered

### Make Git a third substrate

Rejected because a repository identifies provenance, not ownership or mutation
authority. It would make shadowing and update targets ambiguous.

### Update core packages in place

Rejected because Brain upgrades own `.brain-core/`. User operations instead use
copy-on-write into the user substrate.

### Copy complete packages into clients

Rejected because copies drift from the active Brain, duplicate executable
content, and require reinstallation after every update.

### Merge conflicts automatically

Rejected because skill packages can contain arbitrary structured resources.
Brain stages and explains the three-way state without inventing merge semantics.

## Consequences

- Ownership, provenance, updates, resolution, and exposure have independent
  explicit state.
- Same-name user packages always shadow core packages without deleting either.
- Updates and conflict replacement are whole-package, recoverable operations.
- Global exposure follows the normal active-Brain resolution ladder, including
  the machine default. Project exposure resolves an existing canonical workspace
  binding first and otherwise uses the machine default; it creates no binding as
  a side effect.
- Existing shaping adapter ownership markers and safety behaviour remain
  compatible while the generic exposure surface supersedes the fixed workflow.

## Implementation Notes

Git acquisition fetches into a temporary repository and stages blobs with
`git archive`; it does not check out a worktree, run hooks, or invoke repository
filters. Package validation rejects symlinks and unsupported entries, enforces
portable collision-free paths and bounded size/count, and hashes every file path,
content digest, and executable bit.
