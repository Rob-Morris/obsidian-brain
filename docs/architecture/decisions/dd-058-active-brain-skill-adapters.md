# DD-058: Resolve client skill workflows from the active Brain

**Status:** Implemented (v0.53.0)
**Extends:** DD-024, DD-052, DD-055

## Context

Brain's core skills are versioned inside each vault under `.brain-core/skills/`,
but Claude and Codex discover globally installed skills under separate user-home
directories. Copying a complete core skill into both clients creates three
independent workflow copies. It also creates cross-vault version skew: upgrading
one Brain can install instructions that call a new MCP contract while another
active Brain still exposes the old contract.

The shaping action rename in v0.53 makes this concrete. Existing global shaping
copies still call `start-shaping`, while the matching v0.53 core skill calls
`shape`.

## Decision

Install a thin, version-neutral `shaping` discovery adapter for Claude and/or
Codex. The adapter calls `brain_session`, then reads and follows
`.brain-core/skills/shaping/SKILL.md` and its relative sub-skills through
`brain_read(resource="file", ...)`. The active Brain therefore owns the workflow
and MCP contract together.

`configure.py agent-skills --client claude|codex|all` owns installation. Claude
and Codex share one adapter document and one ownership policy; only their
destination roots differ.

The shared document is checked in at
`client-adapters/shaping/SKILL.md`. This gives upgrade an exact content identity:
when that file is introduced or modified, `upgrade.py` emits a structured
`configure_agent_skills` follow-up and renders its command in human output.
Changes under `skills/shaping/` do not emit the follow-up because installed
adapters already resolve that workflow dynamically.

Each installed adapter has a sibling Brain ownership marker containing the
expected content hash. Configuration updates only an unmodified managed adapter.
An unmanaged existing skill is preserved unless the operator passes
`--replace`, which moves the complete old directory to a recoverable sibling
backup under `~/.<client>/.brain-skill-backups/` before installing the adapter.
Keeping backups outside `skills/` prevents clients from rediscovering archived
workflow files. Removal likewise touches only an unmodified Brain-managed
adapter. Symlinked destinations, including the backup root, fail closed.

Normal vault upgrade does not write client-global skill directories. Once the
stable adapter is installed, no per-release client update is needed because it
loads workflow instructions from the active Brain at invocation time. Upgrade
only reports the explicit command when the stable adapter itself changed.

## Consequences

- Claude and Codex discover shaping natively without owning its workflow.
- Multiple vault versions can coexist without a newest-vault-wins global copy.
- User-authored or locally modified client skills are never silently replaced.
- Initial migration from a full copied skill is explicit and recoverable.
- The adapter requires a working Brain MCP connection, which shaping already
  requires.
- Additional core skills should receive adapters only when a real client
  discovery need exists; this decision does not pre-emptively mirror every core
  skill.
