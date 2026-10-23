# MCP Command Interface

Brain Core exposes the selected-Brain application catalogue as granular MCP tools. `server.py` is a small composition root: it registers catalogue projections, composes trusted local invocation context and installs the replacement-proxy protocol gate. Semantic logic belongs to application commands, not the MCP adapter.

The server is long-lived, so work whose memory would otherwise stay resident for the rest of the session runs elsewhere: `session.start` warm-up spawns a detached worker, and the semantic maintenance commands (`retrieval.repair-semantic`, `retrieval.rebuild-semantic`, `retrieval.enable`) run their lifecycle owner in a fresh interpreter through `_lifecycle/fresh_interpreter.py` while the calling process holds the vault mutation lock. Query encoding for `artefact.search` stays in-process on the CPU-only `onnxruntime` encoder.

The transport boundary uses the official Python `MCPServer` API at the exact
reviewed `mcp==2.0.0` pin. It continues to serve supported 2025 protocol clients;
the dependency admission and runtime policy are recorded in
[MCP SDK 2.0 Dependency Admission](../architecture/mcp-sdk-2-dependency-admission.md).

## Tool grammar and discovery

`type_status` v3 accepts library keys or their mapped frontmatter types in
`type_keys`; `type_sync` v3 accepts either in `type_key`. For example,
`living/notes` and `living/note` select the same library entry. This lookup is
shared with CLI/script/Python, uses existing taxonomy metadata and rejects
ambiguous or unknown identifiers. Results retain canonical library keys.
See [library type lookup](../../src/brain-core/artefact-library/README.md#installing-a-type)
for mapping precedence and operation-consent behaviour.

An MCP-eligible command projects its canonical `<noun>.<verb>` ID to the raw
MCP name `<noun>_<verb>`. Only the single dot changes; hyphens are preserved.
For example, `session.start` becomes `session_start` and
`document.update-frontmatter` becomes `document_update-frontmatter`.

The MCP server name remains `brain`. This projection is global, reversible and
collision-free under the command grammar. Raw dotted names are not aliases.
Command IDs in results, discovery arguments, permission profiles and access
requests remain dotted; CLI commands remain `brain <noun> <verb>`.
An existing session on an incompatible earlier interface epoch must reconnect
and rediscover tools when upgrading to epoch 3. Same-epoch upgrades instead
follow the selected connection's `brain_proxy_status` recovery action; reconnect
is conditional. Project MCP registration repair updates exact managed bootstrap lines.

The application catalogue owns the installed command inventory and marks each projection explicitly. A running server exposes only the MCP-eligible commands within the authenticated profile ceiling. Exact catalogue and profile counts are generated and checked from the authoritative catalogue; use MCP discovery or `command.list` for the selected installation rather than treating prose counts as a compatibility contract.

Start an MCP session with `session_start`. On a cold Brain it starts or joins background warm-up and returns the shared `brain.runtime-status/1` snapshot with guidance to poll `runtime.status`; retry `session.start` when ready. `runtime.status` is a cheap read-only observation, while `runtime.warmup` explicitly starts, joins or retries warm-up. The snapshot is labelled `observation: recorded-warmup`: ready means the recorded warm-up finished, not that caches remain current. `runtime.status` supplies `router_check` (`vault.check` with `check: router`) for blocked writes. Discover commands with `command.list`, and inspect one exact request/result contract with `command.describe`. Default discovery uses static catalogue facts and does not probe optional providers; request an explicit refresh only when current provider availability matters.

`command.list` v5 returns a brief view by default: command ID/version,
summary, required authority, effect class, transport eligibility, initial class and
current `access` (`authorised`, `authorisation_required` or `denied`). Availability
is a provider observation, separate from authorisation. One batched access
observation serves each discovery page. Commands above the credential maximum
remain discoverable as `static_disclosure: true`: installed metadata and an
administrative route only, with dynamic availability unknown and no provider
probes. They are not callable or requestable through the current credential.

Brief and detailed entries expose `mcp_tool` (the exact raw MCP name) and
`cli_argv` (the noun/action arguments after `brain`). A null mapping means the
transport is unsupported; a non-null mapping is not an access grant. The same
fields appear on `command.describe` and its examples. Mappings derive from the
catalogue projection, retaining hyphens: `document.structured-edit` maps to
`document_structured-edit` and `["document", "structured-edit"]`. Above-ceiling
entries retain these static facts without becoming callable.

`query` matches a case-insensitive substring of the canonical ID, summary,
supported MCP name or supported CLI spelling, with or without the `brain`
prefix. For example, `command_list({"domain":"document"})` discovers the edit
family, while `query:"document_structured-edit"` finds that exact command.
Pass the canonical `command_id` to `command_describe` and consent commands.

The default page has at most 25 entries. Both `brief` and `detailed` views
stop before the canonical JSON envelope exceeds 16,000 UTF-8 bytes, leaving
headroom below the observed 20,000-byte client text limit. This is Brain's
compatibility budget, not an MCP protocol limit. Pass `next_cursor` as `cursor`
with the same filters to continue; a page may contain fewer than `page_size`
entries. Shared catalogue identity and availability freshness occur once per
page. Use `view: "detailed"` for provider/projection/retry/lifecycle metadata;
`command.describe` v5 retains full schemas and examples and includes access.
The byte budget applies to list pages, not arbitrary command descriptions.

Related named resources share the strict `resource.create`, `resource.list`, `resource.read` and `resource.search` tools. Each has a shallow resource or target discriminator and a closed resource-specific result union. Presentation and printable output similarly share `shaping.render` with a strict `output.kind` branch. These commands replace target-only leaves without introducing a generic invocation gateway.

`shaping.start` v2 opens or continues a taxonomy-declared shaping session. Its
shared symbolic `workspace_context` follows the semantic mutation policy: new
transcripts receive membership, default parent and tags; the source and existing
transcripts preserve membership/parent and restore configured tags. Preparation
and typed success/partial results expose the frozen effective context. The
default `transition` behaviour enters `status: shaping`; discovery-only
`preserve` leaves an enduring non-terminal status unchanged and rejects
terminal targets before mutation. Its result reports the effective status
behaviour and whether status changed. When several linked same-day transcripts
name the source, it continues the one with the widest distinct resolved source
set and rejects ties; path and basename spellings of the same file count once.

`artefact.migrate-naming`, `retrieval.enable`, `workspace.repair-registry`, the `maintenance.*` commands and `runtime.remove-temporaries` remain available through the CLI, direct script and typed Python interfaces but are deliberately not registered in agent-facing MCP. Their catalogue records state the local-administration reason. The only MCP-visible maintenance signal is the coarse `maintenance` advisory on `session_start` and `runtime_status`, read from the last pass: `needs_person`, `claim_expired`, `failed` and `deferred` counts, a blocked reason and the pass age; it is absent until a pass has run.

Explicit refresh enforces provider-specific and aggregate deadlines. Timed-out probes report `unknown`; a fixed process-wide daemon bound prevents repeated MCP calls from accumulating unbounded stuck probes or delaying CLI process exit.

The former aggregates and variants are removed: `brain_init`, `brain_session`, `brain_read`, `brain_create`, `brain_edit`, `brain_define`, `brain_move`, `brain_action`, `brain_process` and the other flat v1 tools are not aliases and are not callable.

## Result sessions and delivery

`brain_session` is a reserved MCP adapter argument, stripped before domain
request decoding and preparation. Use `session_start({"brain_session":"new"})`
once to open a reusable result session. Save the one-time
`result_session.credential` separately and pass it as `brain_session` on every
subsequent call, including bootstrap continuations. Resume with that credential;
use a distinct session for each logical agent. `result_read`, `result_search` and `result_clear`
require `brain_session` in their MCP transport schemas and reject its absence
before context composition; it remains outside their canonical domain requests.
Other retained source calls may omit it to open an ephemeral context rather
than selecting an account-wide result slot. The diagnostic
reads `runtime.status` and `runtime.read-environment` skip retention and
result-cache writes when no result session is explicitly selected. Retain any issued
credential before following output. Issuance is separate from the persisted
business-result body and cannot be recovered from a public result ID.

For deliberate storage-capacity recovery, call
`result_clear({"brain_session":"<existing-credential>"})`. This Reader control
clears only the authenticated result namespace and keeps its credential, source
files, independent receipts and other owners' results. It takes no domain request
fields; missing, invalid or new-session selectors are refused before execution.
Clearing permanently loses retained output: check recovery needs first. CLI,
script and composed typed-Python callers can use the same `result.clear` command;
see [the CLI recovery command](cli.md#reusable-result-sessions).

Eligible output retains the latest invocation per canonical command, result
session and Brain, with no time expiry. New admission of the same command
supersedes its previous output; unrelated commands and result navigation leave
it intact. Late completion cannot replace a newer admission. Superseded IDs
never select the newer result. Retention failure preserves the source outcome
and committed effects with unavailable delivery; never re-execute a mutation
to recover its output. Use `invocation.read` for uncertain execution outcomes.

The `delivery` descriptor reports availability, backing,
consistency/count scope, units, requested/effective page size, item/page counts,
selection bounds, remaining pages and continuation. Inapplicable counts are
omitted. `continuation` is always present (null at the end).
`delivery.status: "truncated"` means output exists beyond this page or selection, not failed execution. A completed selection can have only earlier pages outside it. Recovery messages distinguish the next selected page, a later selection via `outside_continuation`, and earlier pages available by absolute page number.
Text appears exactly once in `result.content`. Successful `result_read`
continuations project the same originating command envelope and payload shape
as the initial call; navigation failures identify `result.read` and do not
masquerade as source execution failures. Follow `delivery.continuation` with
the same result ID and session until complete. Text has no additional source
cursor. Genuine domain cursors, including graph traversal, retain their rules.

`result.read` v2 accepts:

| Field | Contract |
|---|---|
| `result_id` | Required owned retained invocation ID. |
| `page` | Omitted/null follows the continuation, or starts at 1 without one. Explicit positive integers are absolute, including 1; 0 means get-all intent; `"+1"`/`"-1"` are relative to a supplied continuation. Applies to `@outcome` too. |
| `continuation` | Opaque versioned string token, or null. Pass the returned value unchanged with the same result ID and session; preserves view size and selection. Use explicit request controls to change navigation. |
| `page_unit` | Optional `bytes`, `lines`, `records` or `fields`. Text defaults to bytes and supports explicit lines; other results accept only their own unit. Continuations preserve the mode. |
| `page_size` | Text bytes default/maximum 4,000 UTF-8 bytes; explicit lines default 100, maximum 500; records/fields default 25, maximum 100. Final encoding limits can reduce occupancy. |
| `max_pages` | Positive selection limit across calls, or `"all"` for the complete set. Query selections default to 10 pages; retained output has no default page-count cap. |
| `anchor` | Zero-based text character offset, or record/field position, for anchored navigation or mode/size changes; page 0 explicitly anchors at the beginning. |
| `detail` | Returned child reference. Text supports the same page/anchor controls; linked structured details use the returned continuation or `delivery.next_detail`, and reject unsupported page/anchor/extent overrides. |

Each call returns at most one response page, including `page: 0`; get-all is
not a multi-page bundle. Exact physical counts use byte-aware page boundaries.
`has_more`/`remaining_pages` describe the selected set;
`has_more_outside_selection`, `pages_before_selection` and
`pages_after_selection` describe the rest. Follow `outside_continuation` to
open the next selection. A selection preserves its numeric start and limit,
reapplied to current page counts; it does not freeze membership. An absolute
read of an existing page outside that extent starts a new selection at that
page, preserving `max_pages`; relative navigation stays within its selection.
Pages beyond the current total remain out of range. Changing `page_unit` or size
creates a new view anchored at the supplied character/record position and
reapplies that limit. Supply an anchor, or `page: 0` to explicitly restart.
Omitting the mode on a continuation keeps its existing mode; it does not switch
a line view back to bytes. Byte/line switches preserve character coordinates.
Out-of-range pages report current counts rather than silently substituting a page.
Default retained/immutable/produced-output metadata values and irrelevant nulls
are omitted; callers use those defaults when absent. Capped selections carry
their within/outside bounds; an uncapped full-set view omits redundant selection
counts. `continuation` remains present, null at the end.

Text pages contain only `content` and compact source context. Their `content_range`
reports absolute Unicode character bounds and total length, with no continuation
cursor. Ordinary text defaults to byte paging: `page_size` bounds the UTF-8 bytes
of content, independently of JSON escaping/context and final response caps. A
short complete result is returned in one page even if it contains more than 100
lines. Set `page_unit: "lines"` on `result.read` for explicit line paging; omitted
size then means 100 lines (maximum 500), still subject to the byte caps. Byte
mode defaults to its current maximum of 4,000; smaller sizes are supported. A
size too small for a complete UTF-8 character is refused with minimum-size
guidance rather than splitting that character or exceeding the request.
An explicit field page size limits field occupancy; without one, a compact
structured root retains its single-page shape when it fits. These are distinct
cached layouts even when the explicit size equals the default, so a prior compact
read cannot override a requested field count.
In either text mode, byte caps can split an oversized line safely between
Unicode characters. Line mode selects logical lines, but does not override the
response byte cap. Byte pages may also split words or identifiers: join adjacent
`content` chunks in `content_range` order before extracting values across a
boundary. No per-line objects or duplicate text are emitted. Records/fields use bounded
detail references rather than silently dropping content.

Cursor-free `artefact.list` with result delivery enabled produces the full
filtered summaries for generic paging. Active lists' dynamic pages check the maintained
projection revision and refresh current ordering; unchanged pages reuse the
cached view. `change_notice` is tracked per page/view, so each revisited page
warns independently after a change, without repeating on unchanged rereads.
Cross-revision traversal may repeat or skip items. `observed-projection`
consistency is not proof of live-file reindexing. Explicit legacy list cursors
retain their owner path. Other output is retained; ranked search and graph
counts describe their produced window, not every possible match or graph node.
Archived/all lists use retained backing and immutable-output consistency,
with a persistent notice that freshness is unverified after opening. Archive
files have no cheap maintained revision, so navigation never rescans or refreshes
them; invoke a new `artefact.list` for current data. Requested page size, exact
counts and the default ten-page list selection remain in effect, including
outside continuation and an explicit `max_pages: "all"` selection.

`result.search` v1 accepts `result_id`, case-sensitive `literal` (1–256
characters), `start_page` (default 1), zero-based `start_offset` (default 0),
`limit` (default 10, maximum 25) and `scan_pages` (default 10, maximum 50).
It returns one bounded snippet per matching
text slice or structured record. `matches` include page/unit `offset`,
character `column` and line/record identity. Text overlap finds literals crossing
page boundaries, including after resume. `scanned_pages` counts forward page reads; `overlap_pages`
counts additional look-behind reads, bounded at 256 per call. Byte limits can
stop before `limit`. Resume using `start_page: next_page`,
`start_offset: next_offset` so no unreturned match is lost. Coverage is derived
from immutable view metadata on every call; no coverage flag needs to be echoed.

`coverage: retained-body-units` scopes every reply to the retained result body's
declared text slices, records or structured field units. Search excludes
canonical envelope metadata such as command identity, errors and warnings, and
contextual payload extras repeated beside line/record units. It does not search
the entire canonical JSON blob.

`scan_complete` means those body units are exhausted; continuation positions
then become null. Within that scope, `complete` additionally requires no unsearched detail
references. Detail bodies are not traversed: if the retained view has any details,
`details_skipped` is true on every call, with `complete: false` even at exhaustion.
Neither completion flag claims coverage of envelope metadata or contextual extras.
This is neither regex nor live artefact search; dynamic queries are refused.

The final assistant-visible canonical envelope is capped at **8,000 UTF-8
bytes**, and the final encoded MCP tool-result value at **32,768 bytes**,
including duplicated text/structured content and proxy notices. JSON-RPC
correlation IDs/framing are outside those limits. Byte limits win over requested
unit counts; final guards report unavailable delivery without slicing JSON or
changing the source execution outcome. Native host delivery still requires
host verification; these limits do not prove a host upgrade or tool refresh.

Credentials select private output namespaces; they grant neither permissions
nor consent. Every page, detail and search checks current disclosure authority.
Eligible exceptional output can continue after one-shot execution consent is
spent through a narrow delivery entitlement under the original consent owner.
It ends on owner end, revocation or incompatible authority change. Resuming a
result session does not renew it or authorise source execution. Dynamic refresh
requires current source-query authority separately. See the
[security boundary](../architecture/security.md#retained-result-security).

## Bounded bootstrap and document reads

`session.start` produces the complete canonical bootstrap Markdown that is
also published as `.brain/local/session.md`. Generic delivery is the sole
pager. Consume `delivery.continuation` via `result.read` until all guidance is
retrieved before ordinary work. There is no separate bootstrap-completion flag;
the generic page metadata describes delivery.

`artefact.read`, `vault.read-file` and document variants of
`resource.read` likewise produce one immutable logical text result with its
source revision. Their request schemas do not accept legacy `cursor` or
`max_characters` fields. Generic delivery applies the default byte budget (or the explicitly selected
retained text view) and the final response cap once. Initial and continuation pages return stable
`result.content` and an exact delivered `content_range`, so concatenating consecutive
pages reconstructs the complete text. A new originating read supersedes that
session's previous invocation of the same command; navigation never rereads the
source. Metadata-only resource variants remain structured results. `vault.read-config`
returns redacted structured configuration; it is not a document text reader.

Document reads can decode leading UTF-8 marks and BOM-marked UTF-16/32 without rewriting the source. Every converted page includes a `follow_up_required` warning naming the path and diagnosis within the same byte budget. Revisions still identify the original bytes, so an encoding-only source change invalidates a cursor or prepared read. Truncated or ambiguous text returns a named `conflict` with a recovery action. Document edits that accept a lossless conversion write UTF-8 without a leading mark and report the conversion; they refuse lossy conversion.

Encoding recovery actions respect the shared text-check inventory. Files outside that set, including shipped Core and source-managed skills, receive path/diagnosis and restoration instructions rather than a `vault.repair-text` action that cannot repair them. If inventory inspection fails, the refusal names that failure and asks for access to be restored before retrying.

## Permissions and instance authorisation

Document mutations preflight artefact lifecycle consequences, including derived
filename moves and destination collisions, under the mutation lock before
admission. A known pre-write collision returns `conflict` with no effects and
does not consume staged content. The validated lifecycle plan is reused for
execution. Prepared document authorisation binds the derived destinations and
backlink source/effect set, and its review lists those moves and writes. Changes
to that set require fresh preparation; unrelated content does not. Lifecycle
time is frozen across preparation and execution. Failures after possible effects still report partial or unknown
outcomes; a `FileExistsError` after a write is not treated as a safe refusal.

Credentials determine the maximum command permissions. Normal content operations
and observations begin authorised within those permissions. Configuration can
select a read-only initial set or exact initial commands. Permission and
exceptional authorisation are distinct: `access.request` cannot enlarge a
credential's permissions.

Exceptional consent belongs to this Brain, principal and MCP instance. It ends
when that instance closes; replacing the runtime child preserves the owner, but
restarting the MCP instance requires fresh consent. There are no timed leases,
automatic renewal, or implicit denial/request/retry loops. The harness may require
manual approval specifically for `access_request`, or auto-approve that tool.
Brain records the explicit request and requested scope; it does not claim to
know whether a human clicked an approval button.

- `access.prepare` validates an exact target request and returns its canonical
  review, operation ID and digest. Preparation does not enter the target or
  grant consent. Its inspect variant pages context-owned descriptor details.
- `access.status` v4 with `target_command_id` returns canonical `command_review` for consent
  to that exact command throughout this Brain and current context. Its grants,
  operations and initial views are paged; operation rows recover preparations
  whose response was lost.
  When consent is required, `next_action` is a non-executing instruction to
  choose operation-specific or command-wide scope, not another status call or
  an automatic request. Ordinary blocked invocations still route to status.
  Denied commands do not receive this consent instruction: inspect their
  permission, policy or context boundary instead. Consent never raises the
  credential ceiling.
- `access.request` echoes the returned review for either one prepared operation
  or one command. Specific consent requires the returned operation selector on
  the target call (`brain_operation` in MCP); successful observation also spends
  it. Changed target revisions or affected work sets require new preparation.
- `access.reduce` revokes grants, disposes prepared operations or narrows initial
  authorisation. It cannot restore an initial command or increase permissions.

`session.start` reports `access.permissions.ceiling` separately from
`access.authorisation.initial`, request policy and exceptional-context availability.
Unavailable context or migration-required policy does not advertise a usable
request route. The compact access summary is at most 768 UTF-8 bytes; prepared
reviews and complete consent requests remain below 8,000 bytes.
`vault.read-config` reports the effective initial selection, overrides and
configured request policy with their template/shared/local sources and actionable
configuration diagnostics. `effective_request_policy` separately reports any
restriction caused by those diagnostics. Oversized redacted configuration results use
revision-bound JSON text pages; concatenate their content before parsing.

MCP tool definitions remain stable within the authenticated maximum. Discovery
above that maximum gives the static `permission.set-profile` CLI route; changing
permissions requires an appropriately authorised administrator for the target
Brain. Agents do not search for keys or change their own credential permissions.

Unattended callers should inspect command access, explicitly request a supported
scope when configured to do so, and pass the operation selector for specific
consent. Standalone CLI calls have no reusable exceptional context; use the
explicit CLI job route for a multi-call workflow. An uncertain call is recovered
through `invocation.read`, whose intent and final outcome distinguish admission
from execution. Do not replay entered or uncertain operations automatically.

## Semantic relationships and selected history

The application catalogue exposes the `relationship.*` and `history.*` families
through granular tools such as `relationship_batch`, `relationship_check`,
`relationship_expose`, `history_read` and `history_repair`. Discover each installed
contract with `command_describe`; fields and transport mappings are owned by the
same typed requests used by CLI/script/Python. Relationship checks require no
embedding model or external semantic-retrieval provider.

The [shipped semantic relationship reference](../../src/brain-core/standards/semantic-relationships.md)
owns command roles, endpoint shapes, dependency dispositions, revision semantics,
bounded queries, capture policy and recovery. `history_read` and `history_query`
include correction interpretation; inspecting an original event must not hide its
supersession. `relationship_query` exposes projection freshness and does not certify
a whole-graph snapshot. Use `relationship_check` for bounded source validation and
explicitly selected assessment/transcript policies.

## Request contract

Each tool exposes the exact strict object schema derived from its sealed command request. MCP arguments are the semantic request itself, plus the reserved optional `brain_operation` selector on ordinary tools. There is no generic invocation envelope or caller-supplied command version. Nested consent/preparation variants have explicit scope discriminators. Unknown fields fail before executor entry.

The schema preserves required versus optional fields, explicit nullable fields, enums, nested object structure, descriptions and `additionalProperties: false`. Use the schema returned by MCP discovery or `command.describe`; do not infer one command's fields from a neighbouring command.

Large or retry-sensitive content uses the explicit staging commands where the described command accepts a staged handle. Caller-owned file paths are not an MCP content source because the server may not share the caller's filesystem.

## Workspace discovery filters

`artefact.list` and `artefact.search` accept optional `workspace: workspace/{key}`
and `workspace_match: relevant | primary` (default `relevant`). Relevant means
primary membership plus exact canonical workspace tags; primary means only the
scalar membership. Omit `workspace` for vault-wide discovery, regardless of the
caller's startup workspace. The filter validates canonical syntax without
requiring an active or existing target. `global`, bare slugs and paths are not
workspace filters. These filters select results; they neither select mutation
policy nor grant authority.

Results expose `primary_workspace` (nullable) and `relevant_workspaces` (including
primary membership). Workspace records report their own canonical identity as
primary. Search covers the active namespace, including terminal status folders,
and excludes archives in lexical, semantic and hybrid modes. Listing is exhaustive
and supports `location: active | archived | all` (default `active`); archived
results retain the same membership/relevance projection. Use listing for archive
or membership enumeration and search for ranked content retrieval.

List cursors are opaque and bound to the original filters, workspace match mode,
sort and location. Follow the returned cursor with the same query; restart listing
when those selectors change. Old numeric cursors are rejected. The lexical index
uses generation `1.2.0` and semantic metadata uses generation `2`; refresh outdated
assets with `retrieval.refresh-lexical` and `retrieval.refresh-semantic` rather than
reusing older sidecars. Metadata-only changes retain vectors only when their exact
encoder inputs and model identity still match.

## Trusted invocation context

`artefact.create` and artefact-targeting `document.write-body`,
`document.update-frontmatter`, `document.structured-edit` and
`document.replace-text`, plus artefact rename, naming/status/key changes,
convert, reparent, reparent-children, archive, unarchive, delete and set-workspace accept a
symbolic `workspace_context` override. Omission
uses validated startup binding; `workspace/{key}` selects another workspace in
the same Brain; `global` applies no workspace defaults. Invalid local bindings
cannot be bypassed. This field never accepts filesystem paths and does not
change connection state. Non-artefact document targets reject it.
For `artefact.create` version 5, an omitted selector uses a non-null explicit
parent's membership before startup context. Destination policy supplies the
defaults; an explicit selector conflicting with the parent refuses. Omitted
`parent` uses defaults, while `parent: null` explicitly suppresses them.
No parent still retains primary membership; it does not imply a workspace-record
parent. Cross-destination creation adds the bound source workspace's relationship
tag, independently of the destination membership. The effective context reports
`source_workspace`, `workspace`, `selection_reason`, `parent_source` and
`relevant_workspaces` alongside observed policy revisions. Workspace records
themselves are independent self-scoped roots and cannot have a structural parent.
Semantic document edits preserve a workspace hub's type, including when its
self-membership is explicitly stored.

`artefact_set-workspace` uses that context as its destination membership. It accepts
`path`, `recursive`, optional replacement `parent`, or `clear_parent`. Descendants
require recursive intent; the complete graph includes terminal living, temporal,
and archived records. Only living records have canonical owner identities.
Stored parent references in filename form (`project~key`) resolve to the same
owners as canonical references (`project/key`).
Explicit `global` clears membership; omitted context uses a valid bound workspace
but cannot implicitly clear membership in an unconfigured connection. It is a
contributor command with CLI, script, MCP and typed Python projections.

`vault_check` version 3 includes optional finding `code` values for workspace
reference, ownership, policy, local-binding and relevance diagnostics.
`workspace_relevance_missing` and `workspace_relevance_malformed` are
informational; relationship tags never imply an adoption request.
An archived workspace hub's own self-membership is valid; other artefacts that
still reference that archived hub receive `workspace_reference_archived`.

The version-3 session payload exposes `workspace_policy.workspace` and separate
`shared` / `local` parent-and-tag policy inputs. `workspace_default_tags` remains
the local-only compatibility field, not the effective merged policy. Effective
mutation context and observed policy revisions appear in preparation reviews and
the affected mutation results, including known-partial results when index
reconciliation fails after a semantic mutation commits. These
partials retain truthful committed subjects and their repair action; the generic
receipt schema does not persist workspace policy inputs.

Lifecycle guards preserve discoverable workspace membership and default-parent
references, including temporal and archived members. They report the selected
vault, recorded folder registrations, embedded locations and active local
binding, not unrecorded disconnected clones. Relevance tags alone do not block
archive/deletion and are retained. A permitted workspace rekey updates exact
canonical tags in active and archived living/temporal artefacts, without changing
similarly prefixed tags. Recursive semantic
subjects receive policy tags; deletion and incidental maintenance rewrites do not.

MCP callers provide only semantic fields. The adapter derives selected Brain, workspace binding, authenticated profile, authority, dependency tier, providers, dry-run facilities and receipt storage from trusted server/proxy state.

`workspace.create` (`key`, optional `title`) and `workspace.update-policy` are
selected-Brain content commands available over MCP. Register, rebind,
unregister and metadata updates need local filesystem access and have no MCP
projection. Upgrade and read-only discovery are local CLI operations with
explicit scope/roots; MCP cannot configure a connecting agent's filesystem.

Outdated local workspace metadata returns a structured no-effect recovery
diagnostic with `details.reason: upgrade_required`, schema versions, affected
folder and a pinned local `workspace upgrade` command. `session.start`, discovery
and access controls remain usable; MCP cannot perform that local migration.

`workspace.list` / `workspace.read` return canonical metadata and collections of
zero, one or many known registrations. They accept no arbitrary caller-supplied
filesystem selector. Known inventory paths do not grant access to those paths.
Singular folder resolution needs validated context or local CLI selection and
otherwise refuses with candidates. Invalid linkage never creates synthetic
workspace records or falls back to another Brain.

The local proxy assigns every accepted call a bounded `mcp-...` invocation ID in MCP request metadata before child dispatch. The server refuses calls without that proxy-owned identity. Configure an operator key as trusted `BRAIN_OPERATOR_KEY` server environment when a non-default profile is required; it is not a semantic tool argument.

## Structural results

Every tool returns the same `brain.command-result/1` structure in
`structuredContent`. MCP
[2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#structured-content)
says a tool that returns structured content SHOULD also return that JSON in a
`TextContent` block. Brain does that as the first text block, labelled
`audience: ["assistant"]`, then the concise one-liner labelled
`audience: ["user"]`. Audience is advisory: the host controls display and model delivery. Grok
1.0.30 concatenates both blocks into model text; the first line remains a
complete JSON envelope. No clean human/model display split is assumed.
`structuredContent` is unchanged.

- `ok`: typed `result`, no error;
- `partial`: an error plus the exact known `committed_effects`;
- `error`: no result and `effects: none` or `effects: unknown`.

Warnings, stable error codes, typed details and next actions survive every projection. A dispatched semantic call is never blindly replayed, including observations. When no conclusive receipt exists, the result is non-retryable `command_outcome_unknown` with an outcome reference; query it with `invocation.read`. Lookup never creates receipt storage, locks files or deletes expired records; expiry is reported logically, while writes and explicit maintenance own cleanup.

## Authority profiles

Profiles authorise exact command names. The built-in `reader`, `contributor`, `maintainer`, `operator` and `administrator` ceilings are cumulative, while MCP exposes only each ceiling's eligible subset. Profile migration preserves an older broad document-mutation grant by granting all four replacements; custom profiles otherwise retain only their explicit grants. There is no runtime fallback or compatibility alias after cutover. Credential permission and current-authorisation checks both occur before dynamic request resolution.

## Metadata and client budgets

Tool summaries are short and contain no parameter manuals. Every reachable request property has a schema-resident description. Stable nested shapes remain structural rather than opaque objects. MCP safety annotations are derived from catalogue effect and retry metadata:

- read-only commands set `readOnlyHint`;
- effect-bearing commands set `destructiveHint` as appropriate;
- safe retry commands set `idempotentHint`;
- commands that can contact an external service set `openWorldHint`; all others
  leave it false.

Historical Claude Code, Codex CLI and Grok captures verify fresh and resumed discovery, bootstrap, read and authorised mutation through the portable MCP names. Evidence lives in `tests/fixtures/command_interface_real_client_evidence_v1.json` and `tests/fixtures/command_interface_grok_client_evidence_v1.json`. Those captures do not establish current-client acceptance or loading costs for the compact schemas introduced with semantic relationships; supported-harness verification follows the semantic implementation commit.

The application projection now shares repeated shapes with local `$defs`/`$ref`, preserving descriptions and defaults at each use. Equivalent numeric unions, string enums and identical discriminator branches are compacted without changing typed requests or validation. Examples still use the inline schema. Graph cursors remain structured. Only the accepted-but-unused disposition argument on `artefact.create` was removed; the other 16 native consumers retain it.

The full application-inventory target is **20,000 tokens**, superseding the former 16,384 headroom gate. The current pinned whole-array replay measures **21,465 Claude / 21,501 Codex tokens** for 90 application tools: **both exceed the target**. This excludes the proxy's three recovery tools and is not measured host loading or billed usage. The current capture records the target, excess and those exact measured regression baselines. Tests prevent unexplained growth and verify the known excess; passing them does not certify target compliance or current-harness compatibility.

Every tool retains the hard **3,072-token maximum**. The ordinary compact target remains 512; reviewed exceptions are:

| Tool | Reason for retaining its typed structure |
|---|---|
| `document.structured-edit` | Structural selectors and replace/insert/delete variants |
| `document.replace-text` | Revision-bound matching and native disposition controls |
| `document.write-body` | Typed content, body operations, revisions and required dispositions |
| `relationship.check` | Explicit scoped assessment and transcript-policy inputs |
| `relationship.query` | Structured graph cursor and native-scope selectors; above 512 only in the Claude replay |

`resource.create` is now compact and no longer needs an exception. Historical metadata captures remain unchanged; `command_interface_granular_mcp_projection_v2.json` contains the current deterministic replay.

## Proxy replacement protocol

The server advertises `brain.command-interface-header/1` in discovery and initialize capabilities. It binds proxy protocol range, interface epoch, catalogue and result schemas, catalogue fingerprint, and the exact tool-to-command/version/mutation mapping.

The installed proxy supplies private protocol 5. The interface header still
allows protocol 4 negotiation so deployed proxies can load the replacement and
deliver its restart signal. Application dispatch independently requires protocol
5; the existing protocol gate refuses older callers with
`proxy_restart_required` and no effects. This
rollout prevents an old proxy from silently refreshing Core without the runtime
admission guard. The proxy follows the host's protocol
era: legacy clients initialise normally; modern connections use private
`server/discover` before the first host request, even if the host omits discovery.
Every replacement re-establishes that era and validates its command header.

Three transport-owned tools supplement the application catalogue:

| Tool | Contract |
|---|---|
| `brain_proxy_status` | No arguments. Reports loaded/installed Core and proxy versions, loaded proxy/child and required managed Python paths, runtime restart requirement, child availability, refresh state, interface fingerprint/generation and next action. Works without a healthy child. |
| `brain_proxy_refresh` | No arguments. Loads a compatible server from the selected Brain's already-installed files at an idle boundary. Does not fetch/install code, change permissions or replace the proxy. |
| `brain_proxy_restart` | No arguments. Recover the pinned Brain after external repair. Activate a missing child in the current runtime, or replace the installed proxy/runtime on POSIX while preserving stdio and ending exceptional consent. A no-op requires current code/runtime and a usable validated child. |

These controls appear in MCP `tools/list`, not application `command.list` or the
CLI. Their compact `brain.proxy-result/1` envelope appears in both
`structuredContent` and first-block JSON. They have no application invocation
receipt. `runtime.status` remains the application warm-up observation.

Reachable startup failures use this same transport, not a second degraded server.
Status includes lifecycle phase (`blocked`, `recovering`, `ready`, `stopping`) and
the current startup diagnostic. An unresolved target cannot recover by silently
selecting a new Brain; changed trusted bindings require configuration and host
reconnect. A known target is rechecked before activation/handoff. Repair runtime
or filesystem prerequisites externally, then explicitly request recovery.
Neither recovery nor startup changes MCP registrations, client approvals or installed
dependencies. Failures before proxy entry (including stable CLI/bootstrap or
Python import failure) still require external repair and host reconnect.
Initial trusted target/prerequisite assessment and process setup precede the
input loop; controls are not promised during stalled initial filesystem I/O.
Healthy first discovery retains the complete application catalogue.

One pending lifecycle request is prepared by the existing recovery worker. Ping,
status and required host replies remain serviceable; new semantic work is refused
while preparation runs. The one original call that detected idle version drift
has not been admitted yet: it is held and dispatched once after validation, not
replayed. Cancellation before publication or stdin EOF prevents later activation.
Final POSIX quiescence/drain/retirement is a bounded input-service pause, not a
promise of ping responsiveness during exec.

Idle admission is shared by refresh and restart: outstanding work returns
`server_busy` before recovery assessment, even when status also reports runtime
drift or an unavailable installation. Finish that work, then retry the control.

Before accepting a semantic call, the proxy checks the managed runtime identity
using the same installed-runtime selection as installation and the CLI: reuse
the current Python minor's matching runtime, otherwise select the highest
compatible installed minor for the same dependency hash. No matching installed
runtime is `runtime_installation_unavailable`, not a request to launch a
nonexistent exact-minor path. Both the proxy
and child must use that interpreter; executable paths are compared without
collapsing venv symlinks. Dependency drift returns a transport
`runtime_restart_required` error with `effects: none` and an explicit instruction
to restart MCP. Already in-flight work may finish, including any host replies
it needs. New calls, explicit child refresh and crash-recovery launches cannot
run updated Core in the old runtime. Status reports `runtime_restart_required`
even if the loaded and installed Core versions match. An unreadable dependency
contract returns `runtime_installation_unavailable`; repair the installation
before restarting. These checks do not install dependencies.

When the runtime is unchanged, the proxy checks installed Core drift. With no
in-flight requests it asks its existing recovery worker to launch and negotiate a
candidate, then retires the previous child only after validation. The triggering
call is dispatched once against the replacement. Explicit refresh uses the same
path. If another request is in flight, refresh returns `server_busy` immediately;
there is no forced drain or queued semantic retry. This leaves host responses
available to work that might need them. Wait for that work to finish and request
refresh again. An unchanged healthy server needs no replacement.

Failed validation retains the old child and blocks new semantic admission to the
stale server. Status and explicit refresh remain available to diagnose/retry after
the installation is repaired. A handshake has the existing bounded timeout;
`refresh_in_progress` means inspect status rather than assume completion. Changed command versions or mutation classes require that tool to be rediscovered
before another call can be accepted, including after explicit refresh. Unchanged
contracts continue normally. Refresh never replays a dispatched command, including reads. Unexpected exits and the
residual race between the pre-admission check and child execution continue to
resolve owned receipts; an inconclusive receipt means unknown, not safe to retry.

Status exposes `interface.tool_discovery` independently of Core/runtime readiness:
`state` is `current`, `required` or `unknown` (no validated comparison), and
`pending_tools` lists added, changed or removed contract names compared with the
proxy's connection baseline and subsequent host discovery responses. `current`
means no mapping difference under command ID/version/mutation class; it does not
certify full schemas, descriptions, annotations or control definitions. Initial
private negotiation seeds a compatibility baseline, not a host-listing receipt.
Observed pages establish only the returned names under the current recorder;
an isolated terminal page does not prove a complete cursor traversal. Neither
mapping equality nor observed listings prove model-visible host adoption. Required
discovery includes a `recovery` descriptor with `owner: mcp_host`,
`method: tools/list`, empty initial `params`, and instructions to follow every
`nextCursor`. Once higher-priority runtime/server recovery is resolved,
`next_action` is `rediscover_tools`, not null. Partial pages acknowledge only the
tools they contain; removed mappings retire at the end of a listing. Unchanged
tools remain callable.

The first eligible Brain command result per connection/catalogue generation
carries one typed transport-owned `follow_up_required` warning when dispatch
mappings are pending or proxy-code drift has been observed. Subsequent unaffected
Brain results stay quiet; affected refusals retain their diagnostics and controls
retain full status. Actual generation advancement rearms the notice; no-op or
refused recovery does not. Clearing pending mappings suppresses the notice when
no observed proxy-code drift remains. Partial pages leave other mappings pending.
Notice delivery is not host acknowledgement.

The warning uses a bounded affected-name summary and directs the agent to
`brain_proxy_status({})` first for exact scope and prerequisite recovery. It
describes one consistent in-memory observation at emission, without probing
installed runtime/filesystem state on the output path or rewriting the completed
command's status, body, effects or receipt. Assistant JSON, `structuredContent`
and human text carry the same operational observation. Recovery wording comes
from transport state, never retrieved note content, and grants no authority or
consent. A completed command need not be replayed. Notifications, subscription
frames, listings and arbitrary non-Brain results do not receive this notice.

If the installed Core version is unreadable while the runtime remains usable,
status instead names `repair_installation`: repair the installation externally,
then call `brain_proxy_refresh({})` and inspect `brain_proxy_status({})`.
Pending rediscovery stays visible, but does not override that prerequisite.
When no higher-priority recovery applies, an alive child without a validated
command-interface header reports
`server.refresh: interface_unavailable`, the header diagnostic and
`next_action: brain_proxy_refresh`. Calls refused for this condition return
`server_interface_unavailable` with status, not rediscovery advice. Repair the
installation if needed, then refresh and inspect status; host tools/list cannot
repair a missing or incompatible Core interface.

`interface_changed` returns `effects: none`, `retryable: false` and this same
host-owned recovery action without claiming every refusal happened in flight.
After a known upgrade, a recovery warning, this refusal, or a listed tool absent
from the host's callable tools, inspect `brain_proxy_status({})` and follow its
highest-priority action first. If discovery remains pending, use an actually
exposed host tool-refresh facility, or ask the user to reconnect Brain MCP if
refresh is unavailable/unverified. Brain `command_list` and
`command_describe` inspect application contracts, not host discovery, and may
themselves require rediscovery after a change. They do not bypass the contract
guard. Neither `brain_proxy_refresh` nor the stdio-preserving
`brain_proxy_restart` acknowledges host discovery; proxy replacement is not a
host reconnect.
When negotiated client identity identifies Codex, pending discovery guidance
names Brain MCP in Codex. This depends on observed pending dispatch mappings,
not a Codex version: successful host relisting clears the recommendation.
Unidentified clients receive generic host guidance; these observations do not
prove model-cache or schema-only freshness.

`brain_proxy_status({})` remains independent of the application catalogue.
After host rediscovery, inspect the affected host/model-visible declaration,
reformulate and verify an authorised safe affected call. For mutation-only tools,
record execution as unverified; do not run a mutation or seek consent solely as
a discovery probe. Do not replay completed calls; never treat
Core version equality or an unaffected read as proof that changed tools work.
If still blocked, report the diagnostic instead of repeating rediscovery.
Caller-owned envelope faults are rejected before dispatch with a JSON-RPC error,
not rediscovery advice: a missing or wrong `jsonrpc` version returns `-32600`
(invalid request); non-object `params` or `_meta`, a non-string tool name,
caller-supplied reserved Brain metadata, or a tool name this connection was never
advertised returns `-32602` (invalid params). Correct the reported input, not the
host catalogue. Tool `arguments` are validated by the command itself. The always-loaded bootstrap only routes to recovery; detailed
instructions live in the on-demand status/error action. Both have tested token
budgets, alongside complete response-envelope byte limits.

Compatible child replacement retains the proxy's consent owner. New proxy
instances always need fresh exceptional consent. `brain_proxy_restart` preserves
POSIX stdin/stdout through a bounded in-place process replacement. It resolves
and pins the required managed interpreter for both preflight and exec, rechecking
that selection before retirement and exec. It preflights the installed proxy and
child, preserves unread complete/partial input and drains
completed output only after quiescing its producer. The replacement reports
success after its child is ready; the same PID does not mean the same consent
context. Unsupported platforms require host restart. Killing a proxy does not
portably make the host reconnect.

Preflight terminates and reaps its temporary child, including a child rejected
before publication, with a bounded wait before the parent cleans up the process
group. Preparation failures log the exception class, errno, proxy version and
proxy source line without exception messages or private paths; these diagnostics
remain available after in-place replacement. Permission errors are not ignored.

Preflight, busy or output-drain refusal leaves the old instance intact. Known
exec failure resumes the retained image with a fresh owner and an error. If its
runtime is stale, application calls and child launches remain blocked; use a
full host MCP restart. Missing runtime, unsupported handoff and failed preflight
also give explicit host restart guidance. A
consent-cleanup lock timeout ends consent and reports `proxy_owner_cleanup_pending`;
private-directory cleanup may need attention. These post-retirement failures
report `effects: consent_ended`, not an application receipt. A child/output owner
that cannot be proven stopped fails closed without exec. Concurrent installation
changes after preflight remain a reason host restart may be necessary. Loaded
proxy drift adds a bounded `follow_up_required` warning to the model-visible
command envelope as well as the human line, preserving JSON validity. Ordinary
results without proxy drift have no added payload.

Legacy replacements publish `notifications/tools/list_changed`. Modern
`subscriptions/listen` streams belong to the proxy, not to semantic in-flight
work: acknowledgement precedes filtered, subscription-ID-tagged tool/prompt/
resource events. Limits are 128 streams, 64 KiB of subscription state and 1,024
coalesced pending events. Backlog overflow or unexpected internal stream end
explicitly cancels affected streams: re-listen and refetch. Streams survive child
replacement and versioned private POSIX handoff; retired child/stream events do
not reach newly registered streams. Client cancellation and EOF end ownership.
After each confirmed child subscription acknowledgement, level-triggered
refetch notifications cover changes during asynchronous bridge reconfiguration.
Child pipe writes do not hold the output-publication gate.

Catalogue notification/generation does not guarantee model-visible host cache
refresh. When status requires discovery, use host tools/list and inspect the
affected declaration; verify a safe affected call rather than an unrelated read.
If observed refresh still leaves definitions unavailable, report that host boundary
instead of repeating proxy resets. Existing degraded proxies need a first host
reconnect after deployment to acquire this lifecycle. Windows supports controls
and same-runtime activation; image/runtime replacement requires host reconnect.
The CLI resolves the managed runtime on each invocation and needs no persistent
MCP restart; an already-running CLI invocation remains in its original process.
See [DD-074](../architecture/decisions/dd-074-proxy-owned-server-refresh.md) and
[DD-075](../architecture/decisions/dd-075-bounded-proxy-stdio-handoff.md) and
[DD-079](../architecture/decisions/dd-079-unified-proxy-lifecycle.md).

The long-lived child retains authenticated identity and parsed router/index
snapshots between calls. Baseline and explicitly refreshed `command.list`
capability snapshots also survive bounded cursor pagination across calls, and
are discarded when their config, workspace or dependency-tier inputs change.
Derived snapshots publish
only after a bounded stable-signature observation and are made recursively
read-only at the cache boundary; rebuild commands explicitly
invalidate their corresponding snapshot. MCP `session_start` returns after
publishing its human-readable mirror to one bounded latest-value worker. Server
shutdown drains the newest accepted mirror within a fixed deadline without
evicting it; direct CLI/script calls persist that mirror synchronously.

## Frontmatter, type selectors and archive

`artefact.create`, the memory/skill/style variants of `resource.create`, and
`document.update-frontmatter` accept frontmatter as a JSON object. Values are
strings, numbers, booleans, null or flat arrays of those scalars. For example,
`{"tags": ["review"], "summary": "Ready", "owner": null}`. Omitted optional
frontmatter defaults to `{}`; an explicit null object or nested mapping is
invalid. Frontmatter updates require at least one field. Schemas and command
examples use the same codec as request decoding.

Artefact-facing `type`/`artefact_type` fields report the taxonomy's canonical
frontmatter value, such as `temporal/plan`. Creation, listing and search share
the selector resolver: `plan`, `plans`, `temporal/plan` and `temporal/plans` all
select the same installed type. Definition sync/status retain the qualified
bundle `type_key` (`temporal/plans`); status also exposes `artefact_type`, including
library-only definitions. Content and named-resource definition selectors retain
their configured short key (`plans`), as described by their individual schemas.
Custom taxonomy values come from the installed definition, never guessed plurals.

`type.create` and `type.replace` reject naming patterns containing paths,
including `/` or `\` separators, Windows drive prefixes and dot traversal
segments, before writing definitions. Router compilation and cached-pattern
rendering enforce the same filename-only contract. Artefact creation, shaping
transcript writes, document/lifecycle edits, backlink updates, and lifecycle moves
also check fully resolved destinations; moves verify that source resolution stays
in its requested scope. Ordinary content cannot be written into `_Config/` or
dot-prefixed system folders. Definition and named-resource commands retain their
own configuration access.

`artefact.archive` accepts every artefact type independently of lifecycle status,
including statusless Thoughts. It preserves status, intrinsic dated filenames,
ownership and link updates. Restore removes only the archival date prefix and
returns terminal artefacts to their status folder. Archive, restore and delete
refresh active router/lexical state; known partial mutations also reconcile those
indexes. Refresh failure reports committed effects with repair guidance. AUTO
search falls back to lexical when semantic sidecars are unavailable.
`artefact.delete` continues to require administrator authority.

Every lifecycle move — rename, convert, reparent, archive, restore, status
move, temporal relocation and router-backed delete — prunes the owner folders
it vacates through the shared move engine (`rmdir`-only, bounded by type roots
and `_Archive`, never inside `_Assets/Attachments/`). `vault.check` reports any
remaining vacated-empty artefact folder as an `info` finding, and
`artefact.repair` scope `empty_folders` removes them after an explicit dry run.

Portable `vault.check` performs active model-load verification in a bounded
selected-managed-runtime subprocess. Warm-up uses that same managed interpreter
entry point for semantic assets, preserving the virtual environment symlink.
Missing runtime is deferred, model failures remain failures, and explicit warm-up
can retry a ready snapshot whose semantic component is deferred. A valid CLI
JSON result remains authoritative when native libraries write incidental stderr;
identity, schema and exit-category validation still apply. Diagnostic fallback
messages exclude exception contents.

### Cache coherence and router recovery

Artefact creation, lifecycle changes (status, parent, key and naming fields) and
artefact transitions complete router and lexical maintenance under the vault mutation lock. Other
sessions observe the newly persisted files through their existing signature-based
snapshot stores. These operations do not synchronously rebuild semantic embeddings.
If content commits but index maintenance fails, the command reports a partial
outcome; inspect and repair derived state rather than replaying the content change.

Stale-router admission failures carry `details.cache`, `details.reason`,
`details.source_path` where known, and `next_action: runtime.refresh-router`.
They report no effects and require successful repair before another content
attempt. The same diagnostic is projected through CLI and MCP. Router checks,
repair decisions and mutation admission all verify source content; repair also
verifies its postcondition. If sources change during rebuilding, it reports a
partial outcome with recovery guidance instead of looping. `force: true` requests
an unconditional rebuild, not a stronger repair algorithm. `vault.check` (still
version 3) reports a stale lexical index alongside a semantic finding instead of
suppressing it: the lexical repair is automatic and the semantic repair is a
separate, heavier decision.

Router compilation refuses an undecodable router, taxonomy or memory source
with a definite, no-effect `conflict` naming the file and text classifier code.
Its next action is `vault.repair-text` for clear damage or `vault.check` for an
ambiguous diagnosis. Undecodable router and lexical caches carry the distinct
stale reason `unreadable`. A maintenance pass surfaces the failed repair's
message in a `follow_up_required` warning.

### Text encoding diagnosis and repair

Read-only `links.check` continues over readable sources without converting them. Non-standard sources remain reported by `vault.check`. Mutation planning for `links.fix`, including dry runs, requires every selected source to be inspectable and refuses by name before effects.

`vault.check` reports clear `text_encoding` findings and ambiguous `unreadable_file` failures before the router gate. `vault_repair-text` accepts one optional `paths` array of vault-relative files, requires maintainer authority and content authorisation, and supports the normal dry-run preview. It fixes UTF-8/UTF-16/UTF-32 byte-order marks and previews a clear truncated final UTF-8 character. It never guesses legacy encodings or repairs content unattended. Results enumerate committed paths; changed candidates are skipped and write failures retain successful effects as partial results.
