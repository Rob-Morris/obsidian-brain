# DD-061: Typed selected-Brain command application boundary

**Status:** Implemented (v0.54.1; extended v0.54.2–v0.54.20)
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
