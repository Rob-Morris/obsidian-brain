# Brain Reference

This page is the stable user-facing reference for Brain Core 0.62.13 and CLI 3.1.5. Exact command schemas, examples and availability come from the installed Brain rather than a duplicated hand-maintained inventory.

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

Descriptions include the exact strict request schema, minimal example, result schema, stable error/warning codes, dependency tier, locality, providers, authority, effects, retry class, projection eligibility and current availability. Default discovery is static and does not probe optional providers; request an explicit refresh only when current provider availability matters.

## MCP

MCP names preserve the canonical dotted command ID exactly:

- `session.start` → `session.start`
- `command.list` → `command.list`
- `command.describe` → `command.describe`
- `artefact.read` → `artefact.read`
- `artefact.create` → `artefact.create`
- `vault.check` → `vault.check`

Each granular tool exposes its own top-level request fields. There is no generic `request` envelope and no compatibility aggregate. The removed 1.x tools—including `brain_session`, `brain_read`, `brain_create`, `brain_edit`, `brain_define`, `brain_move`, `brain_action` and `brain_process`—are not aliases.

Start with `session.start`, then use `command.list` and `command.describe` for bounded discovery. For example, inspect `artefact.create` before supplying its fields to the `artefact.create` tool.

`shaping.start` opens or continues a taxonomy-declared shaping session after
the shaping skill selects its mode. It applies the taxonomy's status behaviour:
ordinary contracts enter `shaping`, while discovery-only preserving contracts
leave an enduring non-terminal status unchanged and refuse terminal targets.

The MCP server derives registrations, schemas, descriptions and tool hints from the selected Brain's catalogue, then exposes only the authenticated ceiling. The cumulative built-in ceilings expose 26 reader, 50 contributor, 61 maintainer, 62 operator and 63 administrator MCP tools; custom profiles use exact command names. Active access starts at Reader by default. Use `access.status`, request exact within-ceiling leases with `access.request`, and revoke them with `access.reduce`; leases do not change the visible tool catalogue.

Every MCP call checks the installed Brain Core version before composing context or executing effects. Planned pre-effect drift exits for proxy replacement and is replayed only after positive command compatibility. An unexpectedly lost mutation is never blindly replayed; query its durable reference with `invocation.read`. Receipt lookup is read-only, including for missing or expired references.

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

Use `--request-json -` to read one object from stdin. `--vault`, `--brain` and workspace binding select the Brain; they are adapter inputs, never semantic command fields. `--dry-run` is trusted execution context. Exit categories are stable: 0 success, 1 known partial, 2 request/domain failure, 3 authority/capability unavailable, and 4 infrastructure failure or unknown mutation outcome.

CLI 3 refuses application discovery against a pre-0.55 Brain. Launcher discovery and recovery remain available so the operator can run the checked upgrade. See [CLI](../functional/cli.md).

With external elevation policy, `access.request` returns a pending identifier. A separately trusted local operator approves it with `brain access approve`; that launcher-only command is not exposed to MCP or the selected-Brain direct script.

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
brain workspace bind --vault /path/to/brain \
  --workspace /absolute/path/to/workspace --request-json '{}' --json
```

MCP cannot configure the connecting agent's local filesystem. Remote-Brain transport and gateway hosting are separate from this local command architecture.

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
```

Run `brain command describe <command-id> --json` before relying on an example here: the installed catalogue is authoritative.

## Bootstrap fallback

Agents degrade in this order:

1. MCP `session.start` returns the canonical JSON session model.
2. CLI `brain session start --json` returns the same application result through the selected Brain.
3. Read `.brain-core/index.md`, then the generated `.brain/local/session.md`.
4. Follow `.brain-core/md-bootstrap.md` when generated state is unavailable.

## Further reference

- [System guide](system-guide.md) — artefact lifecycle, structure and naming
- [Workflows](workflows.md) — everyday use
- [MCP tools](../functional/mcp-tools.md) — granular MCP projection
- [CLI](../functional/cli.md) — CLI grammar and lifecycle
- [Scripts](../functional/scripts.md) — direct and Python parity
- [Configuration](../functional/config.md) — config, profiles and skills
