# DD-053: Explicit vault-self flag and unified vault-root predicate

**Status:** Implemented (v0.47.0, refined v0.48.0)
**Extends:** DD-052, DD-049

## Context

DD-052 defined the resolution ladder. Its rung-2 cwd-walk resolves a vault root *by path* (vault-self), gated on `.brain-core/VERSION`. Several gaps surfaced while wiring the installer, auditing the predicate, and signing the work off:

- A Brain you work *inside* still needs an MCP registration, but DD-052's refuse-rule (a vault is not a workspace of itself) means no self-`workspace.yaml`. A project registration that kept `BRAIN_WORKSPACE_DIR` but no binding dead-ended at the ladder's error rung, and relying on the rung-2 cwd-walk to catch it depends on the proxy launching with its cwd at the vault — not guaranteed.
- The "is this a Brain vault root" predicate had drifted. Rung 2 used a narrow `.brain-core/VERSION` check, but other resolution / heal / refuse-guard sites used the broad `_common.is_vault_root`, which also matches an `AGENTS.md`-only directory (e.g. a dev workspace). That let an `AGENTS.md`-bearing workspace be mis-resolved as vault-self, or refused as a binding target.
- DD-052's ladder sent a rung-1 anchor (`BRAIN_WORKSPACE_DIR`) with a *missing* binding "straight to rung 3"; the tail of that path — what happens when rung 3 also fails to resolve — was left as the design's Open Decision #2.

## Decision

1. **Explicit vault-self flag at rung 1.** When `BRAIN_WORKSPACE_DIR` points at a directory that is itself a Brain vault root, resolve vault-self *by path* immediately — no binding lookup. This is the explicit, cwd-independent signal the installer writes for a *this-vault-only* registration (`BRAIN_WORKSPACE_DIR=<vault-root>`, no `workspace.yaml`). A non-vault workspace directory still goes through DD-052's binding classification.

2. **One vault-root predicate, single-sourced in `_common`.** A single narrow predicate — `_common.is_brain_vault` (keyed on `.brain-core/VERSION`) — drives every resolution, heal, and refuse-guard decision, plus the `init.py --skip-mcp` "is this a vault, so skip binding it" check; `workspace_binding` and `mcp_state` both import it. The broad `_common.is_vault_root` (with its `AGENTS.md` tolerance) is retained, under its distinct name, only for CLI `find_vault_root` discovery. `upgrade.py` keeps its own self-contained narrow check by design — it runs at bootstrap, before the managed runtime, and must not import the wider package. Resolution that treats a path as a vault requires `.brain-core` — the resolver re-points `PYTHONPATH` at it, so a directory without one cannot be a resolution target; and an `AGENTS.md`-only workspace must still be bound, not mistaken for a vault.

3. **Vault-self registration mode.** Installing a Brain registers it in vault-self mode: a project-scope `.mcp.json` / `config.toml` carrying `BRAIN_WORKSPACE_DIR=<vault-root>` and **no** self-`workspace.yaml`, for both Claude and Codex.

4. **Refuse-guard.** `converge_workspace_binding` refuses to bind a vault root — a Brain is not a workspace of itself.

5. **Exclusive install scope.** Install-time MCP registration is one exclusive choice — *this vault only* (project scope, vault-self mode) **or** *make this your default Brain* (user scope + registry default) — never both, because `BRAIN_SERVER_NAME` is a single hardcoded `brain` and the two scopes would collide for one vault.

The machine registry is the *identity* layer (self-registration on operation), not a resolution gate; vault-self resolution stays path-based and unstaleable.

### Sign-off refinements (v0.48.0)

A sign-off review of the resolution + legacy-migration work (DD-052, DD-053) refined two behaviours and corrected one record. None reworks the resolution model; each tightens it or fixes an overclaim.

6. **Single-sourcing the narrow predicate, finished.** Decision 2 above consolidated the predicate in intent at v0.47.0, but a byte-identical narrow twin remained as `mcp_state.is_vault_root`, beside the broad `_common.is_vault_root` — two copies of one rule under colliding names. v0.48.0 moved the single definition to `_common.is_brain_vault` and made both `workspace_binding` and `mcp_state` import it; the twin is removed. The scope of the claim, stated honestly: this single-sources the predicate for the resolution / heal / refuse-guard domain — `upgrade.py`'s deliberate carve-out (above) is the one exception.

7. **An explicit anchor with a missing binding hard-errors (design Open Decision #2).** Refining DD-052's "straight to rung 3": if rung 3 does **not** resolve (no usable `BRAIN_VAULT_ROOT`), resolution **hard-errors** rather than falling through to the rung-4 machine default. An explicit anchor means the workspace *was* deliberately project-bound to a specific Brain; a lost binding makes that Brain unknowable, and the machine default could be a *different* Brain — serving it silently is a wrong-brain hazard. The conservative behaviour surfaces "re-bind this workspace." (The brain-less, *no*-anchor case still resolves to the default — there the default was always correct; the distinguishing signal is the presence of `BRAIN_WORKSPACE_DIR`.)

8. **DD-049's dispatch surface includes `setup`.** DD-049 enumerates the dispatched `brain` subcommands; that frozen list predates `setup` joining `configure` as a dispatched subcommand. Both are dispatched, both satisfy DD-049's top-level-`--vault` contract (the Phase-0 `make_vault_parent_parser` work), and both are covered by the dispatch-guard test. Recorded here by reference rather than by editing DD-049's immutable body — which is why this DD extends DD-049.

## Alternatives Considered

**Rely on the rung-2 cwd-walk for the vault's own registration.** Rejected — it depends on the proxy launching with cwd at the vault. The explicit `BRAIN_WORKSPACE_DIR=<vault-root>` flag is deterministic regardless of cwd.

**Register the vault at both project and user scope.** Rejected — one hardcoded server name means the project reg shadows the user one in the vault directory; provisioning both is redundant and confusing. The scope is exclusive.

**Keep the broad `is_vault_root` at every site.** Rejected — it matches `AGENTS.md`-only workspaces, which would then be mis-resolved as vault-self or wrongly refused as binding targets. The narrow predicate is the only correct one for resolution decisions; the broad one stays for CLI discovery.

**Keep a self-`workspace.yaml` for the vault** (exempt the installer from DD-052's refuse-rule). Rejected — it contradicts vault-self-by-path and leaves a bindable circular reference that can go stale.

**Leave the `mcp_state` predicate twin.** Rejected — the same rule under a name colliding with the broad `_common.is_vault_root` is exactly what let the duplicate hide; a single import-from-`_common` predicate removes the drift risk.

**Fall through to the machine default for a missing anchor (Decision #2 alternative).** Rejected — convenient, but a wrong-brain hazard for a workspace that had a specific, now-lost intent. Surfacing the error is the conservative, design-aligned choice.

**Record the sign-off corrections in a separate later DD, or edit DD-049 in place.** Rejected — this changeset is still being authored (unpushed), so DD-053 is not yet frozen and absorbs its own corrections directly; a separate DD would fragment one coherent decision across the index. DD-049 *is* frozen (shipped v0.38.0), so its `setup` correction is recorded here by reference, not by rewriting its body.

## Consequences

**Positive:**
- A Brain you work inside resolves deterministically via the explicit flag, and the "install → open the vault → it works" experience is preserved without a self-binding.
- The vault-root predicate is single-sourced in `_common` and correct for `AGENTS.md`-bearing workspaces (the common dev-workspace case); no silent drift if the marker changes.
- Install scope is unambiguous and never double-registers one Brain.
- A lost project binding can no longer be silently served the machine default — it surfaces a rebind cue.

**Negative:**
- Rung 1 gains a vault-root special case ahead of binding classification.
- The vault-self project registration still relies on the client launching the MCP server with cwd at the vault for the rung-2 fallback, though the explicit rung-1 flag covers the primary path.
- A workspace whose binding was *intentionally* deleted (to follow the default) now errors until re-bound or `BRAIN_WORKSPACE_DIR` is cleared.

## Implementation Notes

- `_common/_vault.py` defines the narrow `is_brain_vault` (the single `.brain-core/VERSION` predicate routing all resolution / heal / refuse sites); `workspace_binding.py`, `_bootstrap/mcp_state.py`, and `init.py` import it (the `mcp_state.is_vault_root` twin is removed).
- `_bootstrap/workspace_binding.py`: the rung-1 vault-self check in `resolve_brain_target`; the refuse-guard in `converge_workspace_binding` (`code="vault_root_not_workspace"`). `resolve_brain_target` tracks an `anchor_missing` flag and raises (`code="no_brain"`) after rung 3 when an explicit anchor's binding is missing and no `BRAIN_VAULT_ROOT` resolved; the stale-binding message distinguishes "id not in the registry" from "registered but its vault is missing/moved" (`_stale_binding_detail`).
- `init.py --skip-mcp` uses `is_brain_vault` to skip the self-binding for a vault root while still binding an `AGENTS.md`-only workspace; `install.sh` calls it in the scaffold step so a vault's Brain-owned git ignore rules (`.brain/local/` etc.) are written independently of the MCP scope choice (covering `--skip-mcp`, user-default, and existing-vault installs).
- `_bootstrap/mcp_transport.py` + `init.py --vault-self`: the vault-self registration mode (no `workspace.yaml`), for both clients.
- `install.sh`: the exclusive scope choice and the optional `--id`.
- Heal `except` clauses are narrowed to the raisable set; the outer best-effort backstop in `resolve_and_heal` is retained so a heal bug cannot crash proxy startup. The dead `find_bound_workspace_dir` is removed. `brain doctor` surfaces a legacy `BRAIN_VAULT_ROOT` as an informational finding (design Decision #6, `_bootstrap/diagnostics.py`).
- `PROXY_VERSION` moves to `0.5.2` (v0.47.0) and then `0.5.3` (v0.48.0) so each resolver change takes effect on the next proxy restart.
