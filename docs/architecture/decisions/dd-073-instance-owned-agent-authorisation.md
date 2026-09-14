# DD-073: Instance-owned agent authorisation

**Status:** Implemented (v0.68.0)
**Supersedes:** DD-062 (v0.68.0 coordinated runtime cutover)
**Extends:** DD-033, DD-061, DD-064, DD-067

## Context

DD-062 separates authenticated permission ceilings from active access, but its
principal-scoped timed leases create repeated failed calls and can share consent
between unrelated agents. Bootstrap also describes the ceiling as an active
profile. Under automatic policy, expiry usually adds another request rather than
a meaningful human decision. Audit remains useful independently of that denial.

The same application serves MCP and separate CLI invocations. The host-connected
proxy has a longer lifetime than its restartable child server. Host tool policy
can independently allow, prompt for or deny a dedicated consent tool; MCP does
not attest that a human approved a particular forwarded call.

## Decision

### Three separate concepts

Permissions define the credential's maximum in the selected Brain. Initial
authorisation and explicit exceptional consent permit use within that maximum.
Durable audit records attributed actions without acting as a grant store.

Effective states are `authorised`, `authorisation_required` and `denied`.
Authorisation never increases permissions. Enduring permission administration is
authenticated administrator CLI-only work, with target identity, before/after
preview, expected configuration revision and atomic audited update. Locality does
not grant administrative authority. No new remote administration transport or
MCP escalation command is introduced.

### Useful defaults and deliberate exceptions

Normal content operations start authorised within credential permissions,
including editable artefacts, memories, skills, templates and styles, archive and
restore, local type/trigger/plugin authoring, router/lexical refresh and skill
detachment. Permanent deletion, repository skill imports/updates, packaged type
sync, bulk retrieval/runtime operations and local workspace mutations require
consent by default. Workspace hub artefact authoring remains normal content work.

The application catalogue declares each command's initial class explicitly;
authority rank, effect class and name prefixes are not substitutes. Configuration
selects `normal`, `read-only` or an explicit initial set, with exact-command
overrides, always intersected with permissions and policy. Explicit sets and maps
replace at their configuration boundary instead of using additive list merge.

The accepted consent-required default set is exact: `artefact.delete`,
`skill.add-git`, `skill.update`, `type.sync`, `retrieval.construct-benchmark`,
`retrieval.evaluate`, `retrieval.rebuild-semantic`, `retrieval.repair-semantic`,
`retrieval.enable`, `workspace.bind`, `workspace.configure-bootstrap`,
`workspace.register`, `workspace.unregister`, `workspace.setup`,
`workspace.update-metadata` and `workspace.repair-registry`. In particular,
`retrieval.evaluate` requires consent despite having no domain write effect.
The implementation catalogue is the executable owner of this mapping; its
coverage tests must also classify commands made exceptional by configuration.

### Context ownership

Specific consent covers one immutable prepared operation. Explicit blanket
consent covers one exact command throughout the selected Brain and owning
context. Both bind Brain identity/root, authenticated principal, private owner
context, permission/policy generation and command/interface contract version.

The stdio MCP proxy owns an in-memory context. A compatible child restart keeps
it; a new proxy, shutdown or crash loses it. An explicit `brain session run --
<program>` supervisor supplies the equivalent CLI job lifetime. Starting either
context grants nothing exceptional. Root job exit closes new admissions even
when background descendants survive. Already admitted effects need not roll back.

Private inherited channels carry owner access to trusted children. Public IDs
are selectors, never capabilities. Brain launchers explicitly preserve handles;
descriptor-closing runtimes require documented forwarding. A missing channel
fails with an actionable context remedy, never a principal-wide fallback.
There is no machine-wide daemon, durable grant bearer, timer, renewal or
cross-instance carryover. A shared host MCP connection is a shared context;
per-agent isolation requires separate owners and must be verified on real hosts.

The application owns policy, matching, reservation and revocation. A small
atomic owner-store port serves the two concrete process owners without moving
policy into the proxy. Same-user filesystem/process control is outside this
command-surface isolation claim.

### Explicit preparation and consent

`access.prepare` checks target permission before reading resources, canonicalises
typed arguments/defaults, and pins effect-significant targets, revisions,
recursive affected sets, staged content digests, replacement intent and resolved
repository commits. It has no target effects; bounded remote reads/private cache
may resolve immutable source input without executing repository code or hooks.

It returns an owned operation ID, digest and compact canonical review. Full
details paginate. `access.request` is the sole granting tool: a specific request
echoes ID, digest and review; blanket scope names one exact command and its
whole-Brain/current-context extent. Brain validates the presented scope and
current permissions. It never interprets a received call as proof of a human
click or silently requests consent after a denial.

An optional reserved `brain_operation` tool input selects a specific operation;
the adapter strips it into an untrusted invocation option before business
decoding. CLI exposes `--operation`. Catalogue validation rejects a field-name
collision. Without a selector, only initial/blanket authorisation applies. MCP
clients need no model-authored `_meta` support.

### Admission, revocation and recovery

Under the actual operation/precondition guard, the application revalidates current
authority and preparation, then atomically reserves specific consent immediately
before entering the approved operation. Successful execution consumes it even
for an observation. Only an attempt proven not to have entered the operation can
release it; a generic effects-none wrapper result alone is not that proof.
Partial or unknown execution consumes it; missing receipts are unknown, not
replay permission. MCP/job receipts bind the trusted Brain/principal/context.
Ordinary standalone CLI receipts use a separate Brain/principal namespace, read
by a subsequent standalone invocation with current receipt-read permission;
that route cannot read MCP/job receipts or restore consent. Repeated consent
request identities recover their original outcome and cannot replenish spent or
revoked grants. Incompatible command changes invalidate affected grants even
when the proxy survives a child upgrade.

Permission/policy changes permanently invalidate earlier grants; restoring
permissions cannot resurrect them. `access.reduce` revokes grants, discards owned
preparation or narrows this context's initial access. It cannot increase access.
Authenticated session controls remain narrowly scoped and usable without the
target authorisation they are designed to request.

Audit persists attributed intent before consent-controlled operation entry in a
record separate from the immutable final outcome; failure prevents admission.
The receipt ledger atomically claims each invocation ID. Only a newly published
intent permits entry; an existing identical or conflicting intent directs the
caller to owned outcome inspection and cannot authorise another execution.
Initial, blanket, specific and mutating-control calls share this requirement.
Duplicate identical completion is idempotent, incompatible completion conflicts,
and surviving intent without completion is unknown. Completion-record failure produces an unknown
outcome and recovery guidance. Audit never stores capabilities, keys, full bodies
or invented human attribution. Historical audit and bounded receipt retention
are distinct from ephemeral authorisation.

Bound owner metadata to 128 prepared operations, 128 grants, 4,096 request
identities and 4 MiB aggregate descriptors/reviews/tombstones. Refuse new requests
at capacity; do not evict live grants or replay protection or introduce timed
expiry to reclaim them. Immutable staged bodies use existing storage quotas.
Prepared inputs are owner-restricted and pinned against routine staging expiry
until explicit disposal or owner end. Preparation inspection does not delete
expired ordinary stages as a side effect.

### Discovery and cutover

`command.list` is a compact paged summary; `command.describe` supplies one full
schema. Static shipped above-maximum metadata remains discoverable without
revealing dynamic Brain/principal values or enabling execution. Denials name the
actual boundary and remedy: consent within permissions, administration above
them, preparation for stale input, receipt recovery for unknown effects.

Bootstrap distinguishes ceiling from initial authorisation, names the consent
tools and lifetime, and sends dynamic grants to paged `access.status`. Budget
the access section at 768 compact UTF-8 bytes, the complete model-facing
bootstrap envelope below 16,000 bytes and review/request below 8,000 bytes.
Measure the duplicated MCP wire representation separately. Preserve structured
results and model-visible JSON text; audience annotations remain display hints.

Existing Brains inherit normal defaults only where no initial-access override
was stored. Translate explicit overrides using their previous effective meaning,
including explicit reader starts. Never increase credential maxima or silently
add controls to restrictive custom credentials. Ambiguous/custom-control conflicts
need a reviewable migration remedy. Old external-approver policy must not silently
become host auto-approval. Historical leases and pending requests never become
grants in new contexts; remove the timed and second-approval runtime at cutover.

Core, proxy and CLI negotiate the replacement contract together. Intermediate
foundation commits must not expose an unenforced or partially working new public
surface. Real upgrade/rollback and installed Claude/Codex/Grok tests are release
gates, not conclusions drawn from mocks or documentation.

Preparation coverage must include indirect affected resources and trusted
context inputs, not only explicit arguments. Domain owners build and validate
immutable plans at their existing admission guards: backlink write sets,
recursive descendants, workspace identity and skill materialisation are part
of those plans. Do not wrap every executor in another non-reentrant vault lock
or assume every executor's dry-run is a complete non-mutating planner.

Prove concurrent CLI channel routing in a bounded process spike before building
the adapter: a shared bidirectional stream must not deliver a sibling's reply
to the wrong child. Frame bounds, disconnect handling and root termination are
part of that concrete transport contract.

## Alternatives Considered

- **Keep optional timers:** rejected for this baseline. Scope, lifetime and
  explicit revocation express the selected consent boundary without renewal.
- **Automatically request on first use:** rejected. It hides the distinct tool
  boundary and creates surprising behaviour when host policy changes.
- **Persist by principal or conversation:** rejected. It transfers exceptional
  consent across independent contexts. Future carryover needs a concrete case.
- **Resource-filtered blanket grants:** deferred. Brain work commonly spans
  resource kinds; one exact command and target Brain is the agreed baseline.
- **A second human-proof protocol:** rejected. Host tool policy is the selected
  boundary; forwarded tool calls do not provide portable approval attestation.

## Consequences

- Routine work avoids a failed write and lease request; exceptional operation
  preparation adds deliberate review only where consent is needed.
- Process ownership and atomic admission add necessary lifecycle coordination,
  while removing timer/renewal/pending-approval machinery and policy duplication.
- Static discovery improves agent recovery without enlarging authority.
- Custom migration conflicts remain explicit; older clients must reconnect to
  the coordinated contract instead of silently using obsolete lease semantics.
- Cross-instance carryover, new remote administration and longer-term audit
  export remain independent follow-ups. Workspace defaults can be revisited
  after experience without changing the ownership model.
