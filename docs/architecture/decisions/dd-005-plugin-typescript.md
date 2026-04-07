# DD-005: Obsidian Plugin Has Its Own TypeScript Implementation

**Status:** Accepted

## Context

Obsidian plugins must be written in TypeScript/JavaScript. Brain-core's logic lives in Python scripts. The question was whether to bridge the two (e.g. shell out from TypeScript to Python scripts) or maintain a separate TypeScript implementation of any overlapping logic.

## Decision

The Obsidian plugin has its own TypeScript implementation. It does not shell out to Python scripts. Where logic overlaps (e.g. frontmatter parsing, timestamp handling), both implementations must agree on behaviour, anchored by shared test fixtures (see DD-007).

## Consequences

- Plugin works on all platforms including mobile where Python is unavailable (aligns with DD-006).
- Two implementations of any shared logic must be kept in sync manually.
- Shared test fixtures (DD-007) are the mechanism for catching divergence between TypeScript and Python paths.
- The plugin is an independent deliverable — it can evolve on its own release cadence.
