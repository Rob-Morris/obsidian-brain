# Bounded Context Map

Brain Core separates semantic application ownership, machine-global lifecycle ownership, transport adapters and lower-level implementation planes. These boundaries are enforced by import and catalogue tests, not naming convention alone.

## Contexts

| Context | Responsibility | Primary location |
|---|---|---|
| Application kernel | Sealed requests/results, invocation boundary, selected-Brain catalogue, resolver, discovery and projection facts | `src/brain-core/scripts/_application/` |
| Launcher application | Machine-global registry, install/upgrade, runtime recovery, MCP/client configuration and maintenance | `cli/_launcher/` |
| Local CLI composition | Parse the canonical grammar, compose discovery, select an owner, invoke without merging semantics, render structural results | `cli/_local_cli/` |
| MCP projection | Mechanical eligible-tool registration, proxy protocol and MCP result projection | `src/brain-core/brain_mcp/` |
| Trusted local composition | Selected vault/workspace, profile, providers, tier and outcome receipt storage | `src/brain-core/scripts/_command_interface/` |
| Bootstrap plane | Stdlib-safe config, selection, session, registry and recovery seams | `src/brain-core/scripts/_bootstrap/` |
| Portable plane | Low-dependency vault reads/mutations and lexical operations | `src/brain-core/scripts/_portable/` and domain packages |
| Managed plane | Optional managed-runtime retrieval, rendering and provider-backed work | managed domain/provider packages |
| Platform adapters | Obsidian and other external capabilities behind explicit provider ports | platform-specific modules |

## Import direction

Dependencies point inward:

```text
MCP / CLI / direct script
          |
trusted context + request resolution
          |
CommandApplication
          |
application executors
          |
bootstrap / portable / managed domain seams
          |
filesystem and explicit providers
```

The rules are:

- `_application` imports no MCP SDK, CLI parser, terminal renderer, implicit environment selector or concrete provisioner.
- Lower-level domain packages do not import back into `_application`.
- The launcher catalogue never imports selected-Brain application executors.
- The selected-Brain catalogue never imports machine-global launcher owners.
- `_local_cli` may compose catalogue presentation but must preserve owner/provenance and invoke across the selected Brain's process boundary.
- MCP derives registrations and schemas from application entries; it does not implement semantic variants.
- Concrete providers implement application ports and cannot elevate authority or change locality/tier metadata.

Package initialisers remain lean so importing bootstrap or portable code does not eagerly import managed dependencies.

## Ownership test

A public operation belongs to exactly one semantic owner. Split it when alternatives differ in any of:

- required fields;
- result or stable error contract;
- authority;
- minimum dependency tier;
- locality or required providers;
- atomicity, effect class or retry safety.

Data enums are appropriate only when every alternative shares those contracts. Adapter convenience is not a reason to combine semantic owners.

## Trusted context versus semantic request

Semantic request fields describe what the command should do. `InvocationContext` describes trusted execution facts: selected Brain, profile/authority, capability snapshot, providers, invocation/correlation identity, receipt writer, current tier, clock and dry-run. Adapters compose context; callers cannot smuggle it through request JSON.

This separation preserves the dependency planes: an executor consumes an already-proven context and does not rediscover environment, authenticate again, provision a runtime or hand off to another process implicitly.

## Public surfaces

The supported surfaces are projections, not owners:

- granular MCP `<noun>.<verb>`;
- CLI `brain <noun> <verb>`;
- direct `command.py <noun> <verb>`;
- sealed typed Python requests through `CommandApplication`.

Legacy aggregate MCP tools, irregular CLI aliases and public top-level operation scripts are removed at the coordinated 0.55.0/CLI 2.0 cutover. Platform install and pre-cutover recovery launchers are explicit lifecycle exceptions.

## Practical guidance

For a new selected-Brain operation, add one request/result, executor, catalogue entry and resolver registration, then prove all eligible projections mechanically. For machine-global behaviour, add a launcher request/result and owner without importing selected-Brain semantics. Shared filesystem/domain mechanics belong below the application executor, not in adapters or catalogue definitions.

See [Architecture overview](overview.md), [Security](security.md) and [DD-061](decisions/dd-061-typed-command-application-boundary.md).
