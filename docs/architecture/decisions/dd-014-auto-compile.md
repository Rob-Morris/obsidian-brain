# DD-014: MCP Server Auto-Compiles Router on Startup

**Status:** Implemented (v0.7.0)
**Extended by:** DD-042

## Context

The compiled router is stale the moment any source file (taxonomy, router.md, VERSION) changes. If the server starts with a stale router, all tools work against outdated configuration until someone manually recompiles. Requiring a manual compile step before each server start is friction that will be skipped.

## Decision

The MCP server checks whether the compiled router is missing or stale (via SHA-256 hash comparison against source files) during startup and compiles automatically if needed. Individual tools also auto-compile when they detect staleness mid-session — for example, when a new taxonomy file appears while the server is running.

## Consequences

- Operators and agents never need to think about router freshness; it is always current.
- Startup time increases slightly when a compile is triggered, but the compile is fast (Python, stdlib only).
- Mid-session auto-recompile on new taxonomy files means the server stays correct without a restart.
- The auto-compile behaviour is in the server only; standalone script calls pay the staleness check cost but also auto-compile via the same mechanism.
