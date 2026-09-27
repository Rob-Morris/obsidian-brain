# Session Core

`.brain-core/` is read-only. Never edit files here directly — changes will be overwritten on version upgrade.

## Using Command Discovery

Before concluding a command is missing, use filtered discovery, for example
MCP `command_list({"domain":"document"})`. Follow `next_cursor` with the same
filters for more results; use `projection:"mcp"` to restrict the transport.
Search `query` accepts command IDs, supported MCP/CLI spellings and summary text.
Use `command_describe({"target_command_id":"document.structured-edit"})` for its
schema and examples, not a full MCP schema dump.

Each entry gives `mcp_tool` and `cli_argv` (arguments after `brain`); null means
that transport is unsupported. Use the exact returned name. Canonical IDs such
as `document.structured-edit` identify commands in discovery and consent;
MCP changes the dot to `_`, preserving hyphens within words. Clients may wrap
tool names; use their exposed callable for that exact server tool.

`projections` describes transport support, `availability` describes runtime
dependencies, and `access` describes current authorisation. An unknown runtime
observation is not a missing command or a permission denial. A listed command
can be denied; `static_disclosure` is metadata, not permission to call it.
Prefer connected MCP for supported vault operations. CLI is appropriate for
local-only commands or unavailable MCP, never to bypass a denial.

## Key Idea

All content in the vault is an **artefact**:

1. **Living** (in vault root) — evolve over time, source of truth
2. **Temporal** (in `_Temporal/`) — bound to a moment, historic record

System folders start with `_` or `.` — these are infrastructure, not artefacts. The top-level `_Archive/` directory holds soft-deleted artefacts, excluded from the vault's active namespace.

The system is self-extending. When content has no appropriate home, add a new artefact type following documented procedures rather than forcing it into an existing folder.

## Principles

1. **Every file belongs in a folder** — no content files in the vault root
2. **Self-extending vault** — when content has no home, add a new artefact type before creating the file
3. **Always link related things** — connect artefacts with wikilinks when they relate by origin, topic, or reference
4. **Save each step before building on it** — multi-stage work produces an artefact at each stage
5. **Keep instruction files lean** — routing tables, not encyclopaedias; detail lives in core docs
6. **Let structure express ownership** — use canonical parent relationships for containment and links or tags for association
7. **Separate concerns** — one topic per artefact; split when a file serves two purposes
8. **Actively seek signal** — notice gaps, ambiguities, and opportunities; ask small questions at natural moments; capture answers as artefacts

## Completing Bootstrap

Finish `session.start(cursor=range.next_cursor)` pages until
`bootstrap_complete` before ordinary work. For document reads, repeat the
same command and reference with `range.next_cursor` until null. On revision
conflict, restart without a cursor.

## Authorisation

Normal content starts authorised within credential permissions. `authorised`
means Brain consent permits invocation, not that the user's task asks for it.
For `authorisation_required`, inspect MCP `access_status` with the canonical
`target_command_id`, then choose the intended consent scope explicitly:

- One operation: `access_prepare` takes a `preparation` object with
  `kind:"operation"`, `command_id` and `arguments`. Then pass `access_request`
  a `consent` object with `scope:"operation"` and the returned `operation_id`,
  `digest` and `review`. After consent, select the operation on the target call
  using MCP `brain_operation` or CLI `--operation`; even a successful read
  spends that operation consent.
- Command-wide consent: only when that broader scope is intended, pass
  `access_request` a `consent` object with `scope:"command"`, `command_id`, and
  `review` set to the exact `command_review` from status. Use `command_describe`
  for full request shapes; CLI equivalents are `brain access <verb>`.

Consent belongs to this Brain and its owning context (MCP instance or managed
CLI job) and ends with that context or revocation. It cannot raise the credential
permission ceiling. For `denied`,
inspect the reported boundary: permission changes require an appropriately
authorised administrator, while policy/context restrictions have their own
reported remedies. Never auto-request/retry a denial or switch transports to
evade it. Recover uncertain calls with `invocation_read` before retrying.

## Core Docs

- [Add types, memories and principles](standards/extending/README.md)
- [Artefact library and installation](artefact-library/README.md)
- [Workflow triggers](triggers.md)
- [Folder colours](colours.md)
- [Plugins for external tools](plugins.md)

## Standards

- [Naming conventions](standards/naming-conventions.md)
- [Canonical keys](standards/keys.md)
- [Wikilinks to existing artefacts](standards/wikilinks.md)
- [Link maintenance](standards/linking.md)
- [Provenance and lineage](standards/provenance.md)
- [Archiving](standards/archiving.md)
- [Hub pattern](standards/hub-pattern.md)
- [Subfolders](standards/subfolders.md)
- [Shaping](standards/shaping.md)
- [Preferences and gotchas](standards/user-preferences.md)

Always:
- Use `artefact.list` rather than `artefact.search` when enumerating or filtering artefacts by type, date range, or tag — list is exhaustive; search is relevance-ranked and suited to content queries.
