# DD-015: Single-Line Install — Never Require Changes to Agents.md

**Status:** Implemented (v0.4.0)

## Context

Brain-core needs to be installed into a vault, and the MCP server needs to be registered with the agent environment. If installation requires the user to manually edit `Agents.md` or any configuration file, the barrier to adoption rises and the chance of misconfiguration increases.

## Decision

Installation is a single command (`bash <(curl ...)` or `bash install.sh ~/vault`). The install script handles everything needed to scaffold the vault: copying brain-core, scaffolding config, and attempting MCP setup via `init.py` unless the caller explicitly skips it. `Agents.md` is never modified by the install process — it is created fresh from a template if absent, left untouched if present.

## Consequences

- New users can have a working vault scaffold without understanding the internal structure.
- `Agents.md` remains user-owned — the system does not overwrite personal agent preferences.
- The install script must remain self-contained and robust enough to handle existing vaults, upgrades, uninstalls, and partial MCP setup failures without manual intervention.
- Keeping the install path working is a maintenance obligation — it must be tested on each release.
