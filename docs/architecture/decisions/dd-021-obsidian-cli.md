# DD-021: Optional Obsidian CLI Integration — CLI-Preferred, Agent-Fallback

**Status:** Implemented (v0.8.0)

## Context

The Obsidian desktop app exposes a local REST API via the dsebastien/obsidian-cli plugin. This API provides richer search (using Obsidian's native index) and can trigger Obsidian's own rename/move operations (which update internal links). The question was whether to require it or treat it as optional.

## Decision

Obsidian CLI integration is optional. When the CLI is available (detected by the server at startup via a probe to `localhost:27124`), the server delegates search and rename operations to it for better results. When unavailable, the server falls back to BM25 search and Python-based rename. Agents always interact with MCP tools — they never talk to the CLI directly.

## Consequences

- The server provides good results without Obsidian running (BM25 fallback).
- When Obsidian is running, results are richer and renames use Obsidian's native link-update mechanism.
- The CLI is an optimisation layer; its absence is not an error condition.
- Search responses include a `source` field (`"obsidian_cli"` or `"bm25"`) so agents know which path was used.
