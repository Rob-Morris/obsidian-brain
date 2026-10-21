# Direct Script and Python Command Interfaces

`configure.py approvals` delegates to the [managed approvals](approvals.md) machine
owner. Native install accepts separate `--approval-client`, `--approval-scope` and
`--approvals` selections. With existing opt-ins, direct install, upgrade and
registry mutations use the same compatible machine owners; a missing writer is an
error before mutation, not a fallback to legacy writes.

## Managed dependency lifecycle

The shared stdlib runtime owner consumes `.brain-core/brain_mcp/requirements.txt`
and the complete optional `requirements-semantic.txt`. Both exports determine
runtime identity and upgrade dependency-change detection. Installation uses
`pip --no-deps -r`; fresh install, dependency-syncing upgrade, semantic provision
and explicit `runtime.repair` verify all applicable package versions plus
`pip check` before recording versioned readiness. Healthy handoff uses matching
readiness without a package audit. Dependency-sync opt-outs remain explicit;
copying core files does not prove dependency convergence. Contributor generation
and certification commands are documented in the
[dependency workflow](../contributor/dependencies.md).

The selected Brain owns one public direct command projection:

```text
.brain-core/scripts/command.py <noun> <verb> \
  --request-json JSON|- \
  [--vault PATH] [--workspace PATH] [--operator-key KEY] \
  [--result-session-file PATH] [--dry-run] [--json]
```

It resolves the exact command from the installed application catalogue, decodes the same sealed request used by MCP and CLI, composes trusted selected-Brain context, invokes the canonical application owner and projects `brain.command-result/1`.

### Result delivery and private context

`command.py` accepts `--result-session-file PATH` for selected-Brain application
commands. Create it with `session start`, then reuse it for source and result
calls; the parent must exist. Issuance writes a private owned regular credential
file atomically, without replacing an existing file. Symlinks, non-private
files and invalid credentials are refused. The credential grants neither
permissions nor consent and is not a domain request field. JSON reports
`result_session_file`; raw credentials do not belong in request bodies or logs.
Without the flag, ordinary standalone scripts create and report a new private client
file; reuse that path explicitly on later calls. Managed jobs inherit one
private result context through their owner channel and reject conflicting
explicit credentials. Without explicit result-session selection,
`runtime.status` and `runtime.read-environment` skip retention and result-cache
write attempts; an existing `--result-session-file` opts them into retention.

Consume generic `delivery.continuation` with `result read` before inspecting
any semantic payload or following its legacy cursor. Pass the same credential
file, `result_id` and returned `continuation` in request JSON. The typed
`ResultReadPayload.envelope` (`result.envelope` on the wire) contains the original
command page, while the outer envelope carries delivery. Finish all generic
pages before source cursor continuation. Bootstrap guidance is mandatory in
full, through `bootstrap_complete`; no truncation or omission is permitted.

`result read` takes `result_id`, `page`, `continuation`, `page_size`, `max_pages`,
`detail` and `anchor`; `result search` takes `result_id`, `literal`, `start_page`,
`start_offset`, `limit` and `scan_pages`. Resume search with both `next_page` and
`next_offset` until `scan_complete`. Coverage excludes envelope metadata,
contextual extras and referenced detail bodies; `complete` can remain false
when `details_skipped` reports unsearched references.
These are the same typed requests as MCP/CLI, not a
command execution facade. See the [field contracts and limits](mcp-tools.md#result-sessions-and-delivery).
Each call emits one bounded page, including get-all (`page: 0`). Query selections
default to 10 pages across calls, retained output to no page-count cap; outside
selection counts and `outside_continuation` expose further output. Dynamic
cursor-free active lists refresh current projection pages with per-page notices;
archived/all lists retain the opening snapshot with an explicit freshness
limitation, preserving page size and the ten-page list selection;
retained output and literal search do not replay source execution.

For targeted retention-capacity recovery, invoke
`command.py result clear --result-session-file PATH --json` with no request fields. It clears only the
verified session's retained output and keeps the credential. `result.clear`
is also available through CLI and typed Python with an authenticated, composed
`ResultDeliveryPort`; constructing a request alone does not select a namespace.
Result navigation and cleanup never issue a fresh namespace: supply an existing
private file, or use a managed job's namespace already opened by `session.start`.
MCP uses `result_clear` with the existing `brain_session` credential, without
transferring it into a CLI file. Cleanup deletes only the authenticated namespace's
output, keeping source files, independent receipts and other owners untouched.
Retention keeps the latest invocation per command per
session per Brain without a TTL; unrelated commands do not replace it. Capacity is a storage
boundary, not a page-count cap. Never rerun a mutation to recover output after
clearing or failed delivery. Source success and committed effects remain true;
uncertain execution uses `invocation.read` recovery.

Shared page layout is byte-aware. MCP final guards enforce 8,000 canonical
UTF-8 bytes and 32,768 encoded tool-result bytes, excluding JSON-RPC correlation;
the script printer also checks final session-file metadata against those limits
and bounds concise output to 32,768 bytes. Retention credentials
recover only the output namespace. Current disclosure checks and the original
consent owner's exceptional-output entitlement still apply; a resumed namespace
cannot renew consent or authorise a fresh source query.

### Retired or lost result namespaces

Result storage uses `.brain/local/results/<binding-hash>/<owner-digest>/`.
The binding includes the absolute Brain root, device/inode and Brain identity.
Moving/copying a Brain, replacing its root or registering it under a new Brain
identity creates a fresh binding namespace. Old credentials cannot open the new
binding, and old output is neither adopted nor rewritten or deleted. The shared
parent lock serialises writers across bindings; the default 4 GiB aggregate
ceiling includes retired bindings, legacy flat-layout data and unfinished files.
Creating a new binding or result session does not reset that accounting.

Use `result.clear` with the existing authenticated session for ordinary cleanup.
If its credential is lost or its binding is retired, that command cannot select
those bytes. A local administrator may explicitly authorise removal of the
identified retired binding directory, or an identified lost owner directory
within a current binding. This is deliberate deletion of local derived result
storage, requiring confirmation of the exact scope and loss of retained output;
it is not a credential-recovery procedure.

Stop all Brain MCP processes, CLI jobs and embedded Python writers using this
vault before manual cleanup. Identify the exact binding/owner directory using
its location and the binding descriptor's non-secret root/identity information.
Legacy flat-layout owner directories need the same explicit identification and
authorisation.

**Before deleting any bytes**, durably mark the shared results parent's
`.result-accounting` file dirty. It is
`.brain/local/results/.result-accounting`, not a marker inside a binding child.
The marker must be a regular, single-link file, never a symlink, with mode 0600.
Open/create it without truncation, write `b'1'` at offset zero, truncate it to
exactly one byte, flush and fsync the file, then fsync the results parent. For
example, on Unix-like systems, substitute the exact authorised absolute path:

```python
import os
from pathlib import Path
import stat

results = Path("/absolute/path/to/Brain/.brain/local/results")
marker = results / ".result-accounting"
fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("Unsafe result accounting marker")
    os.fchmod(fd, 0o600)
    os.lseek(fd, 0, os.SEEK_SET)
    if os.write(fd, b"1") != 1:
        raise RuntimeError("Incomplete dirty-marker write")
    os.ftruncate(fd, 1)
    os.fsync(fd)  # os.write is unbuffered.
finally:
    os.close(fd)
parent_fd = os.open(results, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
```

Only after that succeeds, remove the explicitly authorised binding or owner
subtree using local filesystem administration, without following symlinks.
Fsync each surviving parent whose directory entries changed so deletion is
durable. Leave the root marker at `b'1'`: do not edit counters or reset the marker
to clean. The next store transaction, under the shared parent lock, reconciles
all remaining sibling, legacy and staging bytes and writes clean counters.
Keep both the root marker and `.lock` in place, and permit no concurrent manual
or store operations during this procedure.

Do not edit credential files, owner records or binding descriptors to recover
access. Retired/lost namespaces are never automatically quarantined, evicted or
excluded from quotas. Deleting them permanently loses their output and
corresponding resume ability, while leaving source files and independent outcome
receipts unchanged. Never rerun a mutation to recover deleted output. After
cleanup, start a fresh session for the current binding.

Examples:

```bash
python3 .brain-core/scripts/command.py artefact read \
  --request-json '{"reference":"Designs/Example.md"}' --json

printf '%s' '{"query":"command architecture"}' | \
  python3 .brain-core/scripts/command.py artefact search \
    --request-json - --json

python3 .brain-core/scripts/command.py command describe \
  --request-json '{"target_command_id":"artefact.create"}' --json
```

## Relationship and history projections

Use the shared direct entry point:

```bash
python3 .brain-core/scripts/command.py command describe \
  --request-json '{"target_command_id":"relationship.batch"}' --json
python3 .brain-core/scripts/command.py relationship check \
  --request-json '{"work_budget":5000}' --json
python3 .brain-core/scripts/command.py history policy --request-json '{}' --json
```

The current families are portable, selected-Brain operations without external
providers. Python uses the exported relationship/history request types from
`brain_application.requests` and construction values from
`brain_application.values`, including `Endpoint`, `NodeKind`, `BatchItem`,
`Disposition`, `AssessmentPolicy` and `TranscriptPolicy`. Internal `_relationships`
modules implement records, projection, validation and publication; they are not
public script commands. See the
[semantic relationship reference](../../src/brain-core/standards/semantic-relationships.md)
for the shared behaviour and supported repair sequence.

## Contract parity

MCP, CLI, direct script and typed Python share:

- the command ID and command version owned by the request type;
- strict required, optional, nullable and enum semantics;
- authority, tier, locality, provider, effect and retry classification;
- the same executor and structural result;
- stable error codes and exit categories;
- durable receipt handling for effect-bearing outcomes.

Adapter-only concerns remain outside the semantic request. `--vault`, `--workspace`, `--operator-key`, `--dry-run`, process rendering and invocation identity are trusted composition inputs, not command fields.

For artefact creation, `shaping.start` v2, the existing semantic lifecycle owners and the four
document mutation owners, semantic
`workspace_context` in request JSON accepts `workspace/{key}` or `global`, never
a filesystem path. Document targets other than artefacts reject this selector.
The typed Python construction value is `brain_application.values.WorkspaceSelector`;
CLI, script, Python and MCP share its strict schema and resolver. See the
[workspace mutation contract](cli.md#effective-workspace-context-for-content-mutations)
for parent/tag precedence and preparation freshness. `artefact.set-workspace`
explicitly reassigns the complete owned subtree using that context as its
destination; `global` clears membership only for this dedicated transition.
Shaping creates transcripts with selected membership, default parent and tags;
continuation preserves existing membership/parent and restores configured tags
on the source and transcript. The linked contract above covers its composite
preparation and partial-result behaviour.

The direct projection honours the same credential permissions and configured initial authorisation as MCP. A managed CLI job provides one private lifetime for `access.prepare`, `access.request` and `access.reduce`; standalone calls retain initial authorisation and their own principal-scoped receipts. Permission administration belongs to the CLI-only `permission.set-profile` launcher and is absent from this script and the typed application catalogue.

Frontmatter transport fields are JSON objects; typed Python constructors retain immutable field tuples. Artefact selectors resolve short or qualified singular/plural names through the configured taxonomy and return its canonical frontmatter type. MCP projects the dotted command ID to `noun_verb`; direct scripts keep noun/verb arguments.

## Exit categories

| Exit | Meaning |
|---:|---|
| 0 | `ok` |
| 1 | known `partial` |
| 2 | usage, invalid request or domain conflict |
| 3 | authority, dependency or provider unavailable |
| 4 | infrastructure failure or unknown outcome |

Human output and JSON output are projections of the same result. Diagnostics never replace the structural envelope in JSON mode. Unexpected failures produce bounded public stderr without a traceback or raw exception detail. Once trusted context exists, its diagnostic sink receives the full failure with command and correlation metadata.

## Typed Python

Python consumers import the supported kernel from `brain_application`, construct sealed request types from `brain_application.requests`, and obtain their construction values from `brain_application.values` or a narrow domain module such as `brain_application.documents`. Trusted context contracts, including dependency and availability enums and receipt ports, are exported by `brain_application.context`. Invoke requests through `CommandApplication(context).invoke(request)`. Importing the kernel does not load command owners; importing the all-requests or all-values modules is an explicit opt-in to every dependency tier. The internal `_application` tree owns execution and registration and is not a supported integration surface. Dynamic infrastructure consumers resolve through the catalogue-bound `ApplicationAdapter`; free command strings are permitted only at that explicit adapter boundary.

Generic delivery envelopes, response byte limits and automatic output retention
belong to the serialised MCP/CLI/direct-script adapter boundaries. Direct
`CommandApplication(context).invoke(request)` returns the complete semantic
`Ok`/`Partial`/`Error` value without generic paging or automatic result retention,
even when a delivery port is present on the context. Existing command-owned
source windows and cursors still apply. Supplying a port alone does not perform
the adapter's result admission and publication lifecycle.

Typed `result.read`, `result.search` and `result.clear` requests require a context
with an authenticated, composed result-delivery port for the selected namespace.
Trusted integrations can implement the `ResultDeliveryPort` contract exported by
`brain_application.context`; the protocol grants neither namespace proof nor
execution authority. The transport-neutral kernel and typed domain schemas
remain independent of the concrete file store.

Custom ports may raise `brain_application.results.DeliveryFailure(reason, message)`
from `begin` or `finish` for known delivery failures. Reasons must be stable and
messages bounded and content-safe: never include credentials, private paths or
raw backend diagnostics. A `begin` failure prevents source execution; a `finish`
failure preserves source status and effects without retrying the source.
For `capacity_exceeded`, optional `capacity_limits` contains exactly
`result_bytes`, `owner_bytes` and `brain_bytes`: positive integers, excluding
booleans, no greater than `2**63 - 1`. Final writers validate these primitives
and reconstruct fixed recovery guidance. Representation limits instead direct
readers to retained `@outcome` recovery, without advising quota cleanup.
If disclosure authority ends or cannot be verified, `disclosure_ended` withholds
business output and effect subjects while preserving execution status, effect
certainty and receipt references; it does not report an empty effects list.

The local delivery port accepts one active source invocation at a time, binding
its opening disclosure proof to the invocation ID, command and admission ticket.
Concurrent reuse is refused before source effects without replacing that proof;
use separate composed ports for concurrent calls. Sequential reuse remains
supported. Nested query refreshes do not replace an active source's proof.

The trusted `InvocationContext` contains selected-Brain identity, current permission and authorisation ports, dependency tier, capability snapshot, providers, invocation/correlation identity and owned outcome receipts. Executors do not rediscover these facts from environment variables. Ordinary owners must admit the operation under their existing domain guard before returning success; every entered observation or mutation has an immutable intent and independent execution/effect outcome.

Local Python integrations can use `brain_application.local.LocalContextComposer(vault_root=...)`, invoke `CommandApplication(composer.compose(command_id=..., invocation_id=...)).invoke(request)`, then close the composer. This explicit infrastructure import resolves the same current credential permissions and initial policy as CLI and MCP. A standalone Python context cannot manufacture exceptional consent; a trusted adapter must attach an actual private instance owner. Advanced adapters can implement the public authorisation and owned-receipt ports directly. Discovery uses a batched, non-consuming observation from that authorisation service; it never grants permission to execute.

## Internal script modules

`compile_router.py` and `define.py` share taxonomy parsing. Filename patterns in
simple and advanced `## Naming` contracts reject absolute paths, POSIX/Windows
separators, drive prefixes and dot traversal segments before definition writes
or router publication. Rendering validates cached patterns too. `create.py`,
shaping, and backlink writers use the purpose-specific `safe_write_artefact()`
boundary, while document/lifecycle and repair writers select the active or
archive-only capability through `safe_write_active_or_archived_artefact()`; both check the
fully resolved destination. `rename.py` uses the same destination policy for
moves and verifies that each resolved source remains in its requested scope.
Ordinary artefact destinations
cannot enter `_Config/` or dot-prefixed system folders. Archived artefact,
configuration, and internal writers retain narrower separate capabilities.

Files such as `create.py`, `edit.py`, `read.py`, `repair.py`, `session.py`, `upgrade.py` and domain packages remain implementation providers where application or launcher owners use them. Their old independent aggregate parsers and compatibility entry points are not the public command grammar. `permission_admin.py` is a CLI-owned internal subprocess boundary that receives its operator secret only through trusted process context; it is not a direct-script command. `start_shaping.py` is removed; use `shaping.start` through `command.py`.

For repository maintenance and last-resort recovery, `compile_router.py` accepts
an explicit `--vault`; its `--help` path performs no vault discovery or
compilation. The retained `create.py` compatibility entry point republishes the
compiled router after a committed create, so consecutive recovery creates do
not require a manual compile between commands. If that post-commit refresh
cannot complete, creation still reports its committed result with an explicit
`runtime.refresh-router` warning rather than inviting a duplicate retry.
This compatibility route is restricted to wholly unscoped recovery vaults and
cannot create workspace hubs. If any workspace hub or membership metadata exists,
it fails closed and directs callers to `artefact.create` through `brain` or
`command.py`. Legacy `edit.py`, `rename.py`, and `lifecycle.py` mains are internal
test/maintenance seams, not supported semantic routes; do not use them to bypass
workspace preparation, policy, lifecycle guards, or index completion.

Maintenance is CLI and direct-script only: `command.py maintenance run
[--dry-run]`, `maintenance list`, `maintenance claim`, `maintenance dismiss`,
`maintenance release` and `runtime remove-temporaries` take the same request
objects as the CLI. The pass needs a keyless standalone call; see
[maintenance passes](cli.md#maintenance-passes-and-scheduling).

Machine-global operations do not run through selected-Brain `command.py`. The versioned CLI distribution owns the separate stdlib-safe launcher catalogue and its install, upgrade, registry, runtime, MCP and diagnostic owners.

Use `command.py workspace register --vault VAULT --workspace PATH` or
`brain workspace register --brain ID --workspace PATH` with explicit
`brain_id` and first-registration `slug` semantic fields. Create the canonical
workspace separately with `workspace.create` (`key` and optional `title`).
Register refuses a missing active workspace without writes and returns its
Brain-pinned creation command. An idempotent repeat may retain the validated
slug; a changed identity requires explicit `workspace.rebind`, not force.
Discovery and consent controls remain available before binding; ordinary
commands still validate linkage. Use a local CLI job for exceptional consent.

Create supports MCP, CLI, direct script and Python. Register/rebind/unregister
need caller-local filesystem access and have no MCP projection. Local CLI
`workspace.upgrade` selects explicit folder, Brain or machine scope;
`workspace.discover` scans explicit roots read-only. Discover never registers,
and upgrade never creates missing hubs. Inspect command discovery for their
exact scope and preview/apply fields.

The former ensure-registration/setup commands have no compatibility aliases.
The old `setup.py workspace`, `configure.py workspace binding` and
`workspace_registry.py --register/--unregister` one-ended writers remain retired;
use the canonical owners rather than editing only one end of a binding.

## Dependency boundaries

Application foundations remain tier-strict:

- `bootstrap` uses the standard library and bootstrap-safe Brain modules;
- `portable` adds ordinary local Brain functionality without the managed runtime;
- `managed` may use the selected Brain's managed dependencies.

These are ordered dependency tiers only. Locality, authority and providers remain orthogonal. Lower-tier packages do not import adapter, MCP SDK, terminal renderer or higher-tier implementation packages.

Portable `vault.check` inspects semantic metadata locally and verifies model loading in the selected managed interpreter. Missing dependencies produce findings; timeout or malformed managed output produces a bounded inspection finding. Warm-up also isolates semantic work in that interpreter, and explicit `runtime.warmup` retries a previously deferred component.

## Grok in setup and maintenance

New MCP installation/configuration requires an explicit client. Use
`install.sh --non-interactive --client all /path/to/brain` (Windows: `-Client all`),
or choose one client. An install with `--skip-mcp` needs no client selection; it skips MCP registration only and still provisions the managed runtime.
`configure.py mcp --client ... --user` delegates to the compatible installed
machine CLI without resolving a Brain; project/local operations share the
canonical MCP registration planner. `repair.py mcp` remains vault-local and repairs
only recorded vault-self project projections. Cross-root and user repair belongs
to the [launcher breadth selectors](cli.md#mcp-registration-and-repair).

Upgrade runs machine ownership migration and Brain-breadth reconciliation after
the coordinated CLI/Core/runtime cutover. The shell uninstaller delegates to
the canonical launcher uninstall owner and stops on incomplete cleanup. Legacy
records require explicit `brain mcp migrate`, not adoption by normal repair.

Upgrade follow-ups are retained in the direct-script JSON and upgrade log, and
in the launcher's typed `result.followups`; human output includes their commands.
Crossing 0.70.3 offers optional managed-approval discovery via read-only
`brain approvals inspect --json`, without opting in or modifying manual rules.
The version-crossing notice is also included in upgrade dry-run previews.

The supported `install.py --client`, `configure.py mcp --client`,
and `configure.py agent-skills --client` selections include `grok`; `all` includes all three clients. Grok supports user and project
scope, including vault-self MCP registration. The native CLI commands are shown in
[native Grok setup](cli.md#native-grok-setup). `repair.py mcp` and upgrade
reconciliation inspect existing Grok project MCP registrations and their owned
startup rules. Standalone workspace bootstrap also accepts `--surface grok`.

Bootstrap and document reads use the same bounded application results in every
projection. Finish `session.start` pages until `bootstrap_complete` is true.
For document reads, repeat the same selectors with `cursor: range.next_cursor`
until null; revisions prevent mixing source versions between pages.

### Router repair contract

`runtime.refresh-router` verifies the same source fingerprints as mutation
admission and verifies the persisted result. An unchanged, valid router is a
no-op; `force: true` unconditionally rebuilds using the same algorithm. A source
change detected after rebuilding is a partial repair, with an explicit command
next action through CLI/MCP. `vault.check(check="router")` uses authoritative
content validation and identifies an affected source path when available.

An undecodable router, taxonomy or memory source stops compilation before
any router output is written. `runtime.refresh-router` returns a definite
`conflict` naming the file and classifier code, rather than an unknown outcome.
Clear encoding damage points to `vault.repair-text`; ambiguous damage points
to `vault.check`. A maintenance pass retains a failed repair's message as a
`follow_up_required` warning so the blocking source is visible.

Artefact document reads used for lexical indexing and semantic embeddings, and named-resource body reads used for search, skip files that fail strict UTF-8 decoding. They continue with readable documents and leave source bytes unchanged. The lexical freshness inventory records skipped paths, so a completed rebuild can be fresh while a later `vault.check` still reports unreadable sources within its text-scan scope. Filesystem errors in these document reads remain named failures; these reads do not convert encodings.

Undecodable router and lexical caches are stale with reason `unreadable`.
Client JSON/TOML diagnostics use their existing parse/read failure paths;
an unreadable `CLAUDE.md` has its own bootstrap reason and is not reported as
missing bootstrap text.

`runtime.status` reports recorded warm-up progress, labelled `recorded-warmup`;
ready is not a current-cache health assertion. Use its `router_check` action for
current router diagnosis. Artefact creation and lifecycle-field commands maintain router and lexical
state before returning success; semantic encoding remains separate.

### Unreadable vault text

Vault text is UTF-8 without a byte-order mark. `vault.check` scans artefact Markdown (including temporal and archived files), `_Config` Markdown except managed skill packages, `_Plugins` Markdown and root bootstrap variants. It excludes symlinks, dot folders, config YAML and derived JSON. The scan runs even when the compiled router is missing.

`unreadable_file` errors have no repair family: `os_error` reports an errno and suggests checking permissions or downloading cloud-only files; `not_utf8` names the first bad byte’s line and byte column; `not_text` reports NUL bytes. Convert legacy text to UTF-8 in an editor, or move deliberately kept non-note content into `_Assets`. Dismissals for the latter two codes follow the file and code; permission errors also follow the errno evidence.

An inaccessible directory is reported as `unreadable_file/os_error` and means that part of the scan is incomplete. If skill tracking metadata cannot establish package ownership, `text_scan/skill_ownership_unavailable` reports the metadata path and `_Config/Skills` is conservatively excluded until tracking is restored; other roots remain inspectable.

### Requested text repair

Use `command.py vault repair-text --dry-run` for a byte-count preview, then apply without the flag. The optional request `paths` selects only current clear `text_encoding` findings; the command does not infer a legacy encoding. It operates without a compiled router, checks source digests, skips changes made after planning, and atomically writes bytes without altering line endings. A failed write can produce a partial result naming each committed path. Truncation previews show the dropped bytes in hexadecimal and Windows-1252, with guidance to exclude that file or convert it manually. The judgement family is not a bootstrap-recovery scope or an automatic repair.

### Workspace compatibility before Core cutover

The confirmed upgrade policy allows automatic lossless format conversion only.
Core 0.73.0 introduces the ownership contract. This compatibility scan applies
to earlier installed Core versions; later upgrades do not repeat it.

Before Core copy, VERSION mutation or creation of a new rollback journal, the
upgrader runs a read-only workspace compatibility preflight from the selected
source target. It imports the target's canonical validators in a fresh import
context, so cached installed-Core modules cannot determine target compatibility.
The check covers workspace records, actual hub ownership and shared default parents;
identity and status checks alone are insufficient. A refusal names affected
records and actionable remediation without performing the repair. Existing
journal recovery precedes this check on a real rerun; a preview with pending
recovery may defer the compatibility verdict until restored state is available.

Ownership adoption/reassignment and identity renames require explicit approval
and existing public repair commands, followed by verification and another upgrade
attempt. Neither workspace relevance tags nor a single workspace associated with
a project authorises inferred adoption. A specific workspace rename request is not a
generic migration rule. Retain recovery checkpoints and review intermediate effects;
verify the complete chosen scope before retrying the upgrade. Multi-command repair
is not atomic.
Released Core 0.72 has the required repair commands. When the installed Core
lacks them, including historical releases before 0.69, explicitly preview a source
upgrade from a coherent released 0.72 source with `--dry-run`, then run it normally.
Apply the approved repair in a vault-only context without an external
`--workspace` binding, verify it, then retry the intended target. The bridge is
not automatic and does not permit downgrade.

The Core migration runner already holds the non-re-entrant mutation lock. Its
internal format conversions must use lock-compatible internal helpers, not
re-enter public commands or launch commands that acquire the same lock. Explicit
public repair runs separately before retrying upgrade.

This documents the confirmed contract being added on `dev`, not a release claim:
the release proposal is deferred and VERSION is unchanged. It introduces no new
migration version, public command or automatic repair flag. Compatibility
admission is distinct from registered-folder admission receipts and post-commit
recovery below. A preflight refusal does not automatically reset MCP.

The existing registered-folder format phase validates the active canonical hub
and shared policy, but preserves local defaults without validating them. Format
success therefore does not prove that workspace-scoped mutations are ready.
Check local policy after conversion to the current format; repair invalid local
defaults explicitly with `workspace.update-metadata`. Do not add local-default
validation before format conversion: that would block the conversion needed to
make the repair available.

### Registered-folder metadata during upgrades

After compatible runtime provisioning, normal upgrade runs selected-Brain
registered-folder migration as a separately admitted local phase. Its external
folder effects and recovery are distinct from the vault-only journal and Core
VERSION commit. A folder failure does not roll back a committed Core upgrade;
inspect its partial/follow-up results and exact remediation. Direct machine
scope can cover all compatible registered Brains without upgrading their Core
installations. Unreachable or incompatible Brains leave explicit incomplete
coverage. Discovery scans only explicit roots and reports unknown registration
status when inventory coverage is incomplete; it never silently registers.


### Selecting an installation release

Use `bash install.sh --version X.Y.Z --non-interactive --skip-mcp /path/to/brain` for an exact published release (`latest` selects published main). Native Windows uses `./install.ps1 -Version X.Y.Z -NonInteractive -SkipMcp -VaultPath C:\Brains\Example`. Without a selector, an installer in a complete local checkout uses that checkout; remote installation defaults to latest published source. A published release's permanent tag must match its stored Core version and certified commit. Source selection and the resolved receipt are shared by installer, template and Core; missing/conflicting tags fail rather than silently falling back.

A healthy machine-global CLI is preserved. Explicit `--replace-cli` / `-ReplaceCli` requests changing it; selecting a Core version alone does not. `brain install` / `brain upgrade` also accept `version` or `source_root` and `replace_cli` in request JSON. CLI bootstrap failure stops before creating a new target vault and reports retry/repair guidance; later runtime provisioning and optional MCP setup retain their separate follow-up outcomes. Offline `brain --version` reports CLI identity, distributed-with Core and the advisory full-support baseline. Earlier-release interoperability is best-effort; no dedicated offline bundle is promised. See [CLI contract](cli.md) for the detailed contract.
