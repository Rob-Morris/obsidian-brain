# MCP Command Interface

Brain Core 0.55 exposes the selected-Brain application catalogue as granular MCP tools. `server.py` is a small composition root: it registers catalogue projections, composes trusted local invocation context and installs the replacement-proxy protocol gate. Semantic logic belongs to application commands, not the MCP adapter.

## Tool grammar and discovery

An MCP-eligible command exposes its canonical `<noun>.<verb>` identifier directly. Examples include:

- `session.start`
- `command.list`
- `artefact.read`
- `artefact.create`
- `invocation.read`

The MCP server name remains `brain`; it is not repeated inside every tool name. Clients may encode dots and hyphens internally when projecting MCP tools into a model API. That private encoding does not change the raw MCP name or the command contract.

The released application catalogue owns 86 commands and currently projects 78 of them to MCP. Those numbers and every tool schema are checked from the authoritative catalogue; this document deliberately does not duplicate the full list.

Start a session with `session.start`. Discover commands with `command.list`, and inspect one exact request/result contract with `command.describe`. Default discovery uses static catalogue facts and does not probe optional providers; request an explicit refresh only when current provider availability matters.

The former aggregates and variants are removed: `brain_init`, `brain_session`, `brain_read`, `brain_create`, `brain_edit`, `brain_define`, `brain_move`, `brain_action`, `brain_process` and the other flat v1 tools are not aliases and are not callable.

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

Warnings, stable error codes, typed details and next actions survive every projection. A mutating child loss is never blindly replayed. When no conclusive receipt exists, the result is non-retryable `command_outcome_unknown` with an outcome reference; query it with `invocation.read`.

## Authority profiles

Profiles authorise exact granular tool names. The built-in `reader`, `contributor`, `maintainer`, `operator` and `administrator` projections are cumulative and derived from catalogue authority metadata. Their MCP allow-lists contain 37, 63, 74, 77 and 78 tools respectively. Upgrade migrates exact legacy built-ins and expands explicit custom aggregate grants once; there is no runtime aggregate fallback after cutover. A command is authorised before its request is dynamically resolved.

## Metadata and client budgets

Tool summaries are short and contain no parameter manuals. Every reachable request property has a schema-resident description. Stable nested shapes remain structural rather than opaque objects. MCP safety annotations are derived from catalogue effect and retry metadata:

- read-only commands set `readOnlyHint`;
- effect-bearing commands set `destructiveHint` as appropriate;
- safe retry commands set `idempotentHint`;
- `openWorldHint` is false.

The supported-client gate uses real Claude Code and Codex CLI projections with pinned capture tooling. The catalogue must stay within 16,384 deterministic tokens per supported client. Ordinary tools remain within 512 tokens; the explicitly cohesive `document.edit` schema may use up to 2,048 so its resource and change variants remain structurally strict rather than opaque or artificially split.

## Proxy replacement protocol

The server advertises `brain.command-interface-header/1` during initialise. It binds proxy protocol range, interface epoch, catalogue and result schemas, catalogue fingerprint, and the exact tool-to-command/version/mutation mapping.

Proxy 0.7.0 supplies protocol 2. On replacement it:

1. rejects an incompatible server before tool lookup;
2. records accepted calls before dispatch;
3. replays planned drift only when command identity, version and mutation class remain compatible;
4. retries an unexpected read orphan once;
5. never replays an unexpected mutation, resolving it through outcome receipts instead.

Clients must restart and re-discover tools after the 0.55 cutover. There is no request translation map or legacy server mode.
