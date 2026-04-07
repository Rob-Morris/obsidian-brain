# DD-022: Obsidian CLI is Internal to MCP; Agents Use MCP Tools or Scripts Directly

**Status:** Accepted

## Context

Once Obsidian CLI integration was added (DD-021), a question arose about the tiering: should agents be expected to call the Obsidian CLI REST API directly, or should CLI access always be mediated through the MCP server?

## Decision

The Obsidian CLI is an internal dependency of the MCP server, not a separate agent-facing tier. Agents interact only with MCP tools or, when MCP is unavailable, directly with Python scripts. The CLI is never on the agent's direct call path — the MCP server decides when to delegate to it based on availability.

## Consequences

- Agents have a single interface (MCP tools) and a single fallback (scripts). No third option to reason about.
- The CLI delegation strategy is encapsulated in the server; agents don't need to know whether a given operation used the CLI or BM25.
- If the CLI API changes, only the server needs updating — not agent prompts or skills.
- Scripts provide full functionality without the CLI, matching the server's fallback behaviour.
