# Brain Reference

This page is the stable user-facing reference for Brain Core 0.70.4 and CLI 4.0.3. Exact command schemas, examples and availability come from the installed Brain rather than a duplicated hand-maintained inventory.

Optional [managed approvals](../functional/approvals.md) cover normal reads/writes
in selected clients and surfaces. They preserve user overrides and do not change
Brain permissions or exceptional consent.

Document changes use a read–mutate loop. Read an editable artefact or named
resource, retain its returned `revision`, then pass that value as
`expected_revision` to `document.write-body`, `document.replace-text`, `document.structured-edit`, or
`document.update-frontmatter`. A conflict means the bytes changed; re-read and
reapply the intended change. Use `write` for whole-body text, `patch` for exact
literal replacement, `edit` for headings/callouts/document intro, and
`update-frontmatter` for metadata only.

## Vault model

All user content is an artefact:

- **Living artefacts** evolve over time and live in configured top-level type folders.
- **Temporal artefacts** record a moment and live below `_Temporal/`, optionally beneath a living owner chain.
- `_Config/` contains user-owned taxonomy, templates, skills, memories, styles and triggers.
- `.brain-core/` is versioned application code and must not be edited directly.
- `.brain/` is Brain-owned state; `.brain/local/` is machine-local and gitignored.
- `_Archive/` holds deliberately removed artefacts outside the active namespace.

The compiled `_Config/router.md` is the concise navigation map. Taxonomy definitions under `_Config/Taxonomy/` own artefact contracts; templates under `_Config/Templates/` own initial document shapes.

## Canonical command grammar

Every semantic operation has one dot-separated command identifier. Application commands use the corresponding noun/verb CLI spelling; launcher commands own concise domain-explicit entry points:

```text
artefact.read       brain artefact read
artefact.create     brain artefact create
vault.check         brain vault check
brain.doctor        brain doctor
brain.resolve       brain resolve
runtime.inspect     brain runtime inspect
```

Selected-Brain application commands can project to MCP, CLI, the direct command script and typed Python when the catalogue marks that projection eligible. Machine-global launcher commands project only where their owner and locality permit. The projections share one request type, application owner and structural `brain.command-result/1` result.

Discover the installed interface instead of guessing:

```bash
brain command list --json
brain command list --owner application --json
brain command describe artefact.create --json
brain command describe brain.upgrade --owner launcher --json
```

Lists default to 25 concise entries per owner; follow the returned cursors for more. Use `--view detailed` for full summary metadata. Application entries distinguish current access from dependency availability.

Descriptions include the exact strict request schema, minimal example, result schema, stable error/warning codes, dependency tier, locality, providers, authority, effects, retry class, projection eligibility and current availability. Default discovery is static and does not probe optional providers; request an explicit refresh only when current provider availability matters.

## MCP

MCP tool names replace the canonical command ID’s dot with an underscore:

- `session.start` → `session_start`
- `command.list` → `command_list`
- `command.describe` → `command_describe`
- `artefact.read` → `artefact_read`
- `artefact.create` → `artefact_create`
- `vault.check` → `vault_check`

Each granular tool exposes its own top-level request fields. There is no generic `request` envelope and no compatibility aggregate. The removed 1.x tools—including `brain_session`, `brain_read`, `brain_create`, `brain_edit`, `brain_define`, `brain_move`, `brain_action` and `brain_process`—are not aliases.

Start with `session_start`, then use `command_list` and `command_describe` for bounded discovery. For example, inspect `artefact.create` before supplying its fields to the `artefact_create` tool.

`shaping.start` opens or continues a taxonomy-declared shaping session after
the shaping skill selects its mode. It applies the taxonomy's status behaviour:
ordinary contracts enter `shaping`, while discovery-only preserving contracts
leave an enduring non-terminal status unchanged and refuse terminal targets.

The MCP server derives registrations, schemas, descriptions and tool hints from the selected Brain's catalogue, within the credential's permissions. Initial authorisation normally includes content work; it can be configured as read-only or an explicit command set. Exceptional operations use `access.prepare` for the exact operation and `access.request` for explicit consent, or request blanket consent for one command using its canonical review from `access.status`. A harness can approve the consent tool manually or automatically. `access.reduce` only narrows authorisation. Consent lasts for this Brain and MCP instance or explicit CLI job; new instances require fresh exceptional consent.

Before accepting a command, the proxy checks installed Core drift and refreshes an idle child after validating its replacement. `brain_proxy_status` and `brain_proxy_refresh` are no-argument MCP transport tools available even without a healthy child; they are separate from application `runtime.status`. In-flight work returns busy rather than being interrupted. `brain_proxy_restart` explicitly loads the installed proxy on POSIX while retaining stdio; it ends exceptional consent. Busy output/work is refused. Unchanged code/runtime is a no-op only with a validated child; otherwise restart attempts same-runtime activation, including after external repair of reachable startup failures. Unknown or changed bindings cannot retarget the session. Preparation leaves status/ping responsive. Windows image/runtime replacement requires host restart. Modern subscriptions survive recovery, but stale host tool caches may still need rediscovery or reconnect. A lost dispatched call, including a residual drift race, triggers owned outcome recovery, never automatic replay. `invocation.read` reports execution independently from file effects, including for observations. An absent or incomplete receipt means the outcome remains unknown. Above-permission static command metadata is discoverable, with CLI administration guidance; discovery cannot expand permissions.

See [MCP tools](../functional/mcp-tools.md) for transport, protocol and result details.

## CLI

The installed `brain` command is a machine-global CLI 3 bootloader plus a versioned distribution. It resolves exactly one selected local Brain and executes either:

- a launcher-owned machine command, without importing selected-Brain application semantics; or
- an application command through that selected Brain's own `command.py`.

Supply semantic fields as a strict JSON object:

```bash
brain artefact read --request-json '{"reference":"design/brain"}' --json
brain vault check --request-json '{"actionable":true}' --json
brain upgrade --vault /path/to/brain \
  --request-json '{"acknowledge_global_cli_cutover":true}' --json
```

Successful upgrade includes a recorded ready runtime, so the first ordinary
`session.start` does not have to initiate overlapping warm-up work. A warm-up
failure returns the known-partial exit category with recovery commands. Shared
runtime cleanup remains explicit: upgrade only recommends `brain runtime
remove-orphans --dry-run` and `brain runtime remove-orphans` after read-only
inspection proves candidates.

If the Brain/CLI cutover commits but an old CLI backup cannot be removed, the
upgrade remains committed and returns a known partial result. JSON output names
the exact absolute paths in `error.details.recovery_paths`; remove those backup
paths only after confirming the installed CLI works.

Use `--request-json -` to read one object from stdin. `--vault`, `--brain` and workspace binding select the Brain; they are adapter inputs, never semantic command fields. `--dry-run` is trusted execution context. Exit categories are stable: 0 success, 1 known partial, 2 request/domain failure, 3 authority/capability unavailable, and 4 infrastructure failure or unknown mutation outcome.

CLI 3 refuses application discovery against a pre-0.55 Brain. Launcher discovery and recovery remain available so the operator can run the checked upgrade. See [CLI](../functional/cli.md).

Use `brain session run -- program` for unattended work that needs one shared consent context across descendant CLI calls. Ordinary standalone content commands work within initial policy. Exceptional standalone requests name this explicit job route. The job does not request consent automatically; scripts must inspect the denial, request the intended scope explicitly, then invoke it with `--operation` for specific consent. See [CLI jobs and consent](../functional/cli.md#cli-jobs-and-consent) for platform limits and deliberate Python subprocess forwarding. Permission administration uses the separate CLI-only `brain permission set-profile` command.

## Direct script and Python projections

For selected-Brain automation without the global CLI:

```bash
python3 .brain-core/scripts/command.py artefact read \
  --request-json '{"reference":"design/brain"}' --json
```

This direct projection uses the same catalogue, resolver, invocation boundary and structural result as MCP and CLI. It is not a second command grammar. The typed Python boundary is `CommandApplication(context).invoke(request)` with sealed request types; callers compose trusted context outside `_application`.

Legacy top-level operation scripts are internal implementation components or removed surfaces, not supported semantic entry points. Platform install and pre-cutover recovery launchers remain explicit exceptions. See [Scripts](../functional/scripts.md).

## Dependency and availability model

Dependency tier, locality and providers are independent:

- **bootstrap** commands use the stdlib-safe base needed for discovery and recovery;
- **portable** commands use the portable selected-Brain runtime;
- **managed** commands require the managed runtime;
- locality distinguishes selected-Brain application work from machine-global launcher work;
- required providers block execution when absent; optional providers may enrich an otherwise complete result.

Adapters never silently provision dependencies, switch Brains or elevate authority. Unavailable results state the required/current tier, missing provider or capability, freshness, recoverability and one structured next action.

Explicit availability refresh respects both provider and aggregate deadlines. Timed-out probes degrade to `unknown`, run behind a fixed process-wide background bound and cannot keep a completed CLI process alive.

## Configuration and profiles

Configuration merges:

1. `.brain-core/defaults/config.yaml` — shipped defaults;
2. `.brain/config.yaml` — shared vault configuration;
3. `.brain/local/config.yaml` — machine-local overrides for the `defaults` zone only.

The `vault` zone is shared authority and cannot be overridden locally. The `defaults` zone uses type-aware scalar, boolean, list and mapping merges. Malformed configuration and unknown profile tools fail closed.

MCP reads an operator key from trusted server configuration (`BRAIN_OPERATOR_KEY`); CLI accepts `--operator-key` as adapter input. Neither is part of a semantic request. Store only the key's SHA-256 hash in shared configuration; keep the secret in local configuration or a secrets manager. Generate a key with `brain operator generate-key`.

See [Configuration](../functional/config.md).

## Workspace binding

`.brain/local/workspace.yaml` belongs to the connecting workspace, not the vault. It records the local Brain identity, workspace slug and filing defaults. Configure it on the agent's machine:

```bash
brain workspace setup --vault /path/to/brain \
  --workspace /absolute/path/to/workspace --request-json '{}' --json
```

MCP cannot configure the connecting agent's local filesystem. Remote-Brain transport and gateway hosting are separate from this local command architecture.

Setup ensures a canonical hub before saving the local binding and reports the two
outcomes separately. `links.workspace` stores a bare key; artefacts and selectors
use `workspace/{key}`. Shared `default_parent` / `default_tags` live on the hub;
local `defaults.parent` overrides the shared create parent and local tags add to
shared tags only for the locally bound workspace. Generic metadata cannot relink
the authoritative workspace.

`brain artefact set-workspace --request-json '{"path":"project/example","workspace_context":"workspace/example","recursive":true,"clear_parent":true}'`
explicitly adopts a subtree. Review the root parent choice and follow the
[adoption workflow](workflows.md#explicit-adoption-and-reassignment); tags never
trigger automatic membership migration. Terminal workspaces retain historical
membership but cannot be selected for new scoped writes.

## Recovery and compliance

Useful granular checks and repairs include:

```bash
brain doctor --json
brain vault check --vault /path/to/brain --json
brain runtime repair --vault /path/to/brain --json
brain runtime refresh-router --vault /path/to/brain --request-json '{"force":true}' --json
brain retrieval refresh-lexical --vault /path/to/brain --request-json '{"force":true}' --json
brain retrieval repair-semantic --vault /path/to/brain --json
brain workspace repair-registry --vault /path/to/brain --json
brain mcp repair --vault /path/to/brain --json
brain artefact repair --vault /path/to/brain --request-json '{"scope":"empty_folders"}' --dry-run --json
```

`artefact.repair` takes one explicit scope: `frontmatter`, `ownership` or
`empty_folders`. The `empty_folders` scope removes vacated-empty artefact
folders (folders holding nothing but empty folders and filesystem junk such as
`.DS_Store`) under type roots and `_Archive`; `vault.check` reports them as
`info` findings. Type roots and `_Archive` itself are never removed, and a
folder that gains content between the check and the repair is skipped.

`brain doctor` also reports the physical memory footprint of live Brain runtime processes (`machine.memory` in the JSON result) and warns when one process exceeds 512 MB or the total exceeds 2 GB. A session server that has answered semantic queries sits near 200 MB; anything heavier means a corpus encode or a heavyweight runtime is resident in a long-lived process, and restarting that MCP session reclaims it.

Run `brain command describe <command-id> --json` before relying on an example here: the installed catalogue is authoritative.

Document reads return `range.next_cursor` when more text remains. Repeat the same read with that cursor until it is null; restart without a cursor if the source revision changes.

## Bootstrap fallback

Use the first available route:

1. MCP `session_start` returns the canonical bootstrap. Follow `range.next_cursor` with another `session_start` until `bootstrap_complete` is true.
2. CLI `brain session start --json` returns the same application result through the selected Brain.
3. Supported direct scripts provide tool-backed access without the CLI. From the vault, `python3 .brain-core/scripts/command.py session start --request-json '{}' --json` requires an interpreter that meets the command's managed-runtime requirements.
4. Without usable MCP, CLI or scripts, read `.brain-core/index.md`, then `.brain/local/session.md` if present. This generated Markdown mirror is a projection of the canonical bootstrap, not an independently authored source of truth.
5. If the generated mirror is also unavailable, follow `.brain-core/md-bootstrap.md` to the copied core instructions, authored `_Config/router.md` and relevant taxonomy. This fallback requires no code execution, compilation or generated assets.

Complete every tool-backed `session.start` continuation before ordinary work; restart without a cursor on a source revision conflict.

## Further reference

- [System guide](system-guide.md) — artefact lifecycle, structure and naming
- [Workflows](workflows.md) — everyday use
- [MCP tools](../functional/mcp-tools.md) — granular MCP projection
- [CLI](../functional/cli.md) — CLI grammar and lifecycle
- [Scripts](../functional/scripts.md) — direct and Python parity
- [Configuration](../functional/config.md) — config, profiles and skills
