# DD-090: Semantic relationships and selected history

**Status:** Accepted

## Context

Artefact frontmatter and body links do not provide independently addressable,
attributed assertions about another relationship. Adding arbitrary fields also
does not establish reference continuity, dependency handling or assessment currency.
The approved semantic relationship design requires those capabilities without
making existing artefacts carry a second representation of every claim.

[DD-056](dd-056-authoritative-lifecycle-metadata.md) retains explicit ownership of
lifecycle metadata; [DD-088](dd-088-workspace-destination-and-relevance.md) separates
parentage, primary membership and relevance. Semantic associations must preserve
both boundaries. The typed application and result contracts in
[DD-061](dd-061-typed-command-application-boundary.md) provide the integration point
for authoring, preparation, admission and truthful partial outcomes.

## Decision

### Independent entities through relationship tooling

Represent assertions as independently identified graph entities with typed subject,
predicate and object, explicit scope and logical revision. Equal triples can have
distinct identities. Support metadata directly on artefacts and further assertions
about assertions or terms. Relationship commands own mutation; these records are
not ordinary editable notes or extra relationship frontmatter on existing artefacts.

Require lightweight vault-local predicate registration before first use: identity,
name and clear meaning, with optional constraints added when needed. Event contracts
are separately registered typed definitions. Registration does not install executable
rules or authenticate an assessor. Distinguish a constrained single current value
from independent assessment claims; explicitly selected policies evaluate criteria,
scope, evidence and pinned revisions without latest-wins approval.

### Native authority and occurrence identity

Derive parent, workspace membership/relevance and status facts from their existing
native owners. Make them visible in relationship views without creating competing
authored native claims. Allocate persistent occurrence IDs when facts are exposed
for annotation, separately from read-only visibility.

Native setters retain their structural and lifecycle invariants. Maintain typed
references and unchanged occurrence identity across a rekey or relocation. A changed
or removed structural fact retires its occurrence and requires explicit disposition
of dependent metadata. Recreating the same endpoint tuple creates a fresh occurrence;
old annotations do not revive or silently transfer to a different meaning.

### Shared source records and a disposable projection

Select independent JSON records under `_Relationships/`, with vault-local vocabulary
and capture policy under `_Config/Relationships/`. Keep the SQLite adjacency/locator
index under `.brain/local/` disposable. Logical identity, meaning, definition contract,
slot state, native document revision and physical packing remain distinct concepts.

Independent files give direct identity ownership, bounded rewrite scope and simple
integration with native file planning and conflict handling. This is a simplicity
choice informed by qualified mechanics and integration evidence. The alternative
packing replay consumes native-generated records; it is not a second native backend
or proof that independent files outperform per-artefact collections. Retain the
measurement boundaries and current evidence in the
[acceptance ledger](../../contributor/semantic-relationships-acceptance.md).

### Current truth, selected history and recovery

Keep current assertions separate from selected typed history. Capture qualifying
supported command changes by default, excluding routine body writes, equivalent
no-op sets and reads. Support registered caller-authored events with provenance
distinct from system-observed changes. Events pin their validation contracts.

Correct an account through a linked correction, retaining the original payload and
exposing its corrected/superseded interpretation and successor through supported
direct reads as well as history queries. Explicit cleanup must not revive a deleted
correction's original account. History is neither current-state authority nor an
immutable authenticated audit log.

Use bounded, revision-aware queries and integrity checks. Missing, stale,
inaccessible, conflicting or insufficiently evaluated inputs cannot establish positive
assessment standing. Report incomplete/unknown evaluation rather than certifying a
whole-graph snapshot from a cache generation or observations across pages.

Compose relationship effects with native preparation and admission. Publication can
commit current state before history or projection completion; retain captured inputs
and receipts for targeted repair, report known partial effects and inspect uncertain
outcomes before retry. Repair must not replay a newer state mutation or invent missing
before-values. Safe abandonment requires proof that the captured before-state remains
unchanged. These are recovery semantics, not crash-atomic multi-file transactions.

Guarantee selected automatic history only through supported Brain commands. Direct
filesystem/Obsidian changes receive integrity and observed-staleness checking, without
guaranteed events, reconstructed intermediate states or invented actors/change times.

## Alternatives Considered

- Embed semantic claims in existing artefact frontmatter: couples review metadata
  to reviewed content and provides no independent assertion lifecycle.
- Make every assertion an ordinary note, or author inverse/native copies: confuses
  storage with interaction and introduces competing structural authority.
- Pack records into collections: may reduce file count and loading work, but expands
  rewrite/conflict units and needs additional contained-record coordination. The
  qualified replay does not settle every collection layout's costs.
- Use a closed global ontology or execute predicate descriptions: raises the bar for
  ordinary metadata and conflates vocabulary with policy or authority.
- Rebuild current truth from a complete immutable event log, or watch every external
  edit: exceeds selected-history utility and promises observations not established
  by the supported command boundary.

## Consequences

Native lifecycle, workspace and reference planners must compose semantic dependencies
without transferring assessments to changed facts. Definition evolution, current
property replacement and corrections need separate revision guards. Physical files,
recovery receipts and events consume storage; explicit cleanup does not imply secure
erasure, automatic pruning or bounded lifetime growth.

Queries must disclose scope, budgets and freshness. Operators need distinct paths for
projection refresh, source integrity repair and interrupted publication recovery.
Performance probes remain qualified local evidence, not production guarantees.

Accepted records the architectural choice, not deployment or completed conformance
review. The [shipped reference](../../../src/brain-core/standards/semantic-relationships.md)
owns current command details and limits. The
[behaviour specification](../../../tests/features/semantic_relationships.feature)
states user-visible contracts; the acceptance ledger maps them to native command,
lifecycle, policy, compatibility, recovery, budget and upgrade tests. Review findings
are implementation defects to resolve, not exceptions adopted by this decision.
