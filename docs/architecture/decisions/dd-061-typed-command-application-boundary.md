# DD-061: Typed selected-Brain command application boundary

**Status:** Implemented (v0.54.1; extended v0.54.2–v0.54.11)
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
