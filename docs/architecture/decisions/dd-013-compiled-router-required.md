# DD-013: Compiled Router Required for Tools; Markdown Fallback for Agents Only

**Status:** Accepted

## Context

The compiled router provides a structured, fast-to-read representation of vault configuration. The lean router (`router.md`) is human-readable markdown that agents can follow without tooling. A question arose: should scripts and MCP tools support reading the markdown router as a fallback, or require the compiled form?

## Decision

Scripts and MCP tools require the compiled router — they auto-compile if it is missing or stale (DD-014), but they never fall back to parsing markdown. The markdown router fallback (lean router + wikilink traversal) is for agents operating without MCP or Python, not for tooling.

## Consequences

- Tools have a single, typed data source — no conditional logic for "markdown vs. compiled" paths.
- The compile step is lightweight and automatic; the cost of requiring it is low.
- Agents on mobile or restricted environments use the lean router directly — this is an intentional, supported path (DD-006), not a degraded experience.
- The markdown router is always kept valid and useful, since it is the authoritative fallback for agent reading.
