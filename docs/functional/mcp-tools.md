# MCP Command Interface

Brain Core exposes the selected-Brain application catalogue as granular MCP tools. `server.py` is a small composition root: it registers catalogue projections, composes trusted local invocation context and installs the replacement-proxy protocol gate. Semantic logic belongs to application commands, not the MCP adapter.

The server is long-lived, so work whose memory would otherwise stay resident for the rest of the session runs elsewhere: `session.start` warm-up spawns a detached worker, and the semantic maintenance commands (`retrieval.repair-semantic`, `retrieval.rebuild-semantic`, `retrieval.enable`) run their lifecycle owner in a fresh interpreter through `_lifecycle/fresh_interpreter.py` while the calling process holds the vault mutation lock. Query encoding for `artefact.search` stays in-process on the CPU-only `onnxruntime` encoder.

The transport boundary uses the official Python `MCPServer` API at the exact
reviewed `mcp==2.0.0` pin. It continues to serve supported 2025 protocol clients;
the dependency admission and runtime policy are recorded in
[MCP SDK 2.0 Dependency Admission](../architecture/mcp-sdk-2-dependency-admission.md).

## Tool grammar and discovery

An MCP-eligible command exposes its canonical `<noun>.<verb>` identifier directly. Examples include:

- `session.start`
- `command.list`
- `artefact.read`
- `artefact.create`
- `invocation.read`

The MCP server name remains `brain`; it is not repeated inside every tool name. Clients may encode dots and hyphens internally when projecting MCP tools into a model API. That private encoding does not change the raw MCP name or the command contract.

The application catalogue owns the installed command inventory and marks each projection explicitly. A running server exposes only the MCP-eligible commands within the authenticated profile ceiling. Exact catalogue and profile counts are generated and checked from the authoritative catalogue; use MCP discovery or `command.list` for the selected installation rather than treating prose counts as a compatibility contract.

Start a session with `session.start`. On a cold Brain it starts or joins background warm-up and returns the shared `brain.runtime-status/1` snapshot with guidance to poll `runtime.status`; retry `session.start` when ready. `runtime.status` is a cheap read-only observation, while `runtime.warmup` explicitly starts, joins or retries warm-up. Discover commands with `command.list`, and inspect one exact request/result contract with `command.describe`. Default discovery uses static catalogue facts and does not probe optional providers; request an explicit refresh only when current provider availability matters.

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

## Ceiling, active grant and elevation

Authentication establishes an immutable command ceiling for the MCP process. `tools/list`, the proxy interface header, `command.list` and `command.describe` omit commands above that ceiling. Changing credentials or the ceiling requires proxy replacement and client re-discovery; an ordinary elevation lease does not change tool definitions.

The active grant starts at `defaults.access.initial_profile`, which is `reader` unless configured otherwise and is always intersected with the ceiling. Commands within the ceiling but outside the active grant return `authority_denied` with `boundary: active_grant`, `requestable: true` and an `access.request` next action. Commands above the ceiling are not requestable.

- `access.status` reads the initial grant, active commands, inactive ceiling commands, leases and pending requests without writing state.
- `access.request` requests one exact command or a sorted coherent set of at most eight. Leases have absolute expiry and may have a bounded use count.
- `access.reduce` revokes exact leases or commands, or returns to the initial grant.

`vault.access.elevation_policy` is `automatic`, `external` or `denied`. Automatic elevation records intent but is not a security boundary against the authenticated agent. External elevation creates a pending request that only the CLI-only `brain access approve` launcher command can approve using a separately supplied registered operator secret whose profile covers every requested command; the requesting principal cannot approve its own request. Lease authority is checked on every call and a use is consumed only after request and capability preflight. Expiry or schema removal is never relied on for enforcement.

This deliberately separates authorisation from client-side lazy loading. Claude Code may defer schemas and handles catalogue-change notifications; current Codex clients do not provide the same hot-refresh guarantee. Brain therefore keeps the ceiling-visible catalogue deterministic and uses call-time leases instead of mutating `tools/list` during a session.

## Request contract

Each tool exposes the exact strict object schema derived from its sealed command request. MCP arguments are the semantic request itself—there is no generic `request` envelope, action discriminator, command-name field or caller-supplied command version. Unknown fields fail before executor entry.

The schema preserves required versus optional fields, explicit nullable fields, enums, nested object structure, descriptions and `additionalProperties: false`. Use the schema returned by MCP discovery or `command.describe`; do not infer one command's fields from a neighbouring command.

Large or retry-sensitive content uses the explicit staging commands where the described command accepts a staged handle. Caller-owned file paths are not an MCP content source because the server may not share the caller's filesystem.

## Trusted invocation context

MCP callers provide only semantic fields. The adapter derives selected Brain, workspace binding, authenticated profile, authority, dependency tier, providers, dry-run facilities and receipt storage from trusted server/proxy state.

The local proxy assigns every accepted call a bounded `mcp-...` invocation ID in MCP request metadata before child dispatch. The server refuses calls without that proxy-owned identity. Configure an operator key as trusted `BRAIN_OPERATOR_KEY` server environment when a non-default profile is required; it is not a semantic tool argument.

## Structural results

Every tool returns the same `brain.command-result/1` structure in `structuredContent`, plus concise text derived from that structure:

- `ok`: typed `result`, no error;
- `partial`: an error plus the exact known `committed_effects`;
- `error`: no result and `effects: none` or `effects: unknown`.

Warnings, stable error codes, typed details and next actions survive every projection. A mutating child loss is never blindly replayed. When no conclusive receipt exists, the result is non-retryable `command_outcome_unknown` with an outcome reference; query it with `invocation.read`. Lookup never creates receipt storage, locks files or deletes expired records; expiry is reported logically, while writes and explicit maintenance own cleanup.

## Authority profiles

Profiles authorise exact command names. The built-in `reader`, `contributor`, `maintainer`, `operator` and `administrator` ceilings are cumulative, while MCP exposes only each ceiling's eligible subset. Profile migration preserves an older broad document-mutation grant by granting all four replacements; custom profiles otherwise retain only their explicit grants. There is no runtime fallback or compatibility alias after cutover. Ceiling and active-grant checks both occur before dynamic request resolution.

## Metadata and client budgets

Tool summaries are short and contain no parameter manuals. Every reachable request property has a schema-resident description. Stable nested shapes remain structural rather than opaque objects. MCP safety annotations are derived from catalogue effect and retry metadata:

- read-only commands set `readOnlyHint`;
- effect-bearing commands set `destructiveHint` as appropriate;
- safe retry commands set `idempotentHint`;
- commands that can contact an external service set `openWorldHint`; all others
  leave it false.

The supported-client gate uses real Claude Code and Codex CLI projections with pinned capture tooling. The catalogue must stay within 16,384 deterministic tokens per supported client. Ordinary tools remain within 512 tokens; the explicitly cohesive `document.structured-edit` and `resource.create` schemas may use up to 3,072 so strict structural variants remain typed rather than opaque or artificially split.

## Proxy replacement protocol

The server advertises `brain.command-interface-header/1` during initialise. It binds proxy protocol range, interface epoch, catalogue and result schemas, catalogue fingerprint, and the exact tool-to-command/version/mutation mapping.

The installed proxy supplies protocol 2. On replacement it:

1. rejects an incompatible server before tool lookup;
2. records accepted calls before dispatch;
3. replays planned drift only when command identity, version and mutation class remain compatible;
4. retries an unexpected read orphan once;
5. never replays an unexpected mutation, resolving it through outcome receipts instead.

Clients must restart and re-discover tools after the 0.55 cutover. There is no request translation map or legacy server mode.

After startup, every generated tool handler checks the installed `.brain-core/VERSION` before composing trusted context or entering an executor. Drift exits with the proxy's distinguished code 10 so replacement and compatibility-checked replay occur within the triggering call.

The long-lived child retains authenticated identity and parsed router/index
snapshots between calls. Baseline and explicitly refreshed `command.list`
capability snapshots also survive bounded cursor pagination across calls, and
are discarded when their config, workspace or dependency-tier inputs change.
Derived snapshots publish
only after a bounded stable-signature observation and are made recursively
read-only at the cache boundary; rebuild commands explicitly
invalidate their corresponding snapshot. MCP `session.start` returns after
publishing its human-readable mirror to one bounded latest-value worker. Server
shutdown drains the newest accepted mirror within a fixed deadline without
evicting it; direct CLI/script calls persist that mirror synchronously.
