# MCP Command Interface

Brain Core exposes the selected-Brain application catalogue as granular MCP tools. `server.py` is a small composition root: it registers catalogue projections, composes trusted local invocation context and installs the replacement-proxy protocol gate. Semantic logic belongs to application commands, not the MCP adapter.

The server is long-lived, so work whose memory would otherwise stay resident for the rest of the session runs elsewhere: `session.start` warm-up spawns a detached worker, and the semantic maintenance commands (`retrieval.repair-semantic`, `retrieval.rebuild-semantic`, `retrieval.enable`) run their lifecycle owner in a fresh interpreter through `_lifecycle/fresh_interpreter.py` while the calling process holds the vault mutation lock. Query encoding for `artefact.search` stays in-process on the CPU-only `onnxruntime` encoder.

The transport boundary uses the official Python `MCPServer` API at the exact
reviewed `mcp==2.0.0` pin. It continues to serve supported 2025 protocol clients;
the dependency admission and runtime policy are recorded in
[MCP SDK 2.0 Dependency Admission](../architecture/mcp-sdk-2-dependency-admission.md).

## Tool grammar and discovery

An MCP-eligible command projects its canonical `<noun>.<verb>` ID to the raw
MCP name `<noun>_<verb>`. Only the single dot changes; hyphens are preserved.
For example, `session.start` becomes `session_start` and
`document.update-frontmatter` becomes `document_update-frontmatter`.

The MCP server name remains `brain`. This projection is global, reversible and
collision-free under the command grammar. Raw dotted names are not aliases.
Command IDs in results, discovery arguments, permission profiles and access
requests remain dotted; CLI commands remain `brain <noun> <verb>`.
Interface epoch 3 requires clients to reconnect and rediscover tools after
upgrade. Project registration repair updates exact managed bootstrap lines.

The application catalogue owns the installed command inventory and marks each projection explicitly. A running server exposes only the MCP-eligible commands within the authenticated profile ceiling. Exact catalogue and profile counts are generated and checked from the authoritative catalogue; use MCP discovery or `command.list` for the selected installation rather than treating prose counts as a compatibility contract.

Start an MCP session with `session_start`. On a cold Brain it starts or joins background warm-up and returns the shared `brain.runtime-status/1` snapshot with guidance to poll `runtime.status`; retry `session.start` when ready. `runtime.status` is a cheap read-only observation, while `runtime.warmup` explicitly starts, joins or retries warm-up. Discover commands with `command.list`, and inspect one exact request/result contract with `command.describe`. Default discovery uses static catalogue facts and does not probe optional providers; request an explicit refresh only when current provider availability matters.

`command.list` v4 returns a brief view by default: command ID/version,
summary, required authority, effect class, transport eligibility, initial class and
current `access` (`authorised`, `authorisation_required` or `denied`). Availability
is a provider observation, separate from authorisation. One batched access
observation serves each discovery page. Commands above the credential maximum
remain discoverable as `static_disclosure: true`: installed metadata and an
administrative route only, with dynamic availability unknown and no provider
probes. They are not callable or requestable through the current credential.

The default page has at most 25 entries. Both `brief` and `detailed` views
stop before the canonical JSON envelope exceeds 16,000 UTF-8 bytes, leaving
headroom below the observed 20,000-byte client text limit. This is Brain's
compatibility budget, not an MCP protocol limit. Pass `next_cursor` as `cursor`
with the same filters to continue; a page may contain fewer than `page_size`
entries. Shared catalogue identity and availability freshness occur once per
page. Use `view: "detailed"` for provider/projection/retry/lifecycle metadata;
`command.describe` v4 retains full schemas and examples and includes access.
The byte budget applies to list pages, not arbitrary command descriptions.

Related named resources share the strict `resource.create`, `resource.list`, `resource.read` and `resource.search` tools. Each has a shallow resource or target discriminator and a closed resource-specific result union. Presentation and printable output similarly share `shaping.render` with a strict `output.kind` branch. These commands replace target-only leaves without introducing a generic invocation gateway.

`shaping.start` opens or continues a taxonomy-declared shaping session. The
default `transition` behaviour enters `status: shaping`; discovery-only
`preserve` leaves an enduring non-terminal status unchanged and rejects
terminal targets before mutation. Its result reports the effective status
behaviour and whether status changed. When several linked same-day transcripts
name the source, it continues the one with the widest distinct resolved source
set and rejects ties; path and basename spellings of the same file count once.

`artefact.migrate-naming`, `retrieval.enable` and `workspace.repair-registry` remain available through the CLI, direct script and typed Python interfaces but are deliberately not registered in agent-facing MCP. Their catalogue records state the local-administration reason.

Explicit refresh enforces provider-specific and aggregate deadlines. Timed-out probes report `unknown`; a fixed process-wide daemon bound prevents repeated MCP calls from accumulating unbounded stuck probes or delaying CLI process exit.

The former aggregates and variants are removed: `brain_init`, `brain_session`, `brain_read`, `brain_create`, `brain_edit`, `brain_define`, `brain_move`, `brain_action`, `brain_process` and the other flat v1 tools are not aliases and are not callable.

## Bounded bootstrap and document reads

`session.start` v6 returns the complete lean bootstrap when its canonical
JSON envelope fits 16,000 UTF-8 bytes. It advertises the installed type count
and shared retrieval routes; use `resource.list` with `resource: "type"` and
`resource.read` to learn a type before creating it. Core-document references
carry title and path; load them with `vault.read-file`.

If the full bootstrap exceeds that budget, the same canonical markdown used
by `.brain/local/session.md` is returned in pages. Repeat `session.start` with
`cursor: range.next_cursor` until `bootstrap_complete` is true, before ordinary
work. Mandatory preferences, rules and triggers are preserved across the pages.

`artefact.read` v4, `vault.read-file` v2 and document variants of `resource.read`
v3 return `revision` and `range` alongside their content. Repeat the same
request with `cursor: range.next_cursor` until that cursor is null. Each page
fits the same 16,000-byte envelope budget; `max_characters` optionally limits
the requested window to 1–12,000 Unicode characters. Offsets describe text
after newline normalization; revisions identify the persisted source bytes.
A changed source returns `conflict`: restart without a cursor. These bounds
apply before MCP, CLI and Python projections, so no transport cuts serialized
JSON or silently drops the tail. Metadata-only resource variants remain intact.

## Permissions and instance authorisation

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
- `access.status(command_id=...)` returns canonical `command_review` for consent
  to that exact command throughout this Brain and current context. Its grants,
  operations and initial views are paged; operation rows recover preparations
  whose response was lost.
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

## Request contract

Each tool exposes the exact strict object schema derived from its sealed command request. MCP arguments are the semantic request itself, plus the reserved optional `brain_operation` selector on ordinary tools. There is no generic invocation envelope or caller-supplied command version. Nested consent/preparation variants have explicit scope discriminators. Unknown fields fail before executor entry.

The schema preserves required versus optional fields, explicit nullable fields, enums, nested object structure, descriptions and `additionalProperties: false`. Use the schema returned by MCP discovery or `command.describe`; do not infer one command's fields from a neighbouring command.

Large or retry-sensitive content uses the explicit staging commands where the described command accepts a staged handle. Caller-owned file paths are not an MCP content source because the server may not share the caller's filesystem.

## Trusted invocation context

MCP callers provide only semantic fields. The adapter derives selected Brain, workspace binding, authenticated profile, authority, dependency tier, providers, dry-run facilities and receipt storage from trusted server/proxy state.

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

Warnings, stable error codes, typed details and next actions survive every projection. A mutating child loss is never blindly replayed. When no conclusive receipt exists, the result is non-retryable `command_outcome_unknown` with an outcome reference; query it with `invocation.read`. Lookup never creates receipt storage, locks files or deletes expired records; expiry is reported logically, while writes and explicit maintenance own cleanup.

## Authority profiles

Profiles authorise exact command names. The built-in `reader`, `contributor`, `maintainer`, `operator` and `administrator` ceilings are cumulative, while MCP exposes only each ceiling's eligible subset. Profile migration preserves an older broad document-mutation grant by granting all four replacements; custom profiles otherwise retain only their explicit grants. There is no runtime fallback or compatibility alias after cutover. Credential permission and current-authorisation checks both occur before dynamic request resolution.

## Metadata and client budgets

Tool summaries are short and contain no parameter manuals. Every reachable request property has a schema-resident description. Stable nested shapes remain structural rather than opaque objects. MCP safety annotations are derived from catalogue effect and retry metadata:

- read-only commands set `readOnlyHint`;
- effect-bearing commands set `destructiveHint` as appropriate;
- safe retry commands set `idempotentHint`;
- commands that can contact an external service set `openWorldHint`; all others
  leave it false.

Real Claude Code, Codex CLI and Grok captures verify fresh and resumed discovery, bootstrap, read and authorised mutation through the same portable MCP names. Evidence lives in `tests/fixtures/command_interface_real_client_evidence_v1.json` and `tests/fixtures/command_interface_grok_client_evidence_v1.json`. Deterministic metadata budgets use the Claude Code and Codex projections with pinned capture tooling. The catalogue must stay within 16,384 deterministic tokens per supported client. Ordinary tools remain within 512 tokens; the explicitly cohesive `document.structured-edit` and `resource.create` schemas may use up to 3,072 so strict structural variants remain typed rather than opaque or artificially split.

## Proxy replacement protocol

The server advertises `brain.command-interface-header/1` in discovery and initialize capabilities. It binds proxy protocol range, interface epoch, catalogue and result schemas, catalogue fingerprint, and the exact tool-to-command/version/mutation mapping.

The installed proxy supplies protocol 3. It follows the host's protocol era:
legacy clients initialize normally, while modern connections use a private
`server/discover` before the first host request, even if the host omits discovery.
The child SDK fixes each stdio connection to one era. The proxy re-establishes
that era and its validated command header on every replacement. It:

1. rejects an incompatible server before tool lookup;
2. records accepted calls before dispatch;
3. replays planned drift only when command identity, version and mutation class remain compatible;
4. retries an unexpected read orphan once;
5. never replays an unexpected mutation, resolving it through outcome receipts instead.

The 0.64.2 transport fix requires restarting MCP. Older proxies are gated
before tool lookup; their restart instruction precedes the JSON fallback so
legacy drift decoration cannot corrupt it.

Clients must restart and re-discover tools after the 0.55 cutover. There is no request translation map or legacy server mode.

After startup, every generated tool handler checks the installed `.brain-core/VERSION` before composing trusted context or entering an executor. Drift exits with the proxy's distinguished code 10 so replacement and compatibility-checked replay occur within the triggering call.

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
