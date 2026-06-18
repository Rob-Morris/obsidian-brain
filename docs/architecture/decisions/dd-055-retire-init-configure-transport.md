# DD-055: Retire init.py and consolidate transport CLI onto configure

**Status:** Accepted
**Supersedes:** DD-023
**Extends:** DD-049, DD-051, DD-053

## Context

DD-023 made `init.py` the original setup and MCP registration script. Later
decisions split that overloaded surface: DD-051 introduced `setup workspace`
and targeted `configure ...` commands, DD-053 added explicit vault-self
registration, and the shared MCP transport implementation moved into
`_bootstrap/mcp_transport.py`.

After those changes, `init.py` no longer owned a unique behaviour model. It was
a compatibility shell over the same transport engine that `configure mcp` used,
while `install.sh --uninstall` still called a few legacy-only flags for recorded
transport removal and Claude bootstrap-line cleanup. Keeping both entry points
made the command surface harder to explain and kept tests routed through a
legacy namespace instead of the modules that own the behaviour.

The cleanup must not remove capability. Installer uninstall cleanup, recorded
MCP removal, Claude-local removal, bootstrap-line cleanup, vault-self transport,
and user-scope removal guidance all need supported replacements before deleting
the legacy script.

## Decision

Retire `src/brain-core/scripts/init.py` and remove the `brain init` CLI dispatch
noun. There is no redirect or bespoke deprecation stub; `brain init` now falls
through to the standard unknown-command path.

`configure.py` is the single public transport-management noun:

- `configure.py mcp` owns MCP transport configuration and removal for project,
  local, user, and vault-self scopes.
- `configure.py mcp --vault-self` replaces the legacy vault-self flag surface
  while still delegating to `_bootstrap/mcp_transport.py`.
- `configure.py workspace bootstrap --remove` owns Claude bootstrap-line
  cleanup for installer uninstall flows.
- `_bootstrap/mcp_transport.py` remains the transport/config-write owner.
  `configure.py` validates CLI-level arguments and delegates; it does not
  re-encode transport rules.

`install.sh --uninstall` is repointed to those configure surfaces before
`init.py` is deleted. The installer's former `init.py` calls are now covered by
configure-backed tests and an end-to-end uninstall test that proves recorded
transport state and Claude bootstrap cleanup still converge.

The MCP `brain_init` tool is unrelated. It remains the readiness/warmup probe in
`brain_mcp/_server_init.py` and is not affected by this decision.

## Alternatives Considered

**Keep `init.py` as a compatibility shim.** Rejected. DD-051 explicitly demoted
`init` from the public lifecycle surface, and the remaining behaviours now have
configure equivalents. Keeping the shim preserves an obsolete command noun and
encourages future work to patch the wrong layer.

**Leave `brain init` as a redirect to configure/setup.** Rejected. A bespoke
redirect would be new legacy surface area. The standard unknown-command path is
clearer and keeps the cleanup real.

**Move transport rules into `configure.py`.** Rejected. The transport owner is
`_bootstrap/mcp_transport.py`; duplicating those rules in configure would repeat
the drift that this cleanup removes.

## Consequences

- Automation that called `init.py` directly must move to `setup.py workspace`,
  `configure.py mcp`, or `configure.py workspace bootstrap` depending on the
  concern.
- DD-023's `init.py` setup-script contract is superseded. DD-051's split between
  setup and configure is extended by removing the retained compatibility shim.
  DD-053's vault-self registration mode remains valid, but its public CLI
  surface now lives at `configure.py mcp --vault-self`.
- Tests for permanent MCP transport, MCP state, workspace scaffold, and hook
  behaviour move to those owners directly instead of importing through the
  deleted compatibility script.
- The dead wrapper helpers `mcp_transport.find_python` and
  `mcp_transport.ensure_workspace_manifest` are removed with the shim; managed
  runtime and workspace binding now use their canonical owners.
- Historical DD and changelog entries still mention `init.py` because they record
  prior behaviour. Current-state functional, user, and architecture docs no
  longer teach it as an available script or CLI noun.

## Implementation Notes

Deletion is deliberately sequenced last:

1. Freeze and migrate behaviour coverage from `tests/test_init.py` to the real
   owners.
2. Extend `configure.py` to cover all live behaviours the installer still needs.
3. Repoint `install.sh` and prove the configure-backed uninstall path.
4. Remove the `brain init` dispatch noun.
5. Delete `init.py` and the legacy wrapper-only tests.

Predecessor DD bodies remain immutable. This DD updates only their header
metadata and the decision index.
