# DD-002: Scripts Ship Inside `.brain-core/`

**Status:** Accepted

## Context

Brain-core needs Python scripts to operate on vault files: compiling the router, creating artefacts, running checks. The question was where these scripts live — as a separate installed tool, as a plugin, or bundled inside the `.brain-core/` directory that is already copied into the vault.

## Decision

Scripts ship inside `.brain-core/scripts/`. They are distributed as part of brain-core and copied into the vault during install or upgrade. No separate installation step is required beyond placing `.brain-core/` in the vault.

## Consequences

- Scripts are version-locked to the brain-core engine they ship with — no version mismatch between engine and scripts.
- Upgrading brain-core upgrades the scripts atomically.
- Agents and MCP tools can always locate scripts at a stable relative path inside the vault.
- Scripts are overwritten on upgrade; user-customised copies would be lost (the convention is that user logic lives in `_Config/`, not in scripts).
- No dependency on a system-level install or PATH configuration.
