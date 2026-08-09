# DD-061: Typed selected-Brain command application boundary

**Status:** Implemented (v0.54.1; extended v0.54.2–v0.54.49)
**Extends:** DD-002, DD-003, DD-045, DD-049

## Context

Brain's scripts are the version-matched semantic implementation (DD-002 and
DD-003), while MCP and the machine-global `brain` CLI are adapters. The current
MCP surface nevertheless groups unrelated operations behind broad request
unions, and semantic validation, authority, result and effect handling can be
distributed between scripts and adapters. DD-045 improved privilege grouping
but deliberately retained residual aggregates.

The new command grammar needs one semantic owner per selected-Brain operation
without moving that owner into MCP, the CLI, a separately installed package or
the machine-global launcher. It must also preserve Brain's bootstrap, portable
and managed dependency planes.

## Decision

Canonical selected-Brain commands live under the installed Brain's
`scripts/_application/` package. A concrete frozen request type owns its
command identifier, integer command-contract version and result payload type;
callers cannot pass a free command identifier alongside unrelated input.

Adapters explicitly compose a trusted `InvocationContext` carrying the
selected Brain, authenticated profile and authority evaluator, current ordered
dependency tier, one bounded capability snapshot, provider ports, correlation
and invocation identities, receipt ports and clock. Caller intent remains in
the request rather than the context.

`CommandApplication.invoke(request)` is the shared Python/result boundary. It
resolves one static selected-Brain catalogue entry, rejects authority or
capability failures before executor entry, invokes one internal executor,
validates the structural result, records the strongest provable outcome and
maps unexpected failures without leaking tracebacks.

Shared results use the independently versioned `brain.command-result/1`
structural union:

- `ok` carries a typed command result;
- `partial` enumerates known committed effects;
- `error` proves `effects: none | unknown`;
- unknown effects require a matching, non-retryable typed outcome reference.

The package initialiser imports nothing. Contract modules remain stdlib-only;
command modules may import only their declared dependency tier or lower.
Lower-level packages never import `_application`. Machine-global install,
resolution, repair and self-replacing upgrade commands remain separate
launcher owners and do not gain synthetic application executors.

## Alternatives Considered

### Keep scripts as unrelated public functions and normalise only in adapters

Rejected. The same semantic rule and effect policy would remain distributed
between MCP, CLI, direct scripts and Python, so parity would still be a testing
convention rather than a structural property.

### Put commands in a new top-level installed Python package

Rejected. It would add another code-version and bootstrap resolution path.
The selected Brain already ships the correct version under `scripts/`.

### Put canonical commands under `_bootstrap/`

Rejected. Bootstrap and self-replacing launcher code must run before the
selected application package or managed runtime is necessarily available.
Combining them would collapse the dependency and ownership boundary.

### Accept a dynamic `invoke(command_id, dict)` Python API

Rejected as the canonical Python surface. Dynamic names and dictionaries are
necessary at transport resolvers, but allowing them internally permits invalid
command/input pairings and makes result typing advisory.

## Consequences

- MCP, CLI, direct-script and typed-Python adapters can converge on one
  selected-Brain owner without importing each other.
- Authority and temporary capability failures are stable pre-execution result
  states rather than missing commands or adapter-specific exceptions.
- Mutation failure cannot be represented as safely retryable when effects are
  unknown; `invocation.read` resolves typed outcome references.
- Static catalogue fingerprints exclude dynamic availability and executor
  identity, so they validate installed contracts rather than environment state.
- v0.54.1 ships only the internal foundation. Existing public grammar remains
  unchanged until one coordinated breaking cutover removes old aggregates and
  adapters; no compatibility translator or mixed released grammar is added.

## v0.54.2 extension

The internal boundary now has bounded privacy-minimal receipt storage and
`still_unknown` lookup semantics, strict dynamic request resolution,
independent version-transition validation, deduplicated bounded capability
refresh and explicit supported/unsupported application projections. Canonical
unavailable details include locality and recoverability.

The separate machine-global source owns a complete stdlib-only
`brain.launcher-catalogue/1` for the 23 launcher operations in the closed
disposition inventory. Every entry has one owner and entry point, and records
why MCP, selected-Brain scripts and synthetic application Python are
unsupported. The selected-Brain package does not import this manifest.

## v0.54.3 first owner migration

`command.list`, `command.describe` and `invocation.read` now have real typed
executors in the selected-Brain catalogue. List and describe inspect the
already-bound static catalogue without importing candidate executors or
probing providers; invocation lookup returns a conclusive receipt or explicit
`still_unknown`. Strict transport decoders resolve into the same request types
used by direct Python. Public adapters remain on the old grammar until the
coordinated cutover.

## v0.54.4 first portable domain family

`artefact.read`, `artefact.list` and `artefact.outline` now have separate typed
command modules and one runtime-registry entry each. Adapter-free `_portable`
modules own their filesystem, filtering/pagination and structural-scanner
semantics; the legacy top-level scripts delegate to those modules while public
grammar remains unchanged. New typed list items distinguish canonical
`reference` from the living artefact's raw `frontmatter_key`.

## v0.54.5 portable inspection owners

`runtime.read-environment`, `vault.read-router` and `links.check` now have one
typed command owner each. Runtime facts are bounded scalars; router metadata
uses an exact result type rather than an open metadata bag; and link diagnosis
reuses the lower portable scanner without making router availability a hidden
dependency. The legacy router/environment reader delegates to the same lower
views while public grammar remains unchanged.

## v0.54.6 portable named-document owners

`skill.read/list`, `style.read/list` and `plugin.read/list` now have distinct
typed command owners over one adapter-free named-document seam. The seam owns
exact-name resolution, document loading and case-insensitive list filtering;
legacy readers and listers delegate to it. Application results retain only
bounded command-specific fields, including explicit core/user skill source,
and do not expose router records as unbounded metadata.

## v0.54.7 exact memory and trigger owners

`memory.read/list` and `trigger.read/list` now own exact, bounded router-backed
contracts. Reads select memories by canonical name and triggers by their
unique exact condition; discovery remains the responsibility of the separate
search commands. Trigger category is a closed enum and filtering searches the fields
that compiled trigger records actually own. The portable collection seam also
corrects the legacy trigger read/filter path, which previously assumed a
non-existent `name` field.

## v0.54.8 exact vault-file and archive owners

`vault.read-file`, `artefact.read-archived` and `artefact.list-archived` now
own distinct bounded contracts. Vault-file reads require an exact
vault-relative path and exclude `_Archive`; archived reads require explicit
archive membership. One portable seam owns containment, archive discovery and
legacy top-level/per-type archive layouts, and the legacy readers and listers
delegate to it without changing their public grammar.

## v0.54.9 exact artefact-type and template owners

`type.read/list` and `template.read/list` now have separate typed contracts
over one portable type-definition seam. Canonical reads select only the exact
compiled type key. Type reads return the authored taxonomy document with
bounded identity/source facts, while template reads and list items expose the
actual `.md` path so their output composes with `vault.read-file`. The legacy
adapters retain their documented singular/full-type aliases until cutover.

## v0.54.10 strict workspace read owners

`workspace.read`, `workspace.list` and `workspace.resolve` now have separate
typed contracts. Metadata reads and data-folder resolution no longer share a
result shape. Exact slugs are validated before filesystem resolution, malformed
machine-local registry state fails closed, missing identity is a typed
`not_found`, and list identity follows the same embedded-over-linked precedence
as resolution. Existing public adapters remain on their current grammar until
cutover.

## v0.54.11 bounded portable diagnostics

`vault.read-config`, `vault.check` and `type.status` complete the portable
read-only application group. Config projection is privacy-bounded and fails
closed on malformed nested structures. Compliance results omit dynamic
timestamps and root identity, expose typed findings and translate repair scopes
to canonical command identifiers rather than shell strings. Type status is a
flat exact-key result with typed library/file state and explicit missing-key
failure.

## v0.54.12 typed managed session owner

`session.start` now owns one fully typed bootstrap result and declares managed
dependency plus derived-cache-write effect for its session mirror refresh.
`InvocationContext.workspace_dir` carries optional trusted workspace identity;
the command does not accept or rediscover that adapter fact. The old no-op
context-scoping field is absent from the canonical request. `session.py`
remains the single owner of bootstrap compilation and markdown rendering.

## v0.54.13 typed optional-semantic read owners

Search is now split into one artefact command and one command per searchable
definition resource. Typed results replace the aggregate resource discriminator
and open dictionaries. `content.classify` represents ranked and
context-assembly outcomes structurally; `content.resolve` represents its
create, update or ambiguous decision explicitly.

Each command retains a complete portable lower path over `_search` or
`process.py`. Semantic retrieval is optional only for artefact search,
classification and duplicate resolution; resource-document search remains
honestly lexical-only. Automatic and explicit semantic paths require both a
provider binding and a fresh available capability from trusted invocation
context before loading selected-Brain sidecars. The application layer owns
contract validation and result normalisation while the existing search/process
modules remain the algorithm owners.

## v0.54.14 typed content-transfer mutation owners

`stage.create`, `stage.discard` and `attachment.upload` are the first migrated
selected-Brain mutation owners. They require contributor authority, declare
receipt-required retry, return compact committed effects, and leave unexpected
post-entry failures to the application boundary's non-retryable unknown-outcome
mapping. Validation and lock conflicts return structural no-effect errors.

The commands retain `_staging` and `upload_attachment.py` as their semantic and
path-safety owners. Attachment content crosses the canonical boundary as
bounded base64; caller-file reads belong to local adapters. Dry-run never
allocates a fake stage handle or writes/discards a resource.

## v0.54.15 typed named-resource creation owners

`memory.create`, `skill.create` and `style.create` now own separate granular
requests over one shared named-resource execution seam. Content is an immutable
inline/staged union with the canonical `source` discriminator. Optional
frontmatter is a deterministic tuple of typed scalar or flat-list fields,
rather than an open mutable command bag.

The application layer validates intent, authority, freshness and result/effect
shape; `create.py` retains naming, destination and write semantics. Staged
handles are consumed only after commit, stale-router and duplicate failures
claim no effects, and unexpected failures after mutation entry retain the
kernel's unknown-outcome contract.

## v0.54.16 create-only template owner

`template.create` reuses typed inline/staged content but deliberately excludes
separate frontmatter because the template body is the full markdown document.
The application owner resolves the type-linked destination under the mutation
lock and rejects an existing path before reading or consuming staged content.

The legacy aggregate currently uses template creation as overwrite. That public
behavior remains unchanged during internal migration, while the new unreleased
granular command enforces its target create-only meaning. The legacy path is
removed at coordinated cutover rather than translated or silently changed in a
mixed public grammar.

## v0.54.17 artefact creation owner

`artefact.create` completes granular ownership of the former create aggregate.
Its typed request keeps content optional because omission intentionally selects
the type's authored template; supplied content is inline or staged and never a
caller-owned path. Type, title, parent, key, bounded frontmatter and link-fix
intent remain explicit command data.

The owner continues to delegate type resolution, naming, placement,
frontmatter reconciliation and writes to `create.py`. Its structural result
captures parent advice and wikilink findings without leaking an open metadata
bag. Staged content is consumed only after commit, and unexpected failures
after mutation entry retain the shared unknown-outcome contract.

## v0.54.18 artefact document-mutation owners

Five granular artefact commands replace the document-mutation branch of the
legacy edit aggregate. Edit, append and prepend share typed inline/staged
content, frontmatter, target, selector and scope values; delete-section and
replace-text expose only their valid operation-specific fields. The application
result carries canonical hyphenated verbs even though `edit.py` retains its
internal underscore spellings.

The shared support module owns transport decoding, strict field combinations,
staged-content lifetime, structural result conversion and effect reporting.
`edit.py` remains authoritative for structural ranges, frontmatter merge modes,
lifecycle-field protection, derived moves and wikilink reconciliation. This
keeps one semantic implementation without recreating an aggregate request.

## v0.54.19 named document-mutation owners

Memory, skill, style and template documents now project the same five granular
verbs as artefacts, producing 20 concrete request identities and module-owned
catalogue entries. Small inherited request contracts express the shared named
subject and verb-specific fields; each concrete domain/verb class still owns
its command identity and can evolve independently when semantics diverge.

The modules bind directly to the v0.54.18 structural execution seam and
`edit.py`. They do not accept artefact-only link repair, caller-file content or
fields belonging to another verb. The sealed request union enumerates all 20
types, completing typed ownership of the former edit aggregate without
retaining an aggregate discriminator at the application boundary.

## v0.54.20 artefact lifecycle-mutation owners

Four commands now own the contributor-facing lifecycle mutations:
`artefact.reparent`, `artefact.set-status`, `artefact.set-key` and
`artefact.set-naming-field`. Each request exposes only the field that its verb
owns and returns the resolved path plus the exact old and new lifecycle value.

`artefact.reparent.parent` is required and nullable. A missing field is invalid
request shape; an explicit null is deliberate ownership clearing. This removes
the dangerous transport ambiguity without creating a compatibility mapper.

The application owners provide authority, receipt, locking, error and effect
semantics. They call `edit.update_lifecycle_field` for status enums, protected
metadata, naming preflight and derived path/tag/link/index changes, so lifecycle
rules remain centralised rather than duplicated across projections.

## v0.54.21 destructive artefact-transition owners

Rename, convert, archive, unarchive and delete now have distinct operator
commands instead of sharing a move or action discriminator. Each request
contains only its valid fields, and each result exposes bounded paths, link
counts, recursive members and attachment/archive follow-up state appropriate to
that verb.

Same-type and archive-boundary rename validation moved into `rename.py` beside
the actual move/link implementation. Conversion and archive semantics remain in
`edit.py`; delete remains in `rename.py`. The application seam owns locking,
authority, retry and outcome classification without duplicating those semantic
rules.

A `PartialApplyError` becomes a structural partial result and known-partial
receipt. An unexpected exception after mutation entry remains outcome-unknown.
This preserves the backend's deliberately non-transactional vault-wide link and
move operations without inviting unsafe blind retry.

## v0.54.22 artefact maintenance and link-fix owners

The remaining operator artefact workflows now have typed owners:
`artefact.reparent-children`, the frontmatter and ownership repairs, naming
migration and `links.fix`. Child reparenting uses a closed three-mode target
instead of treating omitted and null `to` values as unrelated operations.

Repair and migration previews consume trusted invocation dry-run state. Their
results retain actionable steps, notes, moves and errors but omit the selected
vault root and managed Python path from the canonical application envelope.
Changed, no-op, planned, partial and unknown outcomes remain distinct.

`links.fix` treats preview/apply as explicit intent within one semantic verb and
allows target filtering only for scoped application. Resolvable, ambiguous and
unresolvable links are bounded typed values; substitutions alone produce a
committed effect.

## v0.54.23 plugin and trigger definition owners

Plugin and trigger definition writes now have five distinct operator commands.
Plugin creation and replacement accept immutable inline or staged content;
replacement requires the current definition SHA-256 and never accepts a
caller-owned filesystem path. Staged bodies are consumed only after commit.

Trigger commands expose condition and target fields directly. Replacement and
deletion can assert the exact current target, while replacement names new
condition/target values separately. The existing `define.py` semantic owner
continues to validate plugin names, trigger targets, router structure and
optimistic preconditions under the application-owned mutation lock.

Results contain definition kind, operation, vault-relative path, semantic
identity and hashes only. The application boundary owns operator authority,
receipt-required retry, dry-run refusal and unknown-outcome classification;
legacy aggregate adapters remain unchanged until coordinated cutover.

## v0.54.24 artefact-type definition owners

`type.create` and `type.replace` now own one paired taxonomy/template bundle.
Their requests carry explicit classification plus independently sourced
definition and template content; a staged handle cannot stand for both
documents. Replacement requires the current hash of each component.

The application layer resolves both inputs before mutation and finalises their
staged handles only after `define.write_definition` returns a committed bundle.
The semantic owner retains its two-file rollback and artefact-folder rules. A
failure whose effect cannot be proven remains receipt-backed outcome-unknown,
even when the local rollback path appears to have restored both files.

The typed result reports both paths and hash transitions, classification,
frontmatter type, status enum and artefact folder. It contains no caller-owned
file path or selected-vault prefix. Public aggregate adapters remain unchanged
until the coordinated breaking cutover.

## v0.54.25 artefact-library install and sync owners

`type.install` and `type.sync` now represent different operator intents.
Installation requires one known uninstalled library type and remains additive;
sync requires one installed type and alone exposes explicit force. Agents use
the read-only `type.status` collection to choose and sequence multiple types.

The application owner holds the selected-Brain mutation lock, overrides the
background sync preference only because invocation itself is explicit intent,
and maps per-role library results into bounded paths and structural outcomes.
The portable `sync_definitions.py` module retains discovery, comparison,
tracking and write authority.

Local customisation is a no-effect conflict unless force is true. The semantic
owner now honours force for local-only drift as well as collision and two-sided
conflict, bringing direct-script behaviour into line with its documented
contract. Public adapters remain unchanged until coordinated cutover.

## v0.54.26 router repair and rebuild owners

`runtime.repair-router` and `runtime.rebuild-router` now express different
operator intent. Repair inspects the derived cache and is a no-op when fresh;
rebuild always recompiles. Both support trusted-context dry-run planning.

One portable router-maintenance module owns cache inspection, compilation,
persistence, semantic-sidecar invalidation and session-mirror refresh. The
packageful repair orchestrator delegates to that module, while `_application`
only projects its structural result and effect policy.

Router persistence followed by session-mirror failure is known partial state.
Other exceptions after mutation entry remain receipt-backed outcome-unknown.
The typed result exposes only relative sidecar identities and bounded state;
public adapters remain unchanged until coordinated cutover.

## v0.54.27 lexical-index repair and rebuild owners

`retrieval.repair-lexical` and `retrieval.rebuild-lexical` separate idempotent
cache repair from unconditional operator intent. Repair returns a typed no-op
for a fresh index; rebuild always constructs and persists the current document
set. Trusted invocation context remains the only source of dry-run state.

One portable lexical-maintenance module owns cache inspection, index
construction, persistence and semantic-sidecar invalidation. The existing
packageful repair orchestrator delegates to that seam. `_application` adds
authority, mutation locking, structural results and receipt-backed outcome
classification without importing adapter or managed-runtime policy.

Unreadable sources and atomic persistence failures are known no-effect
conflicts. A failure after index persistence remains outcome-unknown because
semantic sidecar invalidation may be partial. Results contain only cache reason,
document/term counts and relative sidecar identities; public adapters remain
unchanged until coordinated cutover.

## v0.54.28 workspace-registry repair owner

`workspace.repair-registry` owns repair of the selected Brain's linked-workspace
registry. It is deliberately separate from caller-local workspace binding and
setup commands: those require a caller-filesystem provider and mutate a
different locality, while registry repair is a portable selected-Brain operator
mutation.

One portable seam owns diagnosis, canonical normalisation, malformed-copy
preservation and rollback. When canonical persistence fails after moving a
malformed registry aside, it restores the original before returning a known
no-effect error. Failed restoration is represented as known partial application
and identifies the vault-relative preserved copy.

The application owner adds operator authority, selected-Brain mutation locking,
strict empty input, structural results and receipt policy. It reports only
bounded state and relative paths; existing public adapters remain unchanged
until coordinated cutover.

## v0.54.29 shaping-session start owner

`shaping.start` owns the mechanical opening or continuation of one shaping
session. Its request requires an exact target plus an explicit `brainstorm`,
`refine` or `discover` mode; conversational question selection and completion
decisions remain owned by the shaping skill rather than the command.

The existing portable shaping-session seam remains authoritative for target
resolution, lifecycle transition, same-day transcript identity, transcript
creation/append and source backlinking. The application owner supplies
contributor authority, fresh-router preflight, selected-Brain locking, strict
request decoding and structural effect projection.

Results identify resolved and post-lifecycle target paths, transcript type and
operation, status change and every changed path. Legacy implicit mode and title
fields do not enter the canonical request; public adapters remain unchanged
until coordinated cutover.

## v0.54.30 managed content ingestion owner

`content.ingest` owns the managed classify→resolve→create/update workflow as
one explicit contributor mutation. It accepts immutable inline or staged
content, optional exact type/title hints and a typed classification mode. The
lexical index is required; semantic retrieval remains optional unless explicit
embedding mode requests it.

The command returns typed classification and resolution context for paused
decisions as well as structural create/update effects. Staged content is
consumed only after commit and retained for classification or ambiguity pauses.
Managed tier, authority, locking and receipt policy belong to the application
entry rather than MCP runtime handlers.

During migration, raw BM25 scores were found to be compared against cosine
thresholds, allowing lexical overlap to authorise an unrelated update. BM25 is
now advisory candidate evidence only; exact filename identity or a
high-confidence semantic cosine match is required for automatic update. The
existing public ingest adapter also now honours its declared mode field.

## v0.54.31 managed semantic retrieval owners

`retrieval.enable`, `retrieval.repair-semantic` and
`retrieval.rebuild-semantic` now have distinct typed owners. All three require
the managed tier and explicit `semantic_runtime` provider, carry operator
authority and retain selected-Brain mutation receipts, but they do not collapse
their different intent or failure behaviour into one mode field.

Enable owns opt-in plus provisioning and reports a partial result when the flag
commits before runtime convergence fails. Repair remains health-based and
idempotent. Rebuild is unconditional and forces router, lexical index and
semantic sidecar convergence. The existing configure adapter delegates its
semantic lifecycle work to the extracted owner while public projections remain
unchanged until coordinated cutover.

## v0.54.32 managed document rendering owners

`shaping.render-printable` and `shaping.render-presentation` now have separate
typed contributor owners. Both require the managed `document_renderer`
provider, but printable-only PDF engine and heading controls never leak into
the presentation request, and presentation-only preview control never leaks
into printable rendering.

The owners validate the selected-Brain source before dry-run or execution,
retain the existing Pandoc and Marp implementation seams, and report markdown,
PDF and preview-process effects independently. Missing renderer output after a
new markdown artefact commits is therefore partial; a renderer failure with no
committed output is a no-effect error.

## v0.54.33 managed retrieval benchmark owners

`retrieval.construct-benchmark` and `retrieval.evaluate` now own benchmark
generation and read-only scoring respectively. Both are managed, operator-only
selected-Brain commands, but their catalogue entries explicitly mark MCP as
unsupported while retaining CLI, direct-script and Python eligibility.

Construction restricts fixture, audit and optional seed paths to the selected
Brain, refuses protected system outputs, supports dry-run and records the two
committed files independently. Evaluation validates and reads one Brain-relative
fixture and returns the full report without effects. The split preserves the
mutation/receipt distinction instead of treating both workflows as one command.

## v0.54.34 caller-local workspace mutation owners

`workspace.bind`, `workspace.configure-bootstrap`, `workspace.register`,
`workspace.setup`, `workspace.unregister` and `workspace.update-metadata` now
have distinct typed application owners. All six require contributor authority,
the bootstrap tier and an available `caller_filesystem` provider, and carry
caller-local mutation receipts.

The workspace target is trusted adapter context, not a command argument. This
keeps direct Python, script and eventual CLI projections useful without letting
an untrusted request payload select an arbitrary host path. MCP is explicitly
ineligible because a Brain server cannot safely own the connecting agent's
local filesystem.

The owners retain the existing binding, bootstrap, setup, registry and metadata
semantics, including mutation locking and structural lifecycle steps. Dry-run
returns a no-effect plan; failures after a known workspace-manifest commit are
partial with that effect enumerated. Existing public adapters remain unchanged
until coordinated cutover.

## v0.54.35 machine-global launcher read boundary

The separate machine-global side now has a real stdlib-only invocation boundary
under `cli/_launcher/`, not synthetic selected-Brain executors. Trusted launcher
context carries authority, providers, correlation, receipts, caller directory,
CLI identity and launcher Python. Its structural result and receipt vocabulary
is shape-checked against `brain.command-result/1` without importing
`_application`.

Typed owners now cover `brain.get-default`, `brain.list`, `brain.resolve`,
`brain.version`, `runtime.resolve` and `runtime.resolve-runnable`. They preserve
the existing machine-registry and `_venv` semantic seams, validate nested
outputs, fail authority before execution and map unexpected read failure to a
privacy-bounded internal error. The launcher catalogue now correctly identifies
the runtime owners as managed-runtime resolution rather than Brain-target
resolution. Public CLI dispatch remains unchanged until coordinated cutover.

## v0.54.36 machine-global registry mutation owners

The launcher boundary now owns `brain.register`, `brain.backfill`,
`brain.unregister`, `brain.set-default`, `brain.clear-default` and
`brain.prune` through distinct frozen request types. Each command requires
operator authority, the explicit machine-local caller-filesystem provider and
a receipt because its authoritative state lives outside any selected Brain.

Structured action seams in `vault_registry.py` report change/no-op state while
the established scalar functions retain their public return contracts. The
launcher returns command-specific result shapes and projects registry rows and
the default pointer as separate effects. Dry-run evaluates the same locked
resolution and conflict checks while suppressing writes.
If unregister or prune persists row removal before default cleanup fails, the
result is known partial with exact committed row identities; other unexpected
post-entry failures stay non-retryable and outcome-unknown. Dry-run remains an
explicit no-effect plan. Public CLI dispatch remains unchanged until the
coordinated cutover.

## v0.54.37 effect-free launcher diagnosis and key generation

`brain.doctor` and `operator.generate-key` complete the launcher catalogue's
effect-free owner group. Doctor receives CLI binary/version and launcher Python
as trusted context, accepts only optional vault scope and filtering as request
intent, and returns bounded typed CLI, registry, runtime, Brain and vault
diagnosis. Repair guidance is canonical command identity rather than an
embedded shell command.

The existing Doctor composition synchronises derived `brains.json` as part of
the unreleased old adapter flow, which conflicts with the canonical command's
no-effect contract. The new owner instead calls a read-only
`inspect_machine_registry` path and reports drift. Existing public behaviour is
not switched during internal migration; coordinated cutover removes the
implicit repair behaviour rather than misclassifying it or weakening the
canonical result contract.

Operator-key generation returns a bounded tuple of typed key/SHA-256 candidates
over the existing cryptographic generator. It retains operator authority but
requires no selected Brain, provider or receipt because it produces no stored
effect.

## v0.54.38 agent-skill launcher configuration owner

`agent-skill.configure` is the first machine-global configuration mutation to
move behind the launcher boundary. Its frozen request contains only closed
client selection, configure/remove intent and the configure-only replacement
policy. The home directory is trusted adapter context rather than semantic
caller input, preserving the distinction between command intent and authority
over a local filesystem root.

The existing `_bootstrap/agent_skills.py` semantic seam now supports a genuine
read-only plan. Planning evaluates symlink refusal, ownership markers, content
hashes, unmanaged conflicts, adoption, update, removal and the deterministic
backup destination without creating directories. The launcher resolves every
selected client through this path before applying any write, preventing a
known conflict in a later client from causing an avoidable earlier change.

Committed results identify each changed client adapter and any archived backup
separately. If the filesystem changes between planning and application, or an
I/O failure occurs after mutation entry, the invocation boundary records a
non-retryable unknown outcome rather than asserting no effects. Existing public
configuration behaviour and output remain in place until the coordinated
breaking projection cutover.

## v0.54.39 orphan-runtime pruning launcher owner

`machine.prune-runtimes` now has a frozen empty request and bounded typed target
results. Its source/current Brain is trusted launcher context rather than
semantic request data. This is required for safety: a current but unregistered
Brain must still count as selecting its managed runtime during orphan
classification.

The owner invokes machine discovery with `synchronise_registry=False`, keeping
derived `brains.json` reconciliation out of the pruning effect set. Pruning is
blocked with a known no-effect conflict when the live-process scan is
unavailable, is a real no-write plan under dry-run and records each removed
runtime directory as a separate committed effect.

Recursive removal is not atomic. Any deletion error may mean a target directory
was partly removed, so the launcher returns a receipt-backed, non-retryable
unknown outcome rather than projecting the legacy per-target error as proven
no-effect. The existing public machine adapter remains unchanged until the
coordinated cutover.

## v0.54.40 legacy Brain migration launcher owner

`machine.migrate-legacy` now has one frozen request with an optional typed Brain
ID or absolute-path selector and bounded typed target/step results. Machine
discovery receives the current Brain only through trusted launcher context and
uses a read-only derived-registry comparison, so selecting migration does not
silently reconcile machine registry state.

The owner composes the existing target-Brain runtime, MCP and registry repair
processes, then removes the legacy vault-local runtime only after those repairs
and live-process detection succeed. Child process spawn failures prove no
effect; timeouts, invalid child output and recursive-removal failures preserve
a receipt-backed unknown outcome. A child-reported partial result becomes a
known partial result with its affected repair scope receipted. The existing
public machine adapter remains unchanged until coordinated cutover.

## v0.54.41 managed-runtime repair launcher owner

`runtime.repair` now has a frozen empty request and acts on the Brain selected
through trusted launcher context. Its bootstrap-tier owner provisions or
synchronises the shared managed runtime directly through the existing runtime
orchestrator; it does not silently hand off into the managed interpreter.

The lower orchestrator now reports no-effect, committed, known-partial or
unknown effect state. Missing/unreadable requirements and an unusable existing
runtime fail before writes; the owner refuses to replace a possibly live
runtime without a live-use proof. Successful creation/synchronisation records
one managed-runtime scope effect. A successful dependency mutation followed by
verification or sentinel failure is known partial, while interrupted venv/pip
mutation remains receipt-backed unknown. The existing public repair adapter
remains unchanged until coordinated cutover.

## v0.54.42 transactional MCP configuration launcher owners

`mcp.configure` and `mcp.repair` now have frozen typed launcher requests and a
shared bounded result over concrete clients and file effects. Configuration
selects the Brain, caller directory and home directory only from trusted
launcher context. It refuses a missing or unhealthy managed runtime with an
explicit `runtime.repair` next action; it never provisions or hands off to one.

The bootstrap file-transaction seam reads and validates every affected JSON,
TOML, Markdown and init-state file before its first write. It then applies
deterministic direct edits without invoking an external client CLI. A failure
restores every written file and transaction-created directory; any failed
rollback is a known-partial result naming each surviving path. Concurrent
changes detected between plan and apply fail with proven no effect.

The new owner does not converge workspace bindings or ignore rules because
those belong to separate workspace commands. Configure and repair require a
healthy matching workspace binding, except for vault-self and user scope.
Recorded removal is intentionally close-safe: it needs neither a healthy
runtime nor binding and removes only an exactly matching Brain-owned entry.
The existing public v1 MCP/configure/repair adapters remain unchanged until the
coordinated breaking projection cutover.

## v0.54.43 Brain lifecycle launcher owners

`brain.install`, `brain.uninstall` and `brain.upgrade` now have distinct frozen
launcher requests and bounded structural results. The trusted launcher context
owns the complete distribution root used for install/upgrade; callers cannot
substitute an arbitrary source tree. Install requires an explicit canonical
Brain ID so dry-run and apply share one predictable registry identity.

Install planning uses an unlocked read-only registry preview and creates no
lock, directory or destination state. Apply preserves the current composite
installer workflow while projecting completed steps and any later error as a
known partial result. This is the one explicit lifecycle command that owns the
initial runtime, resolution, registry, ignore and MCP composition.

Uninstall targets only the selected Brain, rejects symlinked or malformed
system roots before mutation, removes exact recorded project/local MCP state,
unregisters the Brain, then recursively removes only `.brain-core/`, `.brain/`
and legacy `.venv/`. Notes, shared runtimes, user-scope MCP and the global CLI
remain outside its deletion set. A recursive deletion failure remains
non-retryable and outcome-unknown.

Upgrade loads the upgrader from the trusted about-to-install distribution,
projects closed definition/dependency sync policies and atomically refreshes
the known running CLI path while preserving its executable mode. The existing
upgrader's unverified rollback error remains outcome-unknown; Phase 6 still owns
the checked multi-Brain breaking-cutover and rollback protocol. Public v1
adapters remain unchanged until that coordinated cutover.

## v0.54.44 mechanical application projection

`_application.projection` is the single adapter-neutral owner of mechanical
application command names, strict request schemas and canonical result wire
envelopes. Canonical `noun.verb` identity maps to `brain_<noun>_<verb>` and
`<noun> <verb>` without aliases; dynamic reverse resolution is always bounded
by the owning catalogue so underscore/hyphen ambiguity cannot invent commands.

Request fields, defaults and nested types derive from the sealed request
dataclass. The projected schema recursively describes properties, forbids
additional object fields and excludes command identity/version from semantic
input. Request-owned descriptions may refine the compact mechanical fallback
without changing field authority.

All future MCP, CLI and direct-script adapters serialise through the same
structural result projection. It preserves `ok`, known `partial`, and `error`
branches, warnings, committed effects, retry classification and unknown-outcome
references. This seam remains internal until the coordinated public cutover.

## v0.54.45 authoritative selected-Brain discovery

`command.list` v2 now returns bounded `CommandSummary` values rather than bare
identifiers. Each summary retains application ownership, command/version,
summary, all projection eligibility, tier, locality, authority, effect/retry
class, provider bindings, snapshot-derived availability and lifecycle guidance.
An application discovery request for launcher ownership returns no entries;
only the local CLI may compose the independent launcher catalogue.

Default listing maps one already-composed capability snapshot and performs no
provider probe. Explicit refresh calls one bounded refresher with the distinct
provider set. Its small retained snapshot window gives each continuation cursor
the same token and availability observation; expired tokens fail explicitly.

`command.describe` v2 derives strict request and result-payload schemas,
structural result branches, stable error/warning vocabularies, safety/provider
requirements, projection names and a minimal JSON request from the owning
catalogue and sealed request type. Every example is executed through the real
dynamic resolver in tests. This exposed and fixed an omitted `skill.search`
resolver registration and aligned `invocation.read` v2 on scalar
`invocation_id`, matching its canonical recovery action.

## v0.54.46 shared dynamic adapter boundary

`_application.adapter.ApplicationAdapter` is the one dynamic seam shared by
future MCP, CLI and direct-script projections. At construction it requires an
exact command/version/request-type match between the authoritative catalogue
and resolver. At invocation it converts one mapping to a sealed request and
calls `CommandApplication` with already-composed trusted context. Unknown
identity, malformed payloads and caller-supplied command metadata fail before
executor entry.

The same boundary projects the returned typed result into canonical structured
content, compact JSON and one concise line without changing its branch. `ok`
maps to exit `0`, `partial` to `1`, request/domain errors to `2`, authority or
capability unavailability to `3`, and infrastructure/unknown outcome to `4`;
native parser failure also uses `2`. MCP error state is true for both `partial`
and `error`. Concrete adapters retain only context composition, transport
registration and stdout/stderr presentation responsibilities.

## v0.54.47 trusted local composition and durable receipts

`scripts/_command_interface/` is the first concrete local adapter package. It
depends inward on `_application` and accepts only values already resolved by a
trusted adapter: selected Brain, authenticated profile and granular allow-list,
dependency tier, provider/capability snapshot, workspace and invocation IDs.
The application package does not import it. Profile authority is evaluated
against the mechanically projected granular MCP name, with no aggregate-name
or legacy fallback.

The same package supplies the durable selected-Brain outcome store required by
direct processes and later proxy recovery. It writes only effect-bearing
receipts beneath `.brain/local/command-outcomes`, hashes caller-visible
invocation IDs into fixed filenames and retains only command identity, outcome
state, time and committed-effect references. A cross-process lock preserves
immutability and bounded cleanup. Symlinked directories, non-regular records,
unknown schemas and malformed values fail closed; request bodies, credentials
and provider values never enter the receipt model.

## v0.54.48 direct selected-Brain projection

`scripts/command.py <noun> <verb>` is the first concrete application adapter.
It resolves only direct-script-eligible mechanical identities, accepts one
strict JSON request object inline or from stdin, and passes the resulting map
through the shared resolver/application adapter. JSON mode writes only the
canonical envelope to stdout; human success uses stdout, human partial/error
uses stderr, and exit categories remain 0–4. Native parsing and resolution
failures occur before executor entry.

The direct adapter resolves the selected Brain, authenticated profile, caller
workspace, current dependency tier and fresh invocation IDs without runtime
handoff or provisioning. It assembles catalogue and resolver once. Default
`command.list` receives an unknown/known-local snapshot without probing any
provider; explicit refresh uses the bounded refresher once per distinct
catalogue provider. Capability-bound commands probe only their declared
providers. Config remains independent of the application catalogue: the
concrete adapter supplies its projected names as additional validation facts.

This script is shipped as implementation scaffolding but is not yet dispatched
by the global CLI or registered through MCP. Public legacy surfaces remain
unchanged until the coordinated fail-closed cutover.

## v0.54.49 granular MCP projection

`brain_mcp._command_adapter` derives each MCP-eligible tool directly from the
authoritative application catalogue. Mechanical `brain_<noun>_<verb>` names,
summaries and flat request schemas therefore cannot drift into a parallel MCP
registry. The installed canonical schema remains authoritative after FastMCP
builds its invocation model; this preserves recursive descriptions and the
documented string-valued dependency-tier vocabulary.

FastMCP normally applies a second Pydantic request model before dispatch. That
model ignores unknown fields by default, applies Python defaults to omitted
fields and cannot faithfully accept canonical wire discriminators represented
by non-init dataclass fields. The granular adapter therefore retains FastMCP's
registration and result conversion but routes the raw argument map directly to
the canonical resolver. This preserves strict unknown-field rejection and the
difference between absence and explicit null without creating another request
contract or moving transport behaviour into semantic request owners.

MCP results reuse the shared structured envelope, concise text and error-state
projection. The real-FastMCP contract gate compares every registered schema
with its canonical source and passes every minimal request through transport
validation under no authority, so projection is tested without executing
effects. Registration remains staged until profiles and all public adapters
move together at the breaking cutover.
