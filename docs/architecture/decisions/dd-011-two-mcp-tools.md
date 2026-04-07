# DD-011: MCP Server Exposes 2 Tools with Read/Write Safety Split

**Status:** Superseded by DD-025 (v0.11.0)

## Context

When the MCP server was first introduced (DD-010), a minimal tool surface was preferred. The primary concern was establishing a safe/unsafe boundary so agents could auto-approve read operations without reviewing every call.

## Decision

The initial MCP server exposed two tools: `brain_read` (safe, no side effects, auto-approvable) and `brain_write` (mutating operations requiring approval). This established the safety split as a first-class design constraint.

## Consequences

- A two-tool surface was simple to reason about but too coarse — `brain_write` covered operations ranging from creating a single file to vault-wide renames, all requiring the same approval level.
- As capabilities grew, the write tool became a catch-all with no granularity for trust escalation.
- This led to DD-020 (adding `brain_search` as a third safe tool) and ultimately DD-025 (splitting mutations into three privilege tiers with granular permissions).
- The core insight — that read operations should be auto-approvable — survived into the current design.
