# DD-051: Workspace-owned binding and workspace-aware MCP

**Status:** Accepted
**Extends:** DD-023, DD-039, DD-040

## Context

DD-023 made `init.py` the practical setup surface for folder-scoped Brain use. DD-039 extended that surface across Claude and Codex while keeping each client's native MCP scope model. DD-040 added workspace manifests and session wiring, but still treated the active Brain route as something chosen by the MCP configuration itself.

That coupling no longer fits the product direction. A folder or external workspace may:

- already rely on a user-scoped MCP route rather than a project-scoped one,
- want no MCP setup at all during first binding,
- use a different agent stack entirely, or
- need to point different workspaces at different Brains through the same user-scoped transport.

In all of those cases, the workspace's primary concern is "which Brain do I belong to?" — not "which MCP layer wrote the route?". Binding the workspace to a Brain and configuring a transport policy are separate concerns. The system is also moving toward one canonical managed server/runtime per Brain, with transports acting as thin routes into that canonical Brain rather than carrying Brain identity themselves.

## Decision

### 1. Workspace-owned binding is the primary identity contract

The canonical workspace manifest is `.brain/local/workspace.yaml` in the workspace folder. The minimum binding contract is:

- `brain`: symbolic Brain ID
- `slug`: workspace slug inside that Brain

`brain` is symbolic, not a literal path or endpoint. Local resolution uses machine-level Brain registry aliases. Remote transport details are not part of the minimum workspace manifest contract.

### 2. Public command surface splits setup from configuration

`init` is no longer the intended public noun for workspace setup. The public lifecycle surface becomes:

- `brain setup workspace` — ensure a workspace is bound and minimally scaffolded
- `brain configure workspace ...` — targeted workspace binding / metadata / bootstrap changes
- `brain configure mcp` — explicit transport policy configuration

`setup workspace` is a convenience/orchestrator surface, not the only way to modify state. The targeted `configure ...` commands remain valid both before and after first-time setup. The distinction is "guided setup" versus "targeted configuration", not "first time" versus "second time".

### 3. `setup workspace` owns only the baseline every workspace needs

The deterministic core of `setup workspace` owns:

- creating or converging `.brain/local/workspace.yaml`
- migrating legacy workspace-manifest placement when found
- Brain-owned local scaffold such as `.brain/local/` and Brain ignore rules for git-backed workspaces

It does not implicitly choose or install MCP policy, agent bootstrap files, or optional Brain-side metadata links.

`setup workspace --guided` may offer optional branches for those concerns, but each guided branch must also exist as a direct explicit command.

### 4. MCP configuration becomes generic and workspace-aware

Persisted MCP configuration no longer binds directly to one Brain via `BRAIN_VAULT_ROOT`. Instead, persisted MCP config is generic and routes into the Brain MCP proxy. The proxy resolves the active workspace, loads its workspace-owned binding, resolves the symbolic Brain ID through machine/user transport configuration, and then launches or connects to that Brain's canonical server/runtime.

This model is the primary design. Legacy Brain-bound MCP compatibility may be tolerated during transition only when it is nearly free and does not distort the new model.

### 5. New-world first, migration second

Implementation should establish the new workspace-owned binding and workspace-aware MCP model first. After that, Brain should provide a graceful upgrade path for stale Brain-bound configs through migration, runtime resilience checks, repair guidance, or a combination of those — without reintroducing the old coupling as a co-equal design.

## Alternatives Considered

**Keep `init.py` as the main public surface and just change its defaults.** Rejected. The noun is already overloaded and keeps mixing binding, transport, and client bootstrap concerns into one ambiguous command.

**Make `configure workspace` the only public verb.** Rejected. A pure `configure` surface hides the meaningful distinction between establishing a usable baseline and changing one targeted aspect. `setup workspace` earns its place because it names a real first-time enablement family, while targeted follow-up operations live under `configure ...`.

**Keep Brain identity in persisted MCP config (`BRAIN_VAULT_ROOT`) and only decouple workspace bootstrap from MCP install.** Rejected. That still prevents one user-scoped MCP route from serving multiple workspaces bound to different Brains, and it keeps transport and identity coupled at the wrong layer.

**Put remote Brain endpoint details directly in every workspace manifest.** Rejected for the minimum contract. Endpoints and auth are transport concerns and may vary by machine; the workspace manifest should identify the Brain symbolically, while machine/user configuration resolves how that Brain is reached.

## Consequences

**Positive:**

- Workspaces can be bound to a Brain without forcing project-scoped MCP registration.
- User-scoped or other shared transport routes can serve different workspaces against different Brains because workspace binding, not the persisted transport record, chooses the Brain.
- The command surface becomes easier to reason about: setup ensures a baseline, configure changes specific aspects.
- The design aligns with the canonical-per-Brain server/runtime direction rather than baking Brain identity into each client registration.

**Negative:**

- The public surface changes decisively: `init` ceases to be a first-class public noun for this area.
- Proxy/server startup becomes more responsible because it must resolve workspace binding and symbolic Brain identity at runtime.
- Diagnostics and repair must learn the new generic-route model and distinguish stale legacy Brain-bound configs during transition.

## Implementation Notes

- `setup.py` owns the public `setup workspace` command.
- `configure.py` owns `workspace binding`, `workspace metadata`, `workspace bootstrap`, and `mcp`.
- `_bootstrap/workspace_binding.py` owns workspace manifest path rules, convergence, and legacy migration.
- `_bootstrap/mcp_state.py`, `brain_mcp/proxy.py`, and `brain_mcp/server.py` carry the workspace-aware MCP routing contract.
- `session.py`, diagnostics, and repair flows must treat workspace binding as primary and transport policy as explicit/configurable.
