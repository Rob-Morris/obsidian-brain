# Architecture Overview

## System overview

Obsidian Brain is a filesystem-first knowledge system with one typed command application shared by agents, CLI users, direct automation and Python callers. Markdown and YAML remain the durable source of truth; generated state is disposable and rebuildable.

The command architecture separates two authorities:

- the **selected-Brain application** owns operations whose meaning belongs to one installed Brain;
- the **machine-global launcher** owns Brain selection, install/upgrade, global CLI replacement, MCP client configuration and machine maintenance.

Neither catalogue imports, copies or manufactures the other's semantic owners.

## Component map

### Vault content

- `_Config/` — user-owned taxonomy, templates, triggers, skills, memories, styles and preferences;
- `_Temporal/` — time-bound artefacts, optionally filed beneath living owner chains;
- living type folders — evolving artefacts and their owner-projected subfolders;
- `_Archive/` — deliberate removals outside the active namespace;
- `_Assets/` — derived and attachment assets;
- `.brain/` — Brain-owned state and machine-local state;
- `.brain-core/` — versioned installed application, immutable to normal vault commands.

### Selected-Brain application

`src/brain-core/scripts/_application/` is the transport-neutral application package. It owns:

- sealed typed request/result payloads;
- immutable command identifiers and command versions;
- `CommandApplication(context).invoke(request)`;
- the authoritative `brain.command-catalogue/1` and dynamic request resolver;
- dependency tier, locality, provider, authority, effect, retry and projection facts;
- structural `ok | partial | error` results and outcome receipts;
- mechanical request/result schemas and minimal examples.

Application executors call lower-level `_bootstrap`, `_portable`, `_common`, `_lifecycle`, `_search` and related domain packages. Those packages do not import back into `_application`. The application package imports no MCP SDK, parser, terminal renderer, implicit environment selector or concrete provisioning owner.

### Machine-global launcher

`cli/_launcher/` owns the independent stdlib-safe `brain.launcher-catalogue/1`. Its typed commands cover Brain registry/selection, install/uninstall/upgrade, managed-runtime recovery, MCP and agent-skill configuration, operator key generation and machine maintenance.

The launcher cannot invent selected-Brain application commands. The outer CLI composes discovery presentation from the two catalogues while preserving owner and provenance; identity collisions fail closed.

### Adapters

- `brain_mcp/` registers every MCP-eligible application command under its canonical `<noun>.<verb>` identifier;
- `cli/_local_cli/` maps the one noun/verb grammar to either a launcher owner or the selected Brain's own `command.py` process;
- `scripts/command.py` is the direct selected-Brain projection;
- typed Python constructs a sealed request and invokes `CommandApplication` with trusted context.

Adapters own parsing, selection, transport and presentation. They do not own semantic branching. Trusted `InvocationContext`—selected Brain, authenticated profile, provider bindings, invocation identity, receipt writer, tier, clock and dry-run—is composed outside the semantic request and cannot be supplied as request JSON.

## One command grammar

Every public semantic operation has one canonical dot identifier:

```text
artefact.create
artefact.read
vault.check
brain.upgrade
```

The CLI spelling is `brain <noun> <verb>`. MCP names preserve canonical `<noun>.<verb>` identifiers exactly. The direct script uses `<noun> <verb>`, and Python uses the corresponding sealed request type. Alternatives become separate commands when they differ in required fields, results/errors, authority, dependency tier, locality, atomicity, retry or effect behaviour.

`command.list` and `command.describe` expose exact installed contracts. Static discovery never probes optional providers; explicit refresh creates one bounded capability snapshot. Provider and aggregate deadlines degrade late work to unknown behind a fixed process-wide daemon bound, so refresh cannot accumulate unbounded stuck workers or delay process exit. Documentation and generated fixtures point to catalogue discovery instead of becoming a second operation inventory.

## Result and recovery model

All application projections preserve `brain.command-result/1`:

- `ok` contains a typed result and may enumerate committed effects;
- `partial` contains the known committed effects and a typed error;
- `error` contains no result and declares either no effects or an unknown mutation outcome.

Unexpected mutation loss is never replayed blindly. Effect-bearing invocation outcomes are written to bounded, privacy-minimal receipts. `invocation.read` resolves a durable reference without creating storage, locking files or deleting expired state; absence of a conclusive receipt never proves no effect. Stable exit categories and MCP error projection derive from the same structure.

## Dependency planes

Commands declare an ordered minimum dependency tier:

1. **bootstrap** — stdlib-safe discovery and recovery;
2. **portable** — portable Brain operations;
3. **managed** — operations requiring the managed runtime.

Tier, locality and providers are independent. Selected-Brain and machine-global locality do not imply a tier. Required providers block execution; optional providers can enrich an otherwise complete result. Adapters do not silently provision, hand off, elevate authority or switch the selected Brain.

## Configuration and generated state

Vault configuration merges:

1. `.brain-core/defaults/config.yaml`;
2. `.brain/config.yaml`;
3. `.brain/local/config.yaml` for permitted machine-local defaults.

The shared `vault` zone cannot be overridden locally. Malformed configuration and unknown profile tools fail closed.

`.brain/local/compiled-router.json`, lexical/semantic indexes, session mirrors, registries and other derived state are hash-validated caches. Human-readable config and content remain authoritative. Owners either rebuild stale state or return an explicit unavailable/error result; they do not silently serve known-stale data.

## Security boundaries

Caller-supplied paths are resolved against explicit roots and checked for traversal, symlinks and protected namespaces before effects. Fixed-destination definition and attachment owners narrow authority rather than broadening general write permissions. Mutating selected-Brain commands share a vault-scoped cross-process lock.

Profile authority is derived from the application catalogue. Built-ins project exact cumulative reader, contributor and operator leaves. The 0.55.0 cutover migrates legacy profile names once; there is no runtime aggregate compatibility fallback.

The MCP proxy and replacement server exchange a strict command-interface header. An incompatible proxy fails before tool lookup. Planned pre-effect restart can replay only a positively compatible command; unexpected read loss retries at most once; unexpected mutation loss uses receipts and never blind replay.

## Installation and checked cutover

The Brain CLI is a small platform bootloader plus a versioned distribution. A fresh install writes a matching Brain Core, catalogue, launcher, CLI, proxy and installer set.

Upgrade preflights the complete local Brain registry and classifies every local/remote/stale entry before mutation. Other local Brains affected by the machine-global CLI replacement require exact acknowledgement; stale exclusions are explicit. Brain Core and the CLI distribution commit inside one checked transaction. Failure either proves restoration of the old set or retains recovery material and reports uncertainty honestly.

The current CLI refuses application discovery against a pre-cutover Brain but retains launcher-owned discovery and recovery. Old direct Brain scripts remain available only as recovery material in the old installation; the new release does not ship public compatibility aliases.

## Agent bootstrap

Agents bootstrap in this order:

1. MCP `session.start` returns the canonical JSON session model.
2. CLI `brain session start --json` invokes the same selected-Brain command.
3. `.brain-core/index.md` routes to the generated `.brain/local/session.md`.
4. `.brain-core/md-bootstrap.md` routes to raw config when generated state is unavailable.

The workspace configuration record describes local CLI work and never substitutes server paths for the connecting agent's filesystem. Remote transport and gateway hosting are separate from this local command architecture.

## Version policy

- incompatible MCP identity/request projection increments the interface epoch;
- breaking command input, result, stable-code or semantic changes increment that command version;
- result, catalogue, launcher and proxy shapes version independently;
- fingerprints validate one exact static catalogue and are not compatibility versions.

Local calls bind to installed versions rather than accepting caller-supplied command versions.

## Cross-references

- [Bounded contexts](bounded-contexts.md)
- [Security model](security.md)
- [Typed command application boundary](decisions/dd-061-typed-command-application-boundary.md)
- [MCP tools](../functional/mcp-tools.md)
- [CLI](../functional/cli.md)
- [Scripts](../functional/scripts.md)
