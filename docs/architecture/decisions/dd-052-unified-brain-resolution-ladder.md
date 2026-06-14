# DD-052: Unified Brain resolution ladder

**Status:** Implemented (v0.45.0)
**Extends:** DD-051
**Extended by:** DD-053, DD-054

## Context

DD-051 decoupled workspace binding from MCP transport and made the Brain MCP proxy responsible for resolving the active Brain at startup: load the workspace-owned binding, resolve the symbolic Brain ID through the registry, connect to that Brain. It named the responsibility but not the *precedence* — what the proxy should do when several signals are present at once (an explicit workspace anchor, a legacy `BRAIN_VAULT_ROOT`, a cwd inside a vault, a machine default), or when a binding is present but cannot be resolved.

Before this decision the proxy resolved in the order `BRAIN_WORKSPACE_DIR` binding → `BRAIN_VAULT_ROOT` → cwd-walk binding → error, and any binding that was not perfectly valid simply raised. Two problems followed. `BRAIN_VAULT_ROOT` sat *above* the cwd-walk binding, so a legacy machine-level override could win over a workspace's own declared binding — the opposite of DD-051's "binding is the primary identity contract". And a present-but-unresolvable ("stale") binding raised indistinguishably from an absent one, so there was no notion of falling through for missing signals while terminating on broken ones.

## Decision

The proxy — and any future non-MCP bootstrap that needs the same answer — resolves a target Brain by walking one ordered ladder. Fall-through applies only to *absent* signals; a *present-but-broken* signal terminates the ladder.

1. **`BRAIN_WORKSPACE_DIR` (explicit anchor)** — consult only that workspace's binding. Valid → use it. Missing (no manifest, or no `brain` key) → go straight to rung 3 (never the cwd-walk: an explicit anchor must not cross-resolve a *different* workspace under the cwd). Stale → terminate.
2. **cwd-walk (only when no anchor is set)** — walk upward from the current directory, nearest-marker-wins: a `.brain-core/VERSION` vault root resolves *by path* (a vault root wins over a co-located stray `workspace.yaml`, and resolving by path cannot go stale); a `.brain/local/workspace.yaml` is classified — valid → use, missing → continue, stale → terminate.
3. **`BRAIN_VAULT_ROOT`** — an optional override, demoted below both bindings. A valid vault root → use.
4. **Machine default Brain** — the registry's optional default pointer (`vault_registry`); resolves → use; set-but-unresolvable → terminate.
5. **Nothing resolved** — error with a cue to bind the workspace or set a machine default.

### Binding state machine

A binding (a `workspace.yaml` carrying a `brain` key) is classified on the `brain` key alone — the `slug` is workspace identity, not Brain identity:

- **Valid** — `brain` resolves to an existing vault → use.
- **Missing** — no manifest, or no `brain` key → continue the ladder (an absent signal).
- **Stale** — `brain` present but unresolvable → **terminate with a prompt; never fall through.**

### Stale terminates; it never falls through

This is the load-bearing rule. A stale `brain` id usually means the *registry* is wrong (wiped, or not yet restored) while the workspace binding is perfectly correct. Falling through to `BRAIN_VAULT_ROOT` or the machine default would route work to the wrong Brain and mask the real fault. A present-but-broken assertion is therefore surfaced to the human (who can restore the registry entry or rebind), never silently overridden.

### The resolver is pure

Resolution is a pure function — inputs (env values, the start directory) to a resolved target or an error, with no side effects: no environment writes, no registration, no self-heal. The caller performs any environment mutation afterwards, and any legacy self-heal lives in a separate layer above the resolver. This keeps the wrong-brain-critical logic deterministic and testable in isolation, including the negative cases.

## Alternatives Considered

**Keep `BRAIN_VAULT_ROOT` above the cwd-walk binding.** Rejected. A legacy machine-level override would silently win over a workspace's own declared binding — the opposite of DD-051's primary-identity contract.

**Treat any imperfect binding as absent and fall through.** Rejected. This is the wrong-brain hazard: a stale binding (registry temporarily broken) would route to whatever lower rung happened to be set, masking the fault and potentially serving a different Brain.

**Let a missing `BRAIN_WORKSPACE_DIR` binding fall to the cwd-walk.** Rejected. An explicit anchor names one workspace; if its binding is missing, consulting the cwd could resolve a *different* workspace the process happens to sit inside — again risking wrong-Brain routing. Missing at the anchor goes straight to the co-written `BRAIN_VAULT_ROOT`.

**Make resolution self-healing inline.** Rejected for the resolver itself. Mixing mutation into resolution couples the wrong-brain-critical path to registry writes and defeats isolation testing; self-heal is a separate concern layered above the pure resolver.

## Consequences

**Positive:**

- One deterministic precedence for "which Brain?", testable as a pure function with table-driven cases, including the negative (stale → raise, never a lower rung).
- `BRAIN_VAULT_ROOT` is demoted to an optional override and is never required; the workspace binding wins, matching DD-051.
- A machine default Brain gives unbound directories a deliberate, opt-in fallback without fabricating one.
- A broken registry surfaces as an explicit prompt rather than silent mis-routing.

**Negative:**

- The proxy startup path is more involved (five rungs plus a state machine) than the previous linear order.
- Callers must supply env/cwd inputs and perform their own environment mutation, since the resolver is pure.

**Forward:** legacy configs are converged onto this ladder by an evidence-based self-heal layered *above* the pure resolver (registering a Brain on operation, completing a missing project-reg binding, seeding the default from a legacy user reg), and the installer offers the default-vs-project choice that populates rung 4. Those build on this decision without changing the precedence.

## Implementation Notes

- `_bootstrap/workspace_binding.py` owns `resolve_brain_target()` (the pure ladder) and the nearest-marker walk; `brain_mcp/proxy.py` calls it and performs the env / `PYTHONPATH` mutation afterwards.
- The machine default pointer lives in `vault_registry.py` as a separate `$XDG_CONFIG_HOME/brain/default` file.
- Vault-self detection keys on `.brain-core/VERSION` (not a looser vault-root check), so a non-vault repo root cannot masquerade as a Brain.
