# DD-010: Brain MCP Server in `.brain-core/mcp/`

**Status:** Implemented (v0.8.0)

## Context

Agents using Claude Code or Cursor need a way to interact with the vault without shelling out to scripts on every operation. The Model Context Protocol (MCP) provides a standard interface for tools that agents can call. The question was where the server lives and what it owns.

## Decision

A long-running MCP server lives at `.brain-core/mcp/server.py`. It is a thin wrapper over the scripts in `.brain-core/scripts/`. The composition root may delegate tool bodies to sibling MCP modules, but vault logic still lives in scripts and is never duplicated in the transport layer. On startup it loads the compiled router and search index into memory, avoiding the cold-start cost that standalone scripts pay on each invocation.

The server is the only component that holds in-memory state (compiled router, search index, Obsidian CLI availability). All vault operations are implemented in scripts; the server adds MCP transport and in-memory caching.

## Consequences

- Agents get a fast, stateful interface; scripts get a slow, stateless interface — same logic, different latency.
- New operations are always implemented in scripts first, then exposed via the server.
- Server failures are isolated from scripts — agents without MCP access can still use scripts directly.
- The server is local to each vault; there is no shared server for multiple vaults.
