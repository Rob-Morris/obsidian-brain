# DD-053: Explicit vault-self flag and unified vault-root predicate

**Status:** Implemented (v0.47.0)
**Extends:** DD-052

## Context

DD-052 defined the resolution ladder. Its rung-2 cwd-walk resolves a vault root *by path* (vault-self), gated on `.brain-core/VERSION`. Two gaps surfaced while wiring the installer and auditing the predicate:

- A Brain you work *inside* still needs an MCP registration, but DD-052's refuse-rule (a vault is not a workspace of itself) means no self-`workspace.yaml`. A project registration that kept `BRAIN_WORKSPACE_DIR` but no binding dead-ended at the ladder's error rung, and relying on the rung-2 cwd-walk to catch it depends on the proxy launching with its cwd at the vault — not guaranteed.
- The "is this a Brain vault root" predicate had drifted. Rung 2 used a narrow `.brain-core/VERSION` check, but five other resolution / heal / refuse-guard sites used the broad `_common.is_vault_root`, which also matches an `AGENTS.md`-only directory (e.g. a dev workspace). That let an `AGENTS.md`-bearing workspace be mis-resolved as vault-self, or refused as a binding target.

## Decision

1. **Explicit vault-self flag at rung 1.** When `BRAIN_WORKSPACE_DIR` points at a directory that is itself a Brain vault root, resolve vault-self *by path* immediately — no binding lookup. This is the explicit, cwd-independent signal the installer writes for a *this-vault-only* registration (`BRAIN_WORKSPACE_DIR=<vault-root>`, no `workspace.yaml`). A non-vault workspace directory still goes through DD-052's binding classification.

2. **One vault-root predicate.** A single public `is_brain_vault` (keyed on `.brain-core/VERSION`) drives every resolution, heal, and refuse-guard decision, and the `init.py --skip-mcp` "is this a vault, so skip binding it" check. The broad `_common.is_vault_root` (with its `AGENTS.md` tolerance) is retained only for CLI `find_vault_root` discovery. Resolution that treats a path as a vault requires `.brain-core` — the resolver re-points `PYTHONPATH` at it, so a directory without one cannot be a resolution target; and an `AGENTS.md`-only workspace must still be bound, not mistaken for a vault.

3. **Vault-self registration mode.** Installing a Brain registers it in vault-self mode: a project-scope `.mcp.json` / `config.toml` carrying `BRAIN_WORKSPACE_DIR=<vault-root>` and **no** self-`workspace.yaml`, for both Claude and Codex.

4. **Refuse-guard.** `converge_workspace_binding` refuses to bind a vault root — a Brain is not a workspace of itself.

5. **Exclusive install scope.** Install-time MCP registration is one exclusive choice — *this vault only* (project scope, vault-self mode) **or** *make this your default Brain* (user scope + registry default) — never both, because `BRAIN_SERVER_NAME` is a single hardcoded `brain` and the two scopes would collide for one vault.

The machine registry is the *identity* layer (self-registration on operation), not a resolution gate; vault-self resolution stays path-based and unstaleable.

## Alternatives Considered

**Rely on the rung-2 cwd-walk for the vault's own registration.** Rejected — it depends on the proxy launching with cwd at the vault. The explicit `BRAIN_WORKSPACE_DIR=<vault-root>` flag is deterministic regardless of cwd.

**Register the vault at both project and user scope.** Rejected — one hardcoded server name means the project reg shadows the user one in the vault directory; provisioning both is redundant and confusing. The scope is exclusive.

**Keep the broad `is_vault_root` at every site.** Rejected — it matches `AGENTS.md`-only workspaces, which would then be mis-resolved as vault-self or wrongly refused as binding targets. The narrow predicate is the only correct one for resolution decisions; the broad one stays for CLI discovery.

**Keep a self-`workspace.yaml` for the vault** (exempt the installer from DD-052's refuse-rule). Rejected — it contradicts vault-self-by-path and leaves a bindable circular reference that can go stale.

## Consequences

**Positive:**
- A Brain you work inside resolves deterministically via the explicit flag, and the "install → open the vault → it works" experience is preserved without a self-binding.
- The vault-root predicate is single-sourced and correct for `AGENTS.md`-bearing workspaces (the common dev-workspace case).
- Install scope is unambiguous and never double-registers one Brain.

**Negative:**
- Rung 1 gains a vault-root special case ahead of binding classification.
- The vault-self project registration still relies on the client launching the MCP server with cwd at the vault for the rung-2 fallback, though the explicit rung-1 flag covers the primary path.

## Implementation Notes

- `_bootstrap/workspace_binding.py`: the rung-1 vault-self check in `resolve_brain_target`; the public `is_brain_vault` (the single `.brain-core/VERSION` predicate routing all resolution / heal / refuse sites, imported by `init.py`); the refuse-guard in `converge_workspace_binding` (`code="vault_root_not_workspace"`).
- `init.py --skip-mcp` uses `is_brain_vault` to skip the self-binding for a vault root while still binding an `AGENTS.md`-only workspace; `install.sh` calls it in the scaffold step so a vault's Brain-owned git ignore rules (`.brain/local/` etc.) are written independently of the MCP scope choice (covering `--skip-mcp`, user-default, and existing-vault installs).
- `_bootstrap/mcp_transport.py` + `init.py --vault-self`: the vault-self registration mode (no `workspace.yaml`), for both clients.
- `install.sh`: the exclusive scope choice and the optional `--id`.
- `PROXY_VERSION` moves to `0.5.2` so the resolver change takes effect on the next proxy restart.
