# DD-054: Machine-level resolution runtime for non-MCP session bootstrap

**Status:** Accepted
**Extends:** DD-048, DD-049, DD-052

## Context

DD-052 made Brain target resolution a shared, pure ladder: workspace binding first, then explicit vault root, then machine default. The MCP proxy already runs that ladder before connecting to a Brain. The non-MCP bootstrap path did not. `brain session --json` still needed a vault in scope before it could run `session.py`, which meant an external workspace with a valid workspace-owned binding could not ask for the same session payload when MCP was unavailable.

The tempting shortcut was to find any registered Brain that shipped `session.py`, run that Brain's resolver, and then jump to the target. That violates the workspace binding model. A workspace binds to one Brain and uses that Brain at that Brain's version; an unbound workspace uses the machine default Brain. No unrelated Brain's code, runtime, or resolver may execute, even transiently.

DD-048 also distinguishes machine-owned runtime infrastructure from Brain-owned managed runtimes. Brain now needs one more machine-owned bootstrap leaf: enough Python plus stdlib-only resolver code to decide which Brain should run, before any Brain has been selected.

## Decision

Install and upgrade provision a light machine-level resolution runtime under the existing `~/.brain` topology. The runtime is not a managed dependency venv. It is a stable Python launcher plus deployed stdlib-only resolver code, version-stamped independently so CLI and upgrade tests can verify it is in sync with the authored Brain source.

The authored source remains in `src/brain-core/scripts/`: `_bootstrap/workspace_binding.py` owns the resolver ladder, and `_machine/resolve_brain.py` owns the JSON entry point used by the `brain` CLI. Provisioning copies only the launcher-safe files required by that entry point into `~/.brain/resolution-runtime/`.

`brain session` is the only dispatched CLI subcommand with this pre-dispatch resolver step:

1. If `--vault` is explicit, dispatch directly to that Brain's `session.py` with the absolute `--vault`.
2. Otherwise, if a workspace anchor is supplied (`BRAIN_WORKSPACE_DIR`, `--workspace-dir`, or deprecated `--project-dir`) or no current vault is directly in scope, run the machine-level resolver.
3. If resolution yields a local Brain, dispatch only to that Brain's own `.brain-core/scripts/session.py` under that Brain's managed runtime, passing `--vault <target>`.
4. If resolution yields a degraded failure, emit a `session_resolution` payload with `vault_root: null`, the real `WorkspaceBindingError.code`, caller-observable context, and recovery guidance.
5. If resolution yields a remote Brain, return explicit "remote Brain not yet supported by non-MCP session" guidance. The binding and target shape preserve the remote seam; non-MCP remote session transport is future work.

The machine default Brain is an input to the resolver, never a host used to run the resolver. The CLI must not scan the registry for "any Brain shipping `session.py`" and must not borrow a foreign Brain's runtime, code, or resolver.

The MCP proxy keeps its current runtime path in this slice. Both proxy and CLI use the same authored resolver contract, but re-homing the proxy onto the machine resolution runtime is a separate decision if the current proxy path becomes a problem.

## Alternatives Considered

**Borrow a registered Brain to run resolver code.** Rejected. It reintroduces the very cross-Brain execution hazard the workspace-owned binding model forbids. It also creates version-floor and capability-gate problems because every Brain has a `session.py`, but only newer Brains know how to re-dispatch correctly.

**Reimplement the resolver ladder in Bash inside `cli/brain`.** Rejected. DD-052's resolver is the canonical contract. A shell copy would drift, especially around stale-vs-missing binding failures and machine registry parsing.

**Create a dedicated managed venv for resolution.** Rejected for now. The resolver is stdlib-only and intentionally tiny. A venv would add lifecycle surface without isolating any third-party dependency. If future resolver work introduces non-stdlib dependencies, that dependency change should justify a new runtime shape.

**Teach `session.py` to resolve cross-Brain bindings by itself.** Rejected. `session.py` is Brain-owned managed-plane code. It should build the payload for the Brain it belongs to, not act as a cross-Brain router.

## Consequences

- `brain session --json` becomes a viable no-MCP bootstrap fallback for valid external workspaces.
- Install and upgrade must keep the machine resolution runtime in sync with the authored source.
- The CLI gains one narrow pre-dispatch exception while preserving the script-authoritative model for actual session payload construction.
- Degraded non-MCP failures can be structured before any vault exists, so they can honestly report `vault_root: null`.
- Remote Brains remain future-compatible but unsupported for non-MCP session transport in this slice.
