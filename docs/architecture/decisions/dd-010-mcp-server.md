# DD-010: Brain MCP Server in `.brain-core/brain_mcp/`

**Status:** Implemented (v0.8.0)
**Extended by:** DD-044, DD-045

## Context

Agents using Claude Code or Cursor need a way to interact with the vault without shelling out to scripts on every operation. The Model Context Protocol (MCP) provides a standard interface for tools that agents can call. The question was where the server lives and what it owns.

## Decision

A long-running MCP server lives at `.brain-core/brain_mcp/server.py`. It is a thin wrapper over the scripts in `.brain-core/scripts/`. The composition root may delegate tool bodies to sibling MCP modules, but vault logic still lives in scripts and is never duplicated in the transport layer. It retains authenticated identity and parsed derived snapshots across calls, avoiding the cold-start cost that standalone scripts pay on each invocation.

All vault operations are implemented in the application/scripts packages; the
server adds MCP transport and process-scoped composition policy. Cached config
identity is reloaded when any of its three input signatures changes and fails
closed while a changed input is invalid. Compiled-router and lexical-index
snapshots are re-parsed when their file signatures change and are explicitly
invalidated after their rebuild commands. The same loaders remain available to
stateless direct scripts.

## Consequences

- Agents get a fast, stateful interface; scripts get a slow, stateless interface — same logic, different latency.
- New operations are always implemented in scripts first, then exposed via the server.
- Server failures are isolated from scripts — agents without MCP access can still use scripts directly.
- The server is local to each vault; there is no shared server for multiple vaults.
- Caching is an adapter concern, not an alternate implementation of application
  semantics.
