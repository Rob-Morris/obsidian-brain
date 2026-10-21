# DD-089: Release source identity and shared CLI ownership

**Status:** Accepted
**Extends:** DD-077

## Context

[DD-077](dd-077-dependency-reproducibility.md) makes dependency versions
repeatable while required downloads remain available. Source selection needs a
matching boundary: fetching an installer from a release tag is insufficient if
it subsequently fetches main or imports lifecycle helpers from another release.

The machine-global CLI serves several independently versioned Brains. Its
component version cannot identify every bundle containing Core, templates and
installers, nor should selecting one Brain's Core implicitly replace that CLI.
The typed launcher boundary in
[DD-049](dd-049-brain-cli-thin-dispatch.md) and upgrade transaction in
[DD-085](dd-085-upgrade-rollback-journal.md) remain the execution and rollback owners.

## Decision

Remote installation defaults to latest published main, checked against its
matching release tag, or an explicit exact version tag. Resolve one commit and
tree, then use that snapshot for installer, template, Core and lifecycle helpers.
Retained acquisitions reject changed tracked files and additional deployable
files, including ignored extras. Local source remains an explicit option without
a claim of publication. Source availability and byte-identical installations are
separate promises; there is no dedicated offline bundle or new release index.

Run lifecycle policy in an isolated selected-source process through the existing
private owner boundary, preserving actual installed CLI identity, destination
selectors, effect mode and structural outcome checks. Preserve a healthy shared
CLI by default; replacement is explicit. Health and operation capability are
separate: an unsupported operation, permission failure or unavailable selection
is not automatic replacement authority. Genuine bootstrap uses the existing
validated distribution transaction and base Python. First CLI bootstrap failure
stops before target-vault creation and gives usable retry/repair guidance; later
runtime provisioning and optional MCP registration retain their separate partial
outcomes. Failed verified acquisitions retain a pinned retry context.

Distinguish CLI component version, bundle/source identity and machine-local
installed integrity. Bundles use a Core/content discriminator even when the CLI
component version is unchanged; installed bootloaders identify their exact
checked bundle. Acquisition/installation receipts record commit/tree after the
commit exists, never a commit's own hash in its tracked tree. Target Core
provenance is independent of the preserved global CLI and participates in the
existing upgrade journal.

Expose exactly three offline version facts before live Brain attachment,
discovery or provisioning: CLI component version, distributed-with Core version
and earliest full-support Core baseline. Supported local Python is still needed.
The baseline is advisory for older Core, not a blanket execution ban. The next
release is the first supported baseline; earlier interoperability is best-effort
and non-gating. Development may declare a pending next-release baseline; release
preparation stamps it and candidate certification requires a concrete baseline
at or below candidate Core.

Publication retains certified version commits. Verify required exact-commit CI
for every newly published ledger release, then atomically update main and its
permanent version tags with expected-ref guards. Matching tags are idempotent;
conflicts refuse. Observe exact published refs before candidate cleanup,
including recovery when main moved but tags are missing. Do not automatically
backfill all historical untagged versions. Tool-enforced immutability and retained
receipts do not cryptographically detect a first-seen out-of-band tag rewrite;
first acquisition trusts the configured official origin.

## Alternatives Considered

- Always replace the shared CLI with the selected Core bundle: changes other
  Brains' machine capability without explicit intent.
- Import a selected installer into the running process: cached helpers can mix
  release policies even when the entry file and payload paths are selected.
- Key bundles only by CLI version: distinct Core/template/provenance contents
  collide despite an unchanged CLI component.
- Add a hosted release index, legacy adapter framework or offline archive
  service: exceeds the agreed repeatable source/dependency-version guarantee.

## Consequences

Installation adds bounded acquisition/process boundaries while retaining the
existing transaction, approval and recovery owners. Errors must report truthful
staging/cleanup outcomes and preserve a usable same-source retry. Publication
needs multi-release CI/tag observation rather than checking only the selected tip.

Verification uses real Git resolution and publication tests, distinct-source
helper execution, absent/unknown CLI identities, offline metadata, complete
shared-CLI/two-Brain manifests and bootstrap failure before target creation.
Docker proves disposable Linux behaviour; native platform certification remains
a separate release gate. Installed-bundle consumers, including MCP registration,
must use the distribution owner's checked identity rather than reconstruct paths.
