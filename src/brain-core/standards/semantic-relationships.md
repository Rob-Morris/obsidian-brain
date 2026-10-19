# Semantic Relationships and Selected History

Use semantic assertions for explicit relationships and attributed metadata about
artefacts, relationships or terms. Existing parentage, primary workspace membership
and lifecycle fields keep their native authority. Ordinary body links remain useful
navigation; they do not automatically create semantic assertions.

This reference describes the current implementation. Discover the selected Brain's
installed contracts before use; an older installation may not expose these commands.
The application catalogue owns request shapes, permissions and transport support.

## Discover and select a command

```bash
brain command list --owner application --query relationship --json
brain command list --owner application --query history --json
brain command describe relationship.batch --owner application --json
brain command describe history.correct --owner application --json
```

MCP uses `command_list` and `command_describe`, with
`target_command_id: relationship.batch`, for example. Discovery returns `mcp_tool`
and `cli_argv`; use those exact projections. `relationship.batch` maps to
`relationship_batch` and `brain relationship batch`; `history.read` maps to
`history_read` and `brain history read`. The direct script uses the same noun and
verb through `python3 .brain-core/scripts/command.py`, at the declared dependency
tier. These commands currently require the portable tier and no external provider.

| Task | Canonical commands | Declared authority |
|---|---|---|
| Discover and inspect definitions | `relationship.definitions`, `relationship.read` | reader |
| Register, refine or remove vocabulary | `relationship.define`, `relationship.undefine` | maintainer |
| Author and disposition assertions | `relationship.batch` | contributor |
| Expose native facts for annotation | `relationship.expose` | contributor |
| Query current assertions and exposed facts | `relationship.query` | reader |
| Check source integrity and selected policies | `relationship.check` | reader |
| Rebuild the disposable projection | `relationship.refresh` | maintainer |
| Inspect events and effective capture policy | `history.read`, `history.query`, `history.policy` | reader |
| Write custom events or linked corrections | `history.write`, `history.correct` | contributor |
| Configure capture, clean up events or repair publication | `history.configure`, `history.cleanup`, `history.repair` | maintainer |

Authority is a command requirement, not a grant. Use the normal access workflow if
the selected Brain reports authorisation required; discovery does not grant access.
Mutation previews use CLI/script `--dry-run`, not a field inside the semantic
request. MCP exposes the normal preparation/consent workflow; do not invent an
MCP dry-run argument.

## Register vocabulary before authoring

Registration is vault-local and mandatory before first use. The minimum definition
is `definition_kind`, `identity`, `name` and a clear `meaning`. It does not require a
complete ontology or executable rules. Inspect existing definitions first.

```bash
brain relationship definitions --request-json '{"definition_kind":"predicate"}' --json
brain relationship define --request-json '{"definition_kind":"predicate","identity":"confidence","name":"Confidence","meaning":"Confidence in the subject"}' --json
brain relationship batch --request-json '{"items":[{"action":"create","identity":"design-confidence","subject":{"kind":"artefact","reference":"design/example"},"predicate":"confidence","object":{"kind":"literal","value":"high"}}]}' --json
brain relationship read --request-json '{"identity":"design-confidence"}' --json
```

Replace `design/example` with an existing artefact. Endpoints have explicit kinds:
`artefact`, `relationship`, `occurrence`, `predicate`, `event` or `literal`. Reference
nodes use `reference`; literals use a scalar `value`. Use canonical living keys or
maintained temporal paths for artefacts, and graph IDs for graph entities. Creating
external metadata does not rewrite the assessed artefact.

Every assertion has its own ID. Equal triples can coexist under the default
`cardinality: many`. Optional `kinds` and `values` constrain objects. A predicate
with `cardinality: one` has one active value per subject + predicate + explicit
`scope`; the default scope is `global`. This assertion scope is distinct from
workspace attribution. Workspace filters select attribution inherited from the
subject, including metadata about an assertion.

Single-value replacement uses `expected_slot_revision` as well as the assertion's
`expected_revision`; use the values returned by `relationship.read`. `empty` admits
only an empty slot. Independent assessment claims may coexist: a single current
property does not make the newest reviewer authoritative.

Definition updates require the exact `document_revision` as `expected_revision`.
Documentation-only `description` edits preserve the semantic contract revision;
constraint changes produce a new contract and require affected data to be checked.
Changing `meaning` requires a new identity and explicit migration. Definitions are
not executable hooks. `relationship.undefine` requires revision-bound disposition
of dependants; built-in event contracts cannot be removed. Definition sync preserves
local catalogue bytes and reports invalid/conflicting contracts for review.

## Change meaning without transferring metadata

`relationship.batch` accepts 1–100 items with `action: create`, `replace` or
`remove`. Replace/remove require the existing `identity` and logical
`expected_revision`. A replacement with different meaning receives a new assertion
ID. Dependent metadata does not silently move to it.

When a change has dependants, the command reports the exact blocking dependency
closure. Inspect those identities and submit explicit `dispositions`, each with
`identity` such as `relationship:review`, its `expected_revision`, and optionally a
typed `explanation` reference to a report/decision. Disposition withdraws the
dependant; revalidation means explicitly authoring new metadata against the
replacement. Re-read after a conflict rather than substituting a new revision
without reviewing the changed inputs.


A valid existing artefact does not need `created` frontmatter to accept metadata.
First-reference bindings do not rewrite its content. After a supported delete with
explicit dependency dispositions, deliberate metadata authoring against a newly
created artefact at the same key allocates a fresh binding identity. Old identity-pinned
references remain invalid; old metadata is never transferred. These guards do not
authenticate birth markers or detect unobservable external delete/recreate sequences.

## Native facts remain owner-backed

```bash
brain relationship expose --request-json '{"subject":{"kind":"artefact","reference":"design/example"}}' --json
brain relationship query --request-json '{"subject":{"kind":"artefact","reference":"design/example"},"include_native":true}' --json
```

`relationship.expose` persists annotatable occurrence IDs for `predicate: native.parent`,
`predicate: native.workspace`, `predicate: native.relevance` and `predicate: native.status`. Queries include native
facts by default; `include_native: false` explicitly excludes them. An artefact
subject query reads current owner-backed facts without writing bindings, occurrences
or events. Each `native-fact` reports `materialised`; an unmaterialised fact has no
`id` and cannot be annotated until explicit exposure. Exposed facts carry their
persisted occurrence ID. The evaluated native facts also bind query cursors; body-only edits do not invalidate them.
Broad queries include materialised occurrences and explicitly report that other
native subjects were not evaluated; use subject queries for current owner facts.
Workspace membership, workspace-hub self-membership and relevance tags remain
distinct roles. `native.*` is reserved and cannot be registered as authored claims.

Use `artefact.reparent`, `artefact.set-workspace` and `artefact.set-status` to change
the native facts, preserving cycle, membership, recursion and lifecycle rules.
Generic document frontmatter edits reject lifecycle-owned status/parent/key fields,
even an equivalent status patch. Supported native transitions compose relationship
dependency handling; supply their exposed `relationship_dispositions` when needed.
`shaping.start` also publishes its target status/disposition effects, and
`artefact.migrate-naming` maintains typed temporal locators through the same
publication/recovery protocol. `artefact.create` accepts workspace selection but
rejects `relationship_dispositions`, which its creation owner does not consume;
the other 16 native consumers retain that field.
Relocation/rekey preserves the identity of an unchanged exposed fact. Removing a
fact retires its occurrence; recreating A→B later creates a new occurrence and does
not revive its annotations. Externally changed exposed facts require inspection and
explicit dispositions through `relationship.expose` before reconciliation.

## Current queries and scoped assessment

`relationship.query` filters by typed `subject`/`object`, `predicate`, assertion
`scope` and `workspace` (`workspace/key` or `global`). Omitted workspace includes
visible workspaces. `limit` defaults to 50, maximum 100; `work_budget` defaults to
1,000, maximum 5,000; `depth` defaults to 1, maximum 10. Result count and traversal
work are separate bounds. Queries and definition discovery return generation-bound
`next_cursor`; repeat selectors unchanged and restart if the cursor conflicts.
Depth queries include an opaque `traversal` stack in that cursor. Preserve the whole
cursor: it carries unfinished roots and subject-metadata descendants across work,
result-count and byte limits. Traversal is depth-first and the stack is bounded by
the requested depth; cycles and roots also reached as descendants do not duplicate
records.

Inspect `records`, `findings`, `complete`, `visited`, `generation` and
`source_freshness`. Cached queries report `observed-projection-only` and incomplete
source certification even when no next page remains. Missing results do not prove
that externally altered, unvisited inputs were absent. A generation identifies a
projection, not a certified simultaneous snapshot of multiple files.

```bash
brain relationship check --request-json '{"work_budget":5000}' --json
```

The default check validates record/reference/definition/cardinality/native-fact and
correction integrity. Correction adjacency and single-value slot membership come
from observed source records, including imports not yet refreshed into SQLite.
When a paged check cannot inspect the complete relevant source family within its
budget, it reports unresolved integrity rather than a clean correction/slot verdict.
Increase the budget to inspect that family; independent source pages do not become
a coherent global snapshot. `vault.check` also includes `relationship_integrity` findings.
Semantic obligations are opt-in fields on `relationship.check`:

- `assessment` selects a subject, criterion, scope and registered policy predicates.
  Claims pin subject, criterion, evidence and relevant definition revisions.
  The definition-revisions map must include all selected policy predicates and,
  when assessing an assertion, that assertion's own predicate contract. Evolving
  its constraints makes old claims stale until explicitly reassessed and repinned.
  Missing, stale or inaccessible inputs cannot establish positive standing;
  conflicting current verdicts are not resolved by latest-wins. Inspect each claim
  and the policy outcome. Attribution is a claim, not authenticated authorship.
- `transcript` selects explicit sources, transcripts and a confirmed persistence
  scope for reciprocal source/transcript assertions. Lifecycle status alone does
  not create a transcript obligation. It checks registered graph assertions, not
  every prose link as an inferred universal rule.

Use `command.describe relationship.check` for the complete nested policy schema.
These purpose-owned policies do not interpret arbitrary predicate prose as code or
change native lifecycle status on a positive result.

## Selected history, custom events and corrections

Current state and history are separate queries. `history.policy` returns the
effective policy revision, selected automatic classes, and bounded event-count and
serialised-byte usage. Defaults select `native-transition`, `relationship-change`,
`definition-change` and `correction`. Capture records actual supported native
lifecycle transitions, exposed structural changes, annotated assertion changes and
semantic definition changes where applicable. Routine body rewrites, equivalent
no-op sets, failed/no-effect writes, reads and projection refreshes do not invent
transition events. Custom events and explicit corrections remain explicit writes.

`history.configure` takes `selected` and the policy's `expected_revision`; an empty
selection disables automatic capture. It does not enable an external file watcher
or automatic pruning. Direct filesystem/Obsidian edits receive integrity checking,
with no guaranteed events, reconstructed intermediates, invented actor or change
time. History is not the authority from which current state is rebuilt.

Register custom `definition_kind: event-type` contracts using `relationship.define`.
`payload_fields` declares `name`, `kind` (`string`, `number`, `boolean`, `reference`)
and `required`. `history.write` takes `event_type`, a typed `subject`, and a list of
`payload` entries with `name` and scalar or typed-reference `value`. For example:

```bash
brain relationship define --request-json '{"definition_kind":"event-type","identity":"milestone","name":"Milestone","meaning":"A caller-reported milestone","payload_fields":[{"name":"detail","kind":"string","required":true}]}' --json
brain history write --request-json '{"event_type":"milestone","subject":{"kind":"artefact","reference":"design/example"},"payload":[{"name":"detail","value":"Compatibility review completed"}]}' --json
brain history query --request-json '{"subject":{"kind":"artefact","reference":"design/example"}}' --json
```

Caller events carry `provenance: caller`; observed native mutations carry
`provenance: observed`. Optional `asserted_at` and `attribution` remain caller claims,
separate from recorded time. Events pin their event-type contract. Reads distinguish
`historical_validation` against that retained contract from `present_conformance`
against today's definition.

`history.query` supports subject, workspace, event-type `predicate`, inclusive
`date_from`/`date_to` with explicit timezones, limit, work budget and cursor.
`history.read` takes `identity` and optional `work_budget` (default 100). Both expose
`interpretation`, including a corrected original's effective `head` and `chain`.
Correction traversal checks the visited revisions against the observed projection;
external changes hidden outside those inputs require explicit integrity checking.
Read the head to obtain its revision, then call `history.correct` with the original
or correction `identity`, `expected_head_revision`, `reason` and `replacement`.
The linked event preserves the original account. Competing heads conflict; imported
forks, cycles or unavailable correction inputs remain unresolved/incomplete.

There is no silent pruning. Preview `history.cleanup` with `identities` (1–100)
and `--dry-run`; include required dependent events in the cleanup set. Non-event
dependants require explicit relationship disposition first. An event captured by an
unresolved publication cannot be cleaned up: complete the reported `history.repair`
operation first, then retry cleanup. This prevents stranding captured payloads or
replaying data after explicit cleanup. Cleanup retains
tombstones, so deleting a correction cannot silently revive the original account.
It is not secure erasure from Git, snapshots or backups, and history is not an
immutable authenticated audit log.

## Source, projection and recovery

| Vault-relative location | Role |
|---|---|
| `_Relationships/<kind>/<id>.json` | Shared assertion, occurrence, endpoint, event, pinned contract and recovery-operation records |
| `_Config/Relationships/predicate/`, `event-type/`, `policy/` | Shared vault-local vocabulary and capture selection (`policy/capture.json`) |
| `.brain/local/relationships.sqlite` | Disposable local adjacency/locator projection |

Relationship commands own authored mutation; generic note/body editing is not a
relationship editing API. A record currently has a 256 KiB bound. Physical files
and the SQLite projection do not define parentage, workspace ownership or identity.

For recovery:

1. Inspect the result envelope and known committed effects. For an uncertain call,
   use `invocation.read` with its returned receipt reference before retrying. A
   `partial` native write may have committed content while publication is unfinished.
2. For a missing/stale projection, run `relationship.refresh` with an explicit
   `work_budget`. Its `records[0]` contains `cursor`, `scan_finished`, `complete`
   and accumulated `total_findings`. Pass the returned opaque `cursor` to the next
   refresh request until the scan finishes. Omission restarts the rebuild; changed
   source inventory conflicts and requires a restart. Semantic mutations and native document/lifecycle plans
   require a complete projection and refuse an unfinished/incomplete rebuild.
   Finish maintenance before resuming authoring. Address findings, complete a clean
   refresh, then run `relationship.check`. Refresh rebuilds locators, not semantic
   truth, and creates no history.
3. For interrupted publication, inspect the exact reported operation and receipt,
   then invoke `history.repair` with `operation`. It validates captured inputs and
   native guards before completing missing publication. A durable state-committed
   marker permits publishing its captured event after later changes, without
   replaying the earlier state. Missing captured events resolve typed locators by
   retained artefact identity while preserving their logical revision. Repair
   reconciles captured removals against current source: absent targets leave the
   index, while subsequently recreated definitions remain intact. Without that
   proof, changed or uncommitted inputs
   refuse repair. Completed repair is idempotent and requires no captured payload.
   If nothing committed and captured before-state revisions prove that fact,
   `history.repair` also accepts explicit `abandon: true` with a retained `reason`.
   Abandonment refuses when proof is missing or state changed; it does not roll back
   effects or permit the abandoned operation to be replayed. Completion and safe
   abandonment replace captured payloads with minimal operation receipts. Unresolved
   operations retain their inputs until safely repaired or abandoned; cleanup of an
   event does not leave its payload in completed operation records. This is logical
   cleanup, not secure erasure from invocation receipts, filesystem history or backups.
4. For malformed external records, inspect the finding's path, identity and reason.
   Valid records can be changed/dispositioned through their owners. Undecodable
   records may require operator repair or restoration from known-good source bytes
   before refresh and a fresh check. There is no general automatic content repair.
   Review changed evidence and explicitly recreate assessments when warranted.

Check and refresh default to a 5,000 work budget and have no workspace selector.
Refresh pages bound content reads and retained memory; filename enumeration is
additional explicit rebuild work. Its cursor differs from query `next_cursor`.
The integrity check spends work on resolution and revision rechecks as well as
source reads. Follow its separate `continuation` value in the next request's
`cursor` to inspect subsequent source pages. For targeted diagnosis, pass up to
100 `identities` such as `["relationship:design-confidence", "predicate:confidence"]`.
A completed scoped check certifies only its selected inputs. If a page exhausts
validation work, its continuation retries that page: increase the work budget or
select explicit identities instead of silently skipping unvalidated records. Paged/scoped checks
cannot establish whole-policy standing; selected policy evaluation reports unknown
when its complete workload exceeds the evaluation budget. Query/refresh pagination
does not turn observed pages into a coherent whole-graph snapshot. Do not remove
useful records merely to obtain a passing check.

Supported writes validate and re-admit their bounded plan under shared mutation
coordination. Publication journals support targeted recovery. This does not promise
crash-atomic transactions across all files, a coherent whole-graph snapshot,
distributed coordination or production performance inferred from prototype probes.

See [Keys](keys.md), [Hub pattern](hub-pattern.md), [Provenance](provenance.md) and
[Archiving](archiving.md) for the native conventions that continue to apply.
