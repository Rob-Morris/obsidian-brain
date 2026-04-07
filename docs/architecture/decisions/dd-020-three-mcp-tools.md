# DD-020: 3 MCP Tools: brain_read + brain_search + brain_action

**Status:** Superseded by DD-025 (v0.11.0)

## Context

DD-011 established two tools: `brain_read` (safe) and `brain_write` (mutating). As the server matured, search emerged as a distinct operation — it is read-only but behaviorally different from reading a specific resource by name. Bundling search into `brain_read` made the read tool do two conceptually different things.

## Decision

Introduce `brain_search` as a third tool. The three-tool surface: `brain_read` (read a specific resource), `brain_search` (search across artefacts), `brain_action` (mutating vault operations). All three are now named actions rather than generic read/write.

## Consequences

- Search operations become explicitly auto-approvable as a safe operation.
- The tool surface grew from 2 to 3, still small enough to be easy to reason about.
- `brain_action` replaced `brain_write` — same mutation scope, clearer name.
- As vault operations continued to grow (create, edit, archive), the single `brain_action` mutation bucket became too coarse again, leading to DD-025's privilege split into five tools.
